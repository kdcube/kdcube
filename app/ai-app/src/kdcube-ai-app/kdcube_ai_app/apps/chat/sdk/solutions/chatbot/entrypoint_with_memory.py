# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics
from kdcube_ai_app.infra.plugin.bundle_loader import api, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import Config

MEMORY_RECONCILIATION_WORK_KIND = "memory.reconciliation.run"
MEMORY_RECONCILER_ROLE = "memory.reconciler"
MEMORY_RECONCILER_AGENT_TYPES = {"lite", "regular", "strong"}
logger = logging.getLogger(__name__)


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
                    "retention_days": 30,
                    "storage_prefix": "memory/reconciliation/jobs",
                    "timeout_seconds": 45.0,
                },
                "snapshots": {
                    "enabled": True,
                    "max_memories": 1000,
                    "max_snapshots": 3,
                    "retention_days": 90,
                    "storage_prefix": "memory/snapshots",
                },
            },
            "ui": {
                "widgets": {
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

    @staticmethod
    def _memory_reconciler_agent_type(value: Any) -> str:
        normalized = str(value or "regular").strip().lower().replace("_", "-")
        if normalized in {"normal", "default", "balanced"}:
            normalized = "regular"
        return normalized if normalized in MEMORY_RECONCILER_AGENT_TYPES else "regular"

    def _memory_reconciler_role_override(self, agent_type: str) -> Dict[str, Dict[str, str]]:
        normalized = self._memory_reconciler_agent_type(agent_type)
        role_models = getattr(getattr(self, "config", None), "role_models", None)
        if not isinstance(role_models, dict):
            role_models = {}
        selected_role = f"{MEMORY_RECONCILER_ROLE}.{normalized}"
        selected_spec = role_models.get(selected_role)
        if not isinstance(selected_spec, dict) and normalized == "regular":
            selected_spec = role_models.get(MEMORY_RECONCILER_ROLE)
        if not isinstance(selected_spec, dict):
            try:
                selected_spec = self.config.get_default_role_spec()
            except Exception:
                selected_spec = {}
        provider = str(selected_spec.get("provider") or "anthropic").strip()
        model = str(selected_spec.get("model") or "").strip()
        if not model:
            return {}
        return {MEMORY_RECONCILER_ROLE: {"provider": provider or "anthropic", "model": model}}

    @staticmethod
    def _memory_json_safe_mapping(value: Any, *, field_name: str) -> Dict[str, Any]:
        if value is None or value == "":
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be a JSON object")
        try:
            return json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be JSON-serializable") from exc

    async def on_memory_reconciliation_request(self, *, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Optional bundle hook for reconciliation request normalization.

        Bundles may override this to validate or augment request-local controls
        before a memory reconciliation background job is enqueued. Return a
        JSON-serializable object with fields to merge back into the request.
        The `reconciliation_context` field is persisted with the job and rebound
        under `bundle_call_context.memory.reconciliation.context` when the job
        runs.
        """

        return None

    async def _memory_prepare_reconciliation_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        prepared = self._memory_json_safe_mapping(request, field_name="memory reconciliation request")
        hook = getattr(self, "on_memory_reconciliation_request", None)
        if callable(hook):
            maybe = hook(request=dict(prepared))
            if inspect.isawaitable(maybe):
                maybe = await maybe
            if maybe is not None:
                patch = self._memory_json_safe_mapping(
                    maybe,
                    field_name="memory reconciliation request hook response",
                )
                if patch.get("ok") is False:
                    return patch
                prepared.update(patch)
        prepared["reconciliation_context"] = self._memory_json_safe_mapping(
            prepared.get("reconciliation_context") or {},
            field_name="memory reconciliation context",
        )
        return prepared

    def _memory_reconciliation_enabled(self) -> bool:
        reconciliation_cfg = self._memory_reconciliation_config()
        return self._memory_widget_enabled() and _truthy(reconciliation_cfg.get("enabled"), True)

    async def _memory_refresh_bundle_props_for_background_job(self, *, reason: str) -> None:
        refresh = getattr(self, "refresh_bundle_props", None)
        if not callable(refresh):
            return
        state = dict(getattr(self, "_app_state", None) or {})
        context = getattr(self, "comm_context", None)
        actor = getattr(context, "actor", None)
        user = getattr(context, "user", None)

        def _fill_state(key: str, value: Any) -> None:
            if value is not None and value != "" and not state.get(key):
                state[key] = value

        if actor is not None:
            _fill_state("tenant", getattr(actor, "tenant_id", None))
            _fill_state("project", getattr(actor, "project_id", None))
        if user is not None:
            _fill_state("user", getattr(user, "user_id", None))
            _fill_state("user_type", getattr(user, "user_type", None))
        try:
            maybe = refresh(state=state, notify=False, reason=reason)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:
            logger.warning(
                "[memory.reconciliation] failed to refresh bundle props before background job",
                exc_info=True,
            )

    def _memory_snapshot_config(self) -> Dict[str, Any]:
        memory_cfg = self._memory_config()
        snapshot_cfg = memory_cfg.get("snapshots") if isinstance(memory_cfg.get("snapshots"), dict) else {}
        return snapshot_cfg if isinstance(snapshot_cfg, dict) else {}

    def _memory_snapshot_enabled(self) -> bool:
        snapshot_cfg = self._memory_snapshot_config()
        return self._memory_widget_enabled() and _truthy(snapshot_cfg.get("enabled"), True)

    def _memory_reconciliation_lock_key(self, scope_filter: str) -> str:
        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        bundle_part = "all" if normalized_scope_filter == "all_user_memories" else (scope.bundle_id or "bundle")
        return ":".join([
            scope.tenant,
            scope.project,
            scope.user_id,
            bundle_part,
            normalized_scope_filter,
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

    async def _memory_user_preferences(self) -> Dict[str, Any]:
        store = self._memory_store()
        ensure = getattr(store, "ensure_schema", None)
        if _truthy(self._memory_widget_config().get("ensure_schema"), True) and callable(ensure):
            await ensure()
        get_preferences = getattr(store, "get_user_preferences", None)
        if callable(get_preferences):
            return await get_preferences(scope=self._memory_scope())
        now = datetime.now(timezone.utc).isoformat()
        return {
            "memory_enabled": True,
            "updated_by": "",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }

    async def _memory_usage_enabled(self) -> bool:
        prefs = await self._memory_user_preferences()
        return bool(prefs.get("memory_enabled", True))

    async def _memory_usage_disabled_error(self) -> Optional[Dict[str, Any]]:
        if await self._memory_usage_enabled():
            return None
        return self._memory_error(
            "memory_usage_disabled_by_user",
            "Memory use is disabled by the user. Enable memory use before creating, changing, snapshotting, or reconciling memory notes.",
        )

    def _memory_viewer_payload(self) -> Dict[str, Any]:
        user_type = self._memory_effective_user_type("registered")
        return {
            "user_type": user_type,
            "is_admin": user_type in {"admin", "privileged"},
        }

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

    def _memory_reconciliation_retention_days(self) -> int:
        cfg = self._memory_reconciliation_config()
        try:
            raw = int(cfg.get("retention_days") or 30)
        except Exception:
            raw = 30
        return max(0, min(raw, 3650))

    def _memory_reconciliation_stale_after_seconds(self) -> float:
        cfg = self._memory_reconciliation_config()
        try:
            configured = float(cfg.get("stale_after_seconds") or 0)
        except Exception:
            configured = 0.0
        if configured > 0:
            return max(60.0, min(configured, 24 * 3600.0))
        try:
            timeout = float(cfg.get("timeout_seconds") or 45.0)
        except Exception:
            timeout = 45.0
        return max(300.0, min(timeout * 4.0, 3600.0))

    @staticmethod
    def _memory_iso_age_seconds(value: Any) -> Optional[float]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return None

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
            raw = int(cfg.get("max_snapshots") or 3)
        except Exception:
            raw = 3
        return max(1, min(raw, 3))

    def _memory_snapshot_retention_days(self) -> int:
        cfg = self._memory_snapshot_config()
        try:
            raw = int(cfg.get("retention_days") or 90)
        except Exception:
            raw = 90
        return max(0, min(raw, 3650))

    def _memory_reconciliation_prefix(self) -> str:
        cfg = self._memory_reconciliation_config()
        prefix = str(cfg.get("storage_prefix") or "memory/reconciliation/jobs").strip().strip("/")
        return prefix or "memory/reconciliation/jobs"

    def _memory_snapshot_prefix(self) -> str:
        cfg = self._memory_snapshot_config()
        prefix = str(cfg.get("storage_prefix") or "memory/snapshots").strip().strip("/")
        return prefix or "memory/snapshots"

    def _memory_reconciliation_storage(self, storage_bundle_id: str | None = None):
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage

        scope = self._memory_scope()
        bundle_id = str(storage_bundle_id or scope.bundle_id or "bundle").strip() or "bundle"
        storage_uri = get_settings().BUNDLE_STORAGE_URL or None
        return AIBundleStorage(
            tenant=scope.tenant,
            project=scope.project,
            ai_bundle_id=bundle_id,
            storage_uri=storage_uri,
        )

    async def _memory_reconciliation_write_text(
        self,
        key: str,
        content: str,
        *,
        mime: str = "text/plain",
        storage_bundle_id: str | None = None,
    ) -> str:
        try:
            storage = self._memory_reconciliation_storage(storage_bundle_id)
        except TypeError as exc:
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            storage = self._memory_reconciliation_storage()
        return await asyncio.to_thread(storage.write, key, content, mime=mime)

    async def _memory_reconciliation_write_json(
        self,
        key: str,
        payload: Dict[str, Any],
        *,
        storage_bundle_id: str | None = None,
    ) -> str:
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        try:
            return await self._memory_reconciliation_write_text(
                key,
                content,
                mime="application/json",
                storage_bundle_id=storage_bundle_id,
            )
        except TypeError as exc:
            if "storage_bundle_id" not in str(exc):
                raise
            return await self._memory_reconciliation_write_text(
                key,
                content,
                mime="application/json",
            )

    async def _memory_reconciliation_read_text(self, key: str, *, storage_bundle_id: str | None = None) -> str:
        try:
            storage = self._memory_reconciliation_storage(storage_bundle_id)
        except TypeError as exc:
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            storage = self._memory_reconciliation_storage()
        return await asyncio.to_thread(storage.read, key, as_text=True)

    async def _memory_reconciliation_read_json(
        self,
        key: str,
        default: Any = None,
        *,
        storage_bundle_id: str | None = None,
    ) -> Any:
        try:
            try:
                text = await self._memory_reconciliation_read_text(key, storage_bundle_id=storage_bundle_id)
            except TypeError as exc:
                if "storage_bundle_id" not in str(exc):
                    raise
                text = await self._memory_reconciliation_read_text(key)
            return json.loads(text) if text else default
        except Exception:
            return default

    def _memory_reconciliation_index_key(self) -> str:
        return f"{self._memory_reconciliation_prefix()}/index.json"

    @staticmethod
    def _memory_safe_storage_id(value: Any, fallback: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "-" for ch in str(value or fallback))

    @staticmethod
    def _memory_date_partition_from_id(value: Any) -> str:
        text = str(value or "").strip()
        for token in text.split("_"):
            if len(token) >= 8 and token[:8].isdigit():
                return f"{token[0:4]}/{token[4:6]}/{token[6:8]}"
        return "undated"

    def _memory_reconciliation_job_key(self, job_id: str, name: str) -> str:
        safe_job = self._memory_safe_storage_id(job_id, "job")
        safe_name = str(name or "status.json").strip().lstrip("/")
        partition = self._memory_date_partition_from_id(safe_job)
        return f"{self._memory_reconciliation_prefix()}/{partition}/{safe_job}/{safe_name}"

    def _memory_reconciliation_legacy_job_key(self, job_id: str, name: str) -> str:
        safe_job = self._memory_safe_storage_id(job_id, "job")
        safe_name = str(name or "status.json").strip().lstrip("/")
        return f"{self._memory_reconciliation_prefix()}/{safe_job}/{safe_name}"

    def _memory_snapshot_index_key(self) -> str:
        return f"{self._memory_snapshot_prefix()}/index.json"

    def _memory_snapshot_key(self, snapshot_id: str, name: str) -> str:
        safe_snapshot = self._memory_safe_storage_id(snapshot_id, "snapshot")
        safe_name = str(name or "status.json").strip().lstrip("/")
        partition = self._memory_date_partition_from_id(safe_snapshot)
        return f"{self._memory_snapshot_prefix()}/{partition}/{safe_snapshot}/{safe_name}"

    def _memory_snapshot_legacy_key(self, snapshot_id: str, name: str) -> str:
        safe_snapshot = self._memory_safe_storage_id(snapshot_id, "snapshot")
        safe_name = str(name or "status.json").strip().lstrip("/")
        return f"{self._memory_snapshot_prefix()}/{safe_snapshot}/{safe_name}"

    def _memory_artifact_storage_bundle_id(self, payload: Dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return self._memory_scope().bundle_id or "bundle"
        scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
        return (
            str(payload.get("storage_bundle_id") or "").strip()
            or str(scope.get("bundle_id") or "").strip()
            or self._memory_scope().bundle_id
            or "bundle"
        )

    async def _memory_get_maintenance_artifact(
        self,
        artifact_type: str,
        artifact_id: str,
        *,
        allow_cross_bundle: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        if getattr(self, "pg_pool", None) is None:
            return None
        try:
            store = self._memory_store()
            await store.ensure_schema()
            if allow_cross_bundle is None:
                allow_cross_bundle = _truthy(self._memory_widget_config().get("allow_all_user_memories"), True)
            return await store.get_maintenance_artifact(
                scope=self._memory_scope(),
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                allow_cross_bundle=bool(allow_cross_bundle),
            )
        except Exception:
            logger.debug(
                "[memory.maintenance] registry lookup failed: artifact_type=%s artifact_id=%s",
                artifact_type,
                artifact_id,
                exc_info=True,
            )
            return None

    async def _memory_register_maintenance_artifact(
        self,
        *,
        artifact_type: str,
        artifact_id: str,
        payload: Dict[str, Any],
    ) -> None:
        if getattr(self, "pg_pool", None) is None or not artifact_id:
            return
        try:
            from kdcube_ai_app.apps.chat.sdk.context.memory import MemoryScope

            item_scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
            current = self._memory_scope()
            scope = MemoryScope(
                tenant=str(item_scope.get("tenant") or current.tenant),
                project=str(item_scope.get("project") or current.project),
                user_id=str(item_scope.get("user_id") or current.user_id),
                bundle_id=str(item_scope.get("bundle_id") or current.bundle_id),
            ).normalized()
            artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
            summary = dict(payload)
            if artifact_type == "snapshot":
                summary.pop("memories", None)
            store = self._memory_store()
            await store.ensure_schema()
            await store.register_maintenance_artifact(
                scope=scope,
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                status=str(payload.get("status") or ""),
                scope_filter=str(payload.get("scope_filter") or "current_bundle"),
                storage_bundle_id=self._memory_artifact_storage_bundle_id(payload),
                summary=summary,
                artifacts=artifacts,
                created_at=payload.get("created_at"),
                updated_at=payload.get("updated_at"),
            )
        except Exception:
            logger.warning(
                "[memory.maintenance] failed to register artifact: artifact_type=%s artifact_id=%s",
                artifact_type,
                artifact_id,
                exc_info=True,
            )

    async def _memory_delete_maintenance_artifact(
        self,
        artifact_type: str,
        artifact_id: str,
        *,
        allow_cross_bundle: bool = False,
    ) -> None:
        if getattr(self, "pg_pool", None) is None or not artifact_id:
            return
        try:
            store = self._memory_store()
            await store.ensure_schema()
            await store.delete_maintenance_artifact(
                scope=self._memory_scope(),
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                allow_cross_bundle=allow_cross_bundle,
            )
        except Exception:
            logger.debug(
                "[memory.maintenance] registry delete failed: artifact_type=%s artifact_id=%s",
                artifact_type,
                artifact_id,
                exc_info=True,
            )

    async def _memory_backfill_maintenance_artifacts(
        self,
        *,
        artifact_type: str,
        items: Sequence[Dict[str, Any]],
    ) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("snapshot_id") if artifact_type == "snapshot" else item.get("job_id")
            artifact_id = str(raw_id or "").strip()
            if artifact_id:
                await self._memory_register_maintenance_artifact(
                    artifact_type=artifact_type,
                    artifact_id=artifact_id,
                    payload=dict(item),
                )

    async def _memory_list_maintenance_artifacts(
        self,
        *,
        artifact_type: str,
        scope_filter: str,
        limit: int,
        offset: int,
        local_items: Sequence[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if getattr(self, "pg_pool", None) is None:
            return None
        try:
            await self._memory_backfill_maintenance_artifacts(artifact_type=artifact_type, items=local_items)
            store = self._memory_store()
            await store.ensure_schema()
            return await store.list_maintenance_artifacts(
                scope=self._memory_scope(),
                artifact_type=artifact_type,
                scope_filter=scope_filter,
                limit=limit,
                offset=offset,
            )
        except Exception:
            logger.warning(
                "[memory.maintenance] registry list failed: artifact_type=%s scope_filter=%s",
                artifact_type,
                scope_filter,
                exc_info=True,
            )
            return None

    async def _memory_reconciliation_read_job_json(self, job_id: str, name: str = "status.json", default: Any = None) -> Any:
        if str(name or "status.json") == "status.json":
            registered = await self._memory_get_maintenance_artifact("reconciliation_job", job_id)
            if isinstance(registered, dict):
                artifacts = registered.get("artifacts") if isinstance(registered.get("artifacts"), dict) else {}
                status = artifacts.get("status") if isinstance(artifacts.get("status"), dict) else None
                key = str(status.get("key") or "") if status else ""
                if key:
                    payload = await self._memory_reconciliation_read_json(
                        key,
                        default=None,
                        storage_bundle_id=self._memory_artifact_storage_bundle_id(registered),
                    )
                    if isinstance(payload, dict):
                        payload.setdefault("storage_bundle_id", self._memory_artifact_storage_bundle_id(registered))
                        return payload
        for key in (
            self._memory_reconciliation_job_key(job_id, name),
            self._memory_reconciliation_legacy_job_key(job_id, name),
        ):
            payload = await self._memory_reconciliation_read_json(key, default=None)
            if payload is not None:
                return payload
        return default

    async def _memory_snapshot_read_json(self, snapshot_id: str, name: str = "status.json", default: Any = None) -> Any:
        if str(name or "status.json") == "status.json":
            registered = await self._memory_get_maintenance_artifact("snapshot", snapshot_id)
            if isinstance(registered, dict):
                artifacts = registered.get("artifacts") if isinstance(registered.get("artifacts"), dict) else {}
                status = artifacts.get("status") if isinstance(artifacts.get("status"), dict) else None
                key = str(status.get("key") or "") if status else ""
                if key:
                    payload = await self._memory_reconciliation_read_json(
                        key,
                        default=None,
                        storage_bundle_id=self._memory_artifact_storage_bundle_id(registered),
                    )
                    if isinstance(payload, dict):
                        payload.setdefault("storage_bundle_id", self._memory_artifact_storage_bundle_id(registered))
                        return payload
        for key in (
            self._memory_snapshot_key(snapshot_id, name),
            self._memory_snapshot_legacy_key(snapshot_id, name),
        ):
            payload = await self._memory_reconciliation_read_json(key, default=None)
            if payload is not None:
                return payload
        return default

    def _memory_snapshot_authorized(self, snapshot: Dict[str, Any]) -> bool:
        snap_scope = snapshot.get("scope") if isinstance(snapshot.get("scope"), dict) else {}
        if not snap_scope:
            return False
        scope = self._memory_scope()
        return (
            str(snap_scope.get("tenant") or "") == scope.tenant
            and str(snap_scope.get("project") or "") == scope.project
            and str(snap_scope.get("user_id") or "") == scope.user_id
            and (
                str(snap_scope.get("bundle_id") or "") == scope.bundle_id
                or _truthy(self._memory_widget_config().get("allow_all_user_memories"), True)
            )
        )

    @staticmethod
    def _memory_parse_iso_datetime(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _memory_index_cutoff(self, retention_days: int) -> Optional[datetime]:
        if retention_days <= 0:
            return None
        return datetime.now(timezone.utc) - timedelta(days=retention_days)

    def _memory_index_item_timestamp(self, item: Dict[str, Any]) -> Optional[datetime]:
        return self._memory_parse_iso_datetime(item.get("updated_at") or item.get("created_at"))

    def _memory_prune_index_items(
        self,
        items: list[Dict[str, Any]],
        *,
        max_items: int,
        retention_days: int,
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        cutoff = self._memory_index_cutoff(retention_days)
        ordered = sorted(
            [dict(item) for item in items if isinstance(item, dict)],
            key=lambda item: self._memory_index_item_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        kept: list[Dict[str, Any]] = []
        pruned: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in ordered:
            item_id = str(item.get("job_id") or item.get("snapshot_id") or "").strip()
            if item_id and item_id in seen:
                pruned.append(item)
                continue
            if item_id:
                seen.add(item_id)
            timestamp = self._memory_index_item_timestamp(item)
            if cutoff is not None and timestamp is not None and timestamp < cutoff:
                pruned.append(item)
                continue
            if len(kept) >= max_items:
                pruned.append(item)
                continue
            kept.append(item)
        return kept, pruned

    @staticmethod
    def _memory_page_window(items: list[Dict[str, Any]], *, limit: Any, offset: Any, default_limit: int = 20, max_limit: int = 100) -> tuple[list[Dict[str, Any]], int, int, bool]:
        try:
            normalized_limit = int(limit or default_limit)
        except Exception:
            normalized_limit = default_limit
        try:
            normalized_offset = int(offset or 0)
        except Exception:
            normalized_offset = 0
        normalized_limit = max(1, min(normalized_limit, max_limit))
        normalized_offset = max(0, normalized_offset)
        page = items[normalized_offset: normalized_offset + normalized_limit]
        return page, normalized_limit, normalized_offset, normalized_offset + normalized_limit < len(items)

    def _memory_reconciliation_job_prefixes(self, job_id: str) -> list[str]:
        safe_job = self._memory_safe_storage_id(job_id, "job")
        partition = self._memory_date_partition_from_id(safe_job)
        new_prefix = f"{self._memory_reconciliation_prefix()}/{partition}/{safe_job}/"
        legacy_prefix = f"{self._memory_reconciliation_prefix()}/{safe_job}/"
        return list(dict.fromkeys([new_prefix, legacy_prefix]))

    def _memory_snapshot_prefixes(self, snapshot_id: str) -> list[str]:
        safe_snapshot = self._memory_safe_storage_id(snapshot_id, "snapshot")
        partition = self._memory_date_partition_from_id(safe_snapshot)
        new_prefix = f"{self._memory_snapshot_prefix()}/{partition}/{safe_snapshot}/"
        legacy_prefix = f"{self._memory_snapshot_prefix()}/{safe_snapshot}/"
        return list(dict.fromkeys([new_prefix, legacy_prefix]))

    async def _memory_delete_storage_prefixes(self, prefixes: Sequence[str], *, storage_bundle_id: str | None = None) -> int:
        try:
            storage = self._memory_reconciliation_storage(storage_bundle_id)
        except TypeError as exc:
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            storage = self._memory_reconciliation_storage()
        deleted = 0
        for prefix in prefixes:
            try:
                deleted += int(await asyncio.to_thread(storage.delete, prefix))
            except Exception:
                logger.warning("[memory.storage] failed to delete prefix: prefix=%s", prefix, exc_info=True)
        return deleted

    async def _memory_delete_reconciliation_job_artifacts(self, job_id: str, *, storage_bundle_id: str | None = None) -> int:
        return await self._memory_delete_storage_prefixes(
            self._memory_reconciliation_job_prefixes(job_id),
            storage_bundle_id=storage_bundle_id,
        )

    async def _memory_delete_snapshot_artifacts(self, snapshot_id: str, *, storage_bundle_id: str | None = None) -> int:
        return await self._memory_delete_storage_prefixes(
            self._memory_snapshot_prefixes(snapshot_id),
            storage_bundle_id=storage_bundle_id,
        )

    async def _memory_reconciliation_load_index(
        self,
        *,
        prune: bool = True,
        storage_bundle_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        raw = await self._memory_reconciliation_read_json(
            self._memory_reconciliation_index_key(),
            default={},
            storage_bundle_id=storage_bundle_id,
        )
        jobs = raw.get("jobs") if isinstance(raw, dict) else []
        if not isinstance(jobs, list):
            return []
        if not prune:
            return [job for job in jobs if isinstance(job, dict)]
        kept, pruned = self._memory_prune_index_items(
            jobs,
            max_items=self._memory_reconciliation_max_jobs(),
            retention_days=self._memory_reconciliation_retention_days(),
        )
        if pruned:
            now = datetime.now(timezone.utc).isoformat()
            await self._memory_reconciliation_write_json(
                self._memory_reconciliation_index_key(),
                {"jobs": kept, "updated_at": now},
                storage_bundle_id=storage_bundle_id,
            )
            for item in pruned:
                job_id = str(item.get("job_id") or "").strip()
                if job_id:
                    await self._memory_delete_reconciliation_job_artifacts(job_id, storage_bundle_id=storage_bundle_id)
            logger.info(
                "[memory.reconciliation] pruned job index: tenant=%s project=%s bundle=%s kept=%s pruned=%s",
                self._memory_scope().tenant,
                self._memory_scope().project,
                self._memory_scope().bundle_id,
                len(kept),
                len(pruned),
            )
        return kept

    async def _memory_snapshot_load_index(
        self,
        *,
        prune: bool = True,
        storage_bundle_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        index_key = self._memory_snapshot_index_key()
        raw = await self._memory_reconciliation_read_json(index_key, default={}, storage_bundle_id=storage_bundle_id)
        snapshots = raw.get("snapshots") if isinstance(raw, dict) else []
        if not isinstance(snapshots, list):
            return []

        normalized: list[Dict[str, Any]] = []
        repaired = False
        for item in snapshots:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("scope"), dict):
                normalized.append(item)
                continue

            snapshot_id = str(item.get("snapshot_id") or "").strip()
            if snapshot_id:
                full = await self._memory_snapshot_read_json(snapshot_id, "status.json", default=None)
                if isinstance(full, dict):
                    normalized.append(self._memory_snapshot_summary(full))
                    repaired = True
                    continue
            normalized.append(item)

        pruned: list[Dict[str, Any]] = []
        if prune:
            normalized, pruned = self._memory_prune_index_items(
                normalized,
                max_items=self._memory_snapshot_max_items(),
                retention_days=self._memory_snapshot_retention_days(),
            )

        if repaired or pruned:
            now = datetime.now(timezone.utc).isoformat()
            await self._memory_reconciliation_write_json(
                index_key,
                {"snapshots": normalized, "updated_at": now},
                storage_bundle_id=storage_bundle_id,
            )
            for item in pruned:
                snapshot_id = str(item.get("snapshot_id") or "").strip()
                if snapshot_id:
                    await self._memory_delete_snapshot_artifacts(snapshot_id, storage_bundle_id=storage_bundle_id)
            logger.info(
                "[memory.snapshot] refreshed index: tenant=%s project=%s bundle=%s count=%s repaired=%s pruned=%s",
                self._memory_scope().tenant,
                self._memory_scope().project,
                self._memory_scope().bundle_id,
                len(normalized),
                repaired,
                len(pruned),
            )
        return normalized

    async def _memory_reconciliation_store_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        job = dict(job)
        job.setdefault("updated_at", now)
        job["updated_at"] = now
        job.setdefault("artifacts", {})
        storage_bundle_id = self._memory_artifact_storage_bundle_id(job)
        job["storage_bundle_id"] = storage_bundle_id
        status_key = self._memory_reconciliation_job_key(str(job.get("job_id") or ""), "status.json")
        status_uri = await self._memory_reconciliation_write_json(status_key, job, storage_bundle_id=storage_bundle_id)
        job["artifacts"]["status"] = {"key": status_key, "uri": status_uri, "mime": "application/json"}

        jobs = await self._memory_reconciliation_load_index(prune=False, storage_bundle_id=storage_bundle_id)
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
                "agent_type",
                "role_model",
                "background_job",
                "active_lock_key",
                "storage_bundle_id",
            )
            if key in job
        }
        summary["artifacts"] = job.get("artifacts", {})
        jobs.insert(0, summary)
        jobs, pruned = self._memory_prune_index_items(
            jobs,
            max_items=self._memory_reconciliation_max_jobs(),
            retention_days=self._memory_reconciliation_retention_days(),
        )
        await self._memory_reconciliation_write_json(
            self._memory_reconciliation_index_key(),
            {"jobs": jobs, "updated_at": now},
            storage_bundle_id=storage_bundle_id,
        )
        for item in pruned:
            job_id = str(item.get("job_id") or "").strip()
            if job_id:
                await self._memory_delete_reconciliation_job_artifacts(job_id, storage_bundle_id=storage_bundle_id)
                await self._memory_delete_maintenance_artifact("reconciliation_job", job_id, allow_cross_bundle=True)
        await self._memory_register_maintenance_artifact(
            artifact_type="reconciliation_job",
            artifact_id=str(job.get("job_id") or ""),
            payload=summary,
        )
        return job

    def _memory_snapshot_summary(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            key: snapshot.get(key)
            for key in (
                "snapshot_id",
                "status",
                "reason",
                "scope",
                "scope_filter",
                "memory_count",
                "created_at",
                "updated_at",
                "linked_job_id",
                "error",
                "storage_bundle_id",
            )
            if key in snapshot
        }
        summary["artifacts"] = snapshot.get("artifacts", {})
        return summary

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
            age = self._memory_iso_age_seconds(job.get("updated_at") or job.get("created_at"))
            if age is not None and age >= self._memory_reconciliation_stale_after_seconds():
                stale = dict(job)
                stale["status"] = "failed"
                stale["error"] = {
                    "code": "memory_reconciliation_stale",
                    "message": (
                        f"Memory reconciliation job was left {job.get('status')} for {int(age)} seconds "
                        "without completion."
                    ),
                }
                stale = await self._memory_reconciliation_store_job(stale)
                await self._memory_reconciliation_release_active_lock(stale)
                logger.warning(
                    "[memory.reconciliation] marked stale active job failed: job_id=%s age_sec=%s",
                    stale.get("job_id"),
                    int(age),
                )
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

    async def _memory_reconciliation_repair_stale_jobs(self) -> None:
        scope = self._memory_scope()
        for job in await self._memory_reconciliation_load_index():
            if str(job.get("status") or "") not in {"queued", "running"}:
                continue
            job_scope = job.get("scope") if isinstance(job.get("scope"), dict) else {}
            if (
                str(job_scope.get("tenant") or "") != scope.tenant
                or str(job_scope.get("project") or "") != scope.project
                or str(job_scope.get("user_id") or "") != scope.user_id
                or str(job_scope.get("bundle_id") or "") != scope.bundle_id
            ):
                continue
            age = self._memory_iso_age_seconds(job.get("updated_at") or job.get("created_at"))
            if age is None or age < self._memory_reconciliation_stale_after_seconds():
                continue
            stale = dict(job)
            stale["status"] = "failed"
            stale["error"] = {
                "code": "memory_reconciliation_stale",
                "message": (
                    f"Memory reconciliation job was left {job.get('status')} for {int(age)} seconds "
                    "without completion."
                ),
            }
            stale = await self._memory_reconciliation_store_job(stale)
            await self._memory_reconciliation_release_active_lock(stale)
            logger.warning(
                "[memory.reconciliation] marked stale job failed: job_id=%s age_sec=%s",
                stale.get("job_id"),
                int(age),
            )

    async def _memory_snapshot_store(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        snapshot = dict(snapshot)
        snapshot.setdefault("updated_at", now)
        snapshot["updated_at"] = now
        snapshot.setdefault("artifacts", {})
        storage_bundle_id = self._memory_artifact_storage_bundle_id(snapshot)
        snapshot["storage_bundle_id"] = storage_bundle_id
        status_key = self._memory_snapshot_key(str(snapshot.get("snapshot_id") or ""), "status.json")
        status_uri = await self._memory_reconciliation_write_json(status_key, snapshot, storage_bundle_id=storage_bundle_id)
        snapshot["artifacts"]["status"] = {"key": status_key, "uri": status_uri, "mime": "application/json"}

        snapshots = await self._memory_snapshot_load_index(prune=False, storage_bundle_id=storage_bundle_id)
        snapshots = [existing for existing in snapshots if existing.get("snapshot_id") != snapshot.get("snapshot_id")]
        summary = self._memory_snapshot_summary(snapshot)
        snapshots.insert(0, summary)
        snapshots, pruned = self._memory_prune_index_items(
            snapshots,
            max_items=self._memory_snapshot_max_items(),
            retention_days=self._memory_snapshot_retention_days(),
        )
        await self._memory_reconciliation_write_json(
            self._memory_snapshot_index_key(),
            {"snapshots": snapshots, "updated_at": now},
            storage_bundle_id=storage_bundle_id,
        )
        for item in pruned:
            snapshot_id = str(item.get("snapshot_id") or "").strip()
            if snapshot_id:
                await self._memory_delete_snapshot_artifacts(snapshot_id, storage_bundle_id=storage_bundle_id)
                await self._memory_delete_maintenance_artifact("snapshot", snapshot_id, allow_cross_bundle=True)
        await self._memory_register_maintenance_artifact(
            artifact_type="snapshot",
            artifact_id=str(snapshot.get("snapshot_id") or ""),
            payload=summary,
        )
        logger.info(
            "[memory.snapshot] stored: snapshot_id=%s tenant=%s project=%s bundle=%s scope_filter=%s memories=%s",
            snapshot.get("snapshot_id"),
            (snapshot.get("scope") or {}).get("tenant"),
            (snapshot.get("scope") or {}).get("project"),
            (snapshot.get("scope") or {}).get("bundle_id"),
            snapshot.get("scope_filter"),
            snapshot.get("memory_count"),
        )
        return snapshot

    async def _memory_snapshot_delete(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        snapshot_id = str(snapshot_id or "").strip()
        if not snapshot_id:
            return None
        registered = await self._memory_get_maintenance_artifact("snapshot", snapshot_id)
        storage_bundle_id = self._memory_artifact_storage_bundle_id(registered) if isinstance(registered, dict) else None
        snapshots = await self._memory_snapshot_load_index(storage_bundle_id=storage_bundle_id)
        target = next((item for item in snapshots if str(item.get("snapshot_id") or "") == snapshot_id), None)
        if not isinstance(target, dict) and isinstance(registered, dict):
            target = registered
        if not isinstance(target, dict) or not self._memory_snapshot_authorized(target):
            return None

        remaining = [item for item in snapshots if str(item.get("snapshot_id") or "") != snapshot_id]
        await self._memory_reconciliation_write_json(
            self._memory_snapshot_index_key(),
            {"snapshots": remaining, "updated_at": datetime.now(timezone.utc).isoformat()},
            storage_bundle_id=storage_bundle_id,
        )

        deleted = 0
        try:
            deleted = await self._memory_delete_snapshot_artifacts(snapshot_id, storage_bundle_id=storage_bundle_id)
            await self._memory_delete_maintenance_artifact("snapshot", snapshot_id, allow_cross_bundle=True)
        except Exception:
            logger.warning(
                "[memory.snapshot] failed to delete snapshot artifacts: snapshot_id=%s",
                snapshot_id,
                exc_info=True,
            )
        logger.info("[memory.snapshot] deleted: snapshot_id=%s artifacts_deleted=%s", snapshot_id, deleted)
        return {"snapshot": target, "deleted_artifacts": deleted}

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
            merged_memory = str(action.get("merged_memory") or "").strip()
            source_ids = action.get("source_memory_ids") if isinstance(action.get("source_memory_ids"), list) else []
            source_label = ", ".join(f"`{source_id}`" for source_id in source_ids) if source_ids else f"`{action.get('source_memory_id') or action.get('memory_id') or ''}`"
            lines.extend([
                f"### {index}. {action.get('action', 'action')}",
                "",
                f"- Source: {source_label}",
                f"- Target: `{action.get('target_memory_id') or ''}`",
                f"- Confidence: {action.get('confidence')}",
                f"- Reason: {action.get('reason') or ''}",
                "",
            ])
            if merged_memory:
                lines.extend([
                    "Merged target memory:",
                    "",
                    merged_memory,
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
        snapshot = await self._memory_snapshot_read_json(snapshot_id, "status.json", default=None)
        if isinstance(snapshot, dict) and not self._memory_snapshot_authorized(snapshot):
            return None
        if isinstance(snapshot, dict) and isinstance(snapshot.get("memories"), list):
            return snapshot
        if isinstance(snapshot, dict):
            artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
            item = artifacts.get("memories") if isinstance(artifacts.get("memories"), dict) else None
            key = str(item.get("key") or "") if item else ""
            if key:
                payload = await self._memory_reconciliation_read_json(
                    key,
                    default=None,
                    storage_bundle_id=self._memory_artifact_storage_bundle_id(snapshot),
                )
                if (
                    isinstance(payload, dict)
                    and self._memory_snapshot_authorized(payload)
                    and isinstance(payload.get("memories"), list)
                ):
                    return payload
        return None

    async def _memory_snapshot_memories_for_restore(self, memories: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
        prepared: list[Dict[str, Any]] = []
        for item in memories:
            if not isinstance(item, dict):
                continue
            memory = dict(item)
            text = "\n".join(
                part
                for part in (
                    str(memory.get("memory") or "").strip(),
                    str(memory.get("context") or "").strip(),
                )
                if part
            )
            memory["embedding"] = await self._memory_embed_one(text)
            memory["embedding_model"] = ""
            prepared.append(memory)
        return prepared

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

    @api(method="POST", alias="memories_widget_preferences", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_preferences(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        return {
            "ok": True,
            "preferences": await self._memory_user_preferences(),
            "capabilities": self._memory_capabilities_payload(),
            "viewer": self._memory_viewer_payload(),
        }

    @api(method="POST", alias="memories_widget_preferences_update", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_preferences_update(
        self,
        memory_enabled: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        store = self._memory_store()
        if _truthy(self._memory_widget_config().get("ensure_schema"), True):
            await store.ensure_schema()
        preferences = await store.set_user_preferences(
            scope=self._memory_scope(),
            memory_enabled=_truthy(memory_enabled, True),
            updated_by="user",
        )
        return {
            "ok": True,
            "preferences": preferences,
            "capabilities": self._memory_capabilities_payload(),
            "viewer": self._memory_viewer_payload(),
        }

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
        preferences = await store.get_user_preferences(scope=scope)
        total_count = await store.count_memories(
            scope=scope,
            query=normalized_query,
            labels=labels_list,
            keywords=keywords_list,
            status=status or "active",
            kind=kind,
            visible_to_user=True,
            include_private=False,
            scope_filter=normalized_scope_filter,
        )
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
        if normalized_query:
            # Semantic ranking is applied after candidate fetch; SQL count can
            # only count lexical matches. Keep the visible count truthful at
            # least for the current semantic page.
            total_count = max(total_count, page_offset + len(page_rows) + (1 if len(rows) > page_limit else 0))
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
            "viewer": self._memory_viewer_payload(),
            "preferences": preferences,
            "memories": [self._memory_record_payload(row) for row in page_rows],
            "count": total_count,
            "limit": page_limit,
            "offset": page_offset,
            "has_more": (page_offset + len(page_rows)) < total_count or len(rows) > page_limit,
        }

    async def _memory_widget_search_records(
        self,
        *,
        scope_filter: str,
        query: str = "",
        status: str = "active",
        kind: str = "",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        limit: int = 5000,
    ) -> list[Any]:
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemorySearchRequest, normalize_terms

        scope = self._memory_scope()
        normalized_query = str(query or "").strip()
        labels_list = normalize_terms(labels)
        keywords_list = normalize_terms(keywords)
        normalized_limit = max(1, min(int(limit or 5000), 5000))
        query_embedding = await self._memory_embed_one(normalized_query) if normalized_query else None
        store = self._memory_store()
        if _truthy(self._memory_widget_config().get("ensure_schema"), True):
            await store.ensure_schema()
        return await store.search(
            MemorySearchRequest(
                scope=scope,
                query=normalized_query,
                mode="hybrid" if normalized_query else "recent",
                labels=labels_list,
                keywords=keywords_list,
                status=status or "active",
                kind=kind,
                visible_to_user=True,
                include_private=False,
                scope_filter=self._memory_scope_filter(scope_filter),
                limit=normalized_limit,
                candidate_limit=normalized_limit,
                query_embedding=query_embedding,
            )
        )

    @staticmethod
    def _memory_export_markdown(memories: Sequence[Dict[str, Any]]) -> str:
        lines = ["# Memory Export", ""]
        for memory in memories:
            lines.extend([
                f"## {memory.get('id')}",
                "",
                str(memory.get("memory") or ""),
                "",
            ])
            context = str(memory.get("context") or "").strip()
            if context:
                lines.extend(["Context:", context, ""])
            lines.extend([
                f"- Status: `{memory.get('status')}`",
                f"- Kind: `{memory.get('kind')}`",
                f"- Labels: {', '.join(memory.get('labels') or []) or 'none'}",
                f"- Keywords: {', '.join(memory.get('keywords') or []) or 'none'}",
                f"- Updated: `{memory.get('updated_at')}`",
                "",
            ])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _memory_export_csv(memories: Sequence[Dict[str, Any]]) -> str:
        import csv
        import io

        out = io.StringIO()
        fields = ["id", "bundle_id", "memory", "context", "kind", "status", "labels", "keywords", "updated_at"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for memory in memories:
            writer.writerow({
                "id": memory.get("id", ""),
                "bundle_id": memory.get("bundle_id", ""),
                "memory": memory.get("memory", ""),
                "context": memory.get("context", ""),
                "kind": memory.get("kind", ""),
                "status": memory.get("status", ""),
                "labels": ", ".join(memory.get("labels") or []),
                "keywords": ", ".join(memory.get("keywords") or []),
                "updated_at": memory.get("updated_at", ""),
            })
        return out.getvalue()

    @api(method="POST", alias="memories_widget_export", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_export(
        self,
        scope_filter: str = "current_bundle",
        query: str = "",
        status: str = "active",
        kind: str = "",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        format: str = "json",
        limit: int = 5000,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_enabled():
            return self._memory_error("memory_disabled")
        rows = await self._memory_widget_search_records(
            scope_filter=scope_filter,
            query=query,
            status=status,
            kind=kind,
            labels=labels,
            keywords=keywords,
            limit=limit,
        )
        memories = [self._memory_record_payload(row) for row in rows]
        normalized_format = str(format or "json").strip().lower()
        if normalized_format == "md":
            normalized_format = "markdown"
        if normalized_format == "csv":
            content = self._memory_export_csv(memories)
            mime = "text/csv"
            filename = "memories.csv"
        elif normalized_format == "markdown":
            content = self._memory_export_markdown(memories)
            mime = "text/markdown"
            filename = "memories.md"
        else:
            content = json.dumps({"memories": memories, "count": len(memories)}, ensure_ascii=False, indent=2)
            mime = "application/json"
            filename = "memories.json"
            normalized_format = "json"
        return {
            "ok": True,
            "format": normalized_format,
            "filename": filename,
            "mime": mime,
            "content": content,
            "count": len(memories),
            "capabilities": self._memory_capabilities_payload(),
        }

    @api(method="POST", alias="memories_widget_delete_search", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_delete_search(
        self,
        scope_filter: str = "current_bundle",
        query: str = "",
        status: str = "active",
        kind: str = "",
        labels: Sequence[str] | str = (),
        keywords: Sequence[str] | str = (),
        limit: int = 5000,
        confirm: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        if not _truthy(confirm, False):
            return self._memory_error("memory_delete_requires_confirmation", "Pass confirm=true to permanently delete memories matching the current filters.")
        rows = await self._memory_widget_search_records(
            scope_filter=scope_filter,
            query=query,
            status=status,
            kind=kind,
            labels=labels,
            keywords=keywords,
            limit=limit,
        )
        store = self._memory_store()
        deleted_ids: list[str] = []
        skipped_ids: list[str] = []
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        for row in rows:
            payload = self._memory_record_payload(row)
            memory_id = str(payload.get("id") or "")
            if not memory_id:
                continue
            deleted = await store.delete_memory(
                scope=self._memory_scope(),
                memory_id=memory_id,
                visible_to_user=True,
                scope_filter=normalized_scope_filter,
                ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
            )
            if not deleted:
                skipped_ids.append(memory_id)
            else:
                deleted_ids.append(memory_id)
        return {
            "ok": True,
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "skipped_ids": skipped_ids,
            "soft_delete": False,
            "capabilities": self._memory_capabilities_payload(),
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
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
    async def memories_widget_delete(
        self,
        memory_id: str,
        scope_filter: str = "current_bundle",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        deleted = await self._memory_store().delete_memory(
            scope=self._memory_scope(),
            memory_id=memory_id,
            visible_to_user=True,
            scope_filter=self._memory_scope_filter(scope_filter),
            ensure_schema=_truthy(self._memory_widget_config().get("ensure_schema"), True),
        )
        if not deleted:
            return self._memory_error("memory_not_found")
        return {"ok": True, "memory_id": memory_id, "deleted": True, "capabilities": self._memory_capabilities_payload()}

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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
        snapshot = await self._memory_snapshot_create(
            scope_filter=self._memory_scope_filter(scope_filter),
            limit=self._memory_snapshot_limit(limit),
            reason=reason or "manual memory snapshot",
        )
        public_snapshot = dict(snapshot)
        public_snapshot.pop("memories", None)
        return {"ok": True, "snapshot": public_snapshot, "capabilities": self._memory_capabilities_payload()}

    @api(method="POST", alias="memories_widget_snapshots", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshots(self, limit: int = 10, offset: int = 0, scope_filter: str = "", **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        snapshots = await self._memory_snapshot_load_index()
        snapshots = [snapshot for snapshot in snapshots if isinstance(snapshot, dict) and self._memory_snapshot_authorized(snapshot)]
        registry = await self._memory_list_maintenance_artifacts(
            artifact_type="snapshot",
            scope_filter=normalized_scope_filter,
            limit=int(limit or 10),
            offset=int(offset or 0),
            local_items=snapshots,
        )
        if isinstance(registry, dict):
            return {
                "ok": True,
                "snapshots": registry.get("items") or [],
                "count": int(registry.get("count") or 0),
                "limit": int(registry.get("limit") or limit or 10),
                "offset": int(registry.get("offset") or offset or 0),
                "has_more": bool(registry.get("has_more")),
                "capabilities": self._memory_capabilities_payload(),
            }
        page, page_limit, page_offset, has_more = self._memory_page_window(
            snapshots,
            limit=limit,
            offset=offset,
            default_limit=10,
            max_limit=25,
        )
        return {
            "ok": True,
            "snapshots": page,
            "count": len(snapshots),
            "limit": page_limit,
            "offset": page_offset,
            "has_more": has_more,
            "capabilities": self._memory_capabilities_payload(),
        }

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
        snapshot = await self._memory_snapshot_read_json(snapshot_id, "status.json", default=None)
        if not isinstance(snapshot, dict) or not self._memory_snapshot_authorized(snapshot):
            return self._memory_error("memory_snapshot_not_found")
        artifacts = snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}
        item = artifacts.get(artifact) if isinstance(artifacts.get(artifact), dict) else None
        if not item:
            return self._memory_error("memory_snapshot_artifact_not_found")
        key = str(item.get("key") or "")
        if not key:
            return self._memory_error("memory_snapshot_artifact_not_found")
        content = await self._memory_reconciliation_read_text(
            key,
            storage_bundle_id=self._memory_artifact_storage_bundle_id(snapshot),
        )
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "artifact": artifact,
            "key": key,
            "uri": item.get("uri"),
            "mime": item.get("mime"),
            "content": content,
        }

    @api(method="POST", alias="memories_widget_snapshot_delete", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_snapshot_delete(
        self,
        snapshot_id: str,
        confirm: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_snapshot_enabled():
            return self._memory_error("memory_snapshots_disabled")
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        if not _truthy(confirm, False):
            return self._memory_error("memory_snapshot_delete_requires_confirmation", "Pass confirm=true to delete a memory snapshot.")
        deleted = await self._memory_snapshot_delete(snapshot_id)
        if deleted is None:
            return self._memory_error("memory_snapshot_not_found")
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "deleted_artifacts": deleted.get("deleted_artifacts", 0),
            "capabilities": self._memory_capabilities_payload(),
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
        if not confirm:
            return self._memory_error("memory_restore_requires_confirmation", "Preview the snapshot diff and pass confirm=true to restore.")

        snapshot = await self._memory_snapshot_load_full(snapshot_id)
        if not isinstance(snapshot, dict):
            return self._memory_error("memory_snapshot_not_found")
        snapshot_memories = snapshot.get("memories") if isinstance(snapshot.get("memories"), list) else []
        normalized_scope_filter = self._memory_scope_filter(scope_filter or snapshot.get("scope_filter") or "current_bundle")
        restore_memories = await self._memory_snapshot_memories_for_restore(snapshot_memories)

        safety_snapshot = await self._memory_snapshot_create(
            scope_filter=normalized_scope_filter,
            limit=self._memory_snapshot_limit(None),
            reason=f"safety snapshot before restoring {snapshot_id}",
            linked_job_id=f"restore:{snapshot_id}",
        )
        result = await self._memory_store().restore_snapshot(
            scope=self._memory_scope(),
            snapshot_id=snapshot_id,
            memories=restore_memories,
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
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
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
    async def memories_widget_reconcile_jobs(self, limit: int = 20, offset: int = 0, scope_filter: str = "", **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        await self._memory_reconciliation_repair_stale_jobs()
        jobs = await self._memory_reconciliation_load_index()
        registry = await self._memory_list_maintenance_artifacts(
            artifact_type="reconciliation_job",
            scope_filter=normalized_scope_filter,
            limit=int(limit or 20),
            offset=int(offset or 0),
            local_items=jobs,
        )
        if isinstance(registry, dict):
            return {
                "ok": True,
                "jobs": registry.get("items") or [],
                "count": int(registry.get("count") or 0),
                "limit": int(registry.get("limit") or limit or 20),
                "offset": int(registry.get("offset") or offset or 0),
                "has_more": bool(registry.get("has_more")),
                "capabilities": self._memory_capabilities_payload(),
            }
        page, page_limit, page_offset, has_more = self._memory_page_window(
            jobs,
            limit=limit,
            offset=offset,
            default_limit=20,
            max_limit=50,
        )
        return {
            "ok": True,
            "jobs": page,
            "count": len(jobs),
            "limit": page_limit,
            "offset": page_offset,
            "has_more": has_more,
            "capabilities": self._memory_capabilities_payload(),
        }

    @api(method="POST", alias="memories_widget_reconcile_job", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_job(self, job_id: str, **kwargs) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        job = await self._memory_reconciliation_read_job_json(job_id, "status.json", default=None)
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
        job = await self._memory_reconciliation_read_job_json(job_id, "status.json", default=None)
        if not isinstance(job, dict):
            return self._memory_error("memory_reconciliation_job_not_found")
        artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
        item = artifacts.get(artifact) if isinstance(artifacts.get(artifact), dict) else None
        if not item:
            return self._memory_error("memory_reconciliation_artifact_not_found")
        key = str(item.get("key") or "")
        if not key:
            return self._memory_error("memory_reconciliation_artifact_not_found")
        content = await self._memory_reconciliation_read_text(
            key,
            storage_bundle_id=self._memory_artifact_storage_bundle_id(job),
        )
        return {
            "ok": True,
            "job_id": job_id,
            "artifact": artifact,
            "key": key,
            "uri": item.get("uri"),
            "mime": item.get("mime"),
            "content": content,
        }

    @staticmethod
    def _memory_reconciliation_selected_indexes(value: Any) -> Optional[set[int]]:
        if value is None or value == "":
            return None
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = [item.strip() for item in value.split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        selected: set[int] = set()
        for item in raw_items:
            try:
                selected.add(int(item))
            except Exception:
                continue
        return selected

    async def _memory_reconciliation_rewrite_target(
        self,
        *,
        store: Any,
        action: Dict[str, Any],
        source_payload: Dict[str, Any],
        target_memory_id: str,
        scope_filter: str,
    ) -> tuple[bool, str]:
        merged_memory = str(action.get("merged_memory") or "").strip()
        if not merged_memory:
            return False, "merged_memory_missing"

        scope = self._memory_scope()
        target = await store.get_memory(
            scope=scope,
            memory_id=target_memory_id,
            visible_to_user=True,
            scope_filter=scope_filter,
        )
        if target is None:
            return False, "target_not_found"

        merged_context = str(action.get("merged_context") or target.context or "").strip()
        validation_error = self._memory_widget_validate_text(memory=merged_memory, context=merged_context)
        if validation_error:
            return False, str(validation_error.get("error") or "merged_memory_invalid")

        labels = self._memory_widget_terms(action.get("merged_labels") or list(target.labels or []))
        keywords = self._memory_widget_terms(action.get("merged_keywords") or list(target.keywords or []))
        updated_target = await store.edit_memory(
            scope=scope,
            memory_id=target_memory_id,
            memory=merged_memory,
            context=merged_context,
            kind=str(action.get("merged_kind") or target.kind or "fact"),
            status=str(target.status or "active"),
            visibility=str(target.visibility or "user"),
            labels=labels,
            keywords=keywords,
            confidence=max(0.75, min(float(action.get("confidence") or 0.85), 0.99)),
            importance=max(0.5, min(float(getattr(target, "importance_score", 0.7) or 0.7), 0.99)),
            pinned=bool(getattr(target, "pinned", False)),
            source=source_payload,
            metadata={"memory_reconciliation_target_rewrite": True},
            embedding=await self._memory_embed_one(f"{merged_memory}\n{merged_context}"),
            visible_to_user=True,
            scope_filter=scope_filter,
        )
        if updated_target is None:
            return False, "target_rewrite_failed"
        return True, ""

    async def _memory_reconciliation_apply_action(
        self,
        *,
        store: Any,
        action: Dict[str, Any],
        job_id: str,
        scope_filter: str,
        index: int,
    ) -> Dict[str, Any]:
        kind = str(action.get("action") or "").strip()
        reason = str(action.get("reason") or f"memory reconciliation job {job_id}").strip()
        source_payload = self._memory_source(
            action=f"reconcile_apply_{kind or 'unknown'}",
            payload={"job_id": job_id, "action_index": index, "action": action},
        )
        scope = self._memory_scope()

        if kind == "no_op":
            return {"index": index, "action": kind, "status": "skipped", "reason": reason or "no operation"}

        if kind == "retire":
            memory_id = str(action.get("memory_id") or "").strip()
            record = await store.retire_memory(
                scope=scope,
                memory_id=memory_id,
                reason=reason or f"retired by memory reconciliation job {job_id}",
                originator="user",
                source=source_payload,
            )
            return {
                "index": index,
                "action": kind,
                "memory_id": memory_id,
                "status": "applied" if record is not None else "skipped",
                "reason": None if record is not None else "memory_not_found",
            }

        if kind == "weaken":
            memory_id = str(action.get("memory_id") or "").strip()
            record = await store.update_status(
                scope=scope,
                memory_id=memory_id,
                status="weakened",
                source=source_payload,
            )
            return {
                "index": index,
                "action": kind,
                "memory_id": memory_id,
                "status": "applied" if record is not None else "skipped",
                "reason": None if record is not None else "memory_not_found",
            }

        if kind == "merge":
            source_memory_id = str(action.get("source_memory_id") or "").strip()
            target_memory_id = str(action.get("target_memory_id") or "").strip()
            target_rewritten = False
            merged_memory = str(action.get("merged_memory") or "").strip()
            if merged_memory:
                rewritten, rewrite_error = await self._memory_reconciliation_rewrite_target(
                    store=store,
                    action=action,
                    source_payload=source_payload,
                    target_memory_id=target_memory_id,
                    scope_filter=scope_filter,
                )
                if not rewritten:
                    return {
                        "index": index,
                        "action": kind,
                        "source_memory_id": source_memory_id,
                        "target_memory_id": target_memory_id,
                        "status": "skipped",
                        "reason": rewrite_error,
                    }
                target_rewritten = True
            result = await store.merge_memories(
                scope=scope,
                source_memory_id=source_memory_id,
                target_memory_id=target_memory_id,
                reason=reason or f"merged by memory reconciliation job {job_id}",
                originator="user",
                source=source_payload,
                scope_filter=scope_filter,
            )
            return {
                "index": index,
                "action": kind,
                "source_memory_id": source_memory_id,
                "target_memory_id": target_memory_id,
                "status": "applied" if result is not None else "skipped",
                "reason": None if result is not None else "source_or_target_not_found",
                "target_rewritten": target_rewritten,
            }

        if kind == "squash":
            target_memory_id = str(action.get("target_memory_id") or "").strip()
            raw_source_ids = action.get("source_memory_ids") if isinstance(action.get("source_memory_ids"), list) else []
            source_memory_ids: list[str] = []
            seen_sources: set[str] = set()
            for item in raw_source_ids:
                source_memory_id = str(item or "").strip()
                if not source_memory_id or source_memory_id == target_memory_id or source_memory_id in seen_sources:
                    continue
                seen_sources.add(source_memory_id)
                source_memory_ids.append(source_memory_id)
            if not target_memory_id or not source_memory_ids:
                return {
                    "index": index,
                    "action": kind,
                    "target_memory_id": target_memory_id,
                    "source_memory_ids": source_memory_ids,
                    "status": "skipped",
                    "reason": "source_or_target_missing",
                }
            merged_memory = str(action.get("merged_memory") or "").strip()
            if not merged_memory:
                return {
                    "index": index,
                    "action": kind,
                    "target_memory_id": target_memory_id,
                    "source_memory_ids": source_memory_ids,
                    "status": "skipped",
                    "reason": "merged_memory_missing",
                }
            merged_context = str(action.get("merged_context") or "").strip()
            validation_error = self._memory_widget_validate_text(memory=merged_memory, context=merged_context)
            if validation_error:
                return {
                    "index": index,
                    "action": kind,
                    "target_memory_id": target_memory_id,
                    "source_memory_ids": source_memory_ids,
                    "status": "skipped",
                    "reason": str(validation_error.get("error") or "merged_memory_invalid"),
                }
            result = await store.squash_memories(
                scope=scope,
                source_memory_ids=source_memory_ids,
                target_memory_id=target_memory_id,
                merged_memory=merged_memory,
                merged_context=merged_context,
                merged_kind=str(action.get("merged_kind") or "fact"),
                labels=self._memory_widget_terms(action.get("merged_labels") or []),
                keywords=self._memory_widget_terms(action.get("merged_keywords") or []),
                confidence=max(0.75, min(float(action.get("confidence") or 0.85), 0.99)),
                importance=0.7,
                embedding=await self._memory_embed_one(f"{merged_memory}\n{merged_context}"),
                reason=reason or f"squashed by memory reconciliation job {job_id}",
                originator="user",
                source=source_payload,
                scope_filter=scope_filter,
            )
            if not isinstance(result, dict):
                return {
                    "index": index,
                    "action": kind,
                    "target_memory_id": target_memory_id,
                    "source_memory_ids": source_memory_ids,
                    "status": "skipped",
                    "reason": "source_or_target_not_found",
                }
            merged_sources = [str(item.id) for item in (result.get("sources") or [])]
            skipped_sources = list(result.get("skipped_sources") or [])
            return {
                "index": index,
                "action": kind,
                "source_memory_ids": source_memory_ids,
                "target_memory_id": target_memory_id,
                "merged_source_ids": merged_sources,
                "skipped_sources": skipped_sources,
                "status": "applied" if merged_sources and not skipped_sources else ("partial" if merged_sources else "skipped"),
                "reason": None if merged_sources and not skipped_sources else ("some_sources_skipped" if merged_sources else "source_or_target_not_found"),
                "target_rewritten": True,
            }

        return {"index": index, "action": kind or "unknown", "status": "skipped", "reason": "unsupported_action"}

    @api(method="POST", alias="memories_widget_reconcile_apply", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_apply(
        self,
        job_id: str,
        confirm: bool = False,
        action_indexes: Any = None,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        if not self._memory_widget_write_enabled():
            return self._memory_error("memory_write_disabled")
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error
        if not _truthy(confirm, False):
            return self._memory_error("memory_reconciliation_apply_requires_confirmation", "Pass confirm=true to apply a reconciliation proposal.")

        job = await self._memory_reconciliation_read_job_json(job_id, "status.json", default=None)
        if not isinstance(job, dict):
            return self._memory_error("memory_reconciliation_job_not_found")
        if str(job.get("status") or "") == "applied":
            return {"ok": True, "job": job, "idempotent": True, "capabilities": self._memory_capabilities_payload()}
        if str(job.get("status") or "") != "succeeded":
            return self._memory_error("memory_reconciliation_job_not_ready", "Only succeeded dry-run jobs can be applied.")

        artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
        proposal_item = artifacts.get("proposal") if isinstance(artifacts.get("proposal"), dict) else None
        proposal_key = str(proposal_item.get("key") or "") if proposal_item else self._memory_reconciliation_job_key(job_id, "proposal.json")
        proposal = await self._memory_reconciliation_read_json(
            proposal_key,
            default=None,
            storage_bundle_id=self._memory_artifact_storage_bundle_id(job),
        )
        if not isinstance(proposal, dict):
            return self._memory_error("memory_reconciliation_proposal_not_found")

        job_scope = job.get("scope") if isinstance(job.get("scope"), dict) else {}
        scope = self._memory_scope()
        if (
            str(job_scope.get("tenant") or "") != scope.tenant
            or str(job_scope.get("project") or "") != scope.project
            or str(job_scope.get("user_id") or "") != scope.user_id
            or (
                str(job_scope.get("bundle_id") or "") != scope.bundle_id
                and not _truthy(self._memory_widget_config().get("allow_all_user_memories"), True)
            )
        ):
            return self._memory_error("memory_reconciliation_job_not_found")

        scope_filter = self._memory_scope_filter(str(job.get("scope_filter") or proposal.get("scope_filter") or "current_bundle"))
        safety_snapshot = await self._memory_snapshot_create(
            scope_filter=scope_filter,
            limit=self._memory_snapshot_limit(None),
            reason=f"safety snapshot before applying reconciliation job {job_id}",
            linked_job_id=f"apply:{job_id}",
        )

        selected = self._memory_reconciliation_selected_indexes(action_indexes)
        actions = proposal.get("actions") if isinstance(proposal.get("actions"), list) else []
        store = self._memory_store()
        results: list[Dict[str, Any]] = []
        for index, action in enumerate(actions):
            if selected is not None and index not in selected:
                continue
            if not isinstance(action, dict):
                results.append({"index": index, "action": "unknown", "status": "skipped", "reason": "invalid_action"})
                continue
            results.append(
                await self._memory_reconciliation_apply_action(
                    store=store,
                    action=action,
                    job_id=job_id,
                    scope_filter=scope_filter,
                    index=index,
                )
            )

        applied_count = sum(1 for item in results if item.get("status") == "applied")
        partial_count = sum(1 for item in results if item.get("status") == "partial")
        skipped_count = sum(1 for item in results if item.get("status") not in {"applied", "partial"})
        now = datetime.now(timezone.utc).isoformat()
        job["status"] = "applied"
        job["applied_at"] = now
        job["apply_result"] = {
            "applied_count": applied_count,
            "partial_count": partial_count,
            "skipped_count": skipped_count,
            "results": results,
            "safety_snapshot_id": safety_snapshot.get("snapshot_id"),
        }
        job.setdefault("artifacts", {})["apply_safety_snapshot"] = {
            "key": (safety_snapshot.get("artifacts") or {}).get("memories", {}).get("key"),
            "uri": (safety_snapshot.get("artifacts") or {}).get("memories", {}).get("uri"),
            "mime": "application/json",
        }
        job = await self._memory_reconciliation_store_job(job)
        await self._memory_reconciliation_release_active_lock(job)
        logger.info(
            "[memory.reconciliation] proposal applied: job_id=%s applied=%s skipped=%s",
            job_id,
            applied_count,
            skipped_count,
        )
        public_safety = dict(safety_snapshot)
        public_safety.pop("memories", None)
        return {
            "ok": True,
            "job": job,
            "safety_snapshot": public_safety,
            "apply_result": job["apply_result"],
            "capabilities": self._memory_capabilities_payload(),
        }

    async def _memory_reconciliation_run_job(
        self,
        *,
        job: Dict[str, Any],
        scope_filter: str = "current_bundle",
        limit: int = 40,
        reason: str = "manual widget reconciliation dry run",
        agent_type: str = "regular",
        reconciliation_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        job = dict(job)
        job_id = str(job.get("job_id") or "")
        created_at = str(job.get("created_at") or datetime.now(timezone.utc).isoformat())
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        normalized_agent_type = self._memory_reconciler_agent_type(agent_type or job.get("agent_type"))
        job["agent_type"] = normalized_agent_type
        job_reconciliation_context = self._memory_json_safe_mapping(
            reconciliation_context if reconciliation_context is not None else job.get("reconciliation_context") or {},
            field_name="memory reconciliation context",
        )
        if job_reconciliation_context:
            job["reconciliation_context"] = job_reconciliation_context
        try:
            logger.info(
                "[memory.reconciliation] job start: job_id=%s bundle=%s scope_filter=%s limit=%s agent_type=%s",
                job_id,
                (job.get("scope") or {}).get("bundle_id"),
                normalized_scope_filter,
                limit,
                normalized_agent_type,
            )
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
            logger.info(
                "[memory.reconciliation] candidates ready: job_id=%s candidates=%s needs_reconciliation=%s",
                job_id,
                len(candidates),
                bool((analysis or {}).get("needs_reconciliation")),
            )

            from kdcube_ai_app.apps.chat.sdk.context.memory import memory_reconciler_stream
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context_patch

            cfg = self._memory_reconciliation_config()
            try:
                timeout = float(cfg.get("timeout_seconds") or 45.0)
            except Exception:
                timeout = 45.0
            call_context_patch: Dict[str, Any] = {
                "memory": {
                    "reconciliation": {
                        "job_id": job_id,
                        "agent_type": normalized_agent_type,
                        "context": job_reconciliation_context,
                    }
                }
            }
            role_override = self._memory_reconciler_role_override(normalized_agent_type)
            if role_override:
                call_context_patch["role_models"] = role_override
                job["role_model"] = role_override[MEMORY_RECONCILER_ROLE]
            with bind_current_bundle_call_context_patch(call_context_patch):
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
                "agent_type": normalized_agent_type,
                "role_model": job.get("role_model"),
                "reconciliation_context": job_reconciliation_context,
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
            logger.info(
                "[memory.reconciliation] job succeeded: job_id=%s proposals=%s warnings=%s",
                job_id,
                job.get("proposal_count", 0),
                job.get("warning_count", 0),
            )
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            await self._memory_reconciliation_store_job(job)
            logger.exception(
                "[memory.reconciliation] job failed: job_id=%s scope_filter=%s",
                job_id,
                normalized_scope_filter,
            )

    async def _memory_reconciliation_handle_background_job(
        self,
        *,
        envelope: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        del envelope
        await self._memory_refresh_bundle_props_for_background_job(reason="memory.reconciliation.background_job")
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
            logger.warning("[memory.reconciliation] background job missing status: job_id=%s", job_id)
            return {
                "ok": False,
                "handled": True,
                "error": {"code": "memory_reconciliation_job_not_found", "message": f"Memory reconciliation job {job_id!r} was not found."},
            }
        if str(job.get("status") or "") in {"succeeded", "applied", "restored"}:
            await self._memory_reconciliation_release_active_lock(job)
            logger.info("[memory.reconciliation] background job idempotent: job_id=%s status=%s", job_id, job.get("status"))
            return {"ok": True, "handled": True, "job": job, "idempotent": True}
        scope_filter = str(payload.get("scope_filter") or job.get("scope_filter") or "current_bundle")
        reason = str(payload.get("reason") or job.get("reason") or "manual widget reconciliation dry run")
        limit = self._memory_reconciliation_limit(payload.get("limit") or None)
        agent_type = self._memory_reconciler_agent_type(payload.get("agent_type") or job.get("agent_type"))
        reconciliation_context = self._memory_json_safe_mapping(
            payload.get("reconciliation_context") if "reconciliation_context" in payload else job.get("reconciliation_context") or {},
            field_name="memory reconciliation context",
        )
        await self._memory_reconciliation_run_job(
            job=job,
            scope_filter=scope_filter,
            limit=limit,
            reason=reason,
            agent_type=agent_type,
            reconciliation_context=reconciliation_context,
        )
        updated = await self._memory_reconciliation_read_json(key, default=job)
        if not isinstance(updated, dict):
            updated = job
        await self._memory_reconciliation_release_active_lock(updated)
        logger.info(
            "[memory.reconciliation] background job handled: job_id=%s status=%s",
            job_id,
            updated.get("status"),
        )
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
            logger.info(
                "[memory.reconciliation] dispatch background job: job_id=%s work_kind=%s",
                str((payload or {}).get("job_id") or (envelope.get("payload") or {}).get("job_id") or kwargs.get("job_id") or ""),
                work_kind,
            )
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
        if work_kind:
            logger.warning(
                "[memory.reconciliation] unsupported background job reached memory entrypoint: work_kind=%s keys=%s",
                work_kind,
                sorted(str(key) for key in kwargs.keys()),
            )
        return {
            "ok": False,
            "handled": False,
            "error": {"code": "unsupported_job", "message": f"Unsupported job kind {work_kind!r}."},
        }

    async def on_turn_completed(
        self,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[BaseException] = None,
        status: str = "completed",
        reason: Optional[str] = None,
        comm_context: ChatTaskPayload = None,
        command: str | None = None,
        **kwargs,
    ) -> None:
        next_hook = getattr(super(), "on_turn_completed", None)
        if callable(next_hook):
            maybe = next_hook(
                result=result,
                error=error,
                status=status,
                reason=reason,
                comm_context=comm_context,
                command=command,
                **kwargs,
            )
            if inspect.isawaitable(maybe):
                await maybe

        try:
            from kdcube_ai_app.infra.jobs.stream import BACKGROUND_JOB_OPERATION
        except Exception:
            BACKGROUND_JOB_OPERATION = "__kdcube_on_job__"

        if command != BACKGROUND_JOB_OPERATION:
            return

        context = comm_context or self.comm_context
        request = getattr(context, "request", None)
        payload = getattr(request, "payload", None) if request is not None else None
        if not isinstance(payload, dict):
            return
        work_kind = str(payload.get("work_kind") or (payload.get("job") or {}).get("work_kind") or "").strip()
        if work_kind != MEMORY_RECONCILIATION_WORK_KIND:
            return

        job_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        job_id = str(job_payload.get("job_id") or payload.get("job_id") or (payload.get("job") or {}).get("job_id") or "").strip()
        if not job_id:
            logger.warning("[memory.reconciliation] background completion hook missing job_id")
            return

        job = await self._memory_reconciliation_read_job_json(job_id, "status.json", default=None)
        if not isinstance(job, dict):
            logger.warning("[memory.reconciliation] background completion hook missing status: job_id=%s", job_id)
            return

        terminal_status = str(job.get("status") or "")
        if terminal_status in {"succeeded", "failed", "applied", "restored"}:
            await self._memory_reconciliation_release_active_lock(job)
            return

        result_ok = isinstance(result, dict) and bool(result.get("ok"))
        result_handled = isinstance(result, dict) and bool(result.get("handled"))
        if status == "completed" and result_ok:
            return

        job["status"] = "failed"
        if isinstance(result, dict) and result.get("error"):
            job["error"] = result.get("error")
        else:
            job["error"] = {
                "code": "memory_reconciliation_background_unfinished",
                "message": (
                    f"Background job finished without completing memory reconciliation "
                    f"(status={status!r}, reason={reason or ''!r}, handled={result_handled})."
                ),
            }
        job = await self._memory_reconciliation_store_job(job)
        await self._memory_reconciliation_release_active_lock(job)
        logger.warning(
            "[memory.reconciliation] background job finalized as failed: job_id=%s status=%s reason=%s result=%s",
            job_id,
            status,
            reason,
            result,
        )

    @api(method="POST", alias="memories_widget_reconcile_run", route="operations", user_types=("registered", "paid", "privileged"))
    async def memories_widget_reconcile_run(
        self,
        scope_filter: str = "current_bundle",
        limit: int = 40,
        reason: str = "manual widget reconciliation dry run",
        agent_type: str = "regular",
        **kwargs,
    ) -> Dict[str, Any]:
        agent_type = kwargs.get("reconciler_agent_type", agent_type)
        request_context = kwargs.get(
            "reconciliation_context",
            kwargs.get("memory_reconciliation_context", kwargs.get("context", {})),
        )
        if not self._memory_reconciliation_enabled():
            return self._memory_error("memory_reconciliation_disabled")
        disabled_error = await self._memory_usage_disabled_error()
        if disabled_error:
            return disabled_error

        try:
            prepared_request = await self._memory_prepare_reconciliation_request({
                "scope_filter": scope_filter,
                "limit": limit,
                "reason": reason,
                "agent_type": agent_type,
                "reconciliation_context": request_context,
            })
        except ValueError as exc:
            return self._memory_error("memory_reconciliation_request_invalid", str(exc))
        if prepared_request.get("ok") is False:
            return {
                "ok": False,
                "error": prepared_request.get("error") or "memory_reconciliation_request_rejected",
                "message": prepared_request.get("message") or "Memory reconciliation request was rejected by the bundle.",
                "capabilities": self._memory_capabilities_payload(),
            }
        scope_filter = str(prepared_request.get("scope_filter") or scope_filter)
        limit = prepared_request.get("limit", limit)
        reason = str(prepared_request.get("reason") or reason)
        agent_type = str(prepared_request.get("agent_type") or agent_type)
        reconciliation_context = self._memory_json_safe_mapping(
            prepared_request.get("reconciliation_context") or {},
            field_name="memory reconciliation context",
        )

        scope = self._memory_scope()
        normalized_scope_filter = self._memory_scope_filter(scope_filter)
        limited = self._memory_reconciliation_limit(limit)
        normalized_agent_type = self._memory_reconciler_agent_type(agent_type)
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
                "agent_type": normalized_agent_type,
                "artifacts": {},
            }
            if reconciliation_context:
                job["reconciliation_context"] = reconciliation_context
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
                        "agent_type": normalized_agent_type,
                        "reconciliation_context": reconciliation_context,
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
