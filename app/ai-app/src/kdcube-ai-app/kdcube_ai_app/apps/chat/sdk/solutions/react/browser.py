# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/browser.py

from __future__ import annotations

import asyncio
import logging
import json
import pathlib
import traceback
import uuid
import base64

import time
from datetime import datetime, timezone

from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.ids import new_exec_id
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import search_context, ContextRAGClient, unwrap_payload
from kdcube_ai_app.apps.chat.sdk.solutions.infra import get_exec_workspace_root
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
    TIMELINE_KIND,
    SOURCES_POOL_KIND,
    parse_timeline_payload,
    Timeline,
    extract_turn_ids_from_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import get_workspace_implementation
from kdcube_ai_app.tools.content_type import is_text_mime_type
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import (
    acquire_live_external_event_owner,
    event_source_id_for_external_kind,
    event_source_pipeline_enabled,
    REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
    release_live_external_event_owner,
    run_live_external_event_listener_loop,
    stamp_event_identity_many,
)

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
        self._external_event_hooks: List[Any] = []
        self._external_event_task: Optional[asyncio.Task] = None
        self._external_event_stop: Optional[asyncio.Event] = None
        self._external_listener_id: str = ""
        self._external_listener_turn_id: str = ""
        self._external_lease_token: str = ""
        self._external_event_listener_requested: bool = False
        self._external_apply_lock = asyncio.Lock()
        self._last_external_event_reader_result: Dict[str, Any] = {}

    @property
    def external_event_source(self):
        return getattr(self._runtime_ctx, "external_event_source", None)

    def add_external_event_hook(self, callback: Any, *, start_listener: bool = True) -> None:
        if not callback:
            return
        self._external_event_hooks.append(callback)
        if start_listener:
            self._external_event_listener_requested = True
        if (
            start_listener
            and self._timeline is not None
            and (self._external_event_task is None or self._external_event_task.done())
        ):
            try:
                asyncio.get_running_loop().create_task(self.start_external_event_listener())
            except Exception:
                self.log.log("[timeline.external] failed to schedule owner listener start\n" + traceback.format_exc(), "ERROR")

    def last_external_event_reader_result(self) -> Dict[str, Any]:
        return dict(self._last_external_event_reader_result or {})

    async def ensure_external_event_listener(self) -> None:
        if self._timeline is None or not self._external_event_hooks or not self._external_event_listener_requested:
            return
        task = self._external_event_task
        if task is not None and not task.done():
            return
        await self.start_external_event_listener()

    async def start_external_event_listener(self) -> None:
        source = self.external_event_source
        if source is None or self._timeline is None:
            self.log.log(
                f"[timeline.external] listener start skipped: source_present={bool(source is not None)} "
                f"timeline_present={bool(self._timeline is not None)}",
                "WARNING",
            )
            return
        turn_id = str(self._runtime_ctx.turn_id or "").strip()
        conversation_id = str(self._runtime_ctx.conversation_id or "").strip()
        if not turn_id or not conversation_id:
            self.log.log(
                f"[timeline.external] listener start skipped: missing identifiers conversation={conversation_id!r} turn_id={turn_id!r}",
                "WARNING",
            )
            return
        if self._external_event_task and not self._external_event_task.done():
            if self._external_listener_turn_id == turn_id:
                return
            await self.stop_external_event_listener()
        self._external_listener_id = self._external_listener_id or f"listener_{uuid.uuid4().hex[:8]}"
        self._external_listener_turn_id = turn_id
        self._external_event_stop = asyncio.Event()
        lease = await acquire_live_external_event_owner(
            source=source,
            runtime_ctx=self._runtime_ctx,
            listener_id=self._external_listener_id,
            log=self.log,
        )
        if lease is None:
            return
        self._external_lease_token = lease.lease_token
        self.log.log(
            f"[timeline.external]: owner lease acquired conversation={conversation_id} turn_id={turn_id} "
            f"listener_id={self._external_listener_id} lease_epoch={lease.lease_epoch}",
            "INFO",
        )
        self._external_event_task = asyncio.create_task(
            run_live_external_event_listener_loop(
                source_getter=lambda: self.external_event_source,
                runtime_ctx=self._runtime_ctx,
                stop_event=self._external_event_stop,
                listener_id=self._external_listener_id,
                lease_token_getter=lambda: self._external_lease_token,
                last_cursor_getter=lambda: (
                    str(self._timeline.last_external_event_id or "") if self._timeline is not None else ""
                ),
                apply_events=lambda events: self.apply_external_events(list(events or []), call_hooks=True),
                log=self.log,
            ),
            name=f"react-timeline-events:{conversation_id}:{turn_id}",
        )

    async def stop_external_event_listener(self) -> None:
        task = self._external_event_task
        stop_evt = self._external_event_stop
        self._external_event_task = None
        self._external_event_stop = None
        self._external_listener_turn_id = ""
        lease_token = self._external_lease_token
        self._external_lease_token = ""
        if stop_evt is not None:
            stop_evt.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        source = self.external_event_source
        await release_live_external_event_owner(
            source=source,
            listener_id=self._external_listener_id,
            lease_token=lease_token,
        )

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
        await self._ensure_workspace()
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
        deferred_current_turn_blocks: List[Dict[str, Any]] = []
        deferred_current_turn_hooks: List[Dict[str, Any]] = []
        try:
            (
                deferred_current_turn_blocks,
                deferred_current_turn_hooks,
            ) = await self._fold_external_events_initial()
        except Exception:
            self.log.log(f"[timeline.load]: external event fold failure {traceback.format_exc()}", "ERROR")
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
        try:
            if deferred_current_turn_blocks:
                await self._timeline.contribute_async(deferred_current_turn_blocks)
                self._timeline.write_local()
                for hook in deferred_current_turn_hooks:
                    await self._emit_external_event_hooks(
                        type=str(hook.get("type") or "external"),
                        event=hook.get("event"),
                        blocks=list(hook.get("blocks") or []),
                    )
        except Exception:
            self.log.log(f"[timeline.load]: deferred current-turn external event fold failure {traceback.format_exc()}", "ERROR")
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
        try:
            if self._timeline:
                await self._timeline.refresh_feedbacks(ctx_client=self.ctx_client, days=days)
        except Exception:
            self.log.log(f"[timeline.load]: refresh feedbacks failure {traceback.format_exc()}", "ERROR")
        if self._external_event_hooks and self._external_event_listener_requested:
            try:
                await self.start_external_event_listener()
            except Exception:
                self.log.log(f"[timeline.load]: external event listener start failure {traceback.format_exc()}", "ERROR")

    async def drain_external_events(self, *, call_hooks: bool) -> int:
        if self._timeline is None:
            return 0
        changed = await self._fold_external_events(call_hooks=call_hooks)
        return int(changed or 0)

    async def wait_and_drain_external_events(
        self,
        *,
        call_hooks: bool,
        block_ms: int = 750,
        limit: int = 100,
    ) -> int:
        source = self.external_event_source
        if source is None or self._timeline is None:
            return 0
        last_cursor = ""
        try:
            last_cursor = str(self._timeline.last_external_event_id or "")
        except Exception:
            last_cursor = ""
        events = await source.wait_for_events_after(last_cursor, block_ms=max(1, int(block_ms or 1)), limit=max(1, int(limit or 1)))
        return int(await self.apply_external_events(events, call_hooks=call_hooks) or 0)

    async def _fold_external_events_initial(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        source = self.external_event_source
        self._last_external_event_reader_result = {
            "source": "initial_fold",
            "events_read": 0,
            "events_materialized": 0,
            "blocks_materialized": 0,
            "current_turn_user_input_materialized": False,
            "current_turn_prompt_text": "",
            "max_sequence": int(getattr(self._timeline, "last_external_event_seq", 0) or 0) if self._timeline is not None else 0,
            "max_stream_id": str(getattr(self._timeline, "last_external_event_id", "") or "") if self._timeline is not None else "",
        }
        if source is None or self._timeline is None:
            return [], []
        last_cursor = self._timeline.last_external_event_id or int(self._timeline.last_external_event_seq or 0)
        events = await source.read_since(last_cursor)
        self._last_external_event_reader_result["events_read"] = len(events or [])
        if not events:
            return [], []
        current_turn_id = str(self._runtime_ctx.turn_id or "").strip()
        deferred_current_turn_blocks: List[Dict[str, Any]] = []
        deferred_current_turn_hooks: List[Dict[str, Any]] = []
        max_seq = int(self._timeline.last_external_event_seq or 0)
        max_cursor = str(self._timeline.last_external_event_id or "")
        max_applied_seq = 0
        current_prompt_texts: List[str] = []
        for event in events:
            blocks = await self._blocks_from_external_event(event)
            max_seq = max(max_seq, int(getattr(event, "sequence", 0) or 0))
            if getattr(event, "stream_id", None):
                max_cursor = str(getattr(event, "stream_id", "") or max_cursor)
            if not blocks:
                continue
            self._last_external_event_reader_result["events_materialized"] = (
                int(self._last_external_event_reader_result.get("events_materialized") or 0) + 1
            )
            self._last_external_event_reader_result["blocks_materialized"] = (
                int(self._last_external_event_reader_result.get("blocks_materialized") or 0) + len(blocks)
            )
            max_applied_seq = max(max_applied_seq, int(getattr(event, "sequence", 0) or 0))
            event_turn_id = (
                str(getattr(event, "owner_turn_id", "") or "").strip()
                or str(getattr(event, "active_turn_id_at_ingress", "") or "").strip()
            )
            block_turn_ids = {
                str(block.get("turn_id") or "").strip()
                for block in blocks
                if isinstance(block, dict) and str(block.get("turn_id") or "").strip()
            }
            is_current_turn_event = bool(current_turn_id and (event_turn_id == current_turn_id or current_turn_id in block_turn_ids))
            if is_current_turn_event:
                deferred_current_turn_blocks.extend(blocks)
                deferred_current_turn_hooks.append({
                    "type": str(getattr(event, "kind", "") or "external"),
                    "event": event,
                    "blocks": list(blocks),
                })
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type == "user.prompt":
                        text = str(block.get("text") or "").strip()
                        if text:
                            current_prompt_texts.append(text)
                    if block_type == "user.prompt" or block_type.startswith("user.attachment."):
                        self._last_external_event_reader_result["current_turn_user_input_materialized"] = True
            else:
                await self._timeline.contribute_async(blocks)
                await self._emit_external_event_hooks(type=str(getattr(event, "kind", "") or "external"), event=event, blocks=blocks)
        self._timeline.last_external_event_id = max_cursor
        self._timeline.last_external_event_seq = max_seq
        self._last_external_event_reader_result["max_sequence"] = max_seq
        self._last_external_event_reader_result["max_stream_id"] = max_cursor
        if current_prompt_texts:
            self._last_external_event_reader_result["current_turn_prompt_text"] = "\n\n".join(current_prompt_texts)
        if max_applied_seq and source is not None:
            try:
                await source.mark_consumed_up_to(
                    max_sequence=max_applied_seq,
                    turn_id=str(self._runtime_ctx.turn_id or ""),
                )
            except Exception:
                self.log.log(f"[timeline.external]: failed to mark initial fold consumed {traceback.format_exc()}", "ERROR")
        if self._timeline.blocks:
            self._timeline.write_local()
        return deferred_current_turn_blocks, deferred_current_turn_hooks

    async def _fold_external_events(self, *, call_hooks: bool) -> int:
        source = self.external_event_source
        if source is None or self._timeline is None:
            return 0
        last_cursor = self._timeline.last_external_event_id or int(self._timeline.last_external_event_seq or 0)
        events = await source.read_since(last_cursor)
        return await self.apply_external_events(events, call_hooks=call_hooks)

    @staticmethod
    def _parse_stream_id(stream_id: str) -> Optional[tuple[int, int]]:
        sid = str(stream_id or "").strip()
        if not sid or "-" not in sid:
            return None
        try:
            left, right = sid.split("-", 1)
            return int(left), int(right)
        except Exception:
            return None

    def _external_event_already_applied(self, event: Any) -> bool:
        if self._timeline is None:
            return False
        event_stream_id = str(getattr(event, "stream_id", "") or "").strip()
        last_stream_id = str(getattr(self._timeline, "last_external_event_id", "") or "").strip()
        if event_stream_id and last_stream_id:
            lhs = self._parse_stream_id(event_stream_id)
            rhs = self._parse_stream_id(last_stream_id)
            if lhs is not None and rhs is not None and lhs <= rhs:
                return True
            if event_stream_id == last_stream_id:
                return True
        if not event_stream_id:
            try:
                seq = int(getattr(event, "sequence", 0) or 0)
                if seq and seq <= int(getattr(self._timeline, "last_external_event_seq", 0) or 0):
                    return True
            except Exception:
                return False
        return False

    async def apply_external_events(self, events: List[Any], *, call_hooks: bool) -> int:
        if self._timeline is None:
            return 0
        async with self._external_apply_lock:
            changed = await self._apply_external_events(events, call_hooks=call_hooks)
            if changed:
                self.log.log(
                    f"[timeline.external]: applied conversation={self._runtime_ctx.conversation_id} "
                    f"turn_id={self._runtime_ctx.turn_id} changed={changed} last_seq={self._timeline.last_external_event_seq} "
                    f"last_id={self._timeline.last_external_event_id}",
                    "INFO",
                )
                self._timeline.write_local()
            return int(changed or 0)

    async def _apply_external_events(self, events: List[Any], *, call_hooks: bool) -> int:
        if self._timeline is None:
            return 0
        if not events:
            return 0
        from kdcube_ai_app.apps.chat.external_events import ConversationExternalEvent

        source = self.external_event_source
        added = 0
        max_applied_seq = 0
        for event in events:
            if not isinstance(event, ConversationExternalEvent):
                continue
            if self._external_event_already_applied(event):
                continue
            blocks = await self._blocks_from_external_event(event)
            stream_id = str(getattr(event, "stream_id", "") or "")
            if not blocks:
                if stream_id:
                    self._timeline.last_external_event_id = stream_id
                self._timeline.last_external_event_seq = max(
                    int(self._timeline.last_external_event_seq or 0),
                    int(event.sequence or 0),
                )
                continue
            await self._timeline.contribute_async(blocks)
            if stream_id:
                self._timeline.last_external_event_id = stream_id
            self._timeline.last_external_event_seq = max(
                int(self._timeline.last_external_event_seq or 0),
                int(event.sequence or 0),
            )
            max_applied_seq = max(max_applied_seq, int(event.sequence or 0))
            added += len(blocks)
            self.log.log(
                f"[timeline.external]: applied conversation={self._runtime_ctx.conversation_id} "
                f"current_turn={self._runtime_ctx.turn_id} event_id={getattr(event, 'message_id', '')} "
                f"kind={getattr(event, 'kind', '')} seq={getattr(event, 'sequence', 0)} "
                f"event_turn={getattr(event, 'owner_turn_id', None) or getattr(event, 'active_turn_id_at_ingress', None) or getattr(event, 'target_turn_id', None)} "
                f"blocks={len(blocks)} text={(str(getattr(event, 'text', '') or '')[:160])!r}",
                "INFO",
            )
            if call_hooks:
                await self._emit_external_event_hooks(type=str(event.kind or "external"), event=event, blocks=blocks)
        if max_applied_seq and source is not None:
            try:
                await source.mark_consumed_up_to(
                    max_sequence=max_applied_seq,
                    turn_id=str(self._runtime_ctx.turn_id or ""),
                )
            except Exception:
                self.log.log(f"[timeline.external]: failed to mark consumed {traceback.format_exc()}", "ERROR")
        return added

    async def _emit_external_event_hooks(self, *, type: str, event: Any, blocks: List[Dict[str, Any]]) -> None:
        hooks = list(self._external_event_hooks or [])
        for callback in hooks:
            try:
                result = callback(type=type, event=event, blocks=list(blocks))
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                self.log.log(f"[timeline.external]: hook failure {traceback.format_exc()}", "ERROR")

    async def _produce_external_event_blocks(
        self,
        *,
        event_source_id: str,
        target: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        event_sources = getattr(self._runtime_ctx, "event_sources", None)
        source = None
        if event_sources is not None:
            by_event_source_id = getattr(event_sources, "by_event_source_id", None)
            if callable(by_event_source_id):
                try:
                    source = by_event_source_id(event_source_id)
                except Exception:
                    source = None
        if source is not None:
            try:
                await event_sources.apply_react_phase_policies_async(
                    "block_production",
                    event_source_id,
                    target,
                    runtime_ctx=self._runtime_ctx,
                    ctx_browser=self,
                    timeline=self._timeline,
                )
            except Exception:
                self.log.log(
                    f"[timeline.external]: event block-production policy failure source={event_source_id!r}\n{traceback.format_exc()}",
                    "ERROR",
                )
        if not target.get("blocks") and not target.get("blocks_produced"):
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
                    canvas_event_default_block_production_policy,
                    external_event_default_block_production_policy,
                    snapshot_event_default_block_production_policy,
                    user_followup_default_block_production_policy,
                    user_attachment_default_block_production_policy,
                    user_prompt_default_block_production_policy,
                    user_steer_default_block_production_policy,
                )
                block_type = str(target.get("block_type") or "").strip()
                if block_type == "event.snapshot":
                    snapshot_event_default_block_production_policy(target)
                elif block_type == "event.canvas":
                    canvas_event_default_block_production_policy(target)
                elif block_type == "event.user.prompt":
                    user_prompt_default_block_production_policy(target)
                elif block_type == "event.user.followup":
                    user_followup_default_block_production_policy(target)
                elif block_type == "event.user.steer":
                    user_steer_default_block_production_policy(target)
                elif block_type.startswith("event.user.attachment"):
                    user_attachment_default_block_production_policy(target)
                else:
                    external_event_default_block_production_policy(target)
            except Exception:
                self.log.log(
                    f"[timeline.external]: default event block-production failure source={event_source_id!r}\n{traceback.format_exc()}",
                    "ERROR",
                )
        return [block for block in (target.get("blocks") or []) if isinstance(block, dict)]

    async def _blocks_from_external_event(self, event: Any) -> List[Dict[str, Any]]:
        if self._timeline is None:
            return []
        kind = str(getattr(event, "kind", "") or "").strip().lower()
        if kind not in {"message", "regular", "followup", "steer", "external_event"}:
            return []
        turn_id = (
            str(getattr(event, "owner_turn_id", "") or "").strip()
            or str(getattr(event, "active_turn_id_at_ingress", "") or "").strip()
            or str(getattr(event, "target_turn_id", "") or "").strip()
            or str(self._runtime_ctx.turn_id or "").strip()
        )
        event_id = str(getattr(event, "message_id", "") or "").strip() or f"seq_{int(getattr(event, 'sequence', 0) or 0)}"
        is_prompt_event = kind in {"message", "regular"}
        if is_prompt_event:
            path = f"ar:{turn_id}.user.prompt.{event_id}" if turn_id else ""
        else:
            path = f"ar:{turn_id}.external.{kind}.{event_id}" if turn_id else ""
        payload = getattr(event, "payload", None) or {}
        payload = payload if isinstance(payload, dict) else {}
        accepted_event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        event_source_id = (
            str(accepted_event.get("event_source_id") or "").strip()
            or event_source_id_for_external_kind(kind)
        )
        block_type = str(accepted_event.get("type") or "").strip()
        if not block_type:
            if kind in {"message", "regular"}:
                block_type = "event.user.prompt"
            elif kind == "followup":
                block_type = "event.user.followup"
            elif kind == "steer":
                block_type = "event.user.steer"
            else:
                block_type = "event.external"
        logical_path = str(accepted_event.get("logical_path") or "").strip()
        hosted_uri = str(accepted_event.get("hosted_uri") or "").strip()
        meta = {
            "event_kind": kind,
            "event_type": block_type,
            "event_source_id": event_source_id,
            "event_id": event_id,
            "is_continuation": bool(getattr(event, "is_continuation", False)),
            "message_id": str(getattr(event, "message_id", "") or ""),
            "stream_id": str(getattr(event, "stream_id", "") or ""),
            "sequence": int(getattr(event, "sequence", 0) or 0),
            "target_turn_id": getattr(event, "target_turn_id", None),
            "active_turn_id_at_ingress": getattr(event, "active_turn_id_at_ingress", None),
            "owner_turn_id": getattr(event, "owner_turn_id", None),
            "explicit": bool(getattr(event, "explicit", False)),
            "source": str(getattr(event, "source", "") or ""),
        }
        if payload:
            meta["payload"] = dict(payload)
        if accepted_event:
            meta["event"] = dict(accepted_event)
        if logical_path:
            meta["logical_path"] = logical_path
        if hosted_uri:
            meta["hosted_uri"] = hosted_uri
        if target:
            meta["target"] = dict(target)
        story_id = str(accepted_event.get("story_id") or target.get("story_id") or "").strip()
        if story_id:
            meta["story_id"] = story_id
        reactive = bool(accepted_event.get("reactive")) if "reactive" in accepted_event else False
        if kind == "external_event":
            meta["reactive"] = reactive
        attachments = self._attachments_from_external_event(event) if kind in {"message", "regular", "followup", "external_event"} else []
        attachment_kind = "user" if is_prompt_event else f"external.{kind}"
        path_root = (
            f"fi:{turn_id}.user.attachments/{event_id}"
            if is_prompt_event
            else f"fi:{turn_id}.external.{kind}.attachments/{event_id}"
        )
        physical_root = (
            f"{turn_id}/attachments/{event_id}"
            if is_prompt_event
            else f"{turn_id}/external/{kind}/attachments/{event_id}"
        )
        if attachments:
            attachments = await self._materialize_external_event_attachments(
                attachments,
                physical_root=physical_root,
            )
            attachments = await self._hydrate_external_event_attachments(attachments)
        if attachments:
            meta["attachments_count"] = len(attachments)
        event_ts = str(accepted_event.get("timestamp") or accepted_event.get("ts") or "").strip()
        if not event_ts:
            event_ts = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(float(getattr(event, "created_at", 0.0) or time.time())),
            )
        if event_ts:
            meta["timestamp"] = event_ts
        text = str(getattr(event, "text", "") or "").strip()
        if not text and accepted_event:
            accepted_payload = accepted_event.get("payload") if isinstance(accepted_event.get("payload"), dict) else {}
            body = accepted_payload.get("event")
            if isinstance(body, dict):
                text = str(body.get("text") or body.get("message") or body.get("request") or "").strip()
            elif body is not None:
                text = str(body or "").strip()
        builtin_user_prompt = block_type == "event.user.prompt"
        builtin_user_followup = block_type == "event.user.followup"
        builtin_user_steer = block_type == "event.user.steer"
        if is_prompt_event or builtin_user_prompt:
            meta["prompt_origin"] = "external_event_lane"
        target = {
            "event": dict(accepted_event or {}),
            "event_source_id": event_source_id,
            "event_id": event_id,
            "block_type": block_type,
            "logical_path": logical_path,
            "hosted_uri": hosted_uri,
            "story_id": story_id,
            "reactive": reactive,
            "text": text,
            "path": logical_path if kind == "external_event" and logical_path else path,
            "turn_id": turn_id,
            "ts": event_ts,
            "mime": "text/markdown",
            "author": "user",
            "meta": meta,
            "blocks": [],
            "block_factory": self._timeline.block,
        }
        blocks = await self._produce_external_event_blocks(
            event_source_id=event_source_id,
            target=target,
        )
        if attachments:
            attachment_target = {
                "event": dict(accepted_event or {}),
                "event_source_id": REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
                "event_id": event_id,
                "block_type": "event.user.attachment",
                "logical_path": path_root,
                "story_id": story_id,
                "reactive": False,
                "turn_id": turn_id,
                "ts": event_ts,
                "attachments": attachments,
                "path_root": path_root,
                "physical_root": physical_root,
                "meta_extra": {
                    "event_kind": kind,
                    "event_type": "event.user.attachment",
                    "event_source_id": event_source_id,
                    "message_id": event_id,
                    "sequence": int(getattr(event, "sequence", 0) or 0),
                    "is_continuation": bool(getattr(event, "is_continuation", False)),
                    "attachment_origin": attachment_kind,
                },
                "blocks": [],
                "block_factory": self._timeline.block,
            }
            blocks.extend(await self._produce_external_event_blocks(
                event_source_id=REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
                target=attachment_target,
            ))
        if event_source_pipeline_enabled(self._runtime_ctx):
            stamp_event_identity_many(
                blocks,
                event_source_id=event_source_id,
                event_id=event_id,
                story_id=story_id or None,
            )
        return blocks

    async def _materialize_external_event_attachments(
        self,
        attachments: List[Dict[str, Any]],
        *,
        physical_root: str,
    ) -> List[Dict[str, Any]]:
        """
        Copy authored external-event artifact rows into the ReAct conversation
        artifact surface before blocks are written to timeline.

        Bundle widgets may stage bytes in bundle storage first and send event
        data with `storage_uri`/`hosted_uri`. ReAct materialization turns that
        source object into a normal conversation-hosted artifact so later
        `react.read`, `react.pull`, and cross-conversation `fi:conv_...` paths
        resolve through the existing artifact pipeline.
        """

        if not attachments:
            return []
        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
        except Exception:
            return [dict(item) for item in attachments if isinstance(item, dict)]

        runtime = self._runtime_ctx
        conversation_id = str(getattr(runtime, "conversation_id", None) or "").strip()
        turn_id = str(getattr(runtime, "turn_id", None) or "").strip()
        user_id = str(getattr(runtime, "user_id", None) or "").strip()
        tenant = str(getattr(runtime, "tenant", None) or "").strip()
        project = str(getattr(runtime, "project", None) or "").strip()
        if not tenant or not project or not conversation_id or not turn_id:
            return [dict(item) for item in attachments if isinstance(item, dict)]

        try:
            store = ConversationStore(get_settings().STORAGE_PATH)
        except Exception:
            return [dict(item) for item in attachments if isinstance(item, dict)]
        materialized: List[Dict[str, Any]] = []
        for raw in attachments:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            source_uri = str(item.get("source_hosted_uri") or item.get("source_storage_uri") or item.get("storage_uri") or item.get("hosted_uri") or "").strip()
            name = str(item.get("filename") or item.get("name") or "").strip() or "attachment.bin"
            safe_name = pathlib.PurePosixPath(name).name or "attachment.bin"
            target_relpath = f"{physical_root.rstrip('/')}/{safe_name}"
            if item.get("key") and item.get("hosted_uri") and str(item.get("physical_path") or "").strip() == target_relpath:
                materialized.append(item)
                continue
            source_data: bytes | None = None
            if source_uri:
                try:
                    source_data = await store.get_blob_bytes(source_uri)
                except Exception:
                    source_data = None
            if source_data is None:
                materialized.append(item)
                continue
            try:
                uri, key, rn = await store.put_artifact_file(
                    tenant=tenant,
                    project=project,
                    user=user_id or None,
                    fingerprint=None,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    relpath=target_relpath,
                    data=source_data,
                    mime=str(item.get("mime") or "application/octet-stream"),
                    role=str(item.get("role") or "external-event-artifact"),
                )
                if source_uri:
                    item.setdefault("source_hosted_uri", source_uri)
                item["hosted_uri"] = uri
                item["key"] = key
                item["rn"] = rn
                item["physical_path"] = target_relpath
                item["materialized"] = True
            except Exception:
                self.log.log(
                    f"[timeline.external] attachment materialization failed source={source_uri!r}\n{traceback.format_exc()}",
                    "WARNING",
                )
            materialized.append(item)
        return materialized

    async def _hydrate_external_event_attachments(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        store = getattr(getattr(self.ctx_client, "store", None), "get_blob_bytes", None)
        if not callable(store):
            return [dict(item) for item in (attachments or []) if isinstance(item, dict)]

        hydrated: List[Dict[str, Any]] = []
        for raw in attachments or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get("text") or item.get("base64"):
                hydrated.append(item)
                continue
            hosted_uri = str(item.get("hosted_uri") or "").strip()
            mime = str(item.get("mime") or "").strip().lower()
            if not hosted_uri:
                hydrated.append(item)
                continue
            try:
                data = await store(hosted_uri)
                if is_text_mime_type(mime):
                    item["text"] = data.decode("utf-8", errors="replace")
                elif mime == "application/pdf" or mime.startswith("image/"):
                    item["base64"] = base64.b64encode(data).decode("utf-8")
            except Exception:
                self.log.log(
                    f"[timeline.external] attachment hydration failed uri={hosted_uri!r}\n{traceback.format_exc()}",
                    "WARNING",
                )
            hydrated.append(item)
        return hydrated

    def _attachments_from_external_event(self, event: Any) -> List[Dict[str, Any]]:
        task_payload = getattr(event, "task_payload", None)
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            accepted = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            event_type = str(accepted.get("type") or "").strip()
            if event_type.startswith("event.user.attachment"):
                accepted_payload = accepted.get("payload") if isinstance(accepted.get("payload"), dict) else {}
                body = accepted_payload.get("event") if isinstance(accepted_payload.get("event"), dict) else {}
                if body:
                    return [dict(body)]
        if isinstance(task_payload, dict):
            request = task_payload.get("request") if isinstance(task_payload.get("request"), dict) else {}
            external_events = request.get("external_events") if isinstance(request.get("external_events"), list) else []
            attachments = []
            for accepted in external_events:
                if not isinstance(accepted, dict):
                    continue
                if not str(accepted.get("type") or "").startswith("event.user.attachment"):
                    continue
                accepted_payload = accepted.get("payload") if isinstance(accepted.get("payload"), dict) else {}
                body = accepted_payload.get("event") if isinstance(accepted_payload.get("event"), dict) else {}
                if body:
                    attachments.append(dict(body))
            if attachments:
                return attachments
            payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
            legacy_attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
            attachments = [dict(item) for item in legacy_attachments if isinstance(item, dict)]
            if attachments:
                return attachments
        try:
            payload_model = event.task_payload_model()
            request = getattr(payload_model, "request", None)
            external_events = getattr(request, "external_events", None)
            if isinstance(external_events, list):
                attachments = []
                for accepted in external_events:
                    if not isinstance(accepted, dict):
                        continue
                    if not str(accepted.get("type") or "").startswith("event.user.attachment"):
                        continue
                    accepted_payload = accepted.get("payload") if isinstance(accepted.get("payload"), dict) else {}
                    body = accepted_payload.get("event") if isinstance(accepted_payload.get("event"), dict) else {}
                    if body:
                        attachments.append(dict(body))
                if attachments:
                    return attachments
        except Exception:
            return []
        return []

    @property
    def feedback_updates(self) -> List[Dict[str, Any]]:
        if not self._timeline:
            return []
        return self._timeline.feedback_updates()

    @property
    def feedback_updates_integrated(self) -> bool:
        if not self._timeline:
            return False
        return self._timeline.feedback_updates_integrated()

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

    async def _ensure_workspace(self) -> tuple[pathlib.Path, pathlib.Path]:
        """
        Ensure per-turn workspace directories exist and are bound to OUTDIR_CV/WORKDIR_CV.
        Returns (workdir, outdir).
        """
        async def _bootstrap_git_workspace_if_needed() -> None:
            if get_workspace_implementation(self._runtime_ctx) != "git":
                return
            from kdcube_ai_app.apps.chat.sdk.solutions.react.git_workspace import ensure_current_turn_git_workspace

            await ensure_current_turn_git_workspace(
                runtime_ctx=self._runtime_ctx,
                outdir=outdir,
                logger=self.log,
            )

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
            await _bootstrap_git_workspace_if_needed()
            return workdir, outdir

        try:
            root = get_exec_workspace_root()
        except Exception:
            # Let caller decide whether to abort the request; do not hide workspace errors.
            raise
        try:
            tmp: Optional[pathlib.Path] = None
            for _ in range(8):
                candidate = pathlib.Path(root) / new_exec_id()
                try:
                    candidate.mkdir(parents=True, exist_ok=False)
                    tmp = candidate
                    break
                except FileExistsError:
                    continue
            if tmp is None:
                raise FileExistsError(f"could not allocate unique exec workspace under {root}")
        except Exception as e:
            try:
                self.log.log(f"[workspace] Failed to create exec workspace under {root}: {type(e).__name__}: {e}", level="ERROR")
            except Exception:
                pass
            raise
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

        await _bootstrap_git_workspace_if_needed()
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

    async def get_turn_log(self, *, turn_id: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        if not turn_id:
            return {}
        effective_conversation_id = str(conversation_id or self._runtime_ctx.conversation_id or "").strip()
        cache_key = f"{effective_conversation_id}\n{turn_id}"
        if cache_key in self._turn_log_cache:
            return self._turn_log_cache[cache_key]
        try:
            mat = await self.ctx_client.materialize_turn(
                user_id=self._runtime_ctx.user_id,
                conversation_id=effective_conversation_id,
                turn_id=turn_id,
                scope="conversation",
                days=365,
                with_payload=True,
                include_turn_log_payload=True,
            )
            turn_log_env = mat.get("turn_log") or {}
            payload = unwrap_payload(turn_log_env or {})
            self._turn_log_cache[cache_key] = payload or {}
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_feedback_stage_block
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_clarification_stage_block
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_clarification_resolution_block
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_suggested_followups_block
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
        meta: Optional[Dict[str, Any]] = None,
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
                "path": f"tc:{turn_id}.{call_id}.notice" if (turn_id and call_id) else "",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
            if call_id:
                block["call_id"] = call_id
            if meta:
                block["meta"] = meta
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
            scope: str = "conversation",
            half_life_days: float = 7.0,
            scoring_mode: str = "hybrid",
            sim_weight: float = 0.8,
            rec_weight: float = 0.2,
            custom_score_fn: Optional[Any] = None,
            with_payload: bool = False,
            timestamp_filters: Optional[List[Dict[str, Any]]] = None,
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
            scope=scope,
            half_life_days=half_life_days,
            scoring_mode=scoring_mode,
            sim_weight=sim_weight,
            rec_weight=rec_weight,
            custom_score_fn=custom_score_fn,
            with_payload=with_payload,
            timestamp_filters=timestamp_filters,
            logger=self.log,
        )

    async def search_turn_catalog(
            self,
            *,
            user: str,
            conv: Optional[str],
            scope: str = "conversation",
            top_k: int = 20,
            days: int = 3650,
            order: str = "asc",
            ordinal: Optional[int] = None,
            from_ts: Optional[Any] = None,
            to_ts: Optional[Any] = None,
            conv_idx: Optional[Any] = None,
            ctx_client: Optional[ContextRAGClient] = None,
    ) -> List[Dict[str, Any]]:
        """
        Deterministic turn catalog lookup for temporal and ordinal memory queries.
        """
        ctx_client = ctx_client or self.ctx_client
        conv_idx = conv_idx or (getattr(ctx_client, "idx", None) if ctx_client else None)
        if not conv_idx:
            raise ValueError("ContextBrowser.search_turn_catalog requires conv_idx.")
        return await conv_idx.fetch_turn_catalog(
            user_id=user,
            conversation_id=conv,
            scope=scope,
            top_k=top_k,
            days=days,
            order=order,
            ordinal=ordinal,
            from_ts=from_ts,
            to_ts=to_ts,
            ctx={
                "user_id": user,
                "conversation_id": conv,
            },
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
