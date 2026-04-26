# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/runtime.py

import asyncio
import os
import json
import pathlib
import random
import traceback

import time
import uuid
from typing import Any, Dict, List, Optional, Callable, Awaitable, Type

from langgraph.graph import StateGraph, END

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.infra import emit_event
from kdcube_ai_app.apps.chat.sdk.runtime.execution import execute_tool, _safe_label
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import react_decision_stream_v2
from kdcube_ai_app.apps.chat.sdk.solutions.react.live_events import (
    compute_reactive_iteration_credit_cap,
    resolve_reactive_iteration_credit,
    sync_reactive_iteration_budget,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import ReactResult
from kdcube_ai_app.apps.chat.sdk.solutions.react.runtime_state import ReactRuntimeState as ReactStateV2
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.canvas import (
    ReactPatchContentStreamer,
    ReactWriteContentStreamer,
    RenderingWriteContentStreamer,
    TimelineStreamer,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
import logging

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import apply_plan_updates
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.round import ReactRound
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import ReactStateSnapshot


class ReactSolverV2:
    MODULE_AGENT_NAME = "solver.react.v2"

    @property
    def continuation_source(self):
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
        return getattr(runtime_ctx, "continuation_source", None) if runtime_ctx else None

    async def pending_continuation_count(self) -> int:
        source = self.continuation_source
        if source is None:
            return 0
        return int(await source.pending_count())

    async def peek_next_continuation(self):
        source = self.continuation_source
        if source is None:
            return None
        return await source.peek_next()

    async def take_next_continuation(self):
        source = self.continuation_source
        if source is None:
            return None
        return await source.take_next()

    @property
    def external_event_source(self):
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
        return getattr(runtime_ctx, "external_event_source", None) if runtime_ctx else None
    DECISION_AGENT_NAME = "decision.v2"

    def __init__(
        self,
        *,
        service: ModelServiceBase,
        logger: AgentLogger,
        tools_subsystem: ToolSubsystem,
        skills_subsystem: Any,

        scratchpad: TurnScratchpad,
        comm: ChatCommunicator,
        comm_context: ChatTaskPayload,
        hosting_service: Optional[ApplicationHostingService] = None,
        ctx_browser: Optional[ContextBrowser] = None,
        additional_instructions: Optional[str] = None,
    ) -> None:
        self.svc = service
        if isinstance(logger, AgentLogger):
            self.log = logger
        else:
            name = getattr(logger, "name", None) or "react.v2"
            level = "INFO"
            try:
                lvl = getattr(logger, "level", None)
                if isinstance(lvl, int):
                    level = logging.getLevelName(lvl)
            except Exception:
                level = "INFO"
            self.log = AgentLogger(str(name), str(level))
        self.tools_subsystem = tools_subsystem
        self.skills_subsystem = skills_subsystem
        self.scratchpad = scratchpad
        self.comm = comm
        self.comm_context = comm_context
        self.hosting_service = hosting_service
        self.ctx_browser = ctx_browser
        self.additional_instructions = str(additional_instructions or "").strip()
        if self.ctx_browser is not None:
            try:
                self.ctx_browser.add_timeline_event_hook(self.on_timeline_event)
            except Exception:
                self.log.log("[react.v2] failed to register timeline event hook\n" + traceback.format_exc(), level="ERROR")
        self.graph = self._build_graph()
        self._timeline_text_idx = {}
        self._outdir_cv_token = None
        self._workdir_cv_token = None
        self._latest_external_event_seq_seen = 0
        self._last_decision_visible_external_event_seq = 0
        self._last_consumed_external_event_seq = 0
        self._steer_interrupt_requested = False
        self._latest_steer_seq_seen = 0
        self._last_handled_steer_seq = 0
        self._latest_steer_text = ""
        self._active_phase_task: Optional[asyncio.Task] = None
        self._active_phase_name: str = ""
        self._active_phase_cancelled_by_steer: bool = False
        self._active_phase_cancel_requested_at: float = 0.0
        self._active_generation_iteration: Optional[int] = None
        self._active_generation_raw_chunks: List[str] = []
        self._interrupted_generation_snapshot: Optional[Dict[str, Any]] = None
        self._active_phase_event_watch_task: Optional[asyncio.Task] = None
        self._active_phase_external_cursor: str = ""
        self._reactive_iteration_credit_total: int = 0
        self._reactive_iteration_credit_cap: int = 0
        self._credited_external_event_ids: set[str] = set()

    def _current_turn_id(self) -> str:
        try:
            turn_id = str(getattr(self.scratchpad, "turn_id", "") or "").strip()
            if turn_id:
                return turn_id
        except Exception:
            pass
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            return str(getattr(runtime_ctx, "turn_id", "") or "").strip()
        except Exception:
            return ""

    def _event_targets_current_turn(self, event: Any) -> bool:
        current_turn_id = self._current_turn_id()
        if not current_turn_id:
            return True
        turn_ids = [
            str(getattr(event, "target_turn_id", "") or "").strip(),
            str(getattr(event, "active_turn_id_at_ingress", "") or "").strip(),
            str(getattr(event, "owner_turn_id", "") or "").strip(),
        ]
        turn_ids = [item for item in turn_ids if item]
        if not turn_ids:
            return True
        return current_turn_id in set(turn_ids)

    async def on_timeline_event(self, *, type: str, event: Any, blocks: List[Dict[str, Any]]) -> bool:
        type_norm = str(type or "").strip().lower()
        if type_norm in {"steer", "followup"} and not self._event_targets_current_turn(event):
            try:
                self.log.log(
                    f"[react.v2] timeline_event ignored: type={type_norm} current_turn={self._current_turn_id()} "
                    f"target_turn={getattr(event, 'target_turn_id', None) or ''} "
                    f"active_turn={getattr(event, 'active_turn_id_at_ingress', None) or ''} "
                    f"owner_turn={getattr(event, 'owner_turn_id', None) or ''}",
                    level="INFO",
                )
            except Exception:
                pass
            return False
        try:
            seq = int(getattr(event, "sequence", 0) or 0)
            if seq > self._latest_external_event_seq_seen:
                self._latest_external_event_seq_seen = seq
        except Exception:
            seq = None
        try:
            if type_norm == "steer":
                if seq is not None and int(seq or 0) > self._latest_steer_seq_seen:
                    self._latest_steer_seq_seen = int(seq or 0)
                self._latest_steer_text = str(getattr(event, "text", "") or "").strip()
                self._steer_interrupt_requested = True
                await self._interrupt_active_phase_for_steer()
        except Exception:
            pass
        try:
            event_id = getattr(event, "message_id", None) or ""
            credit_awarded = self._award_reactive_iteration_credit(type=type_norm, event=event)
            self.log.log(
                f"[react.v2] timeline_event accepted: type={type} event_id={event_id} seq={seq} "
                f"blocks={len(blocks or [])} credit_awarded={credit_awarded} "
                f"total_credit={self._reactive_iteration_credit_total}/{self._reactive_iteration_credit_cap}",
                level="INFO",
            )
        except Exception:
            pass
        return True

    def _award_reactive_iteration_credit(self, *, type: str, event: Any) -> int:
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
        if runtime_ctx is None:
            return 0
        event_id = str(getattr(event, "message_id", "") or "").strip()
        if event_id and event_id in self._credited_external_event_ids:
            return 0
        credit = resolve_reactive_iteration_credit(
            event_type=type,
            event=event,
            runtime_ctx=runtime_ctx,
        )
        if credit <= 0:
            return 0
        cap = max(0, int(self._reactive_iteration_credit_cap or 0))
        if cap <= 0:
            return 0
        remaining = max(0, cap - int(self._reactive_iteration_credit_total or 0))
        if remaining <= 0:
            return 0
        granted = min(remaining, int(credit or 0))
        if granted <= 0:
            return 0
        self._reactive_iteration_credit_total = int(self._reactive_iteration_credit_total or 0) + granted
        if event_id:
            self._credited_external_event_ids.add(event_id)
        return granted

    def _sync_reactive_iteration_budget(self, state: Dict[str, Any]) -> int:
        return sync_reactive_iteration_budget(
            state=state,
            granted_credit=int(self._reactive_iteration_credit_total or 0),
        )

    async def _interrupt_active_phase_for_steer(self) -> bool:
        task = self._active_phase_task
        if task is None or task.done():
            return False
        self._active_phase_cancelled_by_steer = True
        self._active_phase_cancel_requested_at = time.time()
        phase = str(self._active_phase_name or "").strip() or "phase"
        self.log.log(
            f"[react.v2] steer cancelling active {phase} task turn_id={self.scratchpad.turn_id}",
            level="INFO",
        )
        task.cancel()
        try:
            await self.comm.service_event(
                type="timeline.external.steer.cancel_requested",
                step=f"timeline.external.steer.{phase}",
                status="running",
                title="Steer requested live cancellation",
                agent=self.MODULE_AGENT_NAME,
                data={
                    "turn_id": str(self.scratchpad.turn_id or ""),
                    "phase": phase,
                    "sequence": int(self._latest_steer_seq_seen or 0),
                    "text": str(self._latest_steer_text or ""),
                },
            )
        except Exception:
            pass
        return True

    async def _log_external_event_watch_state(self, *, phase: str) -> None:
        ctx_browser = self.ctx_browser
        source = self.external_event_source
        current_turn_id = self._current_turn_id()
        timeline = getattr(ctx_browser, "timeline", None) if ctx_browser is not None else None
        listener_task = getattr(ctx_browser, "_external_event_task", None) if ctx_browser is not None else None
        listener_running = bool(listener_task is not None and not listener_task.done())
        owner_turn_id = ""
        owner_listener_id = ""
        try:
            owner = await source.get_owner() if source is not None else None
            owner_turn_id = str(getattr(owner, "turn_id", "") or "").strip()
            owner_listener_id = str(getattr(owner, "listener_id", "") or "").strip()
        except Exception:
            self.log.log(
                f"[react.v2] phase={phase} failed to inspect external-event owner:\n{traceback.format_exc()}",
                level="ERROR",
            )
            return
        level = "INFO"
        if not owner_turn_id or owner_turn_id != current_turn_id or not listener_running:
            level = "WARNING"
        self.log.log(
            f"[react.v2] phase={phase} external watch state turn_id={current_turn_id} "
            f"timeline_present={bool(timeline is not None)} listener_running={listener_running} "
            f"owner_turn={owner_turn_id or '-'} owner_listener={owner_listener_id or '-'}",
            level=level,
        )

    async def _watch_external_events_during_phase(self, *, phase: str, phase_task: asyncio.Task) -> None:
        ctx_browser = self.ctx_browser
        source = self.external_event_source
        if ctx_browser is None or source is None:
            return
        read_since = getattr(source, "read_since", None)
        poll_interval_s = 0.2
        try:
            ensure_listener = getattr(ctx_browser, "ensure_external_event_listener", None)
            if ensure_listener is not None:
                maybe = ensure_listener()
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception:
            pass
        try:
            await self._log_external_event_watch_state(phase=phase)
        except Exception:
            pass
        try:
            timeline = getattr(ctx_browser, "timeline", None)
            if timeline is not None:
                self._active_phase_external_cursor = str(getattr(timeline, "last_external_event_id", "") or "")
        except Exception:
            self._active_phase_external_cursor = ""
        while not phase_task.done():
            try:
                timeline = getattr(ctx_browser, "timeline", None)
                last_cursor = (
                    str(getattr(timeline, "last_external_event_id", "") or "")
                    if timeline is not None
                    else str(self._active_phase_external_cursor or "")
                )
                events: List[Any] = []
                if callable(read_since):
                    maybe = read_since(last_cursor, limit=100)
                    events = list(await maybe if asyncio.iscoroutine(maybe) else maybe or [])
                if phase_task.done():
                    return
                if not events:
                    await asyncio.sleep(poll_interval_s)
                    continue
                self.log.log(
                    f"[react.v2] phase={phase} direct external-event watch received count={len(events)} turn_id={self.scratchpad.turn_id}",
                    level="INFO",
                )
                apply_events = getattr(ctx_browser, "apply_external_events", None)
                changed = 0
                if apply_events is not None and timeline is not None:
                    maybe = apply_events(events, call_hooks=True)
                    changed = int(await maybe if asyncio.iscoroutine(maybe) else maybe or 0)
                else:
                    for event in events:
                        stream_id = str(getattr(event, "stream_id", "") or "")
                        if stream_id:
                            self._active_phase_external_cursor = stream_id
                        try:
                            handled = await self.on_timeline_event(
                                type=str(getattr(event, "kind", "external") or "external"),
                                event=event,
                                blocks=[],
                            )
                            changed += int(bool(handled))
                        except Exception:
                            self.log.log(
                                f"[react.v2] direct external-event dispatch failure phase={phase}: {traceback.format_exc()}",
                                level="ERROR",
                            )
                if changed and self._steer_interrupt_requested:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.log(
                    f"[react.v2] external-event phase watcher failure phase={phase}: {traceback.format_exc()}",
                    level="ERROR",
                )
                await asyncio.sleep(0.2)

    async def _run_cancellable_phase(self, *, phase: str, coro: Awaitable[Any]) -> tuple[bool, Any]:
        task = asyncio.create_task(coro, name=f"react.{phase}.{self.scratchpad.turn_id}")
        self._active_phase_task = task
        self._active_phase_name = str(phase or "")
        self._active_phase_cancelled_by_steer = False
        self._active_phase_cancel_requested_at = 0.0
        watch_task: Optional[asyncio.Task] = None
        try:
            if self.ctx_browser is not None and self.external_event_source is not None:
                watch_task = asyncio.create_task(
                    self._watch_external_events_during_phase(phase=phase, phase_task=task),
                    name=f"react.{phase}.external_watch.{self.scratchpad.turn_id}",
                )
                self._active_phase_event_watch_task = watch_task
        except Exception:
            watch_task = None
            self._active_phase_event_watch_task = None
        try:
            result = await task
            return False, result
        except asyncio.CancelledError:
            if self._active_phase_cancelled_by_steer or self._steer_interrupt_requested:
                self.log.log(
                    f"[react.v2] active {phase} cancelled by steer turn_id={self.scratchpad.turn_id}",
                    level="INFO",
                )
                return True, None
            raise
        finally:
            if watch_task is not None:
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            if self._active_phase_task is task:
                self._active_phase_task = None
                self._active_phase_name = ""
                self._active_phase_cancelled_by_steer = False
                self._active_phase_cancel_requested_at = 0.0
            if self._active_phase_event_watch_task is watch_task:
                self._active_phase_event_watch_task = None
            self._active_phase_external_cursor = ""
        try:
            await self.comm.service_event(
                type="timeline.external.accepted",
                step="timeline.external",
                status="completed",
                title="External Timeline Event Accepted",
                agent=self.MODULE_AGENT_NAME,
                data={
                    "event_kind": str(type or "external"),
                    "event_id": str(getattr(event, "message_id", "") or ""),
                    "sequence": int(getattr(event, "sequence", 0) or 0),
                    "turn_id": str(self.scratchpad.turn_id or ""),
                    "blocks": len(blocks or []),
                },
            )
        except Exception:
            pass

    async def _drain_external_events(self, *, call_hooks: bool) -> int:
        if not self.ctx_browser:
            return 0
        try:
            before_seq = int(getattr(self.ctx_browser.timeline, "last_external_event_seq", 0) or 0) if getattr(self.ctx_browser, "timeline", None) is not None else 0
            changed = await self.ctx_browser.drain_external_events(call_hooks=call_hooks)
            current_seq = before_seq
            try:
                current_seq = int(getattr(self.ctx_browser.timeline, "last_external_event_seq", 0) or 0)
                if current_seq > self._latest_external_event_seq_seen:
                    self._latest_external_event_seq_seen = current_seq
            except Exception:
                pass
            if changed:
                self.log.log(
                    f"[react.v2] drained external events turn_id={self.scratchpad.turn_id} "
                    f"changed={changed} before_seq={before_seq} after_seq={current_seq}",
                    level="INFO",
                )
            return int(changed or 0)
        except Exception:
            self.log.log(f"[react.v2] external event drain failed: {traceback.format_exc()}", level="ERROR")
            return 0

    async def _wait_and_drain_external_events(self, *, call_hooks: bool, block_ms: int) -> int:
        if not self.ctx_browser:
            return 0
        try:
            before_seq = int(getattr(self.ctx_browser.timeline, "last_external_event_seq", 0) or 0) if getattr(self.ctx_browser, "timeline", None) is not None else 0
            changed = await self.ctx_browser.wait_and_drain_external_events(
                call_hooks=call_hooks,
                block_ms=max(1, int(block_ms or 1)),
                limit=100,
            )
            current_seq = before_seq
            try:
                current_seq = int(getattr(self.ctx_browser.timeline, "last_external_event_seq", 0) or 0)
                if current_seq > self._latest_external_event_seq_seen:
                    self._latest_external_event_seq_seen = current_seq
            except Exception:
                pass
            if changed:
                self.log.log(
                    f"[react.v2] waited/drained external events turn_id={self.scratchpad.turn_id} "
                    f"changed={changed} before_seq={before_seq} after_seq={current_seq} block_ms={block_ms}",
                    level="INFO",
                )
            return int(changed or 0)
        except Exception:
            self.log.log(f"[react.v2] external event wait/drain failed: {traceback.format_exc()}", level="ERROR")
            return 0

    def _visible_external_event_seq(self) -> int:
        try:
            return int(getattr(getattr(self.ctx_browser, "timeline", None), "last_external_event_seq", 0) or 0)
        except Exception:
            return 0

    def _seed_steer_interrupt_from_current_turn(self) -> None:
        browser = self.ctx_browser
        if browser is None or not hasattr(browser, "current_turn_blocks"):
            return
        try:
            current_blocks = list(browser.current_turn_blocks() or [])
        except Exception:
            return
        max_seq = 0
        latest_text = ""
        for blk in current_blocks:
            if not isinstance(blk, dict):
                continue
            if str(blk.get("type") or "").strip().lower() != "user.steer":
                continue
            meta = blk.get("meta") or {}
            try:
                seq = int((meta or {}).get("sequence") or 0)
            except Exception:
                seq = 0
            max_seq = max(max_seq, seq)
            latest_text = str(blk.get("text") or latest_text or "").strip()
        if max_seq > 0 or latest_text:
            self._steer_interrupt_requested = True
            self._latest_steer_seq_seen = max(self._latest_steer_seq_seen, max_seq)
            if latest_text:
                self._latest_steer_text = latest_text

    async def _mark_external_events_consumed_up_to(self, *, max_sequence: int) -> int:
        if max_sequence <= int(self._last_consumed_external_event_seq or 0):
            return 0
        if self.external_event_source is None:
            return 0
        consumed = await self.external_event_source.mark_consumed_up_to(
            max_sequence=int(max_sequence or 0),
            turn_id=str(self.scratchpad.turn_id or ""),
        )
        if consumed:
            self._last_consumed_external_event_seq = max(
                int(self._last_consumed_external_event_seq or 0),
                int(max_sequence or 0),
            )
            self.log.log(
                f"[react.v2] marked external events consumed turn_id={self.scratchpad.turn_id} "
                f"count={consumed} up_to_seq={max_sequence}",
                level="INFO",
            )
        return int(consumed or 0)

    async def _apply_steer_interrupt_if_requested(self, state: Dict[str, Any], *, checkpoint: str) -> bool:
        exit_reason = str(state.get("exit_reason") or "").strip()
        if exit_reason and exit_reason not in {"complete", "exit", "steer"}:
            return False
        if not self._steer_interrupt_requested and int(self._latest_steer_seq_seen or 0) <= int(self._last_handled_steer_seq or 0):
            return False
        await self._enter_steer_finalize_mode(state, checkpoint=checkpoint, cancelled_phase=None)
        return True

    def _steer_finalize_round_limit(self) -> int:
        try:
            return max(1, int(os.getenv("REACT_STEER_FINALIZE_ROUNDS", "2") or "2"))
        except Exception:
            return 2

    def _steer_finalize_max_tokens(self) -> int:
        try:
            return max(512, int(os.getenv("REACT_STEER_FINALIZE_MAX_TOKENS", "3000") or "3000"))
        except Exception:
            return 3000

    def _build_default_steer_final_answer(self) -> str:
        return "Stopped at your request. I preserved the progress made so far."

    def _begin_active_generation_capture(self, *, iteration: int) -> None:
        self._active_generation_iteration = int(iteration or 0)
        self._active_generation_raw_chunks = []

    async def _capture_active_generation_raw(self, piece: str) -> None:
        if self._active_generation_iteration is None:
            return
        if not piece:
            return
        self._active_generation_raw_chunks.append(str(piece))

    def _clear_active_generation_capture(self) -> None:
        self._active_generation_iteration = None
        self._active_generation_raw_chunks = []

    def _stash_interrupted_generation_snapshot(self) -> None:
        raw_text = "".join(self._active_generation_raw_chunks or []).strip()
        if not raw_text:
            return
        self._interrupted_generation_snapshot = {
            "raw_text": raw_text,
            "iteration": int(getattr(self, "_active_generation_iteration", 0) or 0),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _take_interrupted_generation_snapshot(self) -> Optional[Dict[str, Any]]:
        snap = self._interrupted_generation_snapshot
        self._interrupted_generation_snapshot = None
        return snap if isinstance(snap, dict) else None

    def _persist_interrupted_generation(
        self,
        *,
        state: Dict[str, Any],
        checkpoint: str,
        cancelled_phase: Optional[str],
    ) -> None:
        if cancelled_phase != "decision":
            return
        if bool(state.get("interrupted_generation_persisted")):
            return
        snap = self._take_interrupted_generation_snapshot()
        if not snap or not self.ctx_browser or not getattr(self.ctx_browser, "timeline", None):
            return
        raw_text = str(snap.get("raw_text") or "").strip()
        if not raw_text:
            return
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_interrupted_generation_blocks

            blocks = build_interrupted_generation_blocks(
                runtime=self.ctx_browser.runtime_ctx,
                raw_text=raw_text,
                iteration=int(snap.get("iteration") or 0),
                interrupted_at=str(snap.get("captured_at") or ""),
                checkpoint=checkpoint,
                cancelled_phase=cancelled_phase,
                sequence=int(self._latest_steer_seq_seen or 0) if self._latest_steer_seq_seen is not None else None,
                block_factory=self.ctx_browser.timeline.block,
            )
            if blocks:
                self.ctx_browser.contribute(blocks=blocks)
                state["interrupted_generation_persisted"] = True
        except Exception:
            self.log.log(f"[react.v2] failed to persist interrupted generation: {traceback.format_exc()}", level="ERROR")

    async def _enter_steer_finalize_mode(
        self,
        state: Dict[str, Any],
        *,
        checkpoint: str,
        cancelled_phase: Optional[str],
    ) -> None:
        visible_seq = max(self._visible_external_event_seq(), int(self._latest_steer_seq_seen or 0))
        try:
            if visible_seq > int(self._last_consumed_external_event_seq or 0):
                await self._mark_external_events_consumed_up_to(max_sequence=visible_seq)
        except Exception:
            self.log.log(f"[react.v2] failed to mark steer event consumed: {traceback.format_exc()}", level="ERROR")
        self._last_handled_steer_seq = max(int(self._last_handled_steer_seq or 0), int(self._latest_steer_seq_seen or 0), int(visible_seq or 0))
        self._steer_interrupt_requested = False
        steer_text = str(self._latest_steer_text or "").strip()
        state["steer_finalize_mode"] = True
        state["steer_finalize_rounds_remaining"] = self._steer_finalize_round_limit()
        state["steer_finalize_seq"] = int(self._latest_steer_seq_seen or visible_seq or 0)
        state["retry_decision"] = True
        state["exit_reason"] = None
        state["final_answer"] = None
        state["suggested_followups"] = []
        state["last_decision"] = {
            "action": "exit",
            "final_answer": "",
            "notes": "steer_finalize_pending",
            "suggested_followups": [],
        }
        state["steer_interrupt"] = {
            "sequence": int(self._latest_steer_seq_seen or 0),
            "text": steer_text,
            "checkpoint": checkpoint,
            "cancelled_phase": cancelled_phase,
        }
        self.log.log(
            f"[react.v2] steer finalize mode: turn_id={self.scratchpad.turn_id} "
            f"checkpoint={checkpoint} cancelled_phase={cancelled_phase or ''} "
            f"seq={self._latest_steer_seq_seen} text={steer_text!r}",
            level="INFO",
        )
        self._persist_interrupted_generation(
            state=state,
            checkpoint=checkpoint,
            cancelled_phase=cancelled_phase,
        )
        try:
            await self.comm.service_event(
                type="timeline.external.steer.interrupted",
                step="timeline.external.steer",
                status="completed",
                title="Turn interrupted by steer",
                agent=self.MODULE_AGENT_NAME,
                data={
                    "turn_id": str(self.scratchpad.turn_id or ""),
                    "sequence": int(self._latest_steer_seq_seen or 0),
                    "checkpoint": checkpoint,
                    "text": steer_text,
                    "cancelled_phase": cancelled_phase,
                    "finalize_rounds": int(state.get("steer_finalize_rounds_remaining") or 0),
                },
            )
        except Exception:
            pass

    async def _render_timeline_with_announce(
        self,
        *,
        iteration: int,
        max_iterations: int,
        base_max_iterations: Optional[int] = None,
        reactive_iteration_credit: int = 0,
        force_sanitize: bool = False,
    ) -> List[Dict[str, Any]]:
        if not self.ctx_browser:
            return []
        await self._update_announce(
            iteration=iteration,
            max_iterations=max_iterations,
            base_max_iterations=base_max_iterations,
            reactive_iteration_credit=reactive_iteration_credit,
        )
        return await self.ctx_browser.timeline.render(
            cache_last=True,
            force_sanitize=force_sanitize,
            include_sources=True,
            include_announce=True,
        )

    async def _update_announce(
        self,
        *,
        iteration: int,
        max_iterations: int,
        base_max_iterations: Optional[int] = None,
        reactive_iteration_credit: int = 0,
    ) -> None:
        if not self.ctx_browser:
            return
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
            # NOTE: feedback refresh is performed at turn start (timeline load).
            # We intentionally skip per-announce refresh to avoid mid-turn fetches.
            # try:
            #     if self.ctx_browser and self.ctx_browser.timeline:
            #         await self.ctx_browser.timeline.refresh_feedbacks(
            #             ctx_client=self.ctx_browser.ctx_client,
            #             days=365,
            #         )
            # except Exception:
            #     try:
            #         self.log.log("[react.v2] refresh_feedbacks failed: " + traceback.format_exc(), level="ERROR")
            #     except Exception:
            #         pass
            active_block = build_announce_text(
                iteration=iteration,
                max_iterations=max_iterations,
                base_max_iterations=base_max_iterations,
                reactive_iteration_credit=reactive_iteration_credit,
                started_at=getattr(self.scratchpad, "started_at", "") or "",
                timezone=getattr(runtime_ctx, "timezone", None) if runtime_ctx else None,
                runtime_ctx=runtime_ctx,
                timeline_blocks=self.ctx_browser.timeline.blocks,
                constraints=None,
                feedback_updates=self.ctx_browser.feedback_updates if self.ctx_browser else None,
                feedback_incorporated=self.ctx_browser.feedback_updates_integrated if self.ctx_browser else False,
                mode=getattr(runtime_ctx, "announce_mode", "full") if runtime_ctx else "full",
            )
            debug_announce = bool(getattr(runtime_ctx, "debug_log_announce", False))
            debug_sources = bool(getattr(runtime_ctx, "debug_log_sources_pool", False))
            if debug_announce:
                try:
                    self.log.log(f"[react.v2] announce:\n{active_block}", level="INFO")
                except Exception:
                    pass
            if debug_sources:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_sources_pool_text
                    sources_text = build_sources_pool_text(
                        sources_pool=list(self.ctx_browser.sources_pool or []),
                    )
                    if sources_text:
                        self.log.log(f"[react.v2] sources_pool:\n{sources_text}", level="INFO")
                except Exception:
                    pass
            self.ctx_browser.announce(
                blocks=[{"text": active_block}],
            )
        except Exception:
            pass

    def _build_graph(self) -> StateGraph:

        def failing_node(state: Dict[str, Any]):
            """Simulate a tool/API failure"""
            try:
                raise ValueError("PPTX file was not produced")
            except Exception as exc:
                tb = traceback.format_exc()
                raise RuntimeError(f"[react.v2] Error control {exc}/{tb}") from exc

        wf = StateGraph(dict)
        wf.add_node("decision", self._decision_node)
        wf.add_node("tool_execution", self._tool_execution_node)
        wf.add_node("exit", self._exit_node)

        wf.set_entry_point("decision")
        wf.add_conditional_edges(
            "decision",
            self._route_after_decision,
            {"tool_execution": "tool_execution", "decision": "decision", "exit": "exit"},
        )
        wf.add_edge("tool_execution", "decision")
        wf.add_edge("exit", END)
        return wf.compile()

    def _mk_mainstream(self, phase: str) -> Callable[..., Awaitable[None]]:
        counters: Dict[str, int] = {}

        async def emit_delta(**kwargs):
            text = kwargs.get("text") or ""
            if not text:
                return
            marker = kwargs.get("marker") or kwargs.get("channel") or "thinking"
            i = counters.get(marker, 0)
            counters[marker] = i + 1
            author = f"{self.MODULE_AGENT_NAME}.{phase}"
            await self.comm.delta(text=text, index=i, marker=marker, agent=author, completed=bool(kwargs.get("completed")))

        return emit_delta

    def _mk_exec_code_streamer(
        self,
        phase: str,
        idx: int,
        execution_id: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], DecisionExecCodeStreamer]:
        artifact_suffix = execution_id or str(idx)
        streamer = DecisionExecCodeStreamer(
            emit_delta=self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=f"react.exec.{artifact_suffix}",
            execution_id=execution_id,
        )
        return self._wrap_raw_streamer(streamer), streamer

    def _mk_content_streamers(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        artifact_name: Optional[str] = None,
    ) -> tuple[List[Callable[[str], Awaitable[None]]], List[Any]]:
        safe_name = artifact_name or f"react.record.{uuid.uuid4().hex[:8]}"
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        base_args = {
            "emit_delta": self.comm.delta,
            "agent": f"{self.MODULE_AGENT_NAME}.{phase}",
            "artifact_name": safe_name,
            "sources_list": sources_list or [],
            "sources_getter": sources_getter,
            "turn_id": (self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser and self.ctx_browser.runtime_ctx else ""),
        }
        react_streamer = ReactWriteContentStreamer(
            **base_args,
            stream_tool_id="react.write",
        )
        patch_streamer = ReactPatchContentStreamer(
            **base_args,
            stream_tool_id="react.patch",
        )
        rendering_streamer = RenderingWriteContentStreamer(
            **base_args,
            write_tool_prefix="rendering_tools.write_",
        )
        fns = [
            self._wrap_json_streamer(react_streamer, sources_list=sources_list),
            self._wrap_json_streamer(patch_streamer, sources_list=sources_list),
            self._wrap_json_streamer(rendering_streamer, sources_list=sources_list),
        ]
        return fns, [react_streamer, patch_streamer, rendering_streamer]

    def _wrap_json_streamer(
        self,
        streamer: Any,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
    ) -> Callable[[str], Awaitable[None]]:
        if sources_list is not None and hasattr(streamer, "update_sources"):
            try:
                streamer.update_sources(sources_list)
            except Exception:
                pass

        async def emit_json_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed(text)

        return emit_json_delta

    def _wrap_raw_streamer(
        self,
        streamer: Any,
    ) -> Callable[[str], Awaitable[None]]:
        async def emit_raw_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed_raw(text)

        return emit_raw_delta

    def _mk_timeline_streamer(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        agent: Optional[str] = None,
        stream_final_answer: bool = False,
        notes_artifact_name: Optional[str] = None,
        final_answer_artifact_name: Optional[str] = None,
        plan_artifact_name: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], TimelineStreamer]:
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        final_answer_start_index = 0
        try:
            final_answer_start_index = max(
                0,
                int(getattr(self.scratchpad, "_react_answer_delta_idx", 0) or 0),
            )
        except Exception:
            final_answer_start_index = 0
        streamer = TimelineStreamer(
            emit_delta=self.comm.delta,
            agent=agent or f"{self.MODULE_AGENT_NAME}.{phase}",
            sources_list=sources_list or [],
            sources_getter=sources_getter,
            stream_final_answer=bool(stream_final_answer),
            notes_artifact_name=notes_artifact_name or "timeline_text.react.decision",
            final_answer_artifact_name=final_answer_artifact_name or "react.final_answer",
            final_answer_start_index=final_answer_start_index,
            plan_artifact_name=plan_artifact_name or "timeline_text.react.plan",
        )
        return self._wrap_json_streamer(streamer, sources_list=sources_list), streamer

    def _next_tool_streamer_idx(self, outdir: pathlib.Path, tool_id: str) -> int:
        dest_dir = outdir / "executed_programs"
        if not dest_dir.exists():
            return 0
        execution_dirs = [p for p in dest_dir.iterdir() if p.is_dir() and (p / "main.py").exists()]
        if execution_dirs:
            return len(execution_dirs)

        # Backward compatibility with older flat files:
        label = _safe_label(tool_id)
        max_idx = -1
        for path in dest_dir.glob("*_main.py"):
            name = path.name
            prefix = f"{label}_"
            suffix = "_main.py"
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            middle = name[len(prefix):-len(suffix)]
            try:
                idx = int(middle)
                max_idx = max(max_idx, idx)
            except Exception:
                continue
        return max_idx + 1

    def _short_json(self, obj: Any, *, max_len: int = 800) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except Exception:
            try:
                text = str(obj)
            except Exception:
                return "<unprintable>"
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    def _protocol_violation_message(
        self,
        *,
        code: str,
        state: Dict[str, Any],
        decision: Dict[str, Any] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        action = (decision.get("action") or "").strip()
        tool_id = ((decision.get("tool_call") or {}).get("tool_id") or "").strip()
        final_answer = decision.get("final_answer") or ""
        if code.startswith("invalid_action"):
            return f"Action '{action}' is not allowed. Allowed: call_tool | complete | exit."
        if code == "final_answer_required":
            return "final_answer is required for action=complete/exit."
        if code == "missing_tool_id":
            return "tool_call.tool_id is missing for action=call_tool."
        if code == "missing_contract":
            return f"exec tool requires params.contract (tool_id={tool_id or 'unknown'})."
        if code == "tool_call_invalid":
            return f"tool_call failed protocol validation for tool_id={tool_id or 'unknown'}. No action was executed for this round."
        if code == "tool_signature_red":
            return f"tool params failed signature validation for tool_id={tool_id or 'unknown'}. No action was executed for this round."
        if code == "ReactDecisionOutV2_schema_error":
            return (
                "Bad Protocol. The agent output in <channel:ReactDecisionOutV2> could not be parsed, "
                f"so no action was executed for this round. {error}'."
            )
        if final_answer and action == "call_tool":
            return f"final_answer present with action={action}."
        if extra:
            try:
                return f"Protocol violation: {code}. extra={self._short_json(extra)}"
            except Exception:
                return f"Protocol violation: {code}."
        return f"Protocol violation: {code}."

    def _failed_decision_round_note(
        self,
        *,
        code: str,
        tool_id: str = "",
    ) -> str:
        if code == "ReactDecisionOutV2_schema_error":
            return (
                "Wrong round. The agent wrote invalid content into the action channel, "
                "so this round executed no action. Retry with exactly one valid action."
            )
        if code == "tool_call_invalid":
            target = tool_id or "the requested tool"
            return (
                f"Wrong round. The agent attempted to call {target}, but the tool call did not pass "
                "protocol validation, so no action was executed."
            )
        if code == "tool_signature_red":
            target = tool_id or "the requested tool"
            return (
                f"Wrong round. The agent attempted to call {target}, but the parameter signature was invalid, "
                "so no action was executed."
            )
        if code == "tool_not_allowed_in_react":
            target = tool_id or "the requested tool"
            return (
                f"Wrong round. The agent selected {target}, but that tool is not allowed in the ReAct loop, "
                "so no action was executed."
            )
        return (
            "Wrong round. The agent violated the ReAct action protocol, "
            "so this round executed no action."
        )

    def _record_failed_decision_attempt(
        self,
        *,
        iteration: int,
        tool_call_id: str,
        code: str,
        notice_code: str,
        notice_message: str,
        decision_packet: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        tool_id: str = "",
        notice_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.ctx_browser or not tool_call_id:
            return
        try:
            ReactRound.note(
                ctx_browser=self.ctx_browser,
                notes=self._failed_decision_round_note(code=code, tool_id=tool_id),
                tool_call_id=tool_call_id,
                tool_id=tool_id or "__protocol_violation__",
                action="call_tool",
                iteration=iteration,
            )
        except Exception:
            pass
        try:
            ReactRound.decision_raw(
                ctx_browser=self.ctx_browser,
                decision=decision_packet or {},
                iteration=iteration,
                reason=reason or code,
                tool_call_id=tool_call_id,
            )
        except Exception:
            pass
        try:
            self.ctx_browser.contribute_notice(
                code=notice_code,
                message=notice_message,
                extra=notice_extra or {},
                call_id=tool_call_id,
                meta={
                    "rel": "call",
                    "iteration": iteration,
                    "failed_round": True,
                    "reason": reason or code,
                },
            )
        except Exception:
            pass

    async def _emit_timeline_text(self, *, text: str, agent: str, artifact_name: str):
        if not text:
            return
        idx = int(self._timeline_text_idx.get(artifact_name, 0))
        await self.comm.delta(
            text=text,
            index=idx,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=False,
        )
        self._timeline_text_idx[artifact_name] = idx + 1
        await self.comm.delta(
            text="",
            index=idx + 1,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=True,
        )
        self._timeline_text_idx[artifact_name] = idx + 2

    def _validate_decision(self, decision: Dict[str, Any]) -> Optional[str]:
        action = (decision.get("action") or "").strip()
        if action not in {"call_tool", "complete", "exit"}:
            return f"invalid_action:{action}"
        if action in {"complete", "exit"} and not (decision.get("final_answer") or "").strip():
            return "final_answer_required"
        if action != "call_tool":
            return None
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        if not tool_id:
            return "missing_tool_id"
        if tool_id == "react.read":
            return None
        if tool_id == "react.write":
            params = tool_call.get("params") or {}
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    return "path_not_first"
                if len(keys) >= 2 and keys[1] != "channel":
                    return "channel_not_second"
                if len(keys) >= 3 and keys[2] != "content":
                    return "content_not_third"
                if len(keys) >= 4 and keys[3] != "kind":
                    return "kind_not_fourth"
        return None

    def _validate_tool_call_protocol(
        self,
        *,
        tool_call: Any,
        adapters_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        violations: List[Dict[str, Any]] = []

        tc = tool_call if isinstance(tool_call, dict) else {}
        tool_id = (tc.get("tool_id") or "").strip() or None

        params = tc.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            violations.append({
                "code": "bad_params",
                "message": "tool_call.params must be a dict",
            })
            params = {}

        if not tool_id:
            violations.append({
                "code": "missing_tool_id",
                "message": "tool_call.tool_id is missing or empty",
            })
        else:
            react_tool_ids = {
                "react.read",
                "react.pull",
                "react.checkout",
                "react.write",
                "react.plan",
                "react.hide",
                "react.memsearch",
                "react.patch",
                "react.search_files",
            }
            if tool_id not in adapters_by_id and tool_id not in react_tool_ids:
                violations.append({
                    "code": "unknown_tool_id",
                    "message": f"tool_id '{tool_id}' is not in adapters/available tools",
                    "tool_id": tool_id,
                })
        allowed_params: set[str] = set()
        if tool_id in {
            "react.read",
            "react.pull",
            "react.checkout",
            "react.write",
            "react.plan",
            "react.hide",
            "react.memsearch",
            "react.patch",
            "react.search_files",
        }:
            if tool_id == "react.read":
                allowed_params.update({"paths"})
            elif tool_id == "react.pull":
                allowed_params.update({"paths"})
            elif tool_id == "react.checkout":
                allowed_params.update({"version"})
            elif tool_id == "react.plan":
                allowed_params.update({"mode", "steps", "plan_id"})
            elif tool_id == "react.hide":
                allowed_params.update({"path", "replacement"})
            elif tool_id == "react.memsearch":
                allowed_params.update({"query", "targets", "top_k", "days"})
            elif tool_id == "react.patch":
                allowed_params.update({"path", "channel", "patch", "kind"})
            elif tool_id == "react.search_files":
                allowed_params.update({"root", "name_regex", "content_regex", "max_bytes", "max_hits"})
            else:
                allowed_params.update({"path", "channel", "content", "kind"})
        else:
            adapter = adapters_by_id.get(tool_id) if tool_id else None
            if adapter:
                doc_args = (adapter.get("doc") or {}).get("args") or {}
                if isinstance(doc_args, dict):
                    allowed_params.update(str(k) for k in doc_args.keys())
                template = adapter.get("call_template") or ""
                if "(" in template and ")" in template:
                    inner = template.split("(", 1)[1].rsplit(")", 1)[0]
                    parts = [p.strip() for p in inner.split(",") if p.strip()]
                    for p in parts:
                        name = p.split("=", 1)[0].strip()
                        if name:
                            allowed_params.add(name)

        artifact_specs: List[Dict[str, Any]] = []
        if tools_insights.is_exec_tool(tool_id):
            contract = params.get("contract") if isinstance(params, dict) else None
            if not isinstance(contract, list) or not contract:
                violations.append({
                    "code": "missing_contract",
                    "message": "exec_tools.execute_code_python requires params.contract",
                    "tool_id": tool_id,
                })
            else:
                for a in contract:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get("name") or "").strip()
                    if not nm:
                        continue
                    rec = {"name": nm, "kind": "file"}
                    if a.get("filename"):
                        rec["filename"] = a.get("filename")
                    if a.get("description"):
                        rec["description"] = a.get("description")
                    artifact_specs.append(rec)
        if tool_id == "react.write":
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    violations.append({
                        "code": "path_not_first",
                        "message": "react.write params must start with path",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 2 and keys[1] != "channel":
                    violations.append({
                        "code": "channel_not_second",
                        "message": "react.write params must have channel as the second field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 3 and keys[2] != "content":
                    violations.append({
                        "code": "content_not_third",
                        "message": "react.write params must have content as the third field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 4 and keys[3] != "kind":
                    violations.append({
                        "code": "kind_not_fourth",
                        "message": "react.write params must have kind as the fourth field",
                        "tool_id": tool_id,
                    })
        if tool_id == "react.read":
            if isinstance(params, dict):
                if not isinstance(params.get("paths"), list):
                    violations.append({
                        "code": "bad_params",
                        "message": "react.read params.paths must be a list",
                        "tool_id": tool_id,
                    })
        # ref: bindings validated separately (need visibility + sources_pool rules)

        ok = len(violations) == 0
        return {
            "ok": ok,
            "tool_id": tool_id,
            "artifact_specs": artifact_specs,
            "violations": violations,
            "params": params,
        }

    async def prepare_session(
        self,
        *,
        allowed_plugins: List[str],
    ) -> ReactStateV2:
        outdir = pathlib.Path(self.ctx_browser.runtime_ctx.outdir or "")
        workdir = pathlib.Path(self.ctx_browser.runtime_ctx.workdir or "")
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_active_skills_subsystem
            if self.skills_subsystem:
                set_active_skills_subsystem(self.skills_subsystem)
        except Exception:
            pass

        session_id = f"react-v2-{uuid.uuid4().hex[:8]}"
        turn_id = self.ctx_browser.runtime_ctx.turn_id
        self.log.log(f"[react.v2] Start {session_id} in {workdir}")

        adapters = await self.tools_subsystem.react_tools(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,
        )

        plan_steps: List[str] = []
        max_iterations = int(getattr(self.ctx_browser.runtime_ctx, "max_iterations", None) or 15)
        if max_iterations <= 0:
            max_iterations = 15
        reactive_iteration_credit_cap = compute_reactive_iteration_credit_cap(
            runtime_ctx=getattr(self.ctx_browser, "runtime_ctx", None),
            base_max_iterations=max_iterations,
        )

        return ReactStateV2(
            session_id=session_id,
            turn_id=turn_id,
            plan_steps=plan_steps,
            plan_status={},
            adapters=adapters,
            workdir=workdir,
            outdir=outdir,
            max_iterations=max_iterations,
            base_max_iterations=max_iterations,
            reactive_iteration_credit=0,
            reactive_iteration_credit_cap=reactive_iteration_credit_cap,
            decision_retries=0,
            max_decision_retries=2,
        )

    async def run(
        self,
        *,
        allowed_plugins: List[str],
    ):
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent=self.MODULE_AGENT_NAME,
        )
        await turn_status.send(random.choice(["executing", "working", "solving"]))

        state = await self.prepare_session(
            allowed_plugins=allowed_plugins,
        )
        self._steer_interrupt_requested = False
        self._latest_steer_seq_seen = 0
        self._last_handled_steer_seq = 0
        self._latest_steer_text = ""
        self._reactive_iteration_credit_total = 0
        self._reactive_iteration_credit_cap = int(getattr(state, "reactive_iteration_credit_cap", 0) or 0)
        self._credited_external_event_ids = set()
        try:
            if self.ctx_browser and self.ctx_browser.timeline:
                self._latest_external_event_seq_seen = int(self.ctx_browser.timeline.last_external_event_seq or 0)
                self._last_decision_visible_external_event_seq = int(self.ctx_browser.timeline.last_external_event_seq or 0)
                self._last_consumed_external_event_seq = int(self.ctx_browser.timeline.last_external_event_seq or 0)
        except Exception:
            pass
        try:
            self._seed_steer_interrupt_from_current_turn()
        except Exception:
            pass

        start_ts = time.time()
        try:
            recursion_limit = max(
                20,
                ((int(state.base_max_iterations) + int(state.reactive_iteration_credit_cap)) * 3) + 6,
            )
            final_state = await self.graph.ainvoke(self._to_dict(state), config={"recursion_limit": recursion_limit})
        except Exception as exc:
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] Graph error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise RuntimeError(f"[react.v2] Graph error: {exc}\n{tb}") from exc
        finally:
            # workspace is managed by ContextBrowser; no CV reset here
            self._outdir_cv_token = None
            self._workdir_cv_token = None

        try:
            final_state["total_runtime_sec"] = float(time.time() - start_ts)
        except Exception:
            pass

        return await self._analyze_and_build_result(final_state)

    def _to_dict(self, s: ReactStateV2) -> Dict[str, Any]:
        return {
            "session_id": s.session_id,
            "turn_id": s.turn_id,
            "plan_steps": s.plan_steps,
            "plan_status": s.plan_status,
            "adapters": s.adapters,
            "workdir": str(s.workdir),
            "outdir": str(s.outdir),
            # "next_decision_model": s.next_decision_model,
            "iteration": s.iteration,
            "max_iterations": s.max_iterations,
            "base_max_iterations": s.base_max_iterations,
            "reactive_iteration_credit": s.reactive_iteration_credit,
            "reactive_iteration_credit_cap": s.reactive_iteration_credit_cap,
            "decision_retries": s.decision_retries,
            "max_decision_retries": s.max_decision_retries,
            "exit_reason": s.exit_reason,
            "final_answer": s.final_answer,
            "suggested_followups": s.suggested_followups,
            "last_decision": s.last_decision,
            "last_tool_result": s.last_tool_result,
            "pending_tool_skills": s.pending_tool_skills,
            "session_log": s.session_log,
            "round_timings": s.round_timings,
        }

    async def persist_workspace(self) -> Optional[dict]:
        """
        Persist execution snapshot (out/work trees) into ConversationStore.
        Uses runtime_ctx for all required fields.
        """
        flag = os.getenv("REACT_PERSIST_WORKSPACE", "1").strip().lower()
        if flag in {"0", "false", "no", "off"}:
            return None
        if not self.hosting_service or not self.ctx_browser:
            return None
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
        if not runtime_ctx:
            return None

        def _derive_run_id(path_value: Optional[str]) -> str:
            if not path_value:
                return ""
            try:
                p = pathlib.Path(path_value)
                if p.name in {"work", "out"} and p.parent.name:
                    return p.parent.name
                return p.name
            except Exception:
                return ""

        tenant = getattr(runtime_ctx, "tenant", None)
        project = getattr(runtime_ctx, "project", None)
        user = getattr(runtime_ctx, "user_id", None)
        user_type = getattr(runtime_ctx, "user_type", None) or "anonymous"
        conversation_id = getattr(runtime_ctx, "conversation_id", None)
        turn_id = getattr(runtime_ctx, "turn_id", None)
        workdir = getattr(runtime_ctx, "workdir", None)
        outdir = getattr(runtime_ctx, "outdir", None)
        codegen_run_id = _derive_run_id(workdir) or _derive_run_id(outdir)

        if not (tenant and project and conversation_id and turn_id and codegen_run_id):
            return None

        try:
            return await self.hosting_service.persist_workspace(
                outdir=outdir,
                workdir=workdir,
                tenant=tenant,
                project=project,
                user=user,
                conversation_id=conversation_id,
                user_type=user_type,
                turn_id=turn_id,
                codegen_run_id=codegen_run_id,
            )
        except Exception as exc:
            try:
                self.log.log(f"[react.v2] persist_workspace failed: {exc}", level="ERROR")
            except Exception:
                pass
            return None

    def _route_after_decision(self, state: Dict[str, Any]) -> str:
        if state.get("exit_reason"):
            try:
                self.log.log(f"[react.v2] route=exit exit_reason={state.get('exit_reason')}", level="INFO")
            except Exception:
                pass
            return "exit"
        if state.get("retry_decision"):
            try:
                self.log.log("[react.v2] retry_decision=True -> route=decision", level="INFO")
            except Exception:
                pass
            state["retry_decision"] = False
            return "decision"
        decision = state.get("last_decision") or {}
        action = decision.get("action")
        if action == "call_tool":
            try:
                self.log.log("[react.v2] route=tool_execution", level="INFO")
            except Exception:
                pass
            return "tool_execution"
        try:
            self.log.log(f"[react.v2] route=exit action={action}", level="INFO")
        except Exception:
            pass
        return "exit"

    async def _decision_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self._sync_reactive_iteration_budget(state)
        if await self._apply_steer_interrupt_if_requested(state, checkpoint="decision.start"):
            return state
        if state.get("exit_reason"):
            return state
        if bool(state.get("steer_finalize_mode")) and int(state.get("steer_finalize_rounds_remaining") or 0) <= 0:
            state["exit_reason"] = "steer"
            if not (state.get("final_answer") or "").strip():
                state["final_answer"] = self._build_default_steer_final_answer()
            state["suggested_followups"] = state.get("suggested_followups") or []
            return state
        iteration = int(state.get("iteration") or 0)
        if iteration >= int(state.get("max_iterations") or 0):
            state["exit_reason"] = "max_iterations"
            return state
        try:
            interrupted, result = await self._run_cancellable_phase(
                phase="decision",
                coro=self._decision_node_impl(state, iteration),
            )
            if interrupted:
                await self._enter_steer_finalize_mode(
                    state,
                    checkpoint="decision.cancelled",
                    cancelled_phase="decision",
                )
                return state
            return result
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] decision_node error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise

    async def _decision_node_impl(self, state: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        announced_adapters = [
            a for a in (state.get("adapters") or [])
            if a.get("id") not in tools_insights.CODEGEN_ONLY_TOOL_IDS and not tools_insights.is_code_tool(a["id"])
        ]
        extra_adapters = [a for a in (state.get("adapters") or []) if tools_insights.is_code_tool(a["id"])]
        extra_adapters_for_decision = [
            a for a in extra_adapters if not tools_insights.is_codegen_tool(a["id"])
        ]

        await self._update_announce(
            iteration=iteration,
            max_iterations=int(state.get("max_iterations") or 0),
            base_max_iterations=int(state.get("base_max_iterations") or 0),
            reactive_iteration_credit=int(state.get("reactive_iteration_credit") or 0),
        )
        model_kind = state.get("next_decision_model") or "strong"
        role = f"{self.MODULE_AGENT_NAME}.{self.DECISION_AGENT_NAME}.{model_kind}"
        async with with_accounting(
            self.ctx_browser.runtime_ctx.bundle_id,
            agent=role,
            metadata={"agent": role},
        ):
            mainstream = self._mk_mainstream(f"decision ({iteration})")
            pending_tool_call_id = f"tc_{uuid.uuid4().hex[:12]}"
            exec_id = f"exec_{pending_tool_call_id}"
            exec_streamer_idx = self._next_tool_streamer_idx(
                pathlib.Path(state["outdir"]),
                "exec_tools.execute_code_python",
            )
            exec_streamer_fn, exec_streamer_widget = self._mk_exec_code_streamer(
                f"decision ({iteration})",
                exec_streamer_idx,
                execution_id=exec_id,
            )
            sources_list = []
            try:
                if self.ctx_browser:
                    sources_list = list(self.ctx_browser.sources_pool or [])
            except Exception:
                sources_list = []
        record_streamer_fns, record_streamers = self._mk_content_streamers(
            f"decision.record ({iteration})",
            sources_list=sources_list,
            artifact_name=f"react.record.{pending_tool_call_id}",
        )

        timeline_agent = f"{role}.timeline.{state.get('turn_id') or ''}.{iteration}"
        timeline_streamer_fn, timeline_streamer = self._mk_timeline_streamer(
            f"decision.timeline ({iteration})",
            sources_list=sources_list,
            agent=timeline_agent,
            stream_final_answer=True,
            notes_artifact_name=f"timeline_text.react.decision.{iteration}",
            final_answer_artifact_name=f"react.final_answer.{iteration}",
            plan_artifact_name=f"timeline_text.react.plan.{iteration}",
        )

        async def _hub_on_json(text: str, completed: bool = False, **_kwargs):
            for fn in record_streamer_fns:
                await fn(text, completed=completed)
            await timeline_streamer_fn(text, completed=completed)

        t0 = time.perf_counter()
        from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry import retry_with_compaction
        from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.agents.decision import build_decision_system_text

        async def _decision_agent(*, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            self._begin_active_generation_capture(iteration=iteration)
            from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSubscribers
            subs = ChannelSubscribers().subscribe("ReactDecisionOutV2", _hub_on_json)
            if exec_streamer_widget is not None:
                subs = subs.subscribe("ReactDecisionOutV2", exec_streamer_widget.feed_json)
                subs = subs.subscribe("code", exec_streamer_widget.feed_code)
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
            decision_max_tokens = 20000
            if bool(state.get("steer_finalize_mode")):
                decision_max_tokens = min(decision_max_tokens, self._steer_finalize_max_tokens())
            return await react_decision_stream_v2(
                svc=self.svc,
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
                workspace_implementation=getattr(runtime_ctx, "workspace_implementation", "custom") if runtime_ctx else "custom",
                on_progress_delta=mainstream,
                on_raw_delta=self._capture_active_generation_raw,
                subscribers=subs,
                agent_name=role,
                max_tokens=decision_max_tokens,
                user_blocks=blocks,
            )

        render_params = {
            "cache_last": True,
            "include_sources": True,
            "include_announce": True,
            "force_sanitize": bool(state.get("force_compaction_next_decision")),
        }
        await self._drain_external_events(call_hooks=True)
        visible_external_event_seq = 0
        try:
            visible_external_event_seq = int(getattr(self.ctx_browser.timeline, "last_external_event_seq", 0) or 0)
        except Exception:
            visible_external_event_seq = 0
        self._last_decision_visible_external_event_seq = visible_external_event_seq
        try:
            ReactRound.start(
                ctx_browser=self.ctx_browser,
                tool_call_id=pending_tool_call_id,
                iteration=iteration,
            )
        except Exception:
            pass
        try:
            decision = await retry_with_compaction(
                ctx_browser=self.ctx_browser,
                system_text_fn=lambda: build_decision_system_text(
                    adapters=announced_adapters,
                    infra_adapters=extra_adapters_for_decision,
                    workspace_implementation=getattr(getattr(self.ctx_browser, "runtime_ctx", None), "workspace_implementation", "custom"),
                    additional_instructions=self.additional_instructions,
                ),
                render_params=render_params,
                agent_fn=_decision_agent,
                emit_status=None,
            )
        finally:
            if self._active_phase_cancelled_by_steer or self._steer_interrupt_requested:
                self._stash_interrupted_generation_snapshot()
            self._clear_active_generation_capture()
        try:
            if timeline_streamer is not None and timeline_streamer.has_started("final_answer"):
                self.scratchpad._final_answer_delta_emitted = True
                self.scratchpad._react_answer_delta_idx = max(
                    int(getattr(self.scratchpad, "_react_answer_delta_idx", 0) or 0),
                    int(timeline_streamer.next_index("final_answer") or 0),
                )
        except Exception:
            self.log.log(
                f"[react.v2] failed to sync streamed final-answer index: {traceback.format_exc()}",
                level="ERROR",
            )
        await self._drain_external_events(call_hooks=True)
        if await self._apply_steer_interrupt_if_requested(state, checkpoint="decision.after"):
            state["force_compaction_next_decision"] = False
            state["last_decision_raw"] = decision
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
            return state
        if visible_external_event_seq > self._last_consumed_external_event_seq and self.external_event_source is not None:
            try:
                await self._mark_external_events_consumed_up_to(max_sequence=visible_external_event_seq)
            except Exception:
                self.log.log(f"[react.v2] failed to mark external events consumed: {traceback.format_exc()}", level="ERROR")
        # Reset forced compaction once we have a decision attempt.
        state["force_compaction_next_decision"] = False
        state["last_decision_raw"] = decision
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
        logging_helpers.log_agent_packet(role, "react.decision.v2", decision)
        error = (decision.get("log") or {}).get("error")

        try:
            ReactRound.thinking(
                ctx_browser=self.ctx_browser,
                decision=decision,
                title=f"solver.react.v2.decision ({iteration})",
                iteration=iteration,
            )
        except Exception:
            pass

        notes = None
        action = None
        tool_id = ""
        protocol_entry = None

        if error:
            notice_message = self._protocol_violation_message(
                code="ReactDecisionOutV2_schema_error",
                error=error,
                state=state,
                decision={},
            )
            try:
                self._record_failed_decision_attempt(
                    iteration=iteration,
                    tool_call_id=pending_tool_call_id,
                    code="ReactDecisionOutV2_schema_error",
                    notice_code="protocol_violation.ReactDecisionOutV2_schema_error",
                    notice_message=notice_message,
                    decision_packet=decision,
                    reason="schema_error",
                )
                self.log.log(f"[react.v2] decision schema error: {error}", level="ERROR")
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "ReactDecisionOutV2_schema_error; retry decision"
                try:
                    self.log.log(
                        f"[react.v2] retry decision after schema error (retries={state['decision_retries']})",
                        level="INFO",
                    )
                except Exception:
                    pass
            else:
                decision = {
                    "action": "exit",
                    "final_answer": "ReactDecisionOutV2_schema_error validation failed.",
                    "notes": "ReactDecisionOutV2_schema_error",
                }
                action = "exit"
                tool_call = {}
                tool_id = ""
        else:

            decision = decision.get("agent_response") or {}
            if not isinstance(decision, dict):
                decision = {}

            original_action = (decision.get("action") or "").strip()
            has_final_answer = bool((decision.get("final_answer") or "").strip())
            if original_action == "call_tool" and has_final_answer:
                try:
                    if self.ctx_browser:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.final_answer_with_tool_call",
                            message=self._protocol_violation_message(
                                code="final_answer_with_tool_call",
                                decision={**decision, "action": "call_tool"},
                                state=state,
                            ),
                            extra={"action": "call_tool"},
                            call_id=pending_tool_call_id,
                        )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] final_answer present with call_tool; coercing to complete",
                        level="ERROR",
                    )
                except Exception:
                    pass
                decision["action"] = "complete"
                decision["tool_call"] = None

            validation_error = self._validate_decision(decision)
            if validation_error:
                if validation_error.startswith("invalid_action"):
                    invalid_retries = int(state.get("invalid_action_retries") or 0) + 1
                    state["invalid_action_retries"] = invalid_retries
                    # If we see invalid_action repeatedly, force compaction on next decision.
                    if invalid_retries >= 2:
                        state["force_compaction_next_decision"] = True
                notice_message = self._protocol_violation_message(
                    code=validation_error,
                    decision=decision,
                    state=state,
                )
                try:
                    self._record_failed_decision_attempt(
                        iteration=iteration,
                        tool_call_id=pending_tool_call_id,
                        code=validation_error,
                        notice_code=f"protocol_violation.{validation_error}",
                        notice_message=notice_message,
                        decision_packet=state.get("last_decision_raw") if isinstance(state.get("last_decision_raw"), dict) else None,
                        reason=validation_error,
                        tool_id=((decision.get("tool_call") or {}).get("tool_id") or "").strip(),
                        notice_extra={"validation_error": validation_error},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] decision validation failed: {validation_error} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision = {"action": "call_tool", "notes": f"{validation_error}; retry decision"}
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after validation error (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                    state["session_log"].append({
                        "type": "decision_invalid",
                        "iteration": iteration,
                        "timestamp": time.time(),
                        "error": validation_error,
                    })
                    state["last_decision"] = decision
                    return state
                else:
                    decision = {"action": "exit", "final_answer": "Decision validation failed."}
            else:
                state["invalid_action_retries"] = 0
                state["force_compaction_next_decision"] = False
                # Clear any pending retry flags on a valid decision.
                state["retry_decision"] = False
            action = (decision.get("action") or "").strip()
            notes = (decision.get("notes") or "").strip()
            tool_call = decision.get("tool_call") or {}
            tool_id = (tool_call.get("tool_id") or "").strip()
            if action == "complete" and (decision.get("final_answer") or "").strip():
                state["retry_decision"] = False

            plan_steps = state.get("plan_steps") or []
            if not plan_steps and self.ctx_browser:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import latest_current_plan_snapshot
                    snap = latest_current_plan_snapshot(self.ctx_browser.timeline.blocks)
                    if snap and snap.steps:
                        plan_steps = list(snap.steps)
                        state["plan_steps"] = plan_steps
                except Exception:
                    pass
            try:
                if tool_id != "react.plan":
                    status_map = state.get("plan_status") or {}
                    status_map, plan_blocks = apply_plan_updates(
                        notes=notes,
                        plan_steps=plan_steps,
                        status_map=status_map if isinstance(status_map, dict) else {},
                        timeline_blocks=self.ctx_browser.timeline.blocks if self.ctx_browser else [],
                        turn_id=state.get("turn_id") or "",
                        iteration=iteration,
                        ts=time.time(),
                    )
                    if status_map:
                        state["plan_status"] = status_map
                    if plan_blocks and self.ctx_browser:
                        self.ctx_browser.contribute(blocks=plan_blocks)
                    if self.ctx_browser:
                        from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import latest_current_plan_snapshot
                        current_snap = latest_current_plan_snapshot(self.ctx_browser.timeline.blocks)
                        if current_snap:
                            state["plan_id"] = current_snap.plan_id
                            state["plan_ts"] = current_snap.created_ts
                            state["plan_origin_turn_id"] = current_snap.origin_turn_id
                            state["plan_last_ts"] = current_snap.last_ts
                            state["plan_steps"] = list(current_snap.steps or [])
                            state["plan_status"] = dict(current_snap.status or {})
                        else:
                            state["plan_id"] = ""
                            state["plan_ts"] = ""
                            state["plan_origin_turn_id"] = ""
                            state["plan_last_ts"] = ""
                            state["plan_steps"] = []
                            state["plan_status"] = {}
            except Exception:
                pass

            exec_only_ids = {"ctx_tools.fetch_ctx"}
            if action == "call_tool" and tool_id in exec_only_ids:
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_not_allowed_in_react",
                        message=self._protocol_violation_message(
                            code="tool_not_allowed_in_react",
                            decision=decision,
                            state=state,
                            extra={"tool_id": tool_id},
                        ),
                        extra={"tool_id": tool_id, "iteration": iteration},
                        call_id=pending_tool_call_id,
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(f"[react.v2] tool_not_allowed_in_react: {tool_id}", level="ERROR")
                except Exception:
                    pass
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_not_allowed_in_react; retry decision"
                    action = "call_tool"
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after tool_not_allowed (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                else:
                    decision = {
                        "action": "exit",
                        "final_answer": "Tool not allowed in react loop.",
                        "notes": "tool_not_allowed_in_react",
                    }
                    action = "exit"
            # Plan acknowledgements are informative only; do not block completion.
            if action == "call_tool":
                adapters_by_id = self._adapters_index(state.get("adapters") or [])
                verdict = self._validate_tool_call_protocol(
                    tool_call=tool_call,
                    adapters_by_id=adapters_by_id,
                )
                protocol_entry = {
                    "type": "protocol_verify",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": verdict.get("tool_id"),
                    "ok": bool(verdict.get("ok")),
                    "violations": verdict.get("violations") or [],
                }
                if verdict.get("ok"):
                    state["session_log"].append(protocol_entry)
                else:
                    try:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.tool_call_invalid",
                            message=self._protocol_violation_message(
                                code="tool_call_invalid",
                                decision=decision,
                                state=state,
                            ),
                            extra={
                                "violations": verdict.get("violations") or [],
                                "tool_id": verdict.get("tool_id"),
                                "iteration": iteration,
                            },
                            call_id=pending_tool_call_id,
                            meta={"rel": "call"},
                        )
                    except Exception:
                        pass
                    try:
                        self.log.log(
                            f"[react.v2] tool_call_invalid: {verdict.get('violations')} | decision={self._short_json(decision)}",
                            level="ERROR",
                        )
                    except Exception:
                        pass
                    state["session_log"].append(protocol_entry)
                    retries = int(state.get("decision_retries") or 0)
                    if retries < int(state.get("max_iterations") or 0):
                        state["decision_retries"] = retries + 1
                        state["retry_decision"] = True
                        decision["notes"] = "tool_call_invalid; retry decision"
                        try:
                            self.log.log(
                                f"[react.v2] retry decision after tool_call_invalid (retries={state['decision_retries']})",
                                level="INFO",
                            )
                        except Exception:
                            pass
                    else:
                        decision = {
                            "action": "exit",
                            "final_answer": "Tool call validation failed.",
                            "notes": "tool_call_invalid",
                        }
                        action = "exit"
                        tool_call = {}
                        tool_id = ""

            # ---- tool signature validation (filter params + classify issues) ----
            sig_status = None
            sig_issues: List[Dict[str, Any]] = []
            filtered_params = tool_call.get("params") if isinstance(tool_call, dict) else {}
            try:
                if tool_id and not str(tool_id).startswith("react."):
                    tv = await self.tools_subsystem.validate_tool_params(tool_id=tool_id, params=filtered_params)
                    sig_status = tv.get("status")
                    sig_issues = tv.get("issues") or []
                    filtered_params = tv.get("params") or {}
                    if isinstance(tool_call, dict):
                        tool_call["params"] = filtered_params
                        decision["tool_call"] = tool_call
                        state["last_decision"] = decision
            except Exception:
                sig_status = None
                sig_issues = []


            if sig_status in ("yellow", "red"):
                try:
                    self.ctx_browser.contribute_notice(
                        code="tool_signature_validation",
                        message=f"tool_signature_validation={sig_status}",
                        extra={"iteration": iteration, "tool_id": tool_id, "status": sig_status, "issues": sig_issues},
                        call_id=pending_tool_call_id,
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                state["session_log"].append({
                    "type": "tool_signature_validation",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": tool_id,
                    "status": sig_status,
                    "issues": sig_issues,
                })

            if sig_status == "red":
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_signature_red",
                        message=self._protocol_violation_message(
                            code="tool_signature_red",
                            decision=decision,
                            state=state,
                        ),
                        extra={"violations": sig_issues, "tool_id": tool_id, "iteration": iteration},
                        call_id=pending_tool_call_id,
                        meta={"rel": "call"},
                    )
                except Exception:
                    pass
                try:
                    self.log.log(
                        f"[react.v2] tool_signature_red: {sig_issues} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                if protocol_entry is not None:
                    protocol_entry["ok"] = False
                    protocol_entry["violations"] = (protocol_entry.get("violations") or []) + (sig_issues or [])
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_signature_red; retry decision"
                    try:
                        self.log.log(
                            f"[react.v2] retry decision after tool_signature_red (retries={state['decision_retries']})",
                            level="INFO",
                        )
                    except Exception:
                        pass
                else:
                    decision = {
                        "action": "exit",
                        "final_answer": "Tool signature validation failed.",
                        "notes": "tool_signature_red",
                    }
                    action = "exit"
                    tool_call = {}
                    tool_id = ""
            if notes:
                if tools_insights.is_exec_tool(tool_id) and exec_streamer_widget:
                    await exec_streamer_widget.emit_reasoning(notes)

        if bool(state.get("steer_finalize_mode")):
            remaining_before = int(state.get("steer_finalize_rounds_remaining") or 0)
            remaining_after = max(0, remaining_before - 1)
            state["steer_finalize_rounds_remaining"] = remaining_after
            allowed_finalize_tools = {"react.write", "react.patch", "react.plan", "react.hide"}
            if action == "call_tool" and tool_id not in allowed_finalize_tools:
                decision = {
                    "action": "complete",
                    "final_answer": self._build_default_steer_final_answer(),
                    "notes": "steer_finalize_forced_complete",
                    "suggested_followups": [],
                }
                action = "complete"
                tool_call = {}
                tool_id = ""
                state["last_decision"] = decision
            elif action == "call_tool" and remaining_after <= 0:
                decision = {
                    "action": "complete",
                    "final_answer": self._build_default_steer_final_answer(),
                    "notes": "steer_finalize_budget_exhausted",
                    "suggested_followups": [],
                }
                action = "complete"
                tool_call = {}
                tool_id = ""
                state["last_decision"] = decision

        if not state.get("retry_decision") and action in {"complete", "exit"}:
            exit_grace_ms = 0
            try:
                exit_grace_ms = int(os.getenv("REACT_EXTERNAL_EVENT_EXIT_GRACE_MS", "750") or "750")
            except Exception:
                exit_grace_ms = 750
            if exit_grace_ms > 0:
                await self._wait_and_drain_external_events(call_hooks=True, block_ms=exit_grace_ms)
            latest_seen_after_decision = max(
                int(self._latest_external_event_seq_seen or 0),
                int(getattr(getattr(self.ctx_browser, "timeline", None), "last_external_event_seq", 0) or 0),
            )
            if latest_seen_after_decision > int(visible_external_event_seq or 0):
                state["retry_decision"] = True
                state["exit_reason"] = None
                state["final_answer"] = None
                state["suggested_followups"] = []
                try:
                    self.log.log(
                        f"[react.v2] external event arrived during decision; forcing another round "
                        f"(seen={latest_seen_after_decision} visible={visible_external_event_seq})",
                        level="INFO",
                    )
                except Exception:
                    pass
            else:
                final_answer_text = (decision.get("final_answer") or "").strip()
                state["exit_reason"] = "steer" if bool(state.get("steer_finalize_mode")) else action
                state["final_answer"] = final_answer_text
                state["suggested_followups"] = decision.get("suggested_followups") or []
                try:
                    sf = state.get("suggested_followups") or []
                    self.log.log(
                        f"[react.v2] decision followups: count={len(sf)}",
                        level="INFO",
                    )
                except Exception:
                    pass

        try:
            if notes:
                ReactRound.note(
                    ctx_browser=self.ctx_browser,
                    notes=notes,
                    tool_call_id=pending_tool_call_id,
                    tool_id=tool_id,
                    action=action,
                    iteration=iteration,
                )
        except Exception:
            pass
        await emit_event(
            comm=self.comm,
            etype="solver.react.decision",
            title=f"ReAct Round ({iteration}). Decision Node",
            step=f"react({iteration}).decision",
            data=decision if decision else {"error": "no_decision"},
            agent=f"solver.react({iteration}).decision",
        )
        try:
            self.scratchpad.register_agentic_response(
                f"solver.react.decision ({iteration})",
                decision,
            )
        except Exception as exc:
            self.log.log(f"[react.v2] register_agentic_response failed: {exc}", level="ERROR")

        state["exec_code_streamer"] = exec_streamer_widget
        state["record_streamers"] = record_streamers
        state["timeline_streamer"] = timeline_streamer
        state["pending_exec_id"] = exec_id
        state["pending_tool_call_id"] = pending_tool_call_id
        state["last_decision"] = decision
        state["iteration"] = iteration + 1
        bs = state.get("budget_state_v2")
        if bs is not None:
            try:
                bs.decision_rounds_used = int(state.get("iteration") or 0)
            except Exception:
                self.log.log(traceback.format_exc())
        return state

    async def _tool_execution_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if await self._apply_steer_interrupt_if_requested(state, checkpoint="tool.before_execute"):
            return state
        interrupted, result = await self._run_cancellable_phase(
            phase="tool_execution",
            coro=ReactRound.execute(react=self, state=state),
        )
        if interrupted:
            await self._enter_steer_finalize_mode(
                state,
                checkpoint="tool.cancelled",
                cancelled_phase="tool_execution",
            )
            return state
        state = result
        await self._drain_external_events(call_hooks=True)
        if await self._apply_steer_interrupt_if_requested(state, checkpoint="tool.after_execute"):
            return state
        pending_sources = state.pop("pending_sources", None)
        if pending_sources:
            try:
                self.log.log(
                    f"[react.v2] merge_sources: pending_sources={len(pending_sources or [])}",
                    level="INFO",
                )

                await self._merge_with_pool(
                    collections=[pending_sources],
                    workdir=pathlib.Path(state["workdir"]),
                    outdir=pathlib.Path(state["outdir"]),
                    record_streamers=state.get("record_streamers"),
                    timeline_streamer=state.get("timeline_streamer"),
                )
            except Exception:
                self.log.log(traceback.format_exc())
        return state

    async def _exit_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reason = state.get("exit_reason", "complete")
        state["session_log"].append({
            "type": "exit",
            "timestamp": time.time(),
            "reason": reason,
        })
        pre_blocks = []
        post_blocks = []

        exit_block = {
            "type": "react.exit",
            "author": "react",
            "turn_id": self.scratchpad.turn_id or "",
            "ts": time.time(),
            "mime": "application/json",
            "path": f"ar:{self.scratchpad.turn_id}.react.exit",
            "text": json.dumps({
                "reason": reason,
            }, ensure_ascii=False, indent=2),
        }

        # persist final turn stats to contrib log (after completion), then clear announce
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            final_text = ""
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
                final_text = build_announce_text(
                    iteration=int(state.get("iteration") or 0),
                    max_iterations=int(state.get("max_iterations") or 0),
                    base_max_iterations=int(state.get("base_max_iterations") or 0),
                    reactive_iteration_credit=int(state.get("reactive_iteration_credit") or 0),
                    started_at=getattr(self.scratchpad, "started_at", "") or "",
                    timezone=getattr(runtime_ctx, "timezone", None) if runtime_ctx else None,
                    runtime_ctx=runtime_ctx,
                    timeline_blocks=self.ctx_browser.timeline.blocks if self.ctx_browser else [],
                    constraints=None,
                    mode="turn_finalize",
                ).strip()
            except Exception:
                final_text = ""
            if final_text:
                post_blocks.append({"text": final_text})
            if self.ctx_browser:
                self.ctx_browser.announce(blocks=None)
        except Exception:
            self.log.log(traceback.format_exc())
        # persist react_state snapshot as a contribution block
        try:
            if self.ctx_browser:
                react_state = ReactStateSnapshot.from_state(state)
                post_blocks.append({
                    "type": "react.state",
                    "author": "react",
                    "turn_id": self.ctx_browser.runtime_ctx.turn_id or "",
                    "ts": time.time(),
                    "mime": "application/json",
                    "path": f"ar:{self.ctx_browser.runtime_ctx.turn_id}.react.state",
                    "text": json.dumps(react_state.to_dict(), ensure_ascii=False, indent=2),
                })
        except Exception:
            self.log.log(traceback.format_exc())
        if exit_block:
            post_blocks.append(exit_block)
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            if runtime_ctx is not None:
                if pre_blocks:
                    existing = getattr(runtime_ctx, "on_before_completion_contribution", None)
                    def _hook_pre(blocks=pre_blocks, prior=existing):
                        out = []
                        if callable(prior):
                            try:
                                out.extend(prior() or [])
                            except Exception:
                                self.log.log(traceback.format_exc())
                        out.extend(list(blocks))
                        blocks.clear()
                        return out
                    runtime_ctx.on_before_completion_contribution = _hook_pre
                if post_blocks:
                    existing = getattr(runtime_ctx, "on_after_completion_contribution", None)
                    def _hook_post(blocks=post_blocks, prior=existing):
                        out = []
                        if callable(prior):
                            try:
                                out.extend(prior() or [])
                            except Exception:
                                self.log.log(traceback.format_exc())
                        out.extend(list(blocks))
                        blocks.clear()
                        return out
                    runtime_ctx.on_after_completion_contribution = _hook_post
        except Exception as ex:
            self.log.log(f"[react.v2] completion_hooks: {ex}", level="ERROR")

        # Emit citations used in this turn (files already emitted on host)
        try:
            if self.hosting_service and self.ctx_browser and self.ctx_browser.timeline:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import extract_sources_used_from_blocks
                blocks = self.ctx_browser.timeline.get_turn_blocks()
                used_sids = extract_sources_used_from_blocks(blocks)
                try:
                    answer_sids = citations_module.extract_citation_sids_any(state.get("final_answer") or "")
                except Exception:
                    answer_sids = []
                if answer_sids:
                    for sid in answer_sids:
                        if sid not in used_sids:
                            used_sids.append(sid)
                if used_sids:
                    sid_set = set(used_sids)
                    citations = []
                    pool = list(self.ctx_browser.timeline.sources_pool or [])
                    pool_updated = False
                    non_citable = []

                    def _is_citable_source(row: dict) -> bool:
                        st = (row.get("source_type") or "").strip().lower()
                        if st in {"file", "attachment"}:
                            return False
                        url = (row.get("url") or "").strip().lower()
                        return url.startswith("http://") or url.startswith("https://")
                    for row in pool:
                        if not isinstance(row, dict):
                            continue
                        if row.get("sid") in sid_set:
                            if not _is_citable_source(row):
                                non_citable.append(row.get("sid"))
                                continue
                            if row.get("used") is not True:
                                row["used"] = True
                                pool_updated = True
                            # strip heavy fields
                            clean = {k: v for k, v in row.items() if k not in ("content", "base64")}
                            citations.append(clean)
                    if pool_updated:
                        try:
                            self.ctx_browser.set_sources_pool(sources_pool=pool)
                        except Exception:
                            self.log.log(traceback.format_exc())
                    if non_citable:
                        self.log.log(
                            f"[react.v2] emit_citations: skipped non-citable sids={sorted(set(non_citable))}",
                            level="INFO",
                        )
                    self.log.log(
                        f"[react.v2] emit_citations: used_sids={sorted(sid_set)} "
                        f"pool={len(pool)} citations={len(citations)}",
                        level="INFO",
                    )
                    if citations:
                        await self.hosting_service.emit_solver_artifacts(files=[], citations=citations)
                else:
                    self.log.log("[react.v2] emit_citations: no used_sids detected", level="INFO")
        except Exception:
            self.log.log(traceback.format_exc())
        return state
    def _adapters_index(self, adapters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
        for a in adapters or []:
            tid = (a.get("id") or "").strip()
            if tid:
                idx[tid] = a
        return idx

    def _append_react_timing(
            self,
            *,
            round_idx: int,
            stage: str,
            elapsed_ms: Optional[int],
            tool_id: Optional[str] = None,
            artifact_id: Optional[str] = None,
    ) -> None:
        if elapsed_ms is None:
            return
        if not getattr(self, "scratchpad", None):
            return
        timings = getattr(self.scratchpad, "timings", None)
        if not isinstance(timings, list):
            return
        title = f"react.{round_idx}.{stage}"
        if tool_id:
            title += f".{tool_id}"
        if artifact_id:
            title += f".{artifact_id}"
        timings.append({"title": title, "elapsed_ms": int(elapsed_ms)})

    async def _merge_with_pool(
        self,
        *,
        collections: List[List[Dict[str, Any]]],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        record_streamers: Optional[List[Any]] = None,
        timeline_streamer: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:

        params = {
            "source_collections": [list(self.ctx_browser.sources_pool or [])] + [c or [] for c in collections]
        }
        try:
            self.log.log(
                f"[react.v2] merge_sources: existing={len(self.ctx_browser.sources_pool or [])} "
                f"collections={[len(c or []) for c in collections]}",
                level="INFO",
            )
        except Exception:
            pass
        tr = await execute_tool(
            runtime_ctx=self.ctx_browser.runtime_ctx,
            tool_execution_context={"tool_id": "ctx_tools.merge_sources", "params": params},
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tools_subsystem,
            logger=self.log,
        )
        merged = tr.get("output")
        if not isinstance(merged, list):
            items = tr.get("items") or []
            first_item = next(iter(items), None)
            merged = first_item.get("output") if isinstance(first_item, dict) else None
        if not isinstance(merged, list):
            merged = []
        if merged:
            turn_id = self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser else ""
            if turn_id:
                for row in merged:
                    if isinstance(row, dict) and not row.get("turn_id"):
                        row["turn_id"] = turn_id

        if not merged and collections:
            self.log.log(
                f"[react.merge_sources] empty merge result; "
                f"inputs={[len(c or []) for c in collections]} "
                f"raw_type={type(merged).__name__}",
                level="WARNING",
            )
        else:
            self.ctx_browser.set_sources_pool(sources_pool=merged)
            try:
                self.log.log(
                    f"[react.v2] merge_sources: merged={len(merged)}",
                    level="INFO",
                )
            except Exception:
                pass
        if merged:
            try:
                mx = max(int(s.get("sid") or 0) for s in merged if isinstance(s, dict))
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
                    try:
                        next_sid = int(mx) + 1
                    except (TypeError, ValueError):
                        next_sid = 0
                    SOURCE_ID_CV.set({"next": next_sid})
                except Exception:
                    pass
                try:
                    for rs in (record_streamers or []):
                        if rs and hasattr(rs, "update_sources"):
                            rs.update_sources(self.ctx_browser.sources_pool if self.ctx_browser else [])
                except Exception:
                    pass
                try:
                    if timeline_streamer and hasattr(timeline_streamer, "update_sources"):
                        timeline_streamer.update_sources(self.ctx_browser.sources_pool if self.ctx_browser else [])
                except Exception:
                    pass
            except Exception:
                pass
        return merged

    async def _analyze_and_build_result(self, state: Dict[str, Any])-> ReactResult:

        outdir = pathlib.Path(state["outdir"])
        workdir = pathlib.Path(state["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        sources_pool = [s for s in (self.ctx_browser.sources_pool or []) if isinstance(s, dict)]
        sources_pool.sort(key=lambda s: int(s.get("sid") or 0))

        artifacts_out: List[Dict[str, Any]] = []
        try:
            if self.scratchpad is not None:
                react_state = ReactStateSnapshot.from_state(state)
                self.scratchpad.react_state = react_state.to_dict()
        except Exception:
            pass

        react_result = ReactResult(
            ok=True,
            out=artifacts_out,
            sources_pool=sources_pool,
            final_answer=(state.get("final_answer") or "").strip(),
            suggested_followups=state.get("suggested_followups") or [],
            error=state.get("error"),
            round_timings=state.get("round_timings") or [],
            total_runtime_sec=float(state.get("total_runtime_sec") or 0.0),
            run_id=state.get("session_id") or "",
            outdir=str(outdir),
            workdir=str(workdir),
        )

        (outdir / "result.json").write_text(
            json.dumps(react_result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return react_result
