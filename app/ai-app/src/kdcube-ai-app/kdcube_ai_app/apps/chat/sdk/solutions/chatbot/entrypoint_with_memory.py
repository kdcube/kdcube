# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics
from kdcube_ai_app.infra.plugin.agentic_loader import api, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import Config

MEMORY_RECONCILIATION_WORK_KIND = "memory.reconciliation.run"


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"", "0", "false", "off", "disabled", "no"}:
        return False
    if normalized in {"1", "true", "on", "enabled", "yes"}:
        return True
    return default


def _deep_merge_missing(target: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            _deep_merge_missing(target[key], value)
    return target


_memory_reconciliation_locks_guard = threading.Lock()
_memory_reconciliation_locks: dict[str, asyncio.Lock] = {}
_memory_user_override: ContextVar[dict[str, str] | None] = ContextVar("kdcube_memory_user_override", default=None)


class MemoryEntrypointMixin:
    """Optional user-memory API/widget capability.

    This is intentionally a mixin, not a BaseEntrypoint subclass.  Use it before
    the concrete base in the MRO:

        class MyEntrypoint(MemoryEntrypointMixin, BaseEntrypointWithEconomics): ...

    or use the convenience classes below.
    """

    @property
    def configuration(self) -> Dict[str, Any]:
        config = copy.deepcopy(super().configuration or {})
        return _deep_merge_missing(config, self.memory_configuration_defaults())

    def memory_configuration_defaults(self) -> Dict[str, Any]:
        return {
            "memory": {
                "enabled": False,
                "announce": {
                    "enabled": False,
                    "limit": 8,
                    "scope_filter": "current_bundle",
                    "timeout_seconds": 1.5,
                },
                "tools": {
                    "enabled": False,
                    "allow_write": False,
                    "default_scope_filter": "current_bundle",
                    "embedding_enabled": True,
                    "embedding_timeout_seconds": 3.0,
                },
                "widget": {
                    "enabled": False,
                    "allow_write": True,
                    "default_scope_filter": "current_bundle",
                    "allow_all_user_memories": True,
                    "ensure_schema": True,
                    "limit": 30,
                },
                "reconciliation": {
                    "enabled": True,
                    "max_candidates": 40,
                    "max_jobs": 20,
                    "storage_prefix": "memory/reconciliation/jobs",
                    "timeout_seconds": 45.0,
                },
                "snapshots": {
                    "enabled": True,
                    "max_memories": 1000,
                    "max_snapshots": 30,
                    "storage_prefix": "memory/snapshots",
                },
            },
            "ui": {
                "web_app_widgets": {
                    "memories": {
                        "enabled": False,
                        "src_folder": str(
                            Path(__file__).resolve().parents[2] / "context" / "memory" / "ui" / "widget" / "memories"
                        ),
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                },
            },
        }

    def _memory_config(self) -> Dict[str, Any]:
        memory_cfg = (self.configuration or {}).get("memory") or {}
        return memory_cfg if isinstance(memory_cfg, dict) else {}

    def _memory_widget_config(self) -> Dict[str, Any]:
        memory_cfg = self._memory_config()
        widget_cfg = memory_cfg.get("widget") if isinstance(memory_cfg.get("widget"), dict) else {}
        return widget_cfg if isinstance(widget_cfg, dict) else {}

    def _memory_widget_enabled(self) -> bool:
        memory_cfg = self._memory_config()
        widget_cfg = self._memory_widget_config()
        return _truthy(memory_cfg.get("enabled"), False) and _truthy(widget_cfg.get("enabled"), False)

    def _memory_widget_write_enabled(self) -> bool:
        widget_cfg = self._memory_widget_config()
        return self._memory_widget_enabled() and _truthy(widget_cfg.get("allow_write"), True)

    def _memory_reconciliation_config(self) -> Dict[str, Any]:
        memory_cfg = self._memory_config()
        reconciliation_cfg = memory_cfg.get("reconciliation") if isinstance(memory_cfg.get("reconciliation"), dict) else {}
        return reconciliation_cfg if isinstance(reconciliation_cfg, dict) else {}

    def _memory_reconciliation_enabled(self) -> bool:
        reconciliation_cfg = self._memory_reconciliation_config()
        return self._memory_widget_enabled() and _truthy(reconciliation_cfg.get("enabled"), True)

    def _memory_snapshot_config(self) -> Dict[str, Any]:
        memory_cfg = self._memory_config()
        snapshot_cfg = memory_cfg.get("snapshots") if isinstance(memory_cfg.get("snapshots"), dict) else {}
        return snapshot_cfg if isinstance(snapshot_cfg, dict) else {}

    def _memory_snapshot_enabled(self) -> bool:
        snapshot_cfg = self._memory_snapshot_config()
        return self._memory_widget_enabled() and _truthy(snapshot_cfg.get("enabled"), True)

    def _memory_reconciliation_lock_key(self, scope_filter: str) -> str:
        scope = self._memory_scope()
        return ":".join([
            scope.tenant,
            scope.project,
            scope.user_id,
            scope.bundle_id or "bundle",
            self._memory_scope_filter(scope_filter),
        ])

    def _memory_reconciliation_active_lock_key(self, scope_filter: str) -> str:
        digest = hashlib.sha256(self._memory_reconciliation_lock_key(scope_filter).encode("utf-8")).hexdigest()[:24]
        return f"kdcube:memory:reconciliation:active:{digest}"

    def _memory_reconciliation_lock(self, scope_filter: str) -> asyncio.Lock:
        loop_key = str(id(asyncio.get_running_loop()))
        key = f"{loop_key}:{self._memory_reconciliation_lock_key(scope_filter)}"
        with _memory_reconciliation_locks_guard:
            lock = _memory_reconciliation_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _memory_reconciliation_locks[key] = lock
            return lock

    @contextmanager
    def _memory_user_identity(
        self,
        *,
        user_id: str,
        fingerprint: str = "",
        user_type: str = "registered",
    ):
        token = _memory_user_override.set({
            "user_id": str(user_id or "").strip(),
            "fingerprint": str(fingerprint or "").strip(),
            "user_type": str(user_type or "registered").strip() or "registered",
        })
        try:
            yield
        finally:
            _memory_user_override.reset(token)

    def _memory_effective_user_type(self, default: str = "registered") -> str:
        override = _memory_user_override.get()
        if isinstance(override, dict) and str(override.get("user_type") or "").strip():
            return str(override.get("user_type") or "").strip()
        return str(default or "registered").strip() or "registered"

    def _memory_scope(self):
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemoryScope

        actor = getattr(self.comm_context, "actor", None)
        user = getattr(self.comm_context, "user", None)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        override = _memory_user_override.get()
        override_user_id = ""
        if isinstance(override, dict):
            override_user_id = str(override.get("user_id") or "").strip()
        return MemoryScope(
            tenant=getattr(actor, "tenant_id", None) or self.settings.TENANT,
            project=getattr(actor, "project_id", None) or self.settings.PROJECT,
            user_id=override_user_id or getattr(user, "user_id", None) or getattr(self.comm, "user_id", None) or "anonymous",
            bundle_id=getattr(bundle_spec, "id", None) or "",
        ).normalized()

    def _memory_store(self):
        from kdcube_ai_app.apps.chat.sdk.context.memory import UserMemoryStore

        if self.pg_pool is None:
            raise RuntimeError("memory widget requires pg_pool")
        scope = self._memory_scope()
        return UserMemoryStore(pg_pool=self.pg_pool, tenant=scope.tenant, project=scope.project)

    def _memory_scope_filter(self, value: str = "") -> str:
        from kdcube_ai_app.apps.chat.sdk.context.memory import normalize_scope_filter

        widget_cfg = self._memory_widget_config()
        default_filter = str(widget_cfg.get("default_scope_filter") or "current_bundle")
        normalized = normalize_scope_filter(value or default_filter)
        allow_all = _truthy(widget_cfg.get("allow_all_user_memories"), True)
        if normalized == "all_user_memories" and not allow_all:
            return "current_bundle"
        return normalized

    def _memory_widget_text_limit(self, key: str, default: int) -> int:
        try:
            value = int(self._memory_widget_config().get(key) or default)
        except Exception:
            value = default
        return max(1, min(value, 20000))

    def _memory_widget_validate_text(self, *, memory: Any, context: Any) -> Optional[Dict[str, Any]]:
        memory_text = str(memory or "").strip()
        context_text = str(context or "").strip()
        if not memory_text:
            return self._memory_error("memory_required", "Memory text is required.")
        memory_max = self._memory_widget_text_limit("max_memory_chars", 4000)
        context_max = self._memory_widget_text_limit("max_context_chars", 4000)
        if len(memory_text) > memory_max:
            return self._memory_error("memory_too_long", f"Memory text is too long; max {memory_max} characters.")
        if len(context_text) > context_max:
            return self._memory_error("memory_context_too_long", f"Memory context is too long; max {context_max} characters.")
        return None

    def _memory_widget_terms(self, values: Sequence[str] | str) -> list[str]:
        from kdcube_ai_app.apps.chat.sdk.context.memory import normalize_terms

        try:
            max_terms = int(self._memory_widget_config().get("max_terms") or 32)
        except Exception:
            max_terms = 32
        try:
            max_chars = int(self._memory_widget_config().get("max_term_chars") or 64)
        except Exception:
            max_chars = 64
        max_terms = max(1, min(max_terms, 128))
        max_chars = max(1, min(max_chars, 256))
        return [term[:max_chars] for term in normalize_terms(values)[:max_terms]]

    def _memory_limit(self, value: Any = None) -> int:
        try:
            raw = int(value or self._memory_widget_config().get("limit") or 30)
        except Exception:
            raw = 30
        return max(1, min(raw, 100))

    def _memory_reconciliation_limit(self, value: Any = None) -> int:
        cfg = self._memory_reconciliation_config()
        try:
            raw = int(value or cfg.get("max_candidates") or 40)
        except Exception:
            raw = 40
        return max(1, min(raw, 200))

    def _memory_reconciliation_max_jobs(self) -> int:
        cfg = self._memory_reconciliation_config()
        try:
            raw = int(cfg.get("max_jobs") or 20)
        except Exception:
            raw = 20
        return max(1, min(raw, 100))

    def _memory_snapshot_limit(self, value: Any = None) -> int:
        cfg = self._memory_snapshot_config()
        try:
            raw = int(value or cfg.get("max_memories") or 1000)
        except Exception:
            raw = 1000
        return max(1, min(raw, 5000))

    def _memory_snapshot_max_items(self) -> int:
        cfg = self._memory_snapshot_config()
        try:
            raw = int(cfg.get("max_snapshots") or 30)
        except Exception:
            raw = 30
        return max(1, min(raw, 200))

    def _memory_reconciliation_prefix(self) -> str:
        cfg = self._memory_reconciliation_config()
        prefix = str(cfg.get("storage_prefix") or "memory/reconciliation/jobs").strip().strip("/")
        return prefix or "memory/reconciliation/jobs"

    def _memory_snapshot_prefix(self) -> str:
        cfg = self._memory_snapshot_config()
        prefix = str(cfg.get("storage_prefix") or "memory/snapshots").strip().strip("/")
        return prefix or "memory/snapshots"

    def _memory_reconciliation_storage(self):
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage

        scope = self._memory_scope()
        storage_uri = get_settings().BUNDLE_STORAGE_URL or None
        return AIBundleStorage(
            tenant=scope.tenant,
            project=scope.project,
            ai_bundle_id=scope.bundle_id or "bundle",
            storage_uri=storage_uri,
        )

    async def _memory_reconciliation_write_text(self, key: str, content: str, *, mime: str = "text/plain") -> str:
        storage = self._memory_reconciliation_storage()
        return await asyncio.to_thread(storage.write, key, content, mime=mime)

    async def _memory_reconciliation_write_json(self, key: str, payload: Dict[str, Any]) -> str:
        return await self._memory_reconciliation_write_text(
            key,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            mime="application/json",
        )

    async def _memory_reconciliation_read_text(self, key: str) -> str:
        storage = self._memory_reconciliation_storage()
        return await asyncio.to_thread(storage.read, key, as_text=True)

    async def _memory_reconciliation_read_json(self, key: str, default: Any = None) -> Any:
        try:
            text = await self._memory_reconciliation_read_text(key)
            return json.loads(text) if text else default
        except Exception:
            return default

    def _memory_reconciliation_index_key(self) -> str:
        return f"{self._memory_reconciliation_prefix()}/index.json"

    def _memory_reconciliation_job_key(self, job_id: str, name: str) -> str:
        safe_job = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "-" for ch in str(job_id or "job"))
        safe_name = str(name or "status.json").strip().lstrip("/")
        return f"{self._memory_reconciliation_prefix()}/{safe_job}/{safe_name}"

    def _memory_snapshot_index_key(self) -> str:
        return f"{self._memory_snapshot_prefix()}/index.json"

    def _memory_snapshot_key(self, snapshot_id: str, name: str) -> str:
        safe_snapshot = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "-" for ch in str(snapshot_id or "snapshot"))
        safe_name = str(name or "status.json").strip().lstrip("/")
        return f"{self._memory_snapshot_prefix()}/{safe_snapshot}/{safe_name}"

    def _memory_snapshot_authorized(self, snapshot: Dict[str, Any]) -> bool:
        snap_scope = snapshot.get("scope") if isinstance(snapshot.get("scope"), dict) else {}
        if not snap_scope:
            return False
        scope = self._memory_scope()
        return (
            str(snap_scope.get("tenant") or "") == scope.tenant
            and str(snap_scope.get("project") or "") == scope.project
            and str(snap_scope.get("user_id") or "") == scope.user_id
            and str(snap_scope.get("bundle_id") or "") == scope.bundle_id
        )

    async def _memory_reconciliation_load_index(self) -> list[Dict[str, Any]]:
        raw = await self._memory_reconciliation_read_json(self._memory_reconciliation_index_key(), default={})
        jobs = raw.get("jobs") if isinstance(raw, dict) else []
        return jobs if isinstance(jobs, list) else []

    async def _memory_snapshot_load_index(self) -> list[Dict[str, Any]]:
        raw = await self._memory_reconciliation_read_json(self._memory_snapshot_index_key(), default={})
        snapshots = raw.get("snapshots") if isinstance(raw, dict) else []
        return snapshots if isinstance(snapshots, list) else []

    async def _memory_reconciliation_store_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        job = dict(job)
        job.setdefault("updated_at", now)
        job["updated_at"] = now
        job.setdefault("artifacts", {})
        status_key = self._memory_reconciliation_job_key(str(job.get("job_id") or ""), "status.json")
        status_uri = await self._memory_reconciliation_write_json(status_key, job)
        job["artifacts"]["status"] = {"key": status_key, "uri": status_uri, "mime": "application/json"}

        jobs = await self._memory_reconciliation_load_index()
        jobs = [existing for existing in jobs if existing.get("job_id") != job.get("job_id")]
        summary = {
            key: job.get(key)
            for key in (
                "job_id",
                "status",
                "reason",
                "scope_filter",
                "candidate_count",
                "proposal_count",
                "warning_count",
                "created_at",
                "updated_at",
                "scope",
                "error",
                "snapshot_id",
                "dry_run",
                "background_job",
                "active_lock_key",
            )
            if key in job
        }
        summary["artifacts"] = job.get("artifacts", {})
        jobs.insert(0, summary)
        jobs = jobs[: self._memory_reconciliation_max_jobs()]
        await self._memory_reconciliation_write_json(
            self._memory_reconciliation_index_key(),
            {"jobs": jobs, "updated_at": now},
        )
        return job

    async def _memory_reconciliation_active_job(self, *, scope_filter: str) -> Optional[Dict[str, Any]]:
        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        for job in await self._memory_reconciliation_load_index():
            if str(job.get("status") or "") not in {"queued", "running"}:
                continue
            if str(job.get("scope_filter") or "") != normalized_scope_filter:
                continue
            job_scope = job.get("scope") if isinstance(job.get("scope"), dict) else {}
            if str(job_scope.get("tenant") or "") != scope.tenant:
                continue
            if str(job_scope.get("project") or "") != scope.project:
                continue
            if str(job_scope.get("user_id") or "") != scope.user_id:
                continue
            if str(job_scope.get("bundle_id") or "") != scope.bundle_id:
                continue
            return job
        return None

    async def _memory_reconciliation_release_active_lock(self, job: Dict[str, Any]) -> None:
        redis = getattr(self, "redis", None)
        lock_key = str(job.get("active_lock_key") or "").strip()
        job_id = str(job.get("job_id") or "").strip()
        if redis is None or not lock_key or not job_id:
            return
        try:
            value = await redis.get(lock_key)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            if str(value or "") == job_id:
                await redis.delete(lock_key)
        except Exception:
            pass

    async def _memory_snapshot_store(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        snapshot = dict(snapshot)
        snapshot.setdefault("updated_at", now)
        snapshot["updated_at"] = now
        snapshot.setdefault("artifacts", {})
        status_key = self._memory_snapshot_key(str(snapshot.get("snapshot_id") or ""), "status.json")
        status_uri = await self._memory_reconciliation_write_json(status_key, snapshot)
        snapshot["artifacts"]["status"] = {"key": status_key, "uri": status_uri, "mime": "application/json"}

        snapshots = await self._memory_snapshot_load_index()
        snapshots = [existing for existing in snapshots if existing.get("snapshot_id") != snapshot.get("snapshot_id")]
        summary = {
            key: snapshot.get(key)
            for key in (
                "snapshot_id",
                "status",
                "reason",
                "scope_filter",
                "memory_count",
                "created_at",
                "updated_at",
                "linked_job_id",
                "error",
            )
            if key in snapshot
        }
        summary["artifacts"] = snapshot.get("artifacts", {})
        snapshots.insert(0, summary)
        snapshots = snapshots[: self._memory_snapshot_max_items()]
        await self._memory_reconciliation_write_json(
            self._memory_snapshot_index_key(),
            {"snapshots": snapshots, "updated_at": now},
        )
        return snapshot

    @staticmethod
    def _memory_model_dump(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        if isinstance(value, dict):
            return dict(value)
        return {"value": value}

    @staticmethod
    def _memory_text_terms(value: str) -> set[str]:
        import re

        stop = {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in",
            "is", "it", "of", "on", "or", "should", "that", "the", "this", "to", "when",
            "with",
        }
        return {term for term in re.findall(r"[a-z0-9]{3,}", str(value or "").lower()) if term not in stop}

    def _memory_reconciliation_analysis(self, memories: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {}
        tier_counts: Dict[str, int] = {}
        contradiction_count = 0
        weak_count = 0
        stale_count = 0
        duplicate_groups: list[Dict[str, Any]] = []

        for memory in memories:
            status = str(memory.get("status") or "active")
            status_counts[status] = status_counts.get(status, 0) + 1
            tier = str(memory.get("tier") or 3)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if int(memory.get("contradiction_count") or 0) > 0:
                contradiction_count += 1
            if status in {"weakened", "unsupported"}:
                weak_count += 1
            if float(memory.get("freshness_score") or 0.0) < 0.25 and not memory.get("pinned"):
                stale_count += 1

        used: set[str] = set()
        for idx, left in enumerate(memories):
            left_id = str(left.get("id") or "")
            if left_id in used:
                continue
            left_terms = self._memory_text_terms(left.get("memory") or "")
            if not left_terms:
                continue
            group = [left]
            for right in memories[idx + 1:]:
                right_id = str(right.get("id") or "")
                if right_id in used:
                    continue
                right_terms = self._memory_text_terms(right.get("memory") or "")
                if not right_terms:
                    continue
                overlap = len(left_terms & right_terms) / max(1, len(left_terms | right_terms))
                label_overlap = bool(set(left.get("labels") or []) & set(right.get("labels") or []))
                same_kind = str(left.get("kind") or "") == str(right.get("kind") or "")
                if overlap >= 0.58 or (overlap >= 0.42 and same_kind and label_overlap):
                    group.append(right)
            if len(group) > 1:
                for item in group:
                    used.add(str(item.get("id") or ""))
                duplicate_groups.append({
                    "memory_ids": [item.get("id") for item in group],
                    "preview": [str(item.get("memory") or "")[:180] for item in group],
                })

        reasons: list[str] = []
        if duplicate_groups:
            reasons.append(f"{len(duplicate_groups)} possible duplicate group(s)")
        if contradiction_count:
            reasons.append(f"{contradiction_count} memory/memories with contradictions")
        if weak_count:
            reasons.append(f"{weak_count} weakened or unsupported memory/memories")
        if stale_count:
            reasons.append(f"{stale_count} low-freshness unpinned memory/memories")

        return {
            "total": len(memories),
            "status_counts": status_counts,
            "tier_counts": tier_counts,
            "possible_duplicate_groups": duplicate_groups[:12],
            "contradiction_count": contradiction_count,
            "weak_or_unsupported_count": weak_count,
            "low_freshness_count": stale_count,
            "needs_reconciliation": bool(reasons),
            "reasons": reasons,
        }

    def _memory_reconciliation_markdown(
        self,
        *,
        job: Dict[str, Any],
        before: Dict[str, Any],
        proposal: Dict[str, Any],
    ) -> str:
        lines = [
            "# Memory Reconciliation Proposal",
            "",
            f"- Job: `{job.get('job_id')}`",
            f"- Status: `{job.get('status')}`",
            f"- Scope: `{job.get('scope_filter')}`",
            f"- Candidates: {job.get('candidate_count', 0)}",
            f"- Proposed actions: {job.get('proposal_count', 0)}",
            "",
            "## Actions",
            "",
        ]
        actions = proposal.get("actions") if isinstance(proposal.get("actions"), list) else []
        if not actions:
            lines.append("No reconciliation actions were proposed.")
        for index, action in enumerate(actions, start=1):
            lines.extend([
                f"### {index}. {action.get('action', 'action')}",
                "",
                f"- Source: `{action.get('source_memory_id') or action.get('memory_id') or ''}`",
                f"- Target: `{action.get('target_memory_id') or ''}`",
                f"- Confidence: {action.get('confidence')}",
                f"- Reason: {action.get('reason') or ''}",
                "",
            ])
        warnings = proposal.get("warnings") if isinstance(proposal.get("warnings"), list) else []
        if warnings:
            lines.extend(["## Warnings", ""])
            lines.extend(f"- {warning}" for warning in warnings)
            lines.append("")
        lines.extend(["## Candidate Snapshot", ""])
        for memory in before.get("memories", [])[:80]:
            lines.extend([
                f"### `{memory.get('id')}`",
                "",
                str(memory.get("memory") or ""),
                "",
                f"- Status: `{memory.get('status')}`",
                f"- Tier: `{memory.get('tier')}`",
                f"- Revision: `{memory.get('revision')}`",
                "",
            ])
        return "\n".join(lines).rstrip() + "\n"

    def _memory_snapshot_markdown(self, snapshot: Dict[str, Any]) -> str:
        def _csv_or_none(values: Any) -> str:
            items = [str(item) for item in (values or []) if str(item or "").strip()]
            return ", ".join(items) if items else "none"

        lines = [
            "# Memory Snapshot Preview",
            "",
            "This Markdown file is for human review. Use the companion `memories.json` artifact as the structured aggregate snapshot payload for restore/import workflows.",
            "",
            f"- Snapshot: `{snapshot.get('snapshot_id')}`",
            f"- Status: `{snapshot.get('status')}`",
            f"- Scope: `{snapshot.get('scope_filter')}`",
            f"- Memories: {snapshot.get('memory_count', 0)}",
            f"- Created: {snapshot.get('created_at', '')}",
            "",
        ]
        if snapshot.get("reason"):
            lines.extend([f"- Reason: {snapshot.get('reason')}", ""])
        lines.append("## Memories")
        lines.append("")
        for memory in snapshot.get("memories", []):
            lines.extend([
                f"### `{memory.get('id')}`",
                "",
                str(memory.get("memory") or ""),
                "",
                f"- Context: {memory.get('context') or 'none'}",
                f"- Status: `{memory.get('status')}`",
                f"- Tier: `{memory.get('tier')}`",
                f"- Revision: `{memory.get('revision')}`",
                f"- Labels: {_csv_or_none(memory.get('labels'))}",
                f"- Keywords: {_csv_or_none(memory.get('keywords'))}",
                "",
            ])
        return "\n".join(lines).rstrip() + "\n"

    def _memory_snapshot_csv(self, snapshot: Dict[str, Any]) -> str:
        import csv
        import io

        out = io.StringIO()
        writer = csv.DictWriter(
            out,
            fieldnames=[
                "id",
                "bundle_id",
                "memory",
                "context",
                "kind",
                "status",
                "tier",
                "pinned",
                "labels",
                "keywords",
                "revision",
                "updated_at",
            ],
        )
        writer.writeheader()
        for memory in snapshot.get("memories", []):
            writer.writerow({
                "id": memory.get("id", ""),
                "bundle_id": memory.get("bundle_id", ""),
                "memory": memory.get("memory", ""),
                "context": memory.get("context", ""),
                "kind": memory.get("kind", ""),
                "status": memory.get("status", ""),
                "tier": memory.get("tier", ""),
                "pinned": memory.get("pinned", ""),
                "labels": ", ".join(memory.get("labels") or []),
                "keywords": ", ".join(memory.get("keywords") or []),
                "revision": memory.get("revision", ""),
                "updated_at": memory.get("updated_at", ""),
            })
        return out.getvalue()

    async def _memory_snapshot_load_full(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        status_key = self._memory_snapshot_key(snapshot_id, "status.json")
        snapshot = await self._memory_reconciliation_read_json(status_key, default=None)
        if isinstance(snapshot, dict) and not self._memory_snapshot_authorized(snapshot):
            return None
        if isinstance(snapshot, dict) and isinstance(snapshot.get("memories"), list):
            return snapshot
        if isinstance(snapshot, dict):
            artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
            item = artifacts.get("memories") if isinstance(artifacts.get("memories"), dict) else None
            key = str(item.get("key") or "") if item else ""
            if key:
                payload = await self._memory_reconciliation_read_json(key, default=None)
                if (
                    isinstance(payload, dict)
                    and self._memory_snapshot_authorized(payload)
                    and isinstance(payload.get("memories"), list)
                ):
                    return payload
        return None

    @staticmethod
    def _memory_snapshot_compare_fields(left: Dict[str, Any], right: Dict[str, Any]) -> list[str]:
        changed: list[str] = []
        fields = [
            "memory",
            "context",
            "kind",
            "status",
            "visibility",
            "tier",
            "pinned",
            "labels",
            "keywords",
            "confidence_score",
            "importance_score",
            "salience_score",
        ]
        for field in fields:
            left_value = left.get(field)
            right_value = right.get(field)
            if isinstance(left_value, list) or isinstance(right_value, list):
                if list(left_value or []) != list(right_value or []):
                    changed.append(field)
            elif str(left_value or "") != str(right_value or ""):
                changed.append(field)
        return changed

    async def _memory_snapshot_restore_preview(
        self,
        *,
        snapshot_id: str,
        scope_filter: str,
        retire_extra: bool,
    ) -> Dict[str, Any]:
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySearchRequest

        snapshot = await self._memory_snapshot_load_full(snapshot_id)
        if not isinstance(snapshot, dict):
            return self._memory_error("memory_snapshot_not_found")
        snapshot_memories = snapshot.get("memories") if isinstance(snapshot.get("memories"), list) else []
        normalized_scope_filter = self._memory_scope_filter(scope_filter or snapshot.get("scope_filter") or "current_bundle")
        scope = self._memory_scope()
        store = self._memory_store()
        rows = await store.search(
            MemorySearchRequest(
                scope=scope,
                mode="recent",
                status="any",
                visible_to_user=True,
                include_private=False,
                scope_filter=normalized_scope_filter,
                limit=max(1, min(max(len(snapshot_memories) + 250, 1000), 5000)),
                candidate_limit=max(1, min(max(len(snapshot_memories) + 250, 1000), 5000)),
            )
        )
        current = [self._memory_record_payload(row) for row in rows]
        current_by_id = {str(item.get("id") or ""): item for item in current}
        snapshot_by_id = {str(item.get("id") or ""): item for item in snapshot_memories if isinstance(item, dict)}

        changes: list[Dict[str, Any]] = []
        counts = {
            "unchanged": 0,
            "changed": 0,
            "missing_current": 0,
            "extra_current": 0,
            "skipped_out_of_scope": 0,
        }
        for memory_id, memory in snapshot_by_id.items():
            bundle_id = str(memory.get("bundle_id") or scope.bundle_id or "")
            if normalized_scope_filter == "current_bundle" and bundle_id != scope.bundle_id:
                counts["skipped_out_of_scope"] += 1
                continue
            if normalized_scope_filter == "global_only" and bundle_id:
                counts["skipped_out_of_scope"] += 1
                continue
            current_item = current_by_id.get(memory_id)
            if current_item is None:
                counts["missing_current"] += 1
                changes.append({
                    "memory_id": memory_id,
                    "action": "restore_missing",
                    "memory": str(memory.get("memory") or "")[:220],
                    "status": memory.get("status"),
                })
                continue
            changed_fields = self._memory_snapshot_compare_fields(memory, current_item)
            if changed_fields:
                counts["changed"] += 1
                changes.append({
                    "memory_id": memory_id,
                    "action": "restore_changed",
                    "fields": changed_fields,
                    "memory": str(memory.get("memory") or "")[:220],
                    "current_status": current_item.get("status"),
                    "snapshot_status": memory.get("status"),
                })
            else:
                counts["unchanged"] += 1

        for memory_id, memory in current_by_id.items():
            if memory_id in snapshot_by_id or str(memory.get("status") or "") == "retired":
                continue
            counts["extra_current"] += 1
            changes.append({
                "memory_id": memory_id,
                "action": "retire_extra" if retire_extra else "leave_extra",
                "memory": str(memory.get("memory") or "")[:220],
                "status": memory.get("status"),
            })

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "scope_filter": normalized_scope_filter,
            "retire_extra": bool(retire_extra),
            "counts": counts,
            "change_count": len(changes),
            "changes": changes[:200],
            "truncated": len(changes) > 200,
            "snapshot": {
                "snapshot_id": snapshot.get("snapshot_id"),
                "status": snapshot.get("status"),
                "reason": snapshot.get("reason"),
                "memory_count": snapshot.get("memory_count"),
                "created_at": snapshot.get("created_at"),
                "scope_filter": snapshot.get("scope_filter"),
            },
        }

    async def _memory_snapshot_create(
        self,
        *,
        scope_filter: str,
        limit: int,
        reason: str,
        linked_job_id: str = "",
    ) -> Dict[str, Any]:
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySearchRequest

        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        store = self._memory_store()
        if _truthy(self._memory_widget_config().get("ensure_schema"), True):
            await store.ensure_schema()
        rows = await store.search(
            MemorySearchRequest(
                scope=scope,
                mode="hotset",
                status="any",
                visible_to_user=True,
                include_private=False,
                scope_filter=normalized_scope_filter,
                limit=self._memory_snapshot_limit(limit),
                candidate_limit=self._memory_snapshot_limit(limit),
            )
        )
        memories = [self._memory_record_payload(row) for row in rows]
        created_at = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(
            "\n".join([
                scope.tenant,
                scope.project,
                scope.user_id,
                scope.bundle_id,
                normalized_scope_filter,
                str(reason or ""),
                linked_job_id,
                created_at,
                uuid.uuid4().hex,
            ]).encode("utf-8")
        ).hexdigest()[:12]
        snapshot_id = f"memsnap_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{digest}"
        snapshot: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "status": "succeeded",
            "reason": reason or "manual memory snapshot",
            "scope": {
                "tenant": scope.tenant,
                "project": scope.project,
                "user_id": scope.user_id,
                "bundle_id": scope.bundle_id,
            },
            "scope_filter": normalized_scope_filter,
            "memory_count": len(memories),
            "created_at": created_at,
            "linked_job_id": linked_job_id,
            "memories": memories,
            "artifacts": {},
        }
        memories_key = self._memory_snapshot_key(snapshot_id, "memories.json")
        memories_uri = await self._memory_reconciliation_write_json(memories_key, snapshot)
        snapshot["artifacts"]["memories"] = {"key": memories_key, "uri": memories_uri, "mime": "application/json"}

        md_key = self._memory_snapshot_key(snapshot_id, "memories.md")
        md_uri = await self._memory_reconciliation_write_text(md_key, self._memory_snapshot_markdown(snapshot), mime="text/markdown")
        snapshot["artifacts"]["memories_md"] = {"key": md_key, "uri": md_uri, "mime": "text/markdown"}

        csv_key = self._memory_snapshot_key(snapshot_id, "memories.csv")
        csv_uri = await self._memory_reconciliation_write_text(csv_key, self._memory_snapshot_csv(snapshot), mime="text/csv")
        snapshot["artifacts"]["memories_csv"] = {"key": csv_key, "uri": csv_uri, "mime": "text/csv"}

        return await self._memory_snapshot_store(snapshot)

    async def _memory_reconciliation_candidates(
        self,
        *,
        scope_filter: str,
        limit: int,
    ) -> tuple[list[Any], list[Dict[str, Any]], Dict[str, Any]]:
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySearchRequest

        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        store = self._memory_store()
        if _truthy(self._memory_widget_config().get("ensure_schema"), True):
            await store.ensure_schema()
        rows = await store.search(
            MemorySearchRequest(
                scope=scope,
                mode="hotset",
                status="any",
                visible_to_user=True,
                include_private=False,
                scope_filter=normalized_scope_filter,
                limit=limit,
                candidate_limit=limit,
            )
        )
        candidate_rows: list[Any] = []
        candidate_payloads: list[Dict[str, Any]] = []
        for row in rows:
            memory = getattr(row, "memory", row)
            status = str(getattr(memory, "status", "") or "active")
            if status not in {"active", "weakened", "unsupported"}:
                continue
            candidate_rows.append(row)
            candidate_payloads.append(self._memory_record_payload(row))
        return candidate_rows, candidate_payloads, self._memory_reconciliation_analysis(candidate_payloads)

    def _memory_source(self, *, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        routing = getattr(self.comm_context, "routing", None)
        scope = self._memory_scope()
        normalized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(
            "\n".join([
                scope.tenant,
                scope.project,
                scope.user_id,
                scope.bundle_id,
                str(getattr(routing, "conversation_id", "") or ""),
                str(getattr(routing, "turn_id", "") or ""),
                action,
                normalized_payload,
            ]).encode("utf-8")
        ).hexdigest()
        return {
            "origin": "memory_widget",
            "action": action,
            "bundle_id": scope.bundle_id,
            "conversation_id": str(getattr(routing, "conversation_id", "") or ""),
            "turn_id": str(getattr(routing, "turn_id", "") or ""),
            "idempotency_key": f"memory_widget:{action}:{digest}",
        }

    async def _memory_embed_one(self, text: str) -> Optional[Sequence[float]]:
        tools_cfg = self._memory_config().get("tools") if isinstance(self._memory_config().get("tools"), dict) else {}
        if not _truthy(tools_cfg.get("embedding_enabled"), True):
            return None
        value = str(text or "").strip()
        if not value:
            return None
        try:
            timeout = float(tools_cfg.get("embedding_timeout_seconds") or 3.0)
        except Exception:
            timeout = 3.0
        try:
            result = await asyncio.wait_for(
                self.models_service.embed_texts([value]),
                timeout=max(0.1, min(timeout, 10.0)),
            )
            return result[0] if result else None
        except Exception:
            return None

    @staticmethod
    def _memory_record_payload(result: Any) -> Dict[str, Any]:
        nested_memory = getattr(result, "memory", None)
        memory = nested_memory if hasattr(nested_memory, "id") and hasattr(nested_memory, "scope") else result
        payload = {
            "id": memory.id,
            "bundle_id": memory.scope.bundle_id,
            "memory": memory.memory,
            "context": memory.context,
            "kind": memory.kind,
            "status": memory.status,
            "visibility": memory.visibility,
            "labels": list(memory.labels),
            "keywords": list(memory.keywords),
            "tier": memory.tier,
            "pinned": bool(getattr(memory, "pinned", False)),
            "confidence_score": memory.confidence_score,
            "importance_score": memory.importance_score,
            "freshness_score": memory.freshness_score,
            "salience_score": memory.salience_score,
            "confirmation_rate": memory.confirmation_rate,
            "evidence_count": memory.evidence_count,
            "update_count": memory.update_count,
            "confirmation_count": memory.confirmation_count,
            "contradiction_count": memory.contradiction_count,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
            "last_event_at": memory.last_event_at.isoformat(),
            "revision": memory.revision,
        }
        if hasattr(result, "score"):
            payload["score"] = result.score
            payload["score_breakdown"] = dict(result.score_breakdown)
        return payload

    @staticmethod
    def _memory_event_payload(event: Any) -> Dict[str, Any]:
        return {
            "id": event.id,
            "memory_id": event.memory_id,
            "bundle_id": event.scope.bundle_id,
            "event_type": event.event_type,
            "signal_text": event.signal_text,
            "context": event.context,
            "originator": event.originator,
            "confidence": event.confidence,
            "importance": event.importance,
            "labels": list(event.labels),
            "keywords": list(event.keywords),
            "created_at": event.created_at.isoformat(),
            "source": dict(event.source or {}),
            "metadata": dict(event.metadata or {}),
        }

    def _memory_capabilities_payload(self) -> Dict[str, Any]:
        widget_cfg = self._memory_widget_config()
        return {
            "allow_all_user_memories": _truthy(widget_cfg.get("allow_all_user_memories"), True),
            "allow_write": self._memory_widget_write_enabled(),
            "allow_reconciliation": self._memory_reconciliation_enabled(),
            "allow_snapshots": self._memory_snapshot_enabled(),
        }

    def _memory_error(self, code: str, message: str = "") -> Dict[str, Any]:
        return {"ok": False, "error": code, "message": message or code}

    @api(alias="memories_widget", route="operations", user_types=("registered", "paid", "privileged"))
    @ui_widget(
        icon={"tailwind": "heroicons-outline:archive-box", "lucide": "Archive"},
        alias="memories",
        user_types=("registered", "paid", "privileged"),
    )
    def memories_widget(self, **kwargs):
        del kwargs
        if not self._memory_widget_enabled():
            return ["<p>User memory is not enabled for this bundle.</p>"]
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "User memories are served from the built memories widget."
            "</div>"
        ]

    @api(method="POST", alias="memories_widget_data", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_data(
        self,
        scope_filter: str = "current_bundle",
        query: str = "",
        status: str = "active",
        kind: str = "",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        limit: int = 30,
        offset: int = 0,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySearchRequest, normalize_terms

        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        page_limit = self._memory_limit(limit)
        try:
            page_offset = max(0, int(offset or 0))
        except Exception:
            page_offset = 0
        labels_list = normalize_terms(labels)
        keywords_list = normalize_terms(keywords)
        normalized_query = str(query or "").strip()
        query_embedding = await self._memory_embed_one(normalized_query) if normalized_query else None
        search_limit = min(page_limit + 1, 101)
        try:
            min_relevance_score = float(self._memory_widget_config().get("search_min_relevance_score") or 0.58)
        except Exception:
            min_relevance_score = 0.58
        min_relevance_score = max(0.0, min(1.0, min_relevance_score))
        store = self._memory_store()
        if _truthy(self._memory_widget_config().get("ensure_schema"), True):
            await store.ensure_schema()
        rows = await store.search(
            MemorySearchRequest(
                scope=scope,
                query=normalized_query,
                mode="hybrid" if normalized_query else "hotset",
                labels=labels_list,
                keywords=keywords_list,
                status=status or "active",
                kind=kind,
                visible_to_user=True,
                include_private=False,
                scope_filter=normalized_scope_filter,
                limit=search_limit,
                offset=page_offset,
                candidate_limit=page_offset + search_limit,
                query_embedding=query_embedding,
                min_relevance_score=min_relevance_score if normalized_query else 0.0,
            )
        )
        page_rows = rows[:page_limit]
        return {
            "ok": True,
            "scope": {
                "tenant": scope.tenant,
                "project": scope.project,
                "user_id": scope.user_id,
                "bundle_id": scope.bundle_id,
                "filter": normalized_scope_filter,
            },
            "filters": {
                "scope_filter": normalized_scope_filter,
                "query": normalized_query,
                "status": status or "active",
                "kind": kind,
                "labels": labels_list,
                "keywords": keywords_list,
                "min_relevance_score": min_relevance_score if normalized_query else 0.0,
            },
            "capabilities": self._memory_capabilities_payload(),
            "memories": [self._memory_record_payload(row) for row in page_rows],
            "count": len(page_rows),
            "limit": page_limit,
            "offset": page_offset,
            "has_more": len(rows) > page_limit,
        }

    @api(method="POST", alias="memories_widget_get", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_get(self, memory_id: str, scope_filter: str = "current_bundle", **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        memory = await self._memory_store().get_memory(
            scope=self._memory_scope(),
            memory_id=memory_id,
            visible_to_user=True,
            scope_filter=self._memory_scope_filter(scope_filter),
        )
        if memory is None:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory": self._memory_record_payload(memory), "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_events", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_events(
        self,
        memory_id: str,
        scope_filter: str = "current_bundle",
        limit: int = 25,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        store = self._memory_store()
        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        memory = await store.get_memory(
            scope=scope,
            memory_id=memory_id,
            visible_to_user=True,
            scope_filter=normalized_scope_filter,
        )
        if memory is None:
            return self._memory_error("memory_not_found")
        events = await store.list_memory_events(
            scope=scope,
            memory_id=memory_id,
            visible_to_user=True,
            scope_filter=normalized_scope_filter,
            limit=self._memory_limit(limit),
        )
        return {
            "ok": True,
            "memory": self._memory_record_payload(memory),
            "events": [self._memory_event_payload(event) for event in events],
            "count": len(events),
        }

    @api(method="POST", alias="memories_widget_create", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_create(
        self,
        memory: str,
        context: str = "",
        kind: str = "fact",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        importance: float = 0.7,
        pinned: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        validation_error = self._memory_widget_validate_text(memory=memory, context=context)
        if validation_error:
            return validation_error
        memory = str(memory or "").strip()
        context = str(context or "").strip()
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySignal

        scope = self._memory_scope()
        labels_list = self._memory_widget_terms(labels)
        keywords_list = self._memory_widget_terms(keywords)
        source_payload = {
            "memory": memory,
            "context": context,
            "kind": kind,
            "labels": labels_list,
            "keywords": keywords_list,
            "pinned": bool(pinned),
        }
        embedding = await self._memory_embed_one(f"{memory}\n{context}")
        signal = MemorySignal(
            memory=memory,
            context=context,
            kind=kind or "fact",
            event_type="user_edit",
            originator="user",
            status="active",
            visibility="user",
            labels=labels_list,
            keywords=keywords_list,
            confidence=0.95,
            importance=importance,
            pinned=bool(pinned),
            embedding=embedding,
            source=self._memory_source(action="create", payload=source_payload),
        )
        try:
            record = await self._memory_store().record_signal(
                scope=scope,
                signal=signal,
                merge_threshold=None,
                append_on_canonical_match=False,
                include_retired_canonical=False,
                ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
            )
        except ValueError as exc:
            if str(exc) == "memory_exact_match_is_retired":
                return self._memory_error(
                    "memory_exact_match_is_retired",
                    "An identical retired memory already exists. Restore or unretire it explicitly instead of creating a new user_edit event.",
                )
            raise
        return {"ok": True, "memory": self._memory_record_payload(record)}

    @api(method="POST", alias="memories_widget_update", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_update(
        self,
        memory_id: str,
        memory: str,
        context: str = "",
        kind: str = "fact",
        status: str = "active",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        importance: float = 0.7,
        pinned: bool = False,
        scope_filter: str = "current_bundle",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        validation_error = self._memory_widget_validate_text(memory=memory, context=context)
        if validation_error:
            return validation_error
        memory = str(memory or "").strip()
        context = str(context or "").strip()

        labels_list = self._memory_widget_terms(labels)
        keywords_list = self._memory_widget_terms(keywords)
        source_payload = {
            "memory_id": memory_id,
            "memory": memory,
            "context": context,
            "kind": kind,
            "status": status,
            "labels": labels_list,
            "keywords": keywords_list,
            "pinned": bool(pinned),
        }
        record = await self._memory_store().edit_memory(
            scope=self._memory_scope(),
            memory_id=memory_id,
            memory=memory,
            context=context,
            kind=kind or "fact",
            status=status or "active",
            visibility="user",
            labels=labels_list,
            keywords=keywords_list,
            importance=importance,
            pinned=bool(pinned),
            embedding=await self._memory_embed_one(f"{memory}\n{context}"),
            source=self._memory_source(action="update", payload=source_payload),
            visible_to_user=True,
            scope_filter=self._memory_scope_filter(scope_filter),
            ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
        )
        if record is None:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory": self._memory_record_payload(record)}

    @api(method="POST", alias="memories_widget_pin", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_pin(
        self,
        memory_id: str,
        pinned: bool = True,
        scope_filter: str = "current_bundle",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        store = self._memory_store()
        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        memory = await store.get_memory(
            scope=scope,
            memory_id=memory_id,
            visible_to_user=True,
            scope_filter=normalized_scope_filter,
        )
        if memory is None:
            return self._memory_error("memory_not_found")
        record = await store.edit_memory(
            scope=scope,
            memory_id=memory_id,
            memory=memory.memory,
            context=memory.context,
            kind=memory.kind,
            status=memory.status,
            visibility=memory.visibility,
            labels=list(memory.labels),
            keywords=list(memory.keywords),
            importance=memory.importance_score,
            pinned=bool(pinned),
            source=self._memory_source(action="pin" if pinned else "unpin", payload={"memory_id": memory_id, "pinned": bool(pinned)}),
            visible_to_user=True,
            scope_filter=normalized_scope_filter,
            ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
        )
        if record is None:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory": self._memory_record_payload(record)}

    @api(method="POST", alias="memories_widget_confirm", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_confirm(
        self,
        memory_id: str,
        note: str = "confirmed",
        scope_filter: str = "current_bundle",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        store = self._memory_store()
        scope = self._memory_scope()
        if await store.get_memory(scope=scope, memory_id=memory_id, visible_to_user=True, scope_filter=self._memory_scope_filter(scope_filter)) is None:
            return self._memory_error("memory_not_found")
        record = await store.confirm_memory(
            scope=scope,
            memory_id=memory_id,
            note=note or "confirmed",
            originator="user",
            source=self._memory_source(action="confirm", payload={"memory_id": memory_id, "note": note}),
        )
        if record is None:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory": self._memory_record_payload(record)}

    @api(method="POST", alias="memories_widget_retire", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_retire(
        self,
        memory_id: str,
        reason: str = "retired by user",
        scope_filter: str = "current_bundle",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        store = self._memory_store()
        scope = self._memory_scope()
        if await store.get_memory(scope=scope, memory_id=memory_id, visible_to_user=True, scope_filter=self._memory_scope_filter(scope_filter)) is None:
            return self._memory_error("memory_not_found")
        record = await store.retire_memory(
            scope=scope,
            memory_id=memory_id,
            reason=reason or "retired by user",
            originator="user",
            source=self._memory_source(action="retire", payload={"memory_id": memory_id, "reason": reason}),
        )
        if record is None:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory": self._memory_record_payload(record)}

    @api(method="POST", alias="memories_widget_delete", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_delete(self, memory_id: str, reason: str = "deleted by user", **kwargs) -> Dict[str, Any]:
        return await self.memories_widget_retire(memory_id=memory_id, reason=reason, **kwargs)

    @api(method="POST", alias="memories_widget_snapshot_create", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshot_create(
        self,
        scope_filter: str = "current_bundle",
        limit: int = 1000,
        reason: str = "manual memory snapshot",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        snapshot = await self._memory_snapshot_create(
            scope_filter=self._memory_scope_filter(scope_filter),
            limit=self._memory_snapshot_limit(limit),
            reason=reason or "manual memory snapshot",
        )
        public_snapshot = dict(snapshot)
        public_snapshot.pop("memories", None)
        return {"ok": True, "snapshot": public_snapshot, "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_snapshots", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshots(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        snapshots = await self._memory_snapshot_load_index()
        snapshots = [snapshot for snapshot in snapshots if isinstance(snapshot, dict) and self._memory_snapshot_authorized(snapshot)]
        return {"ok": True, "snapshots": snapshots, "count": len(snapshots), "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_snapshot_export", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshot_export(
        self,
        snapshot_id: str,
        artifact: str = "memories_md",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        status_key = self._memory_snapshot_key(snapshot_id, "status.json")
        snapshot = await self._memory_reconciliation_read_json(status_key, default=None)
        if not isinstance(snapshot, dict) or not self._memory_snapshot_authorized(snapshot):
            return self._memory_error("memory_snapshot_not_found")
        artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
        item = artifacts.get(artifact) if isinstance(artifacts.get(artifact), dict) else None
        if not item:
            return self._memory_error("memory_snapshot_artifact_not_found")
        key = str(item.get("key") or "")
        if not key:
            return self._memory_error("memory_snapshot_artifact_not_found")
        content = await self._memory_reconciliation_read_text(key)
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "artifact": artifact,
            "key": key,
            "uri": item.get("uri"),
            "mime": item.get("mime"),
            "content": content,
        }

    @api(method="POST", alias="memories_widget_snapshot_restore_preview", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshot_restore_preview(
        self,
        snapshot_id: str,
        scope_filter: str = "",
        retire_extra: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        return await self._memory_snapshot_restore_preview(
            snapshot_id=snapshot_id,
            scope_filter=scope_filter,
            retire_extra=bool(retire_extra),
        )

    @api(method="POST", alias="memories_widget_snapshot_restore_apply", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshot_restore_apply(
        self,
        snapshot_id: str,
        scope_filter: str = "",
        retire_extra: bool = True,
        confirm: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        if not confirm:
            return self._memory_error("memory_restore_requires_confirmation", "Preview the snapshot diff and pass confirm=true to restore.")

        snapshot = await self._memory_snapshot_load_full(snapshot_id)
        if not isinstance(snapshot, dict):
            return self._memory_error("memory_snapshot_not_found")
        snapshot_memories = snapshot.get("memories") if isinstance(snapshot.get("memories"), list) else []
        normalized_scope_filter = self._memory_scope_filter(scope_filter or snapshot.get("scope_filter") or "current_bundle")

        safety_snapshot = await self._memory_snapshot_create(
            scope_filter=normalized_scope_filter,
            limit=self._memory_snapshot_limit(None),
            reason=f"safety snapshot before restoring {snapshot_id}",
            linked_job_id=f"restore:{snapshot_id}",
        )
        result = await self._memory_store().restore_snapshot(
            scope=self._memory_scope(),
            snapshot_id=snapshot_id,
            memories=snapshot_memories,
            scope_filter=normalized_scope_filter,
            retire_extra=bool(retire_extra),
            source=self._memory_source(
                action="snapshot_restore_apply",
                payload={"snapshot_id": snapshot_id, "scope_filter": normalized_scope_filter, "retire_extra": bool(retire_extra)},
            ),
            ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
        )
        preview = await self._memory_snapshot_restore_preview(
            snapshot_id=snapshot_id,
            scope_filter=normalized_scope_filter,
            retire_extra=bool(retire_extra),
        )
        public_safety = dict(safety_snapshot)
        public_safety.pop("memories", None)
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "scope_filter": normalized_scope_filter,
            "retire_extra": bool(retire_extra),
            "result": result,
            "safety_snapshot": public_safety,
            "post_restore_preview": preview if preview.get("ok") else None,
            "capabilities": self._memory_capabilities_payload(),
        }

    @api(method="POST", alias="memories_widget_reconcile_analyze", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_analyze(
        self,
        scope_filter: str = "current_bundle",
        limit: int = 40,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        _, candidates, analysis = await self._memory_reconciliation_candidates(
            scope_filter=normalized_scope_filter,
            limit=self._memory_reconciliation_limit(limit),
        )
        return {
            "ok": True,
            "scope_filter": normalized_scope_filter,
            "candidate_count": len(candidates),
            "analysis": analysis,
            "capabilities": self._memory_capabilities_payload(),
        }

    @api(method="POST", alias="memories_widget_reconcile_jobs", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_jobs(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        jobs = await self._memory_reconciliation_load_index()
        return {"ok": True, "jobs": jobs, "count": len(jobs), "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_reconcile_job", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_job(self, job_id: str, **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        key = self._memory_reconciliation_job_key(job_id, "status.json")
        job = await self._memory_reconciliation_read_json(key, default=None)
        if not isinstance(job, dict):
            return self._memory_error("memory_reconciliation_job_not_found")
        return {"ok": True, "job": job, "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_reconcile_export", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_export(
        self,
        job_id: str,
        artifact: str = "proposal_md",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        status_key = self._memory_reconciliation_job_key(job_id, "status.json")
        job = await self._memory_reconciliation_read_json(status_key, default=None)
        if not isinstance(job, dict):
            return self._memory_error("memory_reconciliation_job_not_found")
        artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
        item = artifacts.get(artifact) if isinstance(artifacts.get(artifact), dict) else None
        if not item:
            return self._memory_error("memory_reconciliation_artifact_not_found")
        key = str(item.get("key") or "")
        if not key:
            return self._memory_error("memory_reconciliation_artifact_not_found")
        content = await self._memory_reconciliation_read_text(key)
        return {
            "ok": True,
            "job_id": job_id,
            "artifact": artifact,
            "key": key,
            "uri": item.get("uri"),
            "mime": item.get("mime"),
            "content": content,
        }

    async def _memory_reconciliation_run_job(
        self,
        *,
        job: Dict[str, Any],
        scope_filter: str = "current_bundle",
        limit: int = 40,
        reason: str = "manual widget reconciliation dry run",
    ) -> None:
        job = dict(job)
        job_id = str(job.get("job_id") or "")
        created_at = str(job.get("created_at") or datetime.now(timezone.utc).isoformat())
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        try:
            job["status"] = "running"
            job = await self._memory_reconciliation_store_job(job)

            snapshot = await self._memory_snapshot_create(
                scope_filter=normalized_scope_filter,
                limit=self._memory_snapshot_limit(None),
                reason=f"snapshot before reconciliation job {job_id}",
                linked_job_id=job_id,
            )
            job["snapshot_id"] = snapshot.get("snapshot_id")
            job["snapshot_artifacts"] = snapshot.get("artifacts", {})
            snapshot_artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
            if isinstance(snapshot_artifacts.get("memories"), dict):
                job.setdefault("artifacts", {})["snapshot"] = snapshot_artifacts["memories"]
            job = await self._memory_reconciliation_store_job(job)

            candidate_rows, candidates, analysis = await self._memory_reconciliation_candidates(
                scope_filter=normalized_scope_filter,
                limit=self._memory_reconciliation_limit(limit),
            )
            job["candidate_count"] = len(candidates)
            job["analysis"] = analysis
            job = await self._memory_reconciliation_store_job(job)

            from kdcube_ai_app.apps.chat.sdk.context.memory import memory_reconciler_stream

            cfg = self._memory_reconciliation_config()
            try:
                timeout = float(cfg.get("timeout_seconds") or 45.0)
            except Exception:
                timeout = 45.0
            out, channels, meta = await asyncio.wait_for(
                memory_reconciler_stream(
                    self.models_service,
                    candidates=candidate_rows,
                    reason=reason or "manual widget reconciliation dry run",
                    max_candidates=self._memory_reconciliation_limit(limit),
                ),
                timeout=max(5.0, min(timeout, 180.0)),
            )
            actions = [self._memory_model_dump(action) for action in list(out.actions or [])]
            proposal = {
                "job_id": job_id,
                "snapshot_id": snapshot.get("snapshot_id"),
                "created_at": created_at,
                "scope": job["scope"],
                "scope_filter": normalized_scope_filter,
                "dry_run": True,
                "actions": actions,
                "notes": out.notes,
                "warnings": list(out.warnings or []),
                "channels": channels,
                "meta": meta or {},
            }
            job["status"] = "succeeded"
            job["proposal_count"] = len(actions)
            job["warning_count"] = len(proposal["warnings"])

            proposal_key = self._memory_reconciliation_job_key(job_id, "proposal.json")
            proposal_uri = await self._memory_reconciliation_write_json(proposal_key, proposal)
            job.setdefault("artifacts", {})["proposal"] = {"key": proposal_key, "uri": proposal_uri, "mime": "application/json"}

            proposal_md = self._memory_reconciliation_markdown(job=job, before=snapshot, proposal=proposal)
            proposal_md_key = self._memory_reconciliation_job_key(job_id, "proposal.md")
            proposal_md_uri = await self._memory_reconciliation_write_text(
                proposal_md_key,
                proposal_md,
                mime="text/markdown",
            )
            job["artifacts"]["proposal_md"] = {
                "key": proposal_md_key,
                "uri": proposal_md_uri,
                "mime": "text/markdown",
            }
            await self._memory_reconciliation_store_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            await self._memory_reconciliation_store_job(job)

    async def _memory_reconciliation_handle_background_job(
        self,
        *,
        envelope: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        del envelope
        if not self._memory_reconciliation_enabled():
            return {
                "ok": False,
                "handled": True,
                "error": {"code": "memory_reconciliation_disabled", "message": "Memory reconciliation is disabled."},
            }
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return {
                "ok": False,
                "handled": True,
                "error": {"code": "memory_reconciliation_job_id_missing", "message": "Memory reconciliation job payload has no job_id."},
            }
        key = self._memory_reconciliation_job_key(job_id, "status.json")
        job = await self._memory_reconciliation_read_json(key, default=None)
        if not isinstance(job, dict):
            return {
                "ok": False,
                "handled": True,
                "error": {"code": "memory_reconciliation_job_not_found", "message": f"Memory reconciliation job {job_id!r} was not found."},
            }
        if str(job.get("status") or "") in {"succeeded", "applied", "restored"}:
            await self._memory_reconciliation_release_active_lock(job)
            return {"ok": True, "handled": True, "job": job, "idempotent": True}
        scope_filter = str(payload.get("scope_filter") or job.get("scope_filter") or "current_bundle")
        reason = str(payload.get("reason") or job.get("reason") or "manual widget reconciliation dry run")
        limit = self._memory_reconciliation_limit(payload.get("limit") or None)
        await self._memory_reconciliation_run_job(
            job=job,
            scope_filter=scope_filter,
            limit=limit,
            reason=reason,
        )
        updated = await self._memory_reconciliation_read_json(key, default=job)
        if not isinstance(updated, dict):
            updated = job
        await self._memory_reconciliation_release_active_lock(updated)
        return {
            "ok": str(updated.get("status") or "") == "succeeded",
            "handled": True,
            "job": updated,
        }

    async def handle_job(self, **kwargs) -> Dict[str, Any]:
        envelope = kwargs.get("job") if isinstance(kwargs.get("job"), dict) else {}
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        del metadata
        work_kind = str(kwargs.get("work_kind") or envelope.get("work_kind") or "").strip()
        if work_kind == MEMORY_RECONCILIATION_WORK_KIND:
            return await self._memory_reconciliation_handle_background_job(
                envelope=envelope,
                payload=payload if payload else dict(envelope.get("payload") or {}),
            )

        next_handler = getattr(super(), "handle_job", None)
        if callable(next_handler):
            result = next_handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict):
                return result
        return {
            "ok": False,
            "handled": False,
            "error": {"code": "unsupported_job", "message": f"Unsupported job kind {work_kind!r}."},
        }

    @api(method="POST", alias="memories_widget_reconcile_run", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_run(
        self,
        scope_filter: str = "current_bundle",
        limit: int = 40,
        reason: str = "manual widget reconciliation dry run",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")

        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        limited = self._memory_reconciliation_limit(limit)
        lock = self._memory_reconciliation_lock(normalized_scope_filter)
        async with lock:
            active = await self._memory_reconciliation_active_job(scope_filter=normalized_scope_filter)
            if active:
                return {
                    "ok": False,
                    "error": "memory_reconciliation_already_running",
                    "message": "A memory reconciliation job is already queued or running for this user and scope.",
                    "job": active,
                    "capabilities": self._memory_capabilities_payload(),
                }

            created_at = datetime.now(timezone.utc).isoformat()
            digest = hashlib.sha256(
                "\n".join([
                    scope.tenant,
                    scope.project,
                    scope.user_id,
                    scope.bundle_id,
                    normalized_scope_filter,
                    str(reason or ""),
                    created_at,
                    uuid.uuid4().hex,
                ]).encode("utf-8")
            ).hexdigest()[:12]
            job_id = f"memrec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{digest}"
            job: Dict[str, Any] = {
                "job_id": job_id,
                "status": "queued",
                "reason": reason or "manual widget reconciliation dry run",
                "scope": {
                    "tenant": scope.tenant,
                    "project": scope.project,
                    "user_id": scope.user_id,
                    "bundle_id": scope.bundle_id,
                },
                "scope_filter": normalized_scope_filter,
                "candidate_count": 0,
                "proposal_count": 0,
                "warning_count": 0,
                "created_at": created_at,
                "dry_run": True,
                "artifacts": {},
            }
            redis = getattr(self, "redis", None)
            if redis is None:
                job["status"] = "failed"
                job["error"] = "Redis is required to enqueue memory reconciliation jobs."
                job = await self._memory_reconciliation_store_job(job)
                return {
                    "ok": False,
                    "error": "memory_reconciliation_queue_unavailable",
                    "message": job["error"],
                    "job": job,
                    "capabilities": self._memory_capabilities_payload(),
                }

            active_lock_key = self._memory_reconciliation_active_lock_key(normalized_scope_filter)
            acquired = await redis.set(active_lock_key, job_id, nx=True, ex=3600)
            if not acquired:
                active = await self._memory_reconciliation_active_job(scope_filter=normalized_scope_filter)
                return {
                    "ok": False,
                    "error": "memory_reconciliation_already_running",
                    "message": "A memory reconciliation job is already queued or running for this user and scope.",
                    "job": active or {"status": "running", "scope_filter": normalized_scope_filter},
                    "capabilities": self._memory_capabilities_payload(),
                }
            job["active_lock_key"] = active_lock_key
            job = await self._memory_reconciliation_store_job(job)

            from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream

            user = getattr(self.comm_context, "user", None)
            user_type = self._memory_effective_user_type(str(getattr(user, "user_type", "") or "registered"))
            routing = getattr(self.comm_context, "routing", None)
            stream = RedisBackgroundJobStream(redis, tenant=scope.tenant, project=scope.project)
            try:
                enqueue = await stream.enqueue(
                    work_kind=MEMORY_RECONCILIATION_WORK_KIND,
                    bundle_id=scope.bundle_id,
                    user_id=scope.user_id,
                    user_type=user_type,
                    queue=user_type,
                    job_id=job_id,
                    dedupe_key=f"{MEMORY_RECONCILIATION_WORK_KIND}:{scope.tenant}:{scope.project}:{scope.user_id}:{scope.bundle_id}:{job_id}",
                    source={"surface": "memory_widget", "operation": "reconcile_run"},
                    metadata={
                        "conversation_id": str(getattr(routing, "conversation_id", "") or f"memory_reconciliation_{scope.user_id}"),
                        "turn_id": str(getattr(routing, "turn_id", "") or f"turn_{job_id}"),
                        "text": "Run memory reconciliation dry run.",
                        "timezone": str(getattr(user, "timezone", "") or ""),
                    },
                    payload={
                        "job_id": job_id,
                        "scope_filter": normalized_scope_filter,
                        "limit": limited,
                        "reason": reason or "manual widget reconciliation dry run",
                    },
                )
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job = await self._memory_reconciliation_store_job(job)
                await self._memory_reconciliation_release_active_lock(job)
                return {
                    "ok": False,
                    "error": "memory_reconciliation_enqueue_failed",
                    "message": job["error"],
                    "job": job,
                    "capabilities": self._memory_capabilities_payload(),
                }
            job["background_job"] = {
                "job_id": enqueue.job_id,
                "stream_key": enqueue.stream_key,
                "stream_id": enqueue.stream_id,
                "reason": enqueue.reason,
            }
            if not enqueue.enqueued:
                job["status"] = "failed"
                job["error"] = f"Memory reconciliation job was not enqueued ({enqueue.reason})."
                job = await self._memory_reconciliation_store_job(job)
                await self._memory_reconciliation_release_active_lock(job)
                return {
                    "ok": False,
                    "error": "memory_reconciliation_enqueue_failed",
                    "message": job["error"],
                    "job": job,
                    "capabilities": self._memory_capabilities_payload(),
                }
            job = await self._memory_reconciliation_store_job(job)
            return {
                "ok": True,
                "accepted": True,
                "job": job,
                "capabilities": self._memory_capabilities_payload(),
            }


class BaseEntrypointWithMemory(MemoryEntrypointMixin, BaseEntrypoint):
    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
        event_filter: Optional[Any] = None,
        ctx_client: Optional[Any] = None,
        continuation_source: Optional[Any] = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
            event_filter=event_filter,
            ctx_client=ctx_client,
            continuation_source=continuation_source,
        )


class BaseEntrypointWithEconomicsAndMemory(MemoryEntrypointMixin, BaseEntrypointWithEconomics):
    pass


__all__ = [
    "MemoryEntrypointMixin",
    "BaseEntrypointWithMemory",
    "BaseEntrypointWithEconomicsAndMemory",
]
