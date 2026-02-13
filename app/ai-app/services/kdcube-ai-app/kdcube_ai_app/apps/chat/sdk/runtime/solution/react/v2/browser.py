# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/browser.py

from __future__ import annotations

import logging
import json
import pathlib
import time
from datetime import datetime, timezone
import tempfile
import os

from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import search_context, ContextRAGClient, unwrap_payload
from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import get_exec_workspace_root
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import (
    TIMELINE_KIND,
    SOURCES_POOL_KIND,
    parse_timeline_payload,
    Timeline,
    extract_turn_ids_from_blocks,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx

PROJECT_LOG_SLOTS = { "project_log" }
SOURCES_POOL_ARTIFACT_TAG = f"artifact:{SOURCES_POOL_KIND}"

logger = logging.getLogger(__name__)

class ContextBrowser:

    def __init__(self, *,
                 ctx_client: Optional[ContextRAGClient] = None,
                 logger: Optional[AgentLogger] = None,
                 model_service: Optional[ModelServiceBase] = None,
                 exclude_contributions: Optional[List[str]] = None,
                 include_only_contributions: Optional[List[str]] = None,
                 cache_additional_min_blocks: int = 4,
                 cache_additional_offset: int = 2,
                 runtime_ctx: Optional[RuntimeCtx] = None):
        self.ctx_client = ctx_client
        self.log = logger or AgentLogger("context_browser")
        self.svc = model_service
        self._runtime_ctx: RuntimeCtx = runtime_ctx or RuntimeCtx()
        self._timeline: Optional[Timeline] = None
        self._contrib_exclude = set([t for t in (exclude_contributions or []) if isinstance(t, str)])
        self._contrib_include_only = set([t for t in (include_only_contributions or []) if isinstance(t, str)])
        self._cache_min_blocks = max(1, int(cache_additional_min_blocks))
        self._cache_offset = max(1, int(cache_additional_offset))
        self._turn_log_cache: Dict[str, Dict[str, Any]] = {}

    async def load_timeline(
            self,
            *,
            days: int = 365,
    ):
        """
        Load timeline for this conversation and initialize the in-memory Timeline.
        """
        user_id =self._runtime_ctx.user_id
        conversation_id = self._runtime_ctx.conversation_id
        self._ensure_workspace()
        timeline_payload: Optional[Dict[str, Any]] = None
        try:
            res_ws = await self.ctx_client.recent(
                kinds=(f"artifact:{TIMELINE_KIND}",),
                roles=("artifact",),
                limit=1,
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=True,
            )
            ws_items = list(res_ws.get("items") or [])
            if ws_items:
                payload = unwrap_payload(ws_items[0]) or {}
                parsed = parse_timeline_payload(payload)
                timeline_payload = parsed
                self._write_timeline_payload(payload)
        except Exception:
            timeline_payload = None

        if timeline_payload is None:
            timeline_payload = {"blocks": [], "sources_pool": []}
        # Load sources pool from separate artifact (latest)
        try:
            res_sp = await self.ctx_client.recent(
                kinds=(f"artifact:{SOURCES_POOL_KIND}",),
                roles=("artifact",),
                limit=1,
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                with_payload=True,
            )
            sp_items = list(res_sp.get("items") or [])
            if sp_items:
                sp_payload = unwrap_payload(sp_items[0]) or {}
                if isinstance(sp_payload, dict):
                    sources_pool = sp_payload.get("sources_pool")
                elif isinstance(sp_payload, list):
                    sources_pool = sp_payload
                else:
                    sources_pool = None
                if isinstance(sources_pool, list):
                    timeline_payload["sources_pool"] = sources_pool
        except Exception:
            pass
        # Back-compat: if sources_pool artifact missing, keep any sources_pool inside timeline payload.

        self._timeline = Timeline.from_payload(timeline_payload, runtime=self._runtime_ctx, svc=self.svc)
        if not self._timeline.conversation_started_at:
            try:
                self._timeline.set_conversation_started_at(self._runtime_ctx.started_at or "")
            except Exception:
                pass
        self._timeline.set_current_turn_offset(len(self._timeline.blocks))
        try:
            self._timeline.ensure_turn_header(
                turn_id=self._runtime_ctx.turn_id or "",
                ts=self._runtime_ctx.started_at,
            )
        except Exception:
            pass
        if self._timeline.cache_last_touch_at is None and self._timeline.cache_last_ttl_seconds is None:
            try:
                blocks = list(self._timeline.blocks or [])
                last_idx = None
                for idx in range(len(blocks) - 1, -1, -1):
                    if (blocks[idx].get("type") or "") == "assistant.completion":
                        last_idx = idx
                        break
                candidate_indices: List[int] = []
                if last_idx is not None:
                    if last_idx - 1 >= 0:
                        candidate_indices.append(last_idx - 1)
                    candidate_indices.append(last_idx)

                inferred_ts: Optional[int] = None
                for idx in candidate_indices:
                    blk = blocks[idx]
                    ts_val = blk.get("ts")
                    if isinstance(ts_val, (int, float)):
                        inferred_ts = int(ts_val)
                        break
                    if isinstance(ts_val, str):
                        s = ts_val.strip()
                        if not s:
                            continue
                        try:
                            if s.endswith("Z"):
                                s = s[:-1] + "+00:00"
                            dt = datetime.fromisoformat(s)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            inferred_ts = int(dt.timestamp())
                            break
                        except Exception:
                            try:
                                inferred_ts = int(float(s))
                                break
                            except Exception:
                                continue
                if inferred_ts is not None:
                    self._timeline.cache_last_touch_at = inferred_ts
            except Exception:
                pass
        try:
            session = getattr(self._runtime_ctx, "session", None)
            runtime_ttl = getattr(session, "cache_ttl_seconds", None) if session is not None else None
            if runtime_ttl is None:
                runtime_ttl = getattr(self._runtime_ctx, "cache_ttl_seconds", None)
            info = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "blocks": len(self._timeline.blocks or []),
                "sources_pool": len(self._timeline.sources_pool or []),
                "turn_ids": len(extract_turn_ids_from_blocks(self._timeline.blocks or [])),
                "conversation_title": self._timeline.conversation_title,
                "conversation_started_at": self._timeline.conversation_started_at,
                "timeline_ts": self._timeline.ts,
                "cache_last_touch_at": self._timeline.cache_last_touch_at,
                "cache_last_ttl_seconds": self._timeline.cache_last_ttl_seconds,
                "runtime_cache_ttl_seconds": runtime_ttl,
                "runtime_keep_recent_turns": getattr(session, "keep_recent_turns", None) if session is not None else None,
                "runtime_keep_recent_intact_turns": getattr(session, "keep_recent_intact_turns", None) if session is not None else None,
                "cache_ttl_bootstrap": getattr(self._timeline, "_cache_ttl_bootstrap", None),
            }
            self.log.log("[timeline.load] " + json.dumps(info, ensure_ascii=False, default=str))
        except Exception:
            pass

    def _allow_contribution(self, block_type: str) -> bool:
        if self._contrib_include_only:
            return block_type in self._contrib_include_only
        if self._contrib_exclude and block_type in self._contrib_exclude:
            return False
        return True

    def _filter_contribution_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not blocks:
            return []
        out: List[Dict[str, Any]] = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            btype = str(blk.get("type") or blk.get("kind") or "agent.block")
            if not self._allow_contribution(btype):
                continue
            out.append(blk)
        if self._timeline is None:
            return out
        return self._timeline.apply_hidden_replacements(out)

    @property
    def runtime_ctx(self) -> RuntimeCtx:
        return self._runtime_ctx

    @property
    def timeline(self) -> Timeline:
        if self._timeline is None:
            try:
                self._ensure_workspace()
            except Exception:
                pass
            self._timeline = Timeline(runtime=self._runtime_ctx, svc=self.svc)
        return self._timeline

    def filter_contributions(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._filter_contribution_blocks(blocks)

    def hide_paths(
        self,
        *,
        paths: List[str],
        replacement: str,
    ) -> Dict[str, Any]:
        """
        Mark blocks with matching path as hidden. One block will carry replacement text.
        Returns {"replaced": int, "tokens_hidden": int}.
        """
        if not self._timeline:
            return {"status": "not_found", "blocks_hidden": 0, "tokens_hidden": 0}
        res = self._timeline.hide_paths(paths, replacement)
        self._timeline.write_local()
        return res

    def unhide_paths(
        self,
        *,
        paths: List[str],
    ) -> None:
        if not self._timeline:
            return
        self._timeline.unhide_paths(paths)
        self._timeline.write_local()

    def _ensure_workspace(self) -> tuple[pathlib.Path, pathlib.Path]:
        """
        Ensure per-turn workspace directories exist and are bound to OUTDIR_CV/WORKDIR_CV.
        Returns (workdir, outdir).
        """
        workdir_raw = (self._runtime_ctx.workdir or "").strip()
        outdir_raw = (self._runtime_ctx.outdir or "").strip()
        workdir = pathlib.Path(workdir_raw) if workdir_raw else pathlib.Path("")
        outdir = pathlib.Path(outdir_raw) if outdir_raw else pathlib.Path("")
        if workdir_raw and outdir_raw:
            try:
                OUTDIR_CV.set(str(outdir))
                WORKDIR_CV.set(str(workdir))
            except Exception:
                pass
            os.environ["OUTPUT_DIR"] = str(outdir)
            os.environ["WORKDIR"] = str(workdir)
            return workdir, outdir

        root = get_exec_workspace_root()
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="ctx_v2_", dir=str(root)))
        workdir = tmp / "work"
        outdir = tmp / "out"
        workdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        self._runtime_ctx.workdir = str(workdir)
        self._runtime_ctx.outdir = str(outdir)

        try:
            OUTDIR_CV.set(str(outdir))
            WORKDIR_CV.set(str(workdir))
        except Exception:
            pass

        os.environ["OUTPUT_DIR"] = str(outdir)
        os.environ["WORKDIR"] = str(workdir)
        return workdir, outdir

    def _write_timeline_file(self, *, conversation_id: Optional[str], turn_id: Optional[str]) -> None:
        """
        Persist the current timeline snapshot (history/current/contrib + sources pool) to outdir/timeline.json.
        """
        if self._timeline:
            self._timeline.write_local()

    def _write_timeline_payload(self, payload: Dict[str, Any]) -> None:
        """
        Persist a raw timeline payload to outdir/timeline.json (used when restoring from S3).
        """
        outdir_raw = (self._runtime_ctx.outdir or "").strip()
        if not outdir_raw:
            return
        outdir = pathlib.Path(outdir_raw)
        out_path = outdir / "timeline.json"
        try:
            out_path.write_text(json.dumps(payload or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def get_turn_log(self, *, turn_id: str) -> Dict[str, Any]:
        if not turn_id:
            return {}
        if turn_id in self._turn_log_cache:
            return self._turn_log_cache[turn_id]
        try:
            mat = await self.ctx_client.materialize_turn(
                user_id=self._runtime_ctx.user_id,
                conversation_id=self._runtime_ctx.conversation_id,
                turn_id=turn_id,
                scope="conversation",
                days=365,
                with_payload=True,
                include_turn_log_payload=True,
            )
            turn_log_env = mat.get("turn_log") or {}
            payload = unwrap_payload(turn_log_env or {})
            self._turn_log_cache[turn_id] = payload or {}
            return payload or {}
        except Exception:
            return {}

    # ---------------------------------------------------------------------
    # Unified block builder + cache (per conversation + turn)
    # ---------------------------------------------------------------------


    def contribute(
        self,
        *,
        blocks: List[Dict[str, Any]],
    ) -> None:
        """
        Generic agent contribution hook.
        - Adds blocks to current-turn context (contribution stream).
        - Persisted via timeline when the turn ends.
        """
        try:
            if not blocks or not self._timeline:
                return
            tid = self._runtime_ctx.turn_id
            ts = self._runtime_ctx.started_at
            normalized: List[Dict[str, Any]] = []
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if blk.get("turn_id") and blk.get("author") and blk.get("ts"):
                    normalized.append(blk)
                    continue
                normalized.append(self._timeline.block(
                    type=str(blk.get("type") or blk.get("kind") or "agent.block"),
                    author=str(blk.get("author") or "agent"),
                    turn_id=str(blk.get("turn_id") or tid),
                    ts=str(blk.get("ts") or ts),
                    mime=str(blk.get("mime") or "text/markdown"),
                    text=blk.get("text"),
                    base64=blk.get("base64"),
                    path=blk.get("path"),
                    meta=blk.get("meta"),
                ))
            if normalized:
                self._timeline.contribute(normalized)
                self._timeline.write_local()
        except Exception:
            pass

    def contribute_feedback(
        self,
        *,
        reaction: Dict[str, Any],
    ) -> None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_feedback_stage_block
            self.contribute(
                blocks=[build_feedback_stage_block(runtime=self._runtime_ctx, reaction=reaction)],
            )
        except Exception:
            pass

    def contribute_clarification(
        self,
        *,
        clarification_questions: Optional[List[str]],
        ticket: Any = None,
    ) -> None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_clarification_stage_block
            self.contribute(
                blocks=[build_clarification_stage_block(
                    runtime=self._runtime_ctx,
                    ticket=ticket,
                    clarification_questions=clarification_questions,
                )],
            )
        except Exception:
            pass

    def contribute_clarification_resolution(
        self,
        *,
        ticket: Any = None,
        resolved_with_answer: bool | None = None,
    ) -> None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_clarification_resolution_block
            self.contribute(
                blocks=[build_clarification_resolution_block(
                    runtime_ctx=self._runtime_ctx,
                    ticket=ticket,
                    resolved_with_answer=resolved_with_answer,
                )],
            )
        except Exception:
            pass

    def contribute_suggested_followups(
            self,
            *,
            suggested_followups: Optional[List[str]],
    ) -> None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_suggested_followups_block
            self.contribute(
                blocks=[build_suggested_followups_block(
                    runtime=self._runtime_ctx,
                    suggested_followups=suggested_followups,
                )],
            )
        except Exception:
            pass

    def contribute_notice(
        self,
        *,
        code: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        call_id: Optional[str] = None,
    ) -> None:
        """
        Emit a react.notice block into the timeline (protocol violations, warnings).
        """
        try:
            if not self._timeline:
                return
            turn_id = self._runtime_ctx.turn_id
            payload = {"code": code, "message": message}
            if extra:
                payload.update(extra)

            block = {
                "type": "react.notice",
                "author": "react",
                "turn_id": turn_id,
                "ts": time.time(),
                "mime": "application/json",
                "path": f"tc:{turn_id}.tool_calls.{call_id}.notice.json" if (turn_id and call_id) else "",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
            if call_id:
                block["call_id"] = call_id
            self.contribute(blocks=[block])
        except Exception:
            pass

    def timeline_visible_paths(self) -> set[str]:
        if not self._timeline:
            return set()
        return self._timeline.visible_paths()

    def bind_params_with_refs(
        self,
        *,
        base_params: Dict[str, Any],
        tool_id: Optional[str] = None,
        visible_paths: Optional[set[str]] = None,
    ) -> tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
        """
        Resolve ref: bindings using timeline + outdir.
        """
        if not self._timeline:
            return dict(base_params or {}), [], []
        return self._timeline.bind_params_with_refs(
            base_params=base_params or {},
            tool_id=tool_id,
            visible_paths=visible_paths,
        )

    def timeline_artifacts(
        self,
        *,
        paths: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Resolve context paths into artifact payloads using the current timeline.
        """
        if not self._timeline:
            return []
        return self._timeline.materialize_show_artifacts(paths)

    def get_turn_blocks(
        self,
    ) -> List[Dict[str, Any]]:
        if not self._timeline:
            return []
        blocks = self._timeline.get_turn_blocks()
        return self._filter_contribution_blocks(blocks)


    def set_sources_pool(self, *, sources_pool: List[Dict[str, Any]]) -> None:
        if not self._timeline:
            return
        self._timeline.set_sources_pool(sources_pool)
        self._timeline.write_local()

    @property
    def sources_pool(self) -> List[Dict[str, Any]]:
        if not self._timeline:
            return []
        return list(self._timeline.sources_pool or [])

    def announce(self, *, blocks: Optional[List[Dict[str, Any]]] = None) -> None:
        if not self._timeline:
            return
        self._timeline.set_announce(blocks or [])
        self._timeline.write_local()

    def current_turn_blocks(self) -> List[Dict[str, Any]]:
        if not self._timeline:
            return []
        return self._timeline.get_turn_blocks()

    async def persist_timeline(self,) -> None:
        if not self._timeline:
            return
        await self._timeline.persist(self.ctx_client)

    async def search(
            self,
            *,
            targets: List[dict],
            user: str,
            conv: str,
            top_k: int = 5,
            days: int = 365,
            half_life_days: float = 7.0,
            scoring_mode: str = "hybrid",
            sim_weight: float = 0.8,
            rec_weight: float = 0.2,
            custom_score_fn: Optional[Any] = None,
            with_payload: bool = False,
            conv_idx: Optional[Any] = None,
            ctx_client: Optional[ContextRAGClient] = None,
            model_service: Optional[Any] = None,
    ) -> tuple[Optional[str], List[dict]]:
        """
        Convenience wrapper around ctx_rag.search_context.
        """
        ctx_client = ctx_client or self.ctx_client
        conv_idx = conv_idx or (getattr(ctx_client, "idx", None) if ctx_client else None)
        model_service = model_service or self.svc
        if not conv_idx or not model_service:
            raise ValueError("ContextBrowser.search requires conv_idx and model_service.")
        return await search_context(
            conv_idx=conv_idx,
            ctx_client=ctx_client,
            model_service=model_service,
            targets=targets,
            user=user,
            conv=conv,
            top_k=top_k,
            days=days,
            half_life_days=half_life_days,
            scoring_mode=scoring_mode,
            sim_weight=sim_weight,
            rec_weight=rec_weight,
            custom_score_fn=custom_score_fn,
            with_payload=with_payload,
            logger=self.log,
        )

    async def save_artifact(
            self,
            *,
            kind: str,
            tenant: str,
            project: str,
            user_id: str,
            conversation_id: str,
            user_type: str,
            turn_id: str,
            content: dict,
            content_str: Optional[str] = None,
            meta: Optional[Dict[str, Any]] = None,
            extra_tags: Optional[List[str]] = None,
            bundle_id: Optional[str] = None,
            index_only: bool = False,
            store_only: bool = False,
            embedding: Optional[List[float]] = None,
            ttl_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to ContextRAGClient.save_artifact(...).
        """
        if not self.ctx_client:
            raise ValueError("ContextBrowser.save_artifact requires ctx_client.")
        return await self.ctx_client.save_artifact(
            kind=kind,
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            user_type=user_type,
            turn_id=turn_id,
            content=content,
            content_str=content_str,
            meta=meta,
            extra_tags=extra_tags,
            bundle_id=bundle_id,
            index_only=index_only,
            store_only=store_only,
            embedding=embedding,
            ttl_days=ttl_days,
        )

def payload_unwrap(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap ctx store payloads where payload.payload holds the actual turn log."""
    if not isinstance(rec, dict):
        return {}
    pay = rec.get("payload") or {}
    if isinstance(pay, dict) and isinstance(pay.get("payload"), dict):
        return pay["payload"]
    return pay if isinstance(pay, dict) else {}
