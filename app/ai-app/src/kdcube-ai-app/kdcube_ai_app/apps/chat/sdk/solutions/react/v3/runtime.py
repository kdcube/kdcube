# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/runtime.py

import asyncio
import copy
import datetime
import os
import json
import pathlib
import random
import traceback

import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Callable, Awaitable, Type, Set

from langgraph.graph import StateGraph, END

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.ids import new_exec_id
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.infra import emit_event
from kdcube_ai_app.apps.chat.sdk.runtime.execution import execute_tool, _safe_label
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_call_context_patch,
    get_current_bundle_call_context,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import (
    parse_single_react_decision_from_channel_text,
    react_decision_stream_v2,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.live_events import (
    compute_reactive_iteration_credit_cap,
    resolve_reactive_iteration_credit,
    sync_reactive_iteration_budget,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    assistant_completion_texts,
    build_assistant_completion_attempt_blocks,
    build_tool_catalog,
    build_working_summary_attempt_blocks,
    record_assistant_completion_attempt,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.call import get_react_tools_catalog
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import ReactResult
from kdcube_ai_app.apps.chat.sdk.solutions.react.runtime_state import ReactRuntimeState as ReactStateV2
from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.canvas import (
    ReactPatchContentStreamer,
    ReactWriteContentStreamer,
    RenderingWriteContentStreamer,
    TimelineStreamer,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import (
    UNKNOWN_STRATEGY,
    normalize_tool_traits,
    strategies_compatible,
    strategy_values,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.action_overseer import (
    RoundActionOverseer,
)
from kdcube_ai_app.apps.chat.sdk.streaming.stream_policy import StreamPolicyViolation
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
import logging

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import apply_plan_updates
from kdcube_ai_app.apps.chat.sdk.solutions.react.round import ReactRound, emit_react_tool_rejected_event
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import ReactStateSnapshot


class ReactSolverV2:
    MODULE_AGENT_NAME = "solver.react.v2"
    MAX_ACTIONS_PER_ROUND = 2

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
        comm_context: ExternalEventPayload,
        hosting_service: Optional[ApplicationHostingService] = None,
        ctx_browser: Optional[ContextBrowser] = None,
        additional_instructions: Optional[str] = None,
        instruction_body: Optional[str] = None,
        instruction_blocks: Optional[List[str]] = None,
        include_tool_catalog: bool = True,
        include_skill_gallery: bool = True,
    ) -> None:
        self.svc = service
        if isinstance(logger, AgentLogger):
            self.log = logger
        else:
            name = getattr(logger, "name", None) or "react.v3"
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
        self.instruction_body = str(instruction_body or "").strip()
        if isinstance(instruction_blocks, str):
            self.instruction_blocks = [instruction_blocks] if instruction_blocks.strip() else []
        else:
            self.instruction_blocks = [str(item) for item in (instruction_blocks or []) if str(item or "").strip()]
        self.include_tool_catalog = bool(include_tool_catalog)
        self.include_skill_gallery = bool(include_skill_gallery)
        self.multi_action_mode = getattr(self.ctx_browser.runtime_ctx, "multi_action_mode", "off") if self.ctx_browser.runtime_ctx else "off"
        if self.ctx_browser is not None:
            try:
                self.ctx_browser.add_external_event_hook(self.on_external_event)
            except Exception:
                self.log.log("[react.v3] failed to register external event hook\n" + traceback.format_exc(), level="ERROR")
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
            str(getattr(event, "active_turn_id_at_ingress", "") or "").strip(),
            str(getattr(event, "owner_turn_id", "") or "").strip(),
        ]
        turn_ids = [item for item in turn_ids if item]
        if not turn_ids:
            return True
        return current_turn_id in set(turn_ids)

    async def on_external_event(self, *, type: str, event: Any, blocks: List[Dict[str, Any]]) -> bool:
        type_norm = str(type or "").strip().lower()
        if type_norm in {"steer", "followup"} and not self._event_targets_current_turn(event):
            try:
                self.log.log(
                    f"[react.v3] external_event ignored: type={type_norm} current_turn={self._current_turn_id()} "
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
                f"[react.v3] external_event accepted: type={type} event_id={event_id} seq={seq} "
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
            f"[react.v3] steer cancelling active {phase} task turn_id={self.scratchpad.turn_id}",
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
                f"[react.v3] phase={phase} failed to inspect external-event owner:\n{traceback.format_exc()}",
                level="ERROR",
            )
            return
        level = "INFO"
        if not owner_turn_id or owner_turn_id != current_turn_id or not listener_running:
            level = "WARNING"
        self.log.log(
            f"[react.v3] phase={phase} external watch state turn_id={current_turn_id} "
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
                    f"[react.v3] phase={phase} direct external-event watch received count={len(events)} turn_id={self.scratchpad.turn_id}",
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
                            handled = await self.on_external_event(
                                type=str(getattr(event, "kind", "external") or "external"),
                                event=event,
                                blocks=[],
                            )
                            changed += int(bool(handled))
                        except Exception:
                            self.log.log(
                                f"[react.v3] direct external-event dispatch failure phase={phase}: {traceback.format_exc()}",
                                level="ERROR",
                            )
                if changed and self._steer_interrupt_requested:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.log(
                    f"[react.v3] external-event phase watcher failure phase={phase}: {traceback.format_exc()}",
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
                    f"[react.v3] active {phase} cancelled by steer turn_id={self.scratchpad.turn_id}",
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
                type="event.external.accepted",
                step="event.external",
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
                    f"[react.v3] drained external events turn_id={self.scratchpad.turn_id} "
                    f"changed={changed} before_seq={before_seq} after_seq={current_seq}",
                    level="INFO",
                )
            return int(changed or 0)
        except Exception:
            self.log.log(f"[react.v3] external event drain failed: {traceback.format_exc()}", level="ERROR")
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
                    f"[react.v3] waited/drained external events turn_id={self.scratchpad.turn_id} "
                    f"changed={changed} before_seq={before_seq} after_seq={current_seq} block_ms={block_ms}",
                    level="INFO",
                )
            return int(changed or 0)
        except Exception:
            self.log.log(f"[react.v3] external event wait/drain failed: {traceback.format_exc()}", level="ERROR")
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
                f"[react.v3] marked external events consumed turn_id={self.scratchpad.turn_id} "
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_interrupted_generation_blocks

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
            self.log.log(f"[react.v3] failed to persist interrupted generation: {traceback.format_exc()}", level="ERROR")

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
            self.log.log(f"[react.v3] failed to mark steer event consumed: {traceback.format_exc()}", level="ERROR")
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
            f"[react.v3] steer finalize mode: turn_id={self.scratchpad.turn_id} "
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
            from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_announce_text
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
            #         self.log.log("[react.v3] refresh_feedbacks failed: " + traceback.format_exc(), level="ERROR")
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
                    self.log.log(f"[react.v3] announce:\n{active_block}", level="INFO")
                except Exception:
                    pass
            if debug_sources:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_sources_pool_text
                    sources_text = build_sources_pool_text(
                        sources_pool=list(self.ctx_browser.sources_pool or []),
                    )
                    if sources_text:
                        self.log.log(f"[react.v3] sources_pool:\n{sources_text}", level="INFO")
                except Exception:
                    pass
            announce_blocks = [{"text": active_block}]
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import event_source_pipeline_enabled
                from kdcube_ai_app.apps.chat.sdk.solutions.react.events.projection import produce_event_source_announce_blocks

                if event_source_pipeline_enabled(runtime_ctx):
                    announce_blocks.extend(produce_event_source_announce_blocks(
                        event_sources=getattr(runtime_ctx, "event_sources", None),
                        timeline_blocks=list(self.ctx_browser.timeline.blocks or []),
                        iteration=iteration,
                        max_iterations=max_iterations,
                        base_max_iterations=base_max_iterations,
                        reactive_iteration_credit=reactive_iteration_credit,
                        current_turn_id=str(getattr(runtime_ctx, "turn_id", "") or ""),
                    ))
            except Exception:
                pass
            self.ctx_browser.announce(blocks=announce_blocks)
        except Exception:
            pass

    def _build_graph(self) -> StateGraph:

        def failing_node(state: Dict[str, Any]):
            """Simulate a tool/API failure"""
            try:
                raise ValueError("PPTX file was not produced")
            except Exception as exc:
                tb = traceback.format_exc()
                raise RuntimeError(f"[react.v3] Error control {exc}/{tb}") from exc

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
        # In multi-action rounds the model may emit several <channel:thinking>
        # blocks interleaved with the actions. The versatile streamer assigns
        # each block a distinct channel_instance, but the wire delta strips
        # everything except (text, index, marker, agent, completed). To keep
        # the UI's per-agent aggregation from concatenating all instances into
        # one continuous block we suffix the agent with the instance index and
        # use a separate index counter per (marker, instance). Instance is None
        # in single-action paths; behaviour there is unchanged.
        counters: Dict[tuple[str, Optional[int]], int] = {}

        async def emit_delta(**kwargs):
            text = kwargs.get("text") or ""
            completed = bool(kwargs.get("completed"))
            if not text and not completed:
                return
            marker = kwargs.get("marker") or kwargs.get("channel") or "thinking"
            instance = kwargs.get("channel_instance")
            try:
                instance_i = int(instance) if instance is not None else None
            except Exception:
                instance_i = None
            key = (marker, instance_i)
            i = counters.get(key, 0)
            counters[key] = i + 1
            author = f"{self.MODULE_AGENT_NAME}.{phase}"
            if instance_i is not None:
                author = f"{author}.i{instance_i}"
            await self.comm.delta(
                text=text,
                index=i,
                marker=marker,
                agent=author,
                completed=completed,
            )

        return emit_delta

    def _mk_exec_code_streamer(
        self,
        phase: str,
        idx: int,
        execution_id: Optional[str] = None,
        emit_delta: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], DecisionExecCodeStreamer]:
        artifact_suffix = execution_id or str(idx)
        turn_id = ""
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
            turn_id = str(getattr(runtime_ctx, "turn_id", "") or "")
        except Exception:
            turn_id = ""
        if not turn_id:
            try:
                turn_id = str(getattr(self.scratchpad, "turn_id", "") or "")
            except Exception:
                turn_id = ""
        streamer = DecisionExecCodeStreamer(
            emit_delta=emit_delta or self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=f"react.exec.{artifact_suffix}",
            execution_id=execution_id,
            turn_id=turn_id,
        )
        return self._wrap_raw_streamer(streamer), streamer

    def _mk_content_streamers(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        artifact_name: Optional[str] = None,
        emit_delta: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> tuple[List[Callable[[str], Awaitable[None]]], List[Any]]:
        safe_name = artifact_name or f"react.record.{uuid.uuid4().hex[:8]}"
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        base_args = {
            "emit_delta": emit_delta or self.comm.delta,
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
        iteration: Optional[int] = None,
        emit_delta: Optional[Callable[..., Awaitable[None]]] = None,
        on_action_identity: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], TimelineStreamer]:
        sources_getter = None
        if self.ctx_browser:
            sources_getter = lambda: list(self.ctx_browser.sources_pool or [])
        # Each completion attempt is its own answer: restart the streamed delta
        # index at 0 so the client can tell two answers in the same turn apart
        # (a follow-up that produces a second final answer must render as a new
        # message, not append to the previous one). A monotonic index across the
        # turn made the client merge them. `_react_answer_delta_idx` is still
        # tracked below as a high-water mark but no longer seeds the start index.
        final_answer_start_index = 0
        self.scratchpad._latest_streamed_notes_started_at = None
        if stream_final_answer:
            self.scratchpad._latest_streamed_final_answer_started_at = None

        async def emit_timeline_delta(**kwargs):
            marker = str(kwargs.get("marker") or "")
            completed = bool(kwargs.get("completed"))
            text = str(kwargs.get("text") or "")
            artifact_name = str(kwargs.get("artifact_name") or "")
            started_at = datetime.datetime.utcnow().isoformat() + "Z"
            if (
                stream_final_answer
                and marker == "answer"
                and not completed
                and text
            ):
                current_started_at = getattr(self.scratchpad, "_latest_streamed_final_answer_started_at", None)
                if not current_started_at:
                    self.scratchpad._latest_streamed_final_answer_started_at = started_at
                if iteration is not None and artifact_name.startswith(str(final_answer_artifact_name or "")):
                    by_iteration = getattr(self.scratchpad, "_react_answer_started_at_by_iteration", None)
                    if not isinstance(by_iteration, dict):
                        by_iteration = {}
                        self.scratchpad._react_answer_started_at_by_iteration = by_iteration
                    by_iteration.setdefault(int(iteration), started_at)
            if (
                marker == "timeline_text"
                and not completed
                and text
                and iteration is not None
                and artifact_name.startswith(str(notes_artifact_name or ""))
            ):
                by_iteration = getattr(self.scratchpad, "_react_notes_started_at_by_iteration", None)
                if not isinstance(by_iteration, dict):
                    by_iteration = {}
                    self.scratchpad._react_notes_started_at_by_iteration = by_iteration
                by_iteration.setdefault(int(iteration), started_at)
                if not getattr(self.scratchpad, "_latest_streamed_notes_started_at", None):
                    self.scratchpad._latest_streamed_notes_started_at = started_at
            await (emit_delta or self.comm.delta)(**kwargs)

        streamer = TimelineStreamer(
            emit_delta=emit_timeline_delta,
            agent=agent or f"{self.MODULE_AGENT_NAME}.{phase}",
            sources_list=sources_list or [],
            sources_getter=sources_getter,
            stream_final_answer=bool(stream_final_answer),
            notes_artifact_name=notes_artifact_name or "timeline_text.react.decision",
            final_answer_artifact_name=final_answer_artifact_name or "react.final_answer",
            final_answer_start_index=final_answer_start_index,
            plan_artifact_name=plan_artifact_name or "timeline_text.react.plan",
            on_action_identity=on_action_identity,
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

    def _delta_cache_started_at(
        self,
        *,
        marker: str,
        artifact_name: Optional[str] = None,
        artifact_name_prefix: Optional[str] = None,
    ) -> Optional[str]:
        getter = getattr(self.comm, "get_delta_aggregates", None)
        if not callable(getter):
            return None
        turn_id = self._current_turn_id()
        if not turn_id:
            return None
        try:
            rows = getter(turn_id=turn_id, marker=marker, merge_text=False) or []
        except Exception:
            return None
        best_ts_ms: Optional[int] = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_artifact_name = str(row.get("artifact_name") or "")
            if artifact_name and row_artifact_name != artifact_name:
                continue
            if artifact_name_prefix and not row_artifact_name.startswith(artifact_name_prefix):
                continue
            try:
                ts_first = int(row.get("ts_first") or 0)
            except Exception:
                ts_first = 0
            if ts_first <= 0:
                continue
            if best_ts_ms is None or ts_first < best_ts_ms:
                best_ts_ms = ts_first
        if best_ts_ms is None:
            return None
        try:
            return datetime.datetime.fromtimestamp(
                best_ts_ms / 1000.0,
                tz=datetime.timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except Exception:
            return None

    async def _emit_react_action_step(
        self,
        *,
        iteration: int,
        action_index: int,
        bundle_size: int,
        decision: Dict[str, Any],
        tool_call_id: str = "",
        status: str = "completed",
    ) -> None:
        """Emit one frontend step per executed action so multi-action rounds surface every step.

        Step id is keyed on (iteration, action_index) so each action in a bundle is a distinct
        entity in the UI rather than overwriting a single round-level step. Fires AFTER the
        action's tool execution returns, so `status` reflects the real outcome.
        """
        try:
            iteration_i = int(iteration or 0)
            action_index_i = int(action_index or 0)
            bundle_size_i = max(1, int(bundle_size or 1))
            decision_payload: Dict[str, Any] = dict(decision) if isinstance(decision, dict) else {}
            tool_call = decision_payload.get("tool_call") if isinstance(decision_payload.get("tool_call"), dict) else {}
            tool_id = (tool_call.get("tool_id") or "").strip() if isinstance(tool_call, dict) else ""
            action_label = (decision_payload.get("action") or "").strip()
            label = tool_id or action_label or "action"
            if bundle_size_i > 1:
                title = f"ReAct Round ({iteration_i}). Action {action_index_i + 1}/{bundle_size_i}: {label}"
            else:
                title = f"ReAct Round ({iteration_i}). Action: {label}"
            if tool_call_id:
                decision_payload.setdefault("tool_call_id", tool_call_id)
            decision_payload.setdefault("action_index", action_index_i)
            decision_payload.setdefault("bundle_size", bundle_size_i)
            await emit_event(
                comm=self.comm,
                etype="solver.react.action",
                title=title,
                step=f"react({iteration_i}).action({action_index_i})",
                data=decision_payload,
                agent=f"solver.react({iteration_i}).action({action_index_i})",
                status=status or "completed",
            )
        except Exception:
            # Frontend emission must never break execution.
            try:
                self.log.log(traceback.format_exc(), level="ERROR")
            except Exception:
                pass

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
        if code == "final_answer_with_tool_call":
            return (
                "You used `action=call_tool` and also attached `final_answer` text. `final_answer` closes the turn — but the tool has not run yet, so the answer would be a guess. "
                "The tool action is kept, but the `final_answer` text is suppressed and not shown. Next: complete only after the tool result is visible."
            )
        if code == "tool_call_with_final_answer":
            return (
                "You emitted complete/exit with a `tool_call` still attached. Closing the turn and running a tool whose result you have not seen contradict each other. "
                "No action ran. Next: either emit the tool alone now, or drop `tool_call` and set `notes=\"\"`, `tool_call=null`."
            )
        if code == "final_answer_with_notes":
            return (
                "You emitted complete/exit with root `notes` populated. `notes` are progress strings for tool rounds; on the final round the user-facing message belongs in `final_answer`. "
                "Re-emit with notes empty."
            )
        if code == "missing_tool_id":
            return "tool_call.tool_id is missing for action=call_tool."
        if code == "missing_contract":
            return f"exec tool requires params.contract (tool_id={tool_id or 'unknown'})."
        if code == "tool_call_invalid":
            return f"tool_call failed protocol validation for tool_id={tool_id or 'unknown'}. No action was executed for this round."
        if code == "tool_signature_red":
            return f"tool params failed signature validation for tool_id={tool_id or 'unknown'}. No action was executed for this round."
        if code == "code_channel_with_multi_action":
            return (
                "You emitted non-empty `channel:code` with no `exec_tools.execute_code_python` action to bind it. Code only runs when paired with a single exec action. "
                "No tools were run. Next: add one complete exec action, or emit the round again with `channel:code` empty."
            )
        if code == "code_channel_exec_incomplete_with_multi_action":
            return (
                "You included an exec action in a multi-action round but it is incomplete — it needs both `params.contract` and Python in `channel:code`. "
                "No tools were run. Next: complete the exec, or run exec alone in its own round."
            )
        if code == "code_channel_without_single_exec":
            return (
                f"You emitted Python in `channel:code` but the action is `{tool_id or action or 'not an exec action'}`. `channel:code` only binds to one `exec_tools.execute_code_python` action. "
                "No action ran. Next: clear `channel:code`, or switch the action to exec with `params.contract`."
            )
        if code == "multi_action_bundle_unsafe_tool":
            unsafe_tool = str((extra or {}).get("tool_id") or tool_id or "that tool").strip()
            if (
                unsafe_tool in {"memory.record_memory", "memory.confirm_memory", "memory.retire_memory"}
                or unsafe_tool.endswith((".record_memory", ".confirm_memory", ".retire_memory"))
            ):
                return (
                    f"You bundled `{unsafe_tool}` with other actions, but this catalog did not expose its neutral strategy trait. "
                    f"That action was not run because the runtime could not prove same-round compatibility. Next: retry after the current action results are visible."
                )
            if tools_insights.is_exec_tool(unsafe_tool):
                return (
                    f"You bundled an incomplete `{unsafe_tool}` action with others (missing `params.contract` or `channel:code`). A partial exec cannot be paired safely. "
                    "That action was not run. Next: complete the exec with both, or run it alone."
                )
            return (
                f"You bundled `{unsafe_tool}` with other actions. Its result is not visible until the next round, so any sibling that depends on it would be guessing. "
                f"That action was not run. Next: emit `{unsafe_tool}` alone; depend on it next round."
            )
        if code == "multi_action_bundle_strategy_incompatible":
            rejected_tool = str((extra or {}).get("tool_id") or tool_id or "that tool").strip()
            first_tool = str((extra or {}).get("first_tool_id") or "the earlier action").strip()
            strategy = ", ".join(str(item) for item in ((extra or {}).get("strategy") or []) if str(item or "").strip())
            first_strategy = ", ".join(str(item) for item in ((extra or {}).get("first_strategy") or []) if str(item or "").strip())
            return (
                f"You bundled `{rejected_tool}` with `{first_tool}`, but their action strategies are incompatible for one ReAct round "
                f"({first_strategy or 'unknown'} vs {strategy or 'unknown'}). Tool results are only trustworthy after the round completes. "
                f"The incompatible action was not shown or run. Next: run one strategy group now, then use the result in a later round."
            )
        if code == "multi_action_bundle_final_answer_after_non_neutral":
            first_tool = str((extra or {}).get("first_tool_id") or "the earlier tool").strip()
            first_strategy = ", ".join(str(item) for item in ((extra or {}).get("first_strategy") or []) if str(item or "").strip())
            return (
                f"You emitted a final answer after `{first_tool}` in the same round, but that tool is non-neutral "
                f"({first_strategy or 'unknown'}). Its result is not available yet. "
                "The final answer was not shown. Next: run the tool now; answer after the result is visible."
            )
        if code == "multi_action_bundle_too_many_actions":
            max_actions = int((extra or {}).get("max_actions") or self.MAX_ACTIONS_PER_ROUND)
            return (
                f"You emitted more than {max_actions} actions in one ReAct round. "
                f"Action #{int((extra or {}).get('index') or 0) + 1} and any later actions were not shown or run. "
                f"Next: emit at most {max_actions} compatible actions in one round; continue with another round after their results are visible."
            )
        if code == "multi_action_bundle_non_neutral_after_final_answer":
            rejected_tool = str((extra or {}).get("tool_id") or tool_id or "that tool").strip()
            strategy = ", ".join(str(item) for item in ((extra or {}).get("strategy") or []) if str(item or "").strip())
            return (
                f"You emitted `{rejected_tool}` after a final-answer action in the same round, but that tool is non-neutral "
                f"({strategy or 'unknown'}). A non-neutral action would invalidate the answer already being closed. "
                "The tool action was not shown or run. Next: choose either the tool action, or finish with the final answer."
            )
        if code == "multi_action_bundle_mixed_actions":
            rejected_action = str((extra or {}).get("action") or "").strip() or "a non-tool action"
            return (
                f"You bundled tool calls with a `{rejected_action}` action (final answer / turn close). Closing the turn before the tools' results exist is a guess — you have not seen them yet. "
                f"The `{rejected_action}` action was dropped. Next: keep the tool actions; complete in a later round after their results are visible."
            )
        if code == "multi_action_bundle_final_answer_not_allowed":
            return (
                "You attached `final_answer` text to a tool-call round. `final_answer` closes the turn — it needs the tools' results, which appear only next round. "
                "The final answer was dropped. Next: emit the tools with `final_answer` empty; complete next round."
            )
        if code == "multi_action_bundle_invalid_item":
            return (
                "You emitted an action with malformed JSON. Without a parseable schema the runtime cannot route it. "
                "That action was dropped. Next: emit each action as its own valid `<channel:action>` block with one `tool_call`."
            )
        if code == "multi_action_bundle_too_small":
            return (
                "You emitted in multi-action shape but only one valid action parsed. The runtime cannot tell if the rest were malformed or dropped, so it skipped the bundle. "
                "No tools were run. Next: emit one valid action, or emit each independent action in its own `<channel:action>` block."
            )
        if code == "multi_action_bundle_same_file_collision":
            path = str((extra or {}).get("path") or "").strip() or "the same target file"
            return (
                f"You bundled two actions that target the same file (`{path}`) in this round. The runtime cannot guarantee ordering between them, and the second would be guessing at the state the first leaves behind. "
                "That action was dropped. Next: do the write/patch this round; emit any follow-up edit next round after seeing the result."
            )
        if code == "multi_action_bundle_render_consumes_same_round_source":
            ref = str((extra or {}).get("ref") or "").strip() or "a source produced in this same round"
            return (
                f"You bundled a renderer whose `ref:` points at `{ref}` — a file being written by another action in this same round. The render's input is not visible until the next round, so the renderer would consume an uncertain/incomplete file. "
                "The render was dropped. Next: write the source this round; render it next round after the `fi:` ref is visible."
            )
        if code == "action_schema_error":
            summary, _diagnostic = self._schema_error_diagnostics(error)
            return (
                "Malformed action JSON. <channel:action> could not be parsed, "
                f"so no action was executed for this round. Parser reported: {summary}"
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
        if code == "action_schema_error":
            return (
                "Wrong round. The action was malformed JSON, so this round executed no action. "
                "The protocol violation notice contains the parser error and diagnostic excerpt."
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
        if code == "code_channel_with_multi_action":
            return (
                "Wrong round. Non-empty `channel:code` was emitted while the round contained more than one action. "
                "`channel:code` can only be used in a multi-action round when exactly one action is "
                "exec_tools.execute_code_python and that exec action is complete. No tools were run."
            )
        if code == "code_channel_exec_incomplete_with_multi_action":
            return (
                "Wrong round. The round had one exec action and non-empty `channel:code`, but the exec action "
                "was not complete, so no tools were run."
            )
        if code == "code_channel_without_single_exec":
            return (
                "Wrong round. Code was emitted without a single exec_tools.execute_code_python action, "
                "so no action was executed."
            )
        if code.startswith("multi_action_bundle_"):
            return (
                "Wrong round. Multiple actions were emitted, but at least one action could not be run in this round. "
                "No partial action was executed; the protocol violation notice explains the exact correction."
            )
        return (
            "Wrong round. The agent violated the ReAct action protocol, "
            "so this round executed no action."
        )

    @staticmethod
    def _schema_error_diagnostics(error: Optional[str]) -> tuple[str, str]:
        raw = str(error or "").strip()
        if not raw:
            return "action parser did not provide details.", ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        summary = next((line for line in lines if line.startswith("JSON parse error:")), lines[0])
        detail_lines: List[str] = []
        include_following = 0
        for line in lines:
            if line.startswith(("Block length:", "Characters ", "Still failed")):
                detail_lines.append(line)
                include_following = 2 if line.startswith("Characters ") else 0
                continue
            if include_following > 0:
                detail_lines.append(line)
                include_following -= 1
                continue
        if not detail_lines and len(lines) > 1:
            detail_lines = lines[1:5]
        diagnostic = "\n".join(detail_lines).strip()
        if len(summary) > 500:
            summary = summary[:500].rstrip() + "..."
        if len(diagnostic) > 1800:
            diagnostic = diagnostic[:1800].rstrip() + "..."
        return summary, diagnostic

    async def _record_failed_decision_attempt(
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
        try:
            decision = None
            if isinstance(decision_packet, dict):
                decision = decision_packet.get("agent_response")
                if not isinstance(decision, dict):
                    bundle = decision_packet.get("agent_response_bundle")
                    if isinstance(bundle, list):
                        decision = next((item for item in bundle if isinstance(item, dict)), None)
            decision = decision if isinstance(decision, dict) else {}
            tool_call = decision.get("tool_call") if isinstance(decision.get("tool_call"), dict) else {}
            event_tool_id = (tool_id or tool_call.get("tool_id") or "").strip()
            await emit_react_tool_rejected_event(
                react=self,
                tool_id=event_tool_id,
                tool_call_id=tool_call_id,
                params=tool_call.get("params") if isinstance(tool_call, dict) else None,
                iteration=iteration,
                code=code,
                message=notice_message,
                extra={
                    "reason": reason or code,
                    "notice_code": notice_code,
                    **(notice_extra or {}),
                },
            )
        except Exception:
            pass

    async def _record_dropped_multi_action_items(
        self,
        *,
        iteration: int,
        parent_tool_call_id: str,
        rejected: List[Dict[str, Any]],
        decision_bundle: List[Dict[str, Any]],
        state: Dict[str, Any],
    ) -> None:
        if not self.ctx_browser or not rejected:
            return
        for item in rejected:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index") or 0)
            except Exception:
                idx = 0
            code = str(item.get("code") or "multi_action_item_rejected").strip()
            decision = decision_bundle[idx] if 0 <= idx < len(decision_bundle) and isinstance(decision_bundle[idx], dict) else {}
            extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
            if item.get("tool_id") and "tool_id" not in extra:
                extra = {**extra, "tool_id": item.get("tool_id")}
            tool_call = decision.get("tool_call") if isinstance(decision.get("tool_call"), dict) else {}
            event_tool_id = str(item.get("tool_id") or tool_call.get("tool_id") or "").strip()
            event_tool_call_id = str(tool_call.get("tool_call_id") or f"{parent_tool_call_id}.{idx}").strip()
            notice_message = self._protocol_violation_message(
                code=code,
                decision=decision,
                state=state,
                extra={**extra, "index": idx},
            )
            try:
                self.ctx_browser.contribute_notice(
                    code=f"protocol_violation.{code}",
                    message=f"Action #{idx + 1} was not run. {notice_message}",
                    extra={
                        "index": idx,
                        "parent_tool_call_id": parent_tool_call_id,
                        **({"tool_id": item.get("tool_id")} if item.get("tool_id") else {}),
                        **({"details": extra} if extra else {}),
                    },
                    call_id=parent_tool_call_id,
                    meta={
                        "rel": "call",
                        "iteration": iteration,
                        "partial_multi_action": True,
                    },
                )
            except Exception:
                pass
            try:
                await emit_react_tool_rejected_event(
                    react=self,
                    tool_id=event_tool_id,
                    tool_call_id=event_tool_call_id,
                    parent_tool_call_id=parent_tool_call_id,
                    params=tool_call.get("params") if isinstance(tool_call, dict) else None,
                    iteration=iteration,
                    index=idx,
                    code=code,
                    message=f"Action #{idx + 1} was not run. {notice_message}",
                    extra={**extra, "index": idx},
                )
            except Exception:
                pass

    def _record_suppressed_final_answer_items(
        self,
        *,
        iteration: int,
        parent_tool_call_id: str,
        suppressed: List[Dict[str, Any]],
        decision_bundle: List[Dict[str, Any]],
        state: Dict[str, Any],
    ) -> None:
        if not self.ctx_browser or not suppressed:
            return
        for item in suppressed:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index") or 0)
            except Exception:
                idx = 0
            code = str(item.get("code") or "final_answer_with_tool_call").strip()
            decision = decision_bundle[idx] if 0 <= idx < len(decision_bundle) and isinstance(decision_bundle[idx], dict) else {}
            extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
            if item.get("tool_id") and "tool_id" not in extra:
                extra = {**extra, "tool_id": item.get("tool_id")}
            notice_message = self._protocol_violation_message(
                code=code,
                decision=decision,
                state=state,
                extra={**extra, "index": idx},
            )
            try:
                self.ctx_browser.contribute_notice(
                    code=f"protocol_violation.{code}",
                    message=f"Action #{idx + 1} still ran. {notice_message}",
                    extra={
                        "index": idx,
                        "parent_tool_call_id": parent_tool_call_id,
                        "suppressed_field": "final_answer",
                        **({"tool_id": item.get("tool_id")} if item.get("tool_id") else {}),
                        **({"details": extra} if extra else {}),
                    },
                    call_id=parent_tool_call_id,
                    meta={
                        "rel": "call",
                        "iteration": iteration,
                        "partial_multi_action": True,
                        "field_suppressed": True,
                    },
                )
            except Exception:
                pass

    def _suppress_tool_call_final_answer(
        self,
        decision: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(decision, dict):
            return None
        if (decision.get("action") or "").strip() != "call_tool":
            return None
        final_answer = (decision.get("final_answer") or "").strip()
        if not final_answer:
            return None
        tool_call = decision.get("tool_call") if isinstance(decision.get("tool_call"), dict) else {}
        tool_id = (tool_call.get("tool_id") or "").strip() if isinstance(tool_call, dict) else ""
        decision["final_answer"] = ""
        return {
            "code": "final_answer_with_tool_call",
            **({"tool_id": tool_id} if tool_id else {}),
            "extra": {
                "suppressed_field": "final_answer",
                "suppressed_text_symbols": len(final_answer),
                **({"tool_id": tool_id} if tool_id else {}),
            },
        }

    def _record_suppressed_single_final_answer(
        self,
        *,
        iteration: int,
        tool_call_id: str,
        decision: Dict[str, Any],
        suppressed: Dict[str, Any],
        state: Dict[str, Any],
    ) -> None:
        if not self.ctx_browser or not tool_call_id:
            return
        extra = suppressed.get("extra") if isinstance(suppressed.get("extra"), dict) else {}
        code = str(suppressed.get("code") or "final_answer_with_tool_call").strip()
        tool_id = str(suppressed.get("tool_id") or extra.get("tool_id") or "").strip()
        try:
            self.ctx_browser.contribute_notice(
                code=f"protocol_violation.{code}",
                message=self._protocol_violation_message(
                    code=code,
                    decision=decision,
                    state=state,
                    extra=extra,
                ),
                extra={
                    "suppressed_field": "final_answer",
                    **({"tool_id": tool_id} if tool_id else {}),
                    **extra,
                },
                call_id=tool_call_id,
                meta={
                    "rel": "call",
                    "iteration": iteration,
                    "field_suppressed": True,
                    "tool_action_kept": True,
                },
            )
        except Exception:
            pass
        try:
            ReactRound.note(
                ctx_browser=self.ctx_browser,
                notes=(
                    "Protocol notice: final_answer text was suppressed because this round runs a tool. "
                    "The tool action is still executing; complete after the result is visible."
                ),
                tool_call_id=tool_call_id,
                tool_id=tool_id or "__protocol_violation__",
                action="call_tool",
                iteration=iteration,
            )
        except Exception:
            pass

    async def _record_dropped_action_parse_items(
        self,
        *,
        iteration: int,
        parent_tool_call_id: str,
        parse_errors: List[Dict[str, Any]],
    ) -> None:
        if not self.ctx_browser or not parse_errors:
            return
        for item in parse_errors:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index") or 0)
            except Exception:
                idx = 0
            error_text = str(item.get("error") or "malformed_action_json").strip()
            raw_preview = str(item.get("raw_preview") or "").strip()
            message = (
                f"Action #{idx + 1} was not run. One repeated action was malformed: "
                f"{error_text}. Next: emit that action as one valid <channel:action> JSON object, "
                "with exactly one tool_call per action."
            )
            try:
                self.ctx_browser.contribute_notice(
                    code="protocol_violation.multi_action_bundle_invalid_item",
                    message=message,
                    extra={
                        "index": idx,
                        "parent_tool_call_id": parent_tool_call_id,
                        "parser_error": error_text,
                        **({"raw_preview": raw_preview} if raw_preview else {}),
                    },
                    call_id=parent_tool_call_id,
                    meta={
                        "rel": "call",
                        "iteration": iteration,
                        "partial_multi_action": True,
                    },
                )
            except Exception:
                pass
            try:
                await emit_react_tool_rejected_event(
                    react=self,
                    tool_id="",
                    tool_call_id=f"{parent_tool_call_id}.{idx}",
                    parent_tool_call_id=parent_tool_call_id,
                    params=None,
                    iteration=iteration,
                    index=idx,
                    code="multi_action_bundle_invalid_item",
                    message=message,
                    extra={
                        "parser_error": error_text,
                        **({"raw_preview": raw_preview} if raw_preview else {}),
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
        final_answer = (decision.get("final_answer") or "").strip()
        if action == "call_tool" and final_answer:
            return "final_answer_with_tool_call"
        if action in {"complete", "exit"} and (decision.get("tool_call") or {}):
            return "tool_call_with_final_answer"
        if action in {"complete", "exit"} and not final_answer:
            return "final_answer_required"
        if action in {"complete", "exit"} and (decision.get("notes") or "").strip():
            return "final_answer_with_notes"
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

    @staticmethod
    def _working_summary_from_decision_packet(packet: Dict[str, Any]) -> str:
        if not isinstance(packet, dict):
            return ""
        channels = packet.get("channels") if isinstance(packet.get("channels"), dict) else {}
        summary_channel = channels.get("summary") if isinstance(channels, dict) else {}
        if isinstance(summary_channel, dict):
            text = str(summary_channel.get("text") or "").strip()
            if text:
                return text
        return str(packet.get("working_summary") or "").strip()

    def _decision_bundle_from_packet(self, packet: Dict[str, Any]) -> List[Dict[str, Any]]:
        bundle = packet.get("agent_response_bundle")
        if isinstance(bundle, list):
            out = [item for item in bundle if isinstance(item, dict)]
            if out:
                return out
        single = packet.get("agent_response")
        if isinstance(single, dict) and single:
            return [single]
        return []

    def _stream_policy_recovery_packet(
        self,
        *,
        decision_packet: Optional[Dict[str, Any]],
        decision_stream_instances: Dict[int, Dict[str, Any]],
        accepted_actions: List[Any],
        rejected_items: List[Dict[str, Any]],
        interrupted_raw_text: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Recover a safe decision packet after online policy rejected a later action.

        Some model providers log callback failures but keep streaming. In that
        case the post-stream parser may see the later denied `complete` action
        and return it as the final structured response. The overseer is the
        authoritative ordered-policy state, so rebuild the packet from actions
        it already accepted and attach denied items to packet.log.
        """
        if not rejected_items or not accepted_actions:
            return None

        accepted_bundle: List[Dict[str, Any]] = []
        for observed in accepted_actions:
            try:
                idx = int(getattr(observed, "index", None))
            except Exception:
                continue
            instance_state = decision_stream_instances.get(idx)
            if not isinstance(instance_state, dict):
                continue
            parsed_decision = instance_state.get("parsed_decision")
            if not isinstance(parsed_decision, dict):
                raw_text = "".join(instance_state.get("raw_chunks") or [])
                parsed_decision, parse_error = parse_single_react_decision_from_channel_text(raw_text)
                instance_state["parsed_decision"] = parsed_decision
                instance_state["parse_error"] = parse_error
            if isinstance(parsed_decision, dict):
                accepted_bundle.append(copy.deepcopy(parsed_decision))

        if not accepted_bundle:
            return None

        packet = copy.deepcopy(decision_packet) if isinstance(decision_packet, dict) else {}
        log = copy.deepcopy(packet.get("log")) if isinstance(packet.get("log"), dict) else {}
        log["error"] = None
        log["stream_policy_rejected_items"] = list(rejected_items)
        log["stream_policy_recovered"] = True
        raw_text = str(interrupted_raw_text or "").strip()
        if raw_text:
            log["stream_policy_interrupted_raw_len"] = len(raw_text)
            packet["raw"] = raw_text
        packet["agent_response"] = accepted_bundle[0]
        packet["agent_response_bundle"] = accepted_bundle
        packet["log"] = log
        return packet

    @staticmethod
    def _adapter_tool_traits(adapter: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(adapter, dict):
            return {}
        for source in (
            adapter.get("tool_traits"),
            (adapter.get("doc") or {}).get("tool_traits") if isinstance(adapter.get("doc"), dict) else None,
            (adapter.get("metadata") or {}).get("tool_traits") if isinstance(adapter.get("metadata"), dict) else None,
            ((adapter.get("doc") or {}).get("metadata") or {}).get("tool_traits")
            if isinstance(adapter.get("doc"), dict) and isinstance((adapter.get("doc") or {}).get("metadata"), dict)
            else None,
        ):
            if isinstance(source, dict) and source:
                return dict(source)
        return {}

    @staticmethod
    def _adapter_tool_traits_by_namespace(adapter: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        if not isinstance(adapter, dict):
            return {}
        for source in (
            adapter.get("tool_traits_by_namespace"),
            (adapter.get("doc") or {}).get("tool_traits_by_namespace") if isinstance(adapter.get("doc"), dict) else None,
            (adapter.get("metadata") or {}).get("tool_traits_by_namespace") if isinstance(adapter.get("metadata"), dict) else None,
            ((adapter.get("doc") or {}).get("metadata") or {}).get("tool_traits_by_namespace")
            if isinstance(adapter.get("doc"), dict) and isinstance((adapter.get("doc") or {}).get("metadata"), dict)
            else None,
        ):
            if not isinstance(source, dict):
                continue
            out: Dict[str, Dict[str, Any]] = {}
            for namespace, traits in source.items():
                ns = str(namespace or "").strip().lower().rstrip(":")
                normalized = normalize_tool_traits(traits)
                if ns and normalized:
                    out[ns] = normalized
            if out:
                return out
        return {}

    @staticmethod
    def _namespace_from_tool_params(tool_params: Optional[Dict[str, Any]]) -> str:
        if not isinstance(tool_params, dict):
            return ""
        namespace = str(tool_params.get("namespace") or "").strip().lower().rstrip(":")
        if not namespace:
            return ""
        return namespace.split(":", 1)[0].strip()

    @staticmethod
    def _react_native_tool_traits(tool_id: str) -> Dict[str, Any]:
        tid = str(tool_id or "").strip()
        if not tid:
            return {}
        try:
            for spec in get_react_tools_catalog():
                if str(spec.get("id") or "").strip() == tid and isinstance(spec.get("tool_traits"), dict):
                    return dict(spec.get("tool_traits") or {})
        except Exception:
            return {}
        return {}

    def _tool_traits_for_id(
        self,
        tool_id: str,
        *,
        adapters_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tid = str(tool_id or "").strip()
        if not tid:
            return {}
        adapter = (adapters_by_id or {}).get(tid) or {}
        namespace = self._namespace_from_tool_params(tool_params)
        if namespace:
            traits_by_namespace = self._adapter_tool_traits_by_namespace(adapter)
            namespace_traits = traits_by_namespace.get(str(tool_params.get("namespace") or "").strip().lower().rstrip(":"))
            if not namespace_traits:
                namespace_traits = traits_by_namespace.get(namespace)
            if namespace_traits:
                return namespace_traits
        adapter_traits = self._adapter_tool_traits(adapter)
        if adapter_traits:
            return adapter_traits
        return self._react_native_tool_traits(tid)

    def _tool_strategy_values(
        self,
        tool_id: str,
        *,
        adapters_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_params: Optional[Dict[str, Any]] = None,
    ) -> Set[str]:
        return set(strategy_values(self._tool_traits_for_id(
            tool_id,
            adapters_by_id=adapters_by_id,
            tool_params=tool_params,
        )))

    def _is_known_multi_action_tool(
        self,
        tool_id: str,
        *,
        adapters_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        strategies = self._tool_strategy_values(
            tool_id,
            adapters_by_id=adapters_by_id,
            tool_params=tool_params,
        )
        return bool(strategies)

    @staticmethod
    def _bundle_collision_key(raw: str) -> str:
        """Canonicalize a path or ref: value for cross-action collision/dependency detection.

        Accepts:
          - physical path: 'turn_<id>/files/foo.md', 'turn_<id>/outputs/foo.md'
          - logical ref:   'fi:turn_<id>.files/foo.md', 'fi:turn_<id>.outputs/foo.md'
          - param ref:     'ref:<either form>'
        Returns lowercase canonical 'turn_<id>/<namespace>/<rel>' string, or '' if not recognizable.
        """
        s = str(raw or "").strip()
        if not s:
            return ""
        if s.startswith("ref:"):
            s = s[4:].strip()
        if s.startswith("fi:"):
            body = s[3:]
            for marker, replacement in (
                (".files/", "/files/"),
                (".outputs/", "/outputs/"),
                (".user.attachments/", "/attachments/"),
            ):
                if marker in body:
                    body = body.replace(marker, replacement, 1)
                    break
            s = body
        return s.lstrip("/").lower()

    @classmethod
    def _action_output_paths(cls, decision: Dict[str, Any]) -> Set[str]:
        """Set of normalized output paths a single action will produce/edit, for collision checks."""
        if not isinstance(decision, dict):
            return set()
        tc = decision.get("tool_call") if isinstance(decision.get("tool_call"), dict) else {}
        tool_id = str(tc.get("tool_id") or "").strip()
        params = tc.get("params") if isinstance(tc.get("params"), dict) else {}
        out: Set[str] = set()
        if tool_id in {"react.write", "react.patch"} or tool_id.startswith("rendering_tools.write_"):
            key = cls._bundle_collision_key(params.get("path") or "")
            if key:
                out.add(key)
        elif tools_insights.is_exec_tool(tool_id):
            contract = params.get("contract") if isinstance(params.get("contract"), list) else []
            for entry in contract:
                if isinstance(entry, dict):
                    key = cls._bundle_collision_key(entry.get("filename") or "")
                    if key:
                        out.add(key)
        return out

    @classmethod
    def _action_render_refs(cls, decision: Dict[str, Any]) -> Set[str]:
        """Set of normalized ref: paths a renderer action consumes (empty for non-renderers)."""
        if not isinstance(decision, dict):
            return set()
        tc = decision.get("tool_call") if isinstance(decision.get("tool_call"), dict) else {}
        tool_id = str(tc.get("tool_id") or "").strip()
        if not tool_id.startswith("rendering_tools.write_"):
            return set()
        params = tc.get("params") if isinstance(tc.get("params"), dict) else {}
        out: Set[str] = set()
        content = params.get("content")
        if isinstance(content, str) and content.startswith("ref:"):
            key = cls._bundle_collision_key(content)
            if key:
                out.add(key)
        return out

    def _multi_action_enabled(self) -> bool:
        return (self.multi_action_mode or "").strip().lower() in {"on", "true", "1", "yes", "safe_fanout", "fanout"}

    def _validate_decision_packet_channel_consistency(
        self,
        *,
        packet: Dict[str, Any],
        bundle: List[Dict[str, Any]],
        exec_streamer: Optional[Any] = None,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        channels = packet.get("channels") if isinstance(packet.get("channels"), dict) else {}
        code_text = ""
        if isinstance(channels, dict):
            code_chan = channels.get("code") if isinstance(channels.get("code"), dict) else {}
            code_text = (code_chan.get("text") or "").strip() if isinstance(code_chan, dict) else ""
        if not code_text:
            return None, None
        if len(bundle) != 1:
            # Multi-action rounds are validated per action. A non-empty code
            # channel is consumed only by one complete exec action; otherwise
            # exec actions are dropped while independent actions can still run.
            return None, None
        decision = bundle[0] if bundle else {}
        action = (decision.get("action") or "").strip()
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        if action != "call_tool" or not tools_insights.is_exec_tool(tool_id):
            return "code_channel_without_single_exec", {"action": action, "tool_id": tool_id}
        return None, None

    @staticmethod
    def _exec_indices_in_bundle(bundle: List[Dict[str, Any]]) -> List[int]:
        indices: List[int] = []
        for idx, item in enumerate(bundle or []):
            if not isinstance(item, dict):
                continue
            action = (item.get("action") or "").strip()
            tool_call = item.get("tool_call") if isinstance(item.get("tool_call"), dict) else {}
            tool_id = (tool_call.get("tool_id") or "").strip()
            if action == "call_tool" and tools_insights.is_exec_tool(tool_id):
                indices.append(idx)
        return indices

    @staticmethod
    def _exec_streamer_is_complete(exec_streamer: Optional[Any]) -> bool:
        if exec_streamer is None:
            return False
        try:
            is_complete = getattr(exec_streamer, "is_complete", None)
            if callable(is_complete):
                return bool(is_complete())
        except Exception:
            return False
        try:
            get_code = getattr(exec_streamer, "get_code", None)
            code = get_code() if callable(get_code) else ""
        except Exception:
            code = ""
        try:
            has_contract = getattr(exec_streamer, "has_contract", None)
            contract_ok = bool(has_contract()) if callable(has_contract) else bool(getattr(exec_streamer, "pending_contract", None))
        except Exception:
            contract_ok = False
        return bool(str(code or "").strip()) and contract_ok

    async def _prepare_safe_multi_action_bundle(
        self,
        *,
        bundle: List[Dict[str, Any]],
        adapters_by_id: Dict[str, Dict[str, Any]],
        allow_single_exec_with_code: bool = False,
    ) -> tuple[List[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:
        if len(bundle) < 2:
            return [], "multi_action_bundle_too_small", {"count": len(bundle)}
        accepted: List[Dict[str, Any]] = []
        accepted_idxs: List[int] = []
        rejected: List[Dict[str, Any]] = []
        suppressed: List[Dict[str, Any]] = []
        final_decision: Optional[Dict[str, Any]] = None
        final_idx: Optional[int] = None
        exec_indices = self._exec_indices_in_bundle(bundle)
        max_actions = int(getattr(self, "MAX_ACTIONS_PER_ROUND", 2) or 2)

        def _reject(idx: int, code: str, extra: Optional[Dict[str, Any]] = None, decision: Optional[Dict[str, Any]] = None) -> None:
            tool_call = (decision or {}).get("tool_call") if isinstance(decision, dict) else {}
            tool_id = (tool_call.get("tool_id") or "").strip() if isinstance(tool_call, dict) else ""
            rejected.append({
                "index": idx,
                "code": code,
                **({"tool_id": tool_id} if tool_id else {}),
                **({"extra": extra} if extra else {}),
            })

        for idx, item in enumerate(bundle):
            decision = copy.deepcopy(item) if isinstance(item, dict) else {}
            if not isinstance(decision, dict):
                _reject(idx, "multi_action_bundle_invalid_item")
                continue
            if idx >= max_actions:
                _reject(
                    idx,
                    "multi_action_bundle_too_many_actions",
                    {"index": idx, "max_actions": max_actions},
                    decision,
                )
                continue
            action = (decision.get("action") or "").strip()
            if action in {"complete", "exit"}:
                validation_error = self._validate_decision(decision)
                if validation_error:
                    _reject(idx, validation_error, None, decision)
                    continue
                if final_decision is None:
                    final_decision = decision
                    final_idx = idx
                else:
                    _reject(idx, "multi_action_bundle_mixed_actions", {"action": action}, decision)
                continue
            if action != "call_tool":
                _reject(idx, "multi_action_bundle_mixed_actions", {"action": action}, decision)
                continue
            suppressed_item = self._suppress_tool_call_final_answer(decision)
            if suppressed_item is not None:
                suppressed.append({"index": idx, **suppressed_item})
            validation_error = self._validate_decision(decision)
            if validation_error:
                _reject(idx, validation_error, None, decision)
                continue
            tool_call = decision.get("tool_call") or {}
            tool_id = (tool_call.get("tool_id") or "").strip()
            tool_params = tool_call.get("params") if isinstance(tool_call.get("params"), dict) else {}
            exec_complete_in_bundle = (
                allow_single_exec_with_code
                and len(exec_indices) == 1
                and idx == exec_indices[0]
                and tools_insights.is_exec_tool(tool_id)
            )
            if tools_insights.is_exec_tool(tool_id) and not exec_complete_in_bundle:
                _reject(idx, "multi_action_bundle_unsafe_tool", {"tool_id": tool_id}, decision)
                continue
            if accepted and not self._is_known_multi_action_tool(
                tool_id,
                adapters_by_id=adapters_by_id,
                tool_params=tool_params,
            ):
                _reject(idx, "multi_action_bundle_unsafe_tool", {
                    "tool_id": tool_id,
                    "strategy": sorted(self._tool_strategy_values(
                        tool_id,
                        adapters_by_id=adapters_by_id,
                        tool_params=tool_params,
                    ) or {UNKNOWN_STRATEGY}),
                }, decision)
                continue
            verdict = self._validate_tool_call_protocol(
                tool_call=tool_call,
                adapters_by_id=adapters_by_id,
            )
            if not verdict.get("ok"):
                _reject(idx, "tool_call_invalid", {
                    "index": idx,
                    "tool_id": tool_id,
                    "violations": verdict.get("violations") or [],
                }, decision)
                continue
            filtered_params = tool_call.get("params") or {}
            if tool_id and not tool_id.startswith("react.") and self.tools_subsystem is not None:
                try:
                    tv = await self.tools_subsystem.validate_tool_params(tool_id=tool_id, params=filtered_params)
                except Exception:
                    tv = {}
                sig_status = tv.get("status")
                if sig_status == "red":
                    _reject(idx, "tool_signature_red", {
                        "index": idx,
                        "tool_id": tool_id,
                        "issues": tv.get("issues") or [],
                    }, decision)
                    continue
                filtered_params = tv.get("params") or filtered_params
            if isinstance(tool_call, dict):
                tool_call["params"] = filtered_params
                decision["tool_call"] = tool_call
            accepted.append(decision)
            accepted_idxs.append(idx)

        # --- Strategy compatibility checks ---
        if len(accepted) > 1:
            drop_idxs: Set[int] = set()
            decisions_by_idx = {
                a_idx: a_decision
                for a_idx, a_decision in zip(accepted_idxs, accepted)
            }
            for pos, (a_idx, a_decision) in enumerate(zip(accepted_idxs, accepted)):
                if a_idx in drop_idxs:
                    continue
                tc = a_decision.get("tool_call") if isinstance(a_decision.get("tool_call"), dict) else {}
                tool_id = str(tc.get("tool_id") or "").strip()
                tool_params = tc.get("params") if isinstance(tc.get("params"), dict) else {}
                traits = self._tool_traits_for_id(
                    tool_id,
                    adapters_by_id=adapters_by_id,
                    tool_params=tool_params,
                )
                strategies = sorted(strategy_values(traits) or {UNKNOWN_STRATEGY})
                for prev_idx, prev_decision in zip(accepted_idxs[:pos], accepted[:pos]):
                    if prev_idx in drop_idxs:
                        continue
                    prev_tc = prev_decision.get("tool_call") if isinstance(prev_decision.get("tool_call"), dict) else {}
                    prev_tool_id = str(prev_tc.get("tool_id") or "").strip()
                    prev_params = prev_tc.get("params") if isinstance(prev_tc.get("params"), dict) else {}
                    prev_traits = self._tool_traits_for_id(
                        prev_tool_id,
                        adapters_by_id=adapters_by_id,
                        tool_params=prev_params,
                    )
                    if strategies_compatible(prev_traits, traits):
                        continue
                    drop_idxs.add(a_idx)
                    _reject(
                        a_idx,
                        "multi_action_bundle_strategy_incompatible",
                        {
                            "tool_id": tool_id,
                            "strategy": strategies,
                            "first_index": prev_idx,
                            "first_tool_id": prev_tool_id,
                            "first_strategy": sorted(strategy_values(prev_traits) or {UNKNOWN_STRATEGY}),
                        },
                        decisions_by_idx.get(a_idx),
                    )
                    break
            if drop_idxs:
                accepted = [
                    a_decision
                    for a_idx, a_decision in zip(accepted_idxs, accepted)
                    if a_idx not in drop_idxs
                ]
                accepted_idxs = [
                    a_idx
                    for a_idx in accepted_idxs
                    if a_idx not in drop_idxs
                ]

        # --- Cross-action causality checks ---
        # Catch (a) two accepted actions targeting the same file (collision), and
        # (b) a renderer whose `ref:` points at a path produced by another action in the same round.
        if len(accepted) > 1:
            output_paths_by_idx: Dict[int, Set[str]] = {}
            render_refs_by_idx: Dict[int, Set[str]] = {}
            decisions_by_idx: Dict[int, Dict[str, Any]] = {}
            for a_idx, a_decision in zip(accepted_idxs, accepted):
                decisions_by_idx[a_idx] = a_decision
                outputs = self._action_output_paths(a_decision)
                if outputs:
                    output_paths_by_idx[a_idx] = outputs
                refs = self._action_render_refs(a_decision)
                if refs:
                    render_refs_by_idx[a_idx] = refs

            drop_idxs: Set[int] = set()

            # (a) Same-file collisions: any two accepted actions producing/editing the same canonical path.
            path_owner: Dict[str, int] = {}
            for a_idx in accepted_idxs:
                if a_idx in drop_idxs:
                    continue
                for p in output_paths_by_idx.get(a_idx, ()):
                    if p in path_owner:
                        drop_idxs.add(a_idx)
                        _reject(
                            a_idx,
                            "multi_action_bundle_same_file_collision",
                            {"path": p, "first_index": path_owner[p]},
                            decisions_by_idx.get(a_idx),
                        )
                        break
                    path_owner[p] = a_idx

            # (b) Render-consumes-same-round-source: any renderer whose ref ∈ another action's outputs.
            for a_idx, refs in render_refs_by_idx.items():
                if a_idx in drop_idxs:
                    continue
                matched: Optional[tuple[str, int]] = None
                for ref in refs:
                    for other_idx, outputs in output_paths_by_idx.items():
                        if other_idx == a_idx:
                            continue
                        if ref in outputs:
                            matched = (ref, other_idx)
                            break
                    if matched is not None:
                        break
                if matched is not None:
                    drop_idxs.add(a_idx)
                    _reject(
                        a_idx,
                        "multi_action_bundle_render_consumes_same_round_source",
                        {"ref": matched[0], "source_index": matched[1]},
                        decisions_by_idx.get(a_idx),
                    )

            if drop_idxs:
                new_accepted: List[Dict[str, Any]] = []
                new_accepted_idxs: List[int] = []
                for a_idx, a_decision in zip(accepted_idxs, accepted):
                    if a_idx in drop_idxs:
                        continue
                    new_accepted.append(a_decision)
                    new_accepted_idxs.append(a_idx)
                accepted = new_accepted
                accepted_idxs = new_accepted_idxs

        final_extra: Dict[str, Any] = {}
        if final_decision is not None and final_idx is not None:
            if any(a_idx > final_idx for a_idx in accepted_idxs):
                drop_idxs: Set[int] = set()
                decisions_by_idx = {
                    a_idx: a_decision
                    for a_idx, a_decision in zip(accepted_idxs, accepted)
                }
                for a_idx, a_decision in zip(accepted_idxs, accepted):
                    if a_idx <= final_idx:
                        continue
                    tc = a_decision.get("tool_call") if isinstance(a_decision.get("tool_call"), dict) else {}
                    tid = str(tc.get("tool_id") or "").strip()
                    tool_params = tc.get("params") if isinstance(tc.get("params"), dict) else {}
                    strategies = self._tool_strategy_values(
                        tid,
                        adapters_by_id=adapters_by_id,
                        tool_params=tool_params,
                    )
                    if strategies == {"neutral"}:
                        continue
                    drop_idxs.add(a_idx)
                    _reject(
                        a_idx,
                        "multi_action_bundle_non_neutral_after_final_answer",
                        {
                            "tool_id": tid,
                            "strategy": sorted(strategies or {UNKNOWN_STRATEGY}),
                            "first_index": final_idx,
                            "first_action": str(final_decision.get("action") or "complete").strip() or "complete",
                        },
                        decisions_by_idx.get(a_idx),
                    )
                if drop_idxs:
                    accepted = [
                        a_decision
                        for a_idx, a_decision in zip(accepted_idxs, accepted)
                        if a_idx not in drop_idxs
                    ]
                    accepted_idxs = [
                        a_idx
                        for a_idx in accepted_idxs
                        if a_idx not in drop_idxs
                    ]

            non_neutral: Optional[tuple[int, str, Set[str]]] = None
            for a_idx, a_decision in zip(accepted_idxs, accepted):
                if a_idx > final_idx:
                    continue
                tc = a_decision.get("tool_call") if isinstance(a_decision.get("tool_call"), dict) else {}
                tid = str(tc.get("tool_id") or "").strip()
                tool_params = tc.get("params") if isinstance(tc.get("params"), dict) else {}
                strategies = self._tool_strategy_values(
                    tid,
                    adapters_by_id=adapters_by_id,
                    tool_params=tool_params,
                )
                if strategies != {"neutral"}:
                    non_neutral = (a_idx, tid, strategies or {UNKNOWN_STRATEGY})
                    break
            if non_neutral is not None:
                _reject(
                    final_idx,
                    "multi_action_bundle_final_answer_after_non_neutral",
                    {
                        "first_index": non_neutral[0],
                        "first_tool_id": non_neutral[1],
                        "first_strategy": sorted(non_neutral[2]),
                    },
                    final_decision,
                )
            else:
                final_extra = {"final_decision": final_decision, "final_index": final_idx}

        if accepted or final_extra:
            extra: Dict[str, Any] = {}
            if rejected:
                extra["rejected"] = rejected
            if suppressed:
                extra["suppressed"] = suppressed
            extra.update(final_extra)
            return accepted, None, (extra or None)
        first = rejected[0] if rejected else {}
        return [], str(first.get("code") or "multi_action_bundle_no_valid_actions"), {
            "rejected": rejected,
            **({"suppressed": suppressed} if suppressed else {}),
        }

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
                "react.rg",
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
            "react.rg",
        }:
            if tool_id == "react.read":
                allowed_params.update({"paths", "items", "max_text_symbols", "line_numbers", "stats_only"})
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
            elif tool_id == "react.rg":
                allowed_params.update({"root", "name_regex", "pattern", "context_lines", "max_bytes", "max_matches", "max_files"})
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
                if not isinstance(params.get("paths"), list) and not isinstance(params.get("items"), list):
                    violations.append({
                        "code": "bad_params",
                        "message": "react.read params.paths or params.items must be a list",
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
        allowed_tool_names_by_alias: Dict[str, Any] | None = None,
    ) -> ReactStateV2:
        outdir = pathlib.Path(self.ctx_browser.runtime_ctx.outdir or "")
        workdir = pathlib.Path(self.ctx_browser.runtime_ctx.workdir or "")
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_active_skills_subsystem
            if self.skills_subsystem:
                set_active_skills_subsystem(self.skills_subsystem)
        except Exception:
            pass

        session_id = f"react-v3-{uuid.uuid4().hex[:8]}"
        turn_id = self.ctx_browser.runtime_ctx.turn_id
        self.log.log(f"[react.v3] Start {session_id} in {workdir}")

        adapters = await self.tools_subsystem.react_tools(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,
            allowed_tool_names_by_alias=allowed_tool_names_by_alias,
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

    @contextmanager
    def _bind_runtime_role_models(self):
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
        agent_role_models = getattr(runtime_ctx, "agent_role_models", None)
        if not isinstance(agent_role_models, dict) or not agent_role_models:
            yield
            return
        existing = get_current_bundle_call_context().get("role_models") or {}
        merged: Dict[str, Any] = {**agent_role_models, **existing}
        with bind_current_bundle_call_context_patch({"role_models": merged}):
            yield

    async def run(
        self,
        *,
        allowed_plugins: List[str],
        allowed_tool_names_by_alias: Dict[str, Any] | None = None,
    ):
        with self._bind_runtime_role_models():
            return await self._run_impl(
                allowed_plugins=allowed_plugins,
                allowed_tool_names_by_alias=allowed_tool_names_by_alias,
            )

    async def _run_impl(
        self,
        *,
        allowed_plugins: List[str],
        allowed_tool_names_by_alias: Dict[str, Any] | None = None,
    ):
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent=self.MODULE_AGENT_NAME,
        )
        await turn_status.send(random.choice(["executing", "working", "solving"]))

        state = await self.prepare_session(
            allowed_plugins=allowed_plugins,
            allowed_tool_names_by_alias=allowed_tool_names_by_alias,
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
                self.log.log(f"[react.v3] Graph error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise RuntimeError(f"[react.v3] Graph error: {exc}") from exc
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
                if p.name == "workdir" and p.parent.name == "out" and p.parent.parent.name:
                    return p.parent.parent.name
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
                self.log.log(f"[react.v3] persist_workspace failed: {exc}", level="ERROR")
            except Exception:
                pass
            return None

    def _route_after_decision(self, state: Dict[str, Any]) -> str:
        if state.get("exit_reason"):
            try:
                self.log.log(f"[react.v3] route=exit exit_reason={state.get('exit_reason')}", level="INFO")
            except Exception:
                pass
            return "exit"
        if state.get("retry_decision"):
            try:
                self.log.log("[react.v3] retry_decision=True -> route=decision", level="INFO")
            except Exception:
                pass
            state["retry_decision"] = False
            return "decision"
        decision = state.get("last_decision") or {}
        action = decision.get("action")
        if action == "call_tool":
            try:
                self.log.log("[react.v3] route=tool_execution", level="INFO")
            except Exception:
                pass
            return "tool_execution"
        try:
            self.log.log(f"[react.v3] route=exit action={action}", level="INFO")
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
                self.log.log(f"[react.v3] decision_node error: {exc}\n{tb}", level="ERROR")
            except Exception:
                pass
            raise

    async def _decision_node_impl(self, state: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        state.setdefault("session_log", [])
        state.setdefault("round_timings", [])
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
        mainstream = self._mk_mainstream(f"decision ({iteration})")
        pending_tool_call_id = f"tc_{uuid.uuid4().hex[:12]}"
        exec_id = new_exec_id()
        exec_streamer_idx = self._next_tool_streamer_idx(
            pathlib.Path(state["outdir"]),
            "exec_tools.execute_code_python",
        )
        sources_list = []
        try:
            if self.ctx_browser:
                sources_list = list(self.ctx_browser.sources_pool or [])
        except Exception:
            sources_list = []
        timeline_agent_prefix = f"{role}.timeline.{state.get('turn_id') or ''}.{iteration}"
        decision_stream_instances: Dict[int, Dict[str, Any]] = {}
        record_streamers: List[Any] = []
        timeline_streamer: Optional[Any] = None
        code_stream_state: Dict[str, Any] = {
            "buffer": [],
            "json_attached": False,
            "blocked": False,
            "exec_instance_idx": None,
            "pending_exec_instance": None,
            "bound_exec_instance": None,
            "code_started": False,
        }
        adapters_by_id_for_stream = self._adapters_index(state.get("adapters") or [])
        action_overseer = RoundActionOverseer(
            resolve_traits=lambda tool_id, tool_params=None: self._tool_traits_for_id(
                tool_id,
                adapters_by_id=adapters_by_id_for_stream,
                tool_params=dict(tool_params or {}) if isinstance(tool_params, dict) else None,
            ),
            max_actions=self.MAX_ACTIONS_PER_ROUND,
        )

        async def _exec_emit_delta(**kwargs: Any) -> None:
            instance_state = code_stream_state.get("bound_exec_instance")
            if not isinstance(instance_state, dict):
                instance_state = code_stream_state.get("pending_exec_instance")
            gate = instance_state.get("gate") if isinstance(instance_state, dict) else None
            if gate is not None and hasattr(gate, "emit_delta"):
                await gate.emit_delta(**kwargs)
                return
            await self.comm.delta(**kwargs)

        try:
            exec_streamer_fn, exec_streamer_widget = self._mk_exec_code_streamer(
                f"decision ({iteration})",
                exec_streamer_idx,
                execution_id=exec_id,
                emit_delta=_exec_emit_delta,
            )
        except TypeError as exc:
            if "emit_delta" not in str(exc):
                raise
            exec_streamer_fn, exec_streamer_widget = self._mk_exec_code_streamer(
                f"decision ({iteration})",
                exec_streamer_idx,
                execution_id=exec_id,
            )

        async def _attach_exec_json_if_ready(instance_state: Dict[str, Any]) -> bool:
            if exec_streamer_widget is None or code_stream_state["blocked"]:
                return False
            if code_stream_state["json_attached"]:
                return code_stream_state.get("exec_instance_idx") == (instance_state.get("instance_idx") or 0)
            parsed_decision = instance_state.get("parsed_decision")
            if not isinstance(parsed_decision, dict):
                return False
            tool_call = parsed_decision.get("tool_call") or {}
            tool_id = (tool_call.get("tool_id") or "").strip()
            if (parsed_decision.get("action") or "").strip() != "call_tool" or not tools_insights.is_exec_tool(tool_id):
                return False
            raw_json = "".join(instance_state.get("raw_chunks") or [])
            if raw_json:
                await exec_streamer_widget.feed_json(
                    text=raw_json,
                    completed=True,
                    channel_instance=instance_state.get("instance_idx") or 0,
                )
            else:
                return False
            code_stream_state["json_attached"] = True
            code_stream_state["exec_instance_idx"] = instance_state.get("instance_idx") or 0
            return True

        def _ensure_decision_stream_instance(instance_idx: int) -> Dict[str, Any]:
            nonlocal timeline_streamer
            safe_idx = max(0, int(instance_idx or 0))
            existing = decision_stream_instances.get(safe_idx)
            if existing is not None:
                return existing

            artifact_suffix = f"{pending_tool_call_id}.i{safe_idx}"
            action_gate = action_overseer.gate_for(action_index=safe_idx, emit_delta=self.comm.delta, lane="action")
            answer_gate = action_overseer.gate_for(action_index=safe_idx, emit_delta=self.comm.delta, lane="final_answer")

            async def _timeline_emit_delta(**kwargs: Any) -> None:
                marker = str(kwargs.get("marker") or "")
                if marker == "answer":
                    await answer_gate.emit_delta(**kwargs)
                    return
                await action_gate.emit_delta(**kwargs)

            async def _report_action_identity(
                action: str,
                tool_id: str,
                tool_params: Optional[Dict[str, Any]] = None,
            ) -> None:
                await action_overseer.observe_action_signal(
                    action_index=safe_idx,
                    action=action,
                    tool_id=tool_id,
                    action_gate=action_gate,
                    answer_gate=answer_gate,
                    tool_params=tool_params,
                )

            try:
                record_streamer_fns, instance_record_streamers = self._mk_content_streamers(
                    f"decision.record ({iteration}).{safe_idx}",
                    sources_list=sources_list,
                    artifact_name=f"react.record.{artifact_suffix}",
                    emit_delta=action_gate.emit_delta,
                )
            except TypeError as exc:
                if "emit_delta" not in str(exc):
                    raise
                record_streamer_fns, instance_record_streamers = self._mk_content_streamers(
                    f"decision.record ({iteration}).{safe_idx}",
                    sources_list=sources_list,
                    artifact_name=f"react.record.{artifact_suffix}",
                )
            timeline_streamer_fn, instance_timeline_streamer = self._mk_timeline_streamer(
                f"decision.timeline ({iteration}).{safe_idx}",
                sources_list=sources_list,
                agent=f"{timeline_agent_prefix}.{safe_idx}",
                stream_final_answer=True,
                notes_artifact_name=f"timeline_text.react.decision.{iteration}.{safe_idx}",
                final_answer_artifact_name=f"react.final_answer.{iteration}.{safe_idx}",
                plan_artifact_name=f"timeline_text.react.plan.{iteration}.{safe_idx}",
                iteration=iteration,
                emit_delta=_timeline_emit_delta,
                on_action_identity=_report_action_identity,
            )
            instance_state: Dict[str, Any] = {
                "instance_idx": safe_idx,
                "gate": action_gate,
                "answer_gate": answer_gate,
                "record_streamers": instance_record_streamers,
                "timeline_streamer": instance_timeline_streamer,
                "raw_chunks": [],
                "parsed_decision": None,
                "parse_error": None,
                "completed": False,
            }

            async def _emit_instance_json(text: str = "", completed: bool = False, **_kwargs) -> None:
                if text:
                    instance_state["raw_chunks"].append(text)
                for fn in record_streamer_fns:
                    await fn(text, completed=completed)
                await timeline_streamer_fn(text, completed=completed)
                if completed:
                    parsed_decision, parse_error = parse_single_react_decision_from_channel_text(
                        "".join(instance_state["raw_chunks"])
                    )
                    instance_state["parsed_decision"] = parsed_decision
                    instance_state["parse_error"] = parse_error
                    instance_state["completed"] = True
                    tool_call = parsed_decision.get("tool_call") if isinstance(parsed_decision, dict) else {}
                    tool_id = (tool_call.get("tool_id") or "").strip() if isinstance(tool_call, dict) else ""
                    is_exec = (
                        isinstance(parsed_decision, dict)
                        and (parsed_decision.get("action") or "").strip() == "call_tool"
                        and tools_insights.is_exec_tool(tool_id)
                    )
                    if is_exec:
                        if not code_stream_state["code_started"] and code_stream_state.get("bound_exec_instance") is None:
                            code_stream_state["pending_exec_instance"] = instance_state
                        await _attach_exec_json_if_ready(instance_state)
                    elif (
                        code_stream_state.get("pending_exec_instance") is not None
                        and not code_stream_state["code_started"]
                        and code_stream_state.get("bound_exec_instance") is None
                    ):
                        # Positional binding: an exec action consumes only the
                        # immediately following code channel. Another decision
                        # before code means the exec/code pair is incomplete.
                        code_stream_state["pending_exec_instance"] = None

            instance_state["emit_json"] = _emit_instance_json
            decision_stream_instances[safe_idx] = instance_state
            record_streamers.extend(instance_record_streamers)
            if timeline_streamer is None:
                timeline_streamer = instance_timeline_streamer
            return instance_state

        def _react_decision_subscriber_factory(channel: str, channel_instance: int):
            del channel
            instance_state = _ensure_decision_stream_instance(channel_instance)
            return [instance_state["emit_json"]]

        def _resolve_exec_code_stream_target() -> tuple[Optional[bool], Optional[Dict[str, Any]]]:
            bound_exec = code_stream_state.get("bound_exec_instance")
            if isinstance(bound_exec, dict):
                return True, bound_exec
            pending_exec = code_stream_state.get("pending_exec_instance")
            if isinstance(pending_exec, dict):
                return True, pending_exec
            if not decision_stream_instances:
                return None, None
            unresolved = False
            for instance_state in decision_stream_instances.values():
                if instance_state.get("parsed_decision") is None and instance_state.get("parse_error") is None:
                    parsed_decision, parse_error = parse_single_react_decision_from_channel_text(
                        "".join(instance_state.get("raw_chunks") or [])
                    )
                    instance_state["parsed_decision"] = parsed_decision
                    instance_state["parse_error"] = parse_error
                parsed_decision = instance_state.get("parsed_decision")
                if not isinstance(parsed_decision, dict):
                    if not instance_state.get("completed"):
                        unresolved = True
                    continue
            if unresolved:
                return None, None
            return False, None

        async def _hub_on_code(text: str = "", completed: bool = False, **_kwargs) -> None:
            if code_stream_state["blocked"]:
                return
            allowed, instance_state = _resolve_exec_code_stream_target()
            if allowed is None:
                if text:
                    code_stream_state["buffer"].append(text)
                if completed:
                    code_stream_state["buffer"].clear()
                return
            if not allowed or exec_streamer_widget is None:
                code_stream_state["buffer"].clear()
                code_stream_state["blocked"] = True
                return
            if not code_stream_state["json_attached"]:
                await _attach_exec_json_if_ready(instance_state)
            code_stream_state["code_started"] = True
            code_stream_state["bound_exec_instance"] = instance_state
            code_stream_state["pending_exec_instance"] = None
            if code_stream_state["buffer"]:
                buffered = "".join(code_stream_state["buffer"])
                code_stream_state["buffer"].clear()
                if buffered:
                    await exec_streamer_widget.feed_code(
                        text=buffered,
                        completed=False,
                        channel_instance=instance_state.get("instance_idx") or 0,
                    )
            await exec_streamer_widget.feed_code(
                text=text or "",
                completed=completed,
                channel_instance=instance_state.get("instance_idx") or 0,
            )
            if completed:
                code_stream_state["bound_exec_instance"] = None
                code_stream_state["pending_exec_instance"] = None
                code_stream_state["code_started"] = False

        t0 = time.perf_counter()
        from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry import retry_with_compaction
        from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import build_decision_system_text
        try:
            state["skill_tool_catalog"] = (
                build_tool_catalog(announced_adapters + extra_adapters_for_decision, exclude_tool_ids=[])
                + get_react_tools_catalog()
            )
        except Exception:
            state["skill_tool_catalog"] = []

        async def _decision_agent(*, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            self._begin_active_generation_capture(iteration=iteration)
            from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer_v3 import ChannelSubscribers
            subs = ChannelSubscribers().subscribe_factory("action", _react_decision_subscriber_factory)
            subs = subs.subscribe("code", _hub_on_code)
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
            decision_max_tokens = 20000
            if bool(state.get("steer_finalize_mode")):
                decision_max_tokens = min(decision_max_tokens, self._steer_finalize_max_tokens())
            return await react_decision_stream_v2(
                svc=self.svc,
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
                workspace_implementation=getattr(runtime_ctx, "workspace_implementation", "custom") if runtime_ctx else "custom",
                additional_instructions=self.additional_instructions,
                instruction_body=self.instruction_body or None,
                instruction_blocks=self.instruction_blocks or None,
                include_tool_catalog=self.include_tool_catalog,
                include_skill_gallery=self.include_skill_gallery,
                multi_action_mode=self.multi_action_mode,
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
        decision: Dict[str, Any] = {}
        stream_policy_violation: Optional[StreamPolicyViolation] = None
        interrupted_raw_text = ""
        try:
            async with with_accounting(
                self.ctx_browser.runtime_ctx.bundle_id,
                agent=role,
                metadata={"agent": role},
            ):
                decision = await retry_with_compaction(
                    ctx_browser=self.ctx_browser,
                    system_text_fn=lambda: build_decision_system_text(
                        adapters=announced_adapters,
                        infra_adapters=extra_adapters_for_decision,
                        workspace_implementation=getattr(getattr(self.ctx_browser, "runtime_ctx", None), "workspace_implementation", "custom"),
                        additional_instructions=self.additional_instructions,
                        instruction_body=self.instruction_body or None,
                        instruction_blocks=self.instruction_blocks or None,
                        include_tool_catalog=self.include_tool_catalog,
                        include_skill_gallery=self.include_skill_gallery,
                        multi_action_mode=self.multi_action_mode,
                    ),
                    render_params=render_params,
                    agent_fn=_decision_agent,
                    emit_status=None,
                )
        except StreamPolicyViolation as exc:
            stream_policy_violation = exc
            interrupted_raw_text = "".join(self._active_generation_raw_chunks or "").strip()
            try:
                self.log.log(
                    f"[react.v3] decision stream interrupted by policy: code={exc.code} raw_len={len(interrupted_raw_text)} extra={dict(exc.extra or {})}",
                    "WARNING",
                )
            except Exception:
                pass
            self._stash_interrupted_generation_snapshot()
        finally:
            if self._active_phase_cancelled_by_steer or self._steer_interrupt_requested:
                self._stash_interrupted_generation_snapshot()
            self._clear_active_generation_capture()

        policy_rejected_items = action_overseer.rejected_actions()
        if policy_rejected_items:
            recovered_packet = self._stream_policy_recovery_packet(
                decision_packet=decision if isinstance(decision, dict) else {},
                decision_stream_instances=decision_stream_instances,
                accepted_actions=action_overseer.accepted_actions(),
                rejected_items=policy_rejected_items,
                interrupted_raw_text=interrupted_raw_text,
            )
            if recovered_packet is not None:
                decision = recovered_packet
                stream_policy_violation = None

        if stream_policy_violation is not None:
            code = stream_policy_violation.code
            extra = dict(stream_policy_violation.extra or {})
            try:
                self._persist_interrupted_generation(
                    state=state,
                    checkpoint="decision.stream_policy",
                    cancelled_phase="decision",
                )
            except Exception:
                pass
            notice_message = self._protocol_violation_message(
                code=code,
                decision={},
                state=state,
                extra=extra,
            )
            try:
                await self._record_failed_decision_attempt(
                    iteration=iteration,
                    tool_call_id=pending_tool_call_id,
                    code=code,
                    notice_code=f"protocol_violation.{code}",
                    notice_message=notice_message,
                    decision_packet=None,
                    reason=code,
                    notice_extra=extra,
                )
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                state["force_compaction_next_decision"] = False
                decision = {"action": "call_tool", "notes": f"{code}; retry decision"}
                state["session_log"].append({
                    "type": "decision_invalid",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "error": code,
                    "extra": extra,
                })
                state["last_decision"] = decision
                return state
            decision = {"action": "exit", "final_answer": "Decision validation failed."}
        try:
            for instance_state in decision_stream_instances.values():
                instance_timeline_streamer = instance_state.get("timeline_streamer")
                if instance_timeline_streamer is None:
                    continue
                if instance_timeline_streamer.has_started("final_answer"):
                    self.scratchpad._final_answer_delta_emitted = True
                    self.scratchpad._react_answer_delta_idx = max(
                        int(getattr(self.scratchpad, "_react_answer_delta_idx", 0) or 0),
                        int(instance_timeline_streamer.next_index("final_answer") or 0),
                    )
                    started_at = instance_timeline_streamer.started_at("final_answer")
                    if started_at:
                        self.scratchpad._latest_streamed_final_answer_started_at = started_at
                if instance_timeline_streamer.has_started("notes"):
                    notes_started_at = instance_timeline_streamer.started_at("notes")
                    if notes_started_at:
                        self.scratchpad._latest_streamed_notes_started_at = notes_started_at
        except Exception:
            self.log.log(
                f"[react.v3] failed to sync streamed final-answer index: {traceback.format_exc()}",
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
                self.log.log(f"[react.v3] failed to mark external events consumed: {traceback.format_exc()}", level="ERROR")
        # Reset forced compaction once we have a decision attempt.
        state["force_compaction_next_decision"] = False
        state["last_decision_raw"] = decision
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
        logging_helpers.log_raw_channel_output(
            role,
            "react.decision.v3",
            decision.get("raw") if isinstance(decision, dict) else None,
            channels=decision.get("channels") if isinstance(decision, dict) else None,
        )
        logging_helpers.log_agent_packet(role, "react.decision.v3", decision)
        decision_packet = decision
        working_summary_text = self._working_summary_from_decision_packet(
            decision_packet if isinstance(decision_packet, dict) else {}
        )
        decision_bundle = self._decision_bundle_from_packet(decision_packet)
        decision_parse_error_items = []
        stream_policy_rejected_items = []
        try:
            packet_log = decision_packet.get("log") if isinstance(decision_packet, dict) else {}
            maybe_items = packet_log.get("bundle_error_items") if isinstance(packet_log, dict) else None
            if isinstance(maybe_items, list):
                decision_parse_error_items = [item for item in maybe_items if isinstance(item, dict)]
            maybe_rejected = packet_log.get("stream_policy_rejected_items") if isinstance(packet_log, dict) else None
            if isinstance(maybe_rejected, list):
                stream_policy_rejected_items = [item for item in maybe_rejected if isinstance(item, dict)]
        except Exception:
            decision_parse_error_items = []
            stream_policy_rejected_items = []
        bundle_mode = False
        error = (decision_packet.get("log") or {}).get("error")
        if decision_bundle and self._multi_action_enabled():
            error = None
        packet_validation_error, packet_validation_extra = self._validate_decision_packet_channel_consistency(
            packet=decision_packet if isinstance(decision_packet, dict) else {},
            bundle=decision_bundle,
            exec_streamer=exec_streamer_widget,
        )

        try:
            ReactRound.thinking(
                ctx_browser=self.ctx_browser,
                decision=decision,
                title=f"solver.react.v3.decision ({iteration})",
                iteration=iteration,
                tool_call_id=pending_tool_call_id,
            )
        except Exception:
            pass

        notes = None
        action = None
        tool_id = ""
        protocol_entry = None

        if error:
            error_summary, error_diagnostic = self._schema_error_diagnostics(error)
            notice_message = self._protocol_violation_message(
                code="action_schema_error",
                error=error,
                state=state,
                decision={},
            )
            try:
                await self._record_failed_decision_attempt(
                    iteration=iteration,
                    tool_call_id=pending_tool_call_id,
                    code="action_schema_error",
                    notice_code="protocol_violation.action_schema_error",
                    notice_message=notice_message,
                    decision_packet=decision,
                    reason="schema_error",
                    notice_extra={
                        "parser_error": error_summary,
                        **({"diagnostic_excerpt": error_diagnostic} if error_diagnostic else {}),
                    },
                )
                self.log.log(f"[react.v3] decision schema error: {error}", level="ERROR")
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "action_schema_error; retry action"
                try:
                    self.log.log(
                        f"[react.v3] retry decision after schema error (retries={state['decision_retries']})",
                        level="INFO",
                    )
                except Exception:
                    pass
            else:
                decision = {
                    "action": "exit",
                    "final_answer": "action_schema_error validation failed.",
                    "notes": "action_schema_error",
                }
                action = "exit"
                tool_call = {}
                tool_id = ""
        else:
            if packet_validation_error:
                notice_message = self._protocol_violation_message(
                    code=packet_validation_error,
                    decision={},
                    state=state,
                    extra=packet_validation_extra,
                )
                try:
                    await self._record_failed_decision_attempt(
                        iteration=iteration,
                        tool_call_id=pending_tool_call_id,
                        code=packet_validation_error,
                        notice_code=f"protocol_violation.{packet_validation_error}",
                        notice_message=notice_message,
                        decision_packet=decision_packet if isinstance(decision_packet, dict) else None,
                        reason=packet_validation_error,
                        notice_extra=packet_validation_extra or {},
                    )
                except Exception:
                    pass
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision = {"action": "call_tool", "notes": f"{packet_validation_error}; retry decision"}
                    state["session_log"].append({
                        "type": "decision_invalid",
                        "iteration": iteration,
                        "timestamp": time.time(),
                        "error": packet_validation_error,
                    })
                    state["last_decision"] = decision
                    return state
                decision = {"action": "exit", "final_answer": "Decision validation failed."}

            if decision_parse_error_items and decision_bundle:
                await self._record_dropped_action_parse_items(
                    iteration=iteration,
                    parent_tool_call_id=pending_tool_call_id,
                    parse_errors=decision_parse_error_items,
                )

            if stream_policy_rejected_items and decision_bundle:
                await self._record_dropped_multi_action_items(
                    iteration=iteration,
                    parent_tool_call_id=pending_tool_call_id,
                    rejected=stream_policy_rejected_items,
                    decision_bundle=decision_bundle,
                    state=state,
                )

            if len(decision_bundle) > 1 and self._multi_action_enabled():
                adapters_by_id = self._adapters_index(state.get("adapters") or [])
                accepted_bundle, bundle_error, bundle_extra = await self._prepare_safe_multi_action_bundle(
                    bundle=decision_bundle,
                    adapters_by_id=adapters_by_id,
                    allow_single_exec_with_code=self._exec_streamer_is_complete(exec_streamer_widget),
                )
                if bundle_error:
                    notice_message = self._protocol_violation_message(
                        code=bundle_error,
                        decision={},
                        state=state,
                        extra=bundle_extra,
                    )
                    try:
                        await self._record_failed_decision_attempt(
                            iteration=iteration,
                            tool_call_id=pending_tool_call_id,
                            code=bundle_error,
                            notice_code=f"protocol_violation.{bundle_error}",
                            notice_message=notice_message,
                            decision_packet=decision_packet if isinstance(decision_packet, dict) else None,
                            reason=bundle_error,
                            notice_extra=bundle_extra or {},
                        )
                    except Exception:
                        pass
                    retries = int(state.get("decision_retries") or 0)
                    if retries < int(state.get("max_iterations") or 0):
                        state["decision_retries"] = retries + 1
                        state["retry_decision"] = True
                        decision = {"action": "call_tool", "notes": f"{bundle_error}; retry decision"}
                        state["session_log"].append({
                            "type": "decision_invalid",
                            "iteration": iteration,
                            "timestamp": time.time(),
                            "error": bundle_error,
                        })
                        state["last_decision"] = decision
                        return state
                    decision = {"action": "exit", "final_answer": "Decision validation failed."}
                else:
                    bundle_mode = True
                    state["invalid_action_retries"] = 0
                    state["force_compaction_next_decision"] = False
                    state["retry_decision"] = False
                    final_decision = (bundle_extra or {}).get("final_decision") if isinstance(bundle_extra, dict) else None
                    rejected_items = (bundle_extra or {}).get("rejected") if isinstance(bundle_extra, dict) else None
                    suppressed_items = (bundle_extra or {}).get("suppressed") if isinstance(bundle_extra, dict) else None
                    if isinstance(rejected_items, list) and rejected_items:
                        await self._record_dropped_multi_action_items(
                            iteration=iteration,
                            parent_tool_call_id=pending_tool_call_id,
                            rejected=rejected_items,
                            decision_bundle=decision_bundle,
                            state=state,
                        )
                    if isinstance(suppressed_items, list) and suppressed_items:
                        self._record_suppressed_final_answer_items(
                            iteration=iteration,
                            parent_tool_call_id=pending_tool_call_id,
                            suppressed=suppressed_items,
                            decision_bundle=decision_bundle,
                            state=state,
                        )
                    accepted_for_log = list(accepted_bundle)
                    if isinstance(final_decision, dict):
                        accepted_for_log.append(final_decision)
                        if accepted_bundle:
                            state["pending_final_answer_after_bundle"] = final_decision
                        else:
                            state.pop("pending_final_answer_after_bundle", None)
                    else:
                        state.pop("pending_final_answer_after_bundle", None)
                    if accepted_bundle:
                        decision = accepted_bundle[0]
                    elif isinstance(final_decision, dict):
                        decision = final_decision
                        bundle_mode = False
                    else:
                        decision = {"action": "exit", "final_answer": "Decision validation failed."}
                    state["pending_tool_bundle"] = [
                        {
                            "decision": item,
                            "tool_call_id": f"tc_{uuid.uuid4().hex[:12]}",
                            "iteration": iteration,
                        }
                        for item in accepted_bundle
                    ]
                    state["last_decision_bundle"] = accepted_for_log
            else:
                decision = decision_packet.get("agent_response") or {}
                if not isinstance(decision, dict):
                    decision = {}

                suppressed_single = self._suppress_tool_call_final_answer(decision)
                if suppressed_single is not None:
                    self._record_suppressed_single_final_answer(
                        iteration=iteration,
                        tool_call_id=pending_tool_call_id,
                        decision=decision,
                        suppressed=suppressed_single,
                        state=state,
                    )
                    state["session_log"].append({
                        "type": "decision_field_suppressed",
                        "iteration": iteration,
                        "timestamp": time.time(),
                        "code": suppressed_single.get("code") or "final_answer_with_tool_call",
                        "field": "final_answer",
                        **({"tool_id": suppressed_single.get("tool_id")} if suppressed_single.get("tool_id") else {}),
                    })

                validation_error = self._validate_decision(decision)
                if validation_error:
                    if validation_error.startswith("invalid_action"):
                        invalid_retries = int(state.get("invalid_action_retries") or 0) + 1
                        state["invalid_action_retries"] = invalid_retries
                        if invalid_retries >= 2:
                            state["force_compaction_next_decision"] = True
                    notice_message = self._protocol_violation_message(
                        code=validation_error,
                        decision=decision,
                        state=state,
                    )
                    try:
                        await self._record_failed_decision_attempt(
                            iteration=iteration,
                            tool_call_id=pending_tool_call_id,
                            code=validation_error,
                            notice_code=f"protocol_violation.{validation_error}",
                            notice_message=notice_message,
                            decision_packet=decision_packet if isinstance(decision_packet, dict) else None,
                            reason=validation_error,
                            tool_id=((decision.get("tool_call") or {}).get("tool_id") or "").strip(),
                            notice_extra={"validation_error": validation_error},
                        )
                    except Exception:
                        pass
                    try:
                        self.log.log(
                            f"[react.v3] decision validation failed: {validation_error} | decision={self._short_json(decision)}",
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
                                f"[react.v3] retry decision after validation error (retries={state['decision_retries']})",
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
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import latest_current_plan_snapshot
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
                        from kdcube_ai_app.apps.chat.sdk.solutions.react.plan import latest_current_plan_snapshot
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

            if not bundle_mode:
                exec_only_ids = {"ctx_tools.fetch_ctx"}
                if action == "call_tool" and tool_id in exec_only_ids:
                    notice_message = self._protocol_violation_message(
                        code="tool_not_allowed_in_react",
                        decision=decision,
                        state=state,
                        extra={"tool_id": tool_id},
                    )
                    try:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.tool_not_allowed_in_react",
                            message=notice_message,
                            extra={"tool_id": tool_id, "iteration": iteration},
                            call_id=pending_tool_call_id,
                            meta={"rel": "call"},
                        )
                    except Exception:
                        pass
                    try:
                        self.log.log(f"[react.v3] tool_not_allowed_in_react: {tool_id}", level="ERROR")
                    except Exception:
                        pass
                    try:
                        await emit_react_tool_rejected_event(
                            react=self,
                            tool_id=tool_id,
                            tool_call_id=pending_tool_call_id,
                            params=tool_call.get("params") if isinstance(tool_call, dict) else None,
                            iteration=iteration,
                            code="tool_not_allowed_in_react",
                            message=notice_message,
                            extra={"tool_id": tool_id},
                        )
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
                                f"[react.v3] retry decision after tool_not_allowed (retries={state['decision_retries']})",
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
                        notice_message = self._protocol_violation_message(
                            code="tool_call_invalid",
                            decision=decision,
                            state=state,
                        )
                        try:
                            self.ctx_browser.contribute_notice(
                                code="protocol_violation.tool_call_invalid",
                                message=notice_message,
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
                                f"[react.v3] tool_call_invalid: {verdict.get('violations')} | decision={self._short_json(decision)}",
                                level="ERROR",
                            )
                        except Exception:
                            pass
                        state["session_log"].append(protocol_entry)
                        try:
                            await emit_react_tool_rejected_event(
                                react=self,
                                tool_id=str(verdict.get("tool_id") or tool_id or "").strip(),
                                tool_call_id=pending_tool_call_id,
                                params=tool_call.get("params") if isinstance(tool_call, dict) else None,
                                iteration=iteration,
                                code="tool_call_invalid",
                                message=notice_message,
                                extra={
                                    "violations": verdict.get("violations") or [],
                                    "tool_id": verdict.get("tool_id"),
                                },
                            )
                        except Exception:
                            pass
                        retries = int(state.get("decision_retries") or 0)
                        if retries < int(state.get("max_iterations") or 0):
                            state["decision_retries"] = retries + 1
                            state["retry_decision"] = True
                            decision["notes"] = "tool_call_invalid; retry decision"
                            try:
                                self.log.log(
                                    f"[react.v3] retry decision after tool_call_invalid (retries={state['decision_retries']})",
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
                    notice_message = self._protocol_violation_message(
                        code="tool_signature_red",
                        decision=decision,
                        state=state,
                    )
                    try:
                        self.ctx_browser.contribute_notice(
                            code="protocol_violation.tool_signature_red",
                            message=notice_message,
                            extra={"violations": sig_issues, "tool_id": tool_id, "iteration": iteration},
                            call_id=pending_tool_call_id,
                            meta={"rel": "call"},
                        )
                    except Exception:
                        pass
                    try:
                        self.log.log(
                            f"[react.v3] tool_signature_red: {sig_issues} | decision={self._short_json(decision)}",
                            level="ERROR",
                        )
                    except Exception:
                        pass
                    if protocol_entry is not None:
                        protocol_entry["ok"] = False
                        protocol_entry["violations"] = (protocol_entry.get("violations") or []) + (sig_issues or [])
                    try:
                        await emit_react_tool_rejected_event(
                            react=self,
                            tool_id=tool_id,
                            tool_call_id=pending_tool_call_id,
                            params=filtered_params,
                            iteration=iteration,
                            code="tool_signature_red",
                            message=notice_message,
                            extra={"violations": sig_issues, "tool_id": tool_id},
                        )
                    except Exception:
                        pass
                    retries = int(state.get("decision_retries") or 0)
                    if retries < int(state.get("max_iterations") or 0):
                        state["decision_retries"] = retries + 1
                        state["retry_decision"] = True
                        decision["notes"] = "tool_signature_red; retry decision"
                        try:
                            self.log.log(
                                f"[react.v3] retry decision after tool_signature_red (retries={state['decision_retries']})",
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

        if action in {"complete", "exit"}:
            try:
                answer_started_at = None
                by_iteration = getattr(self.scratchpad, "_react_answer_started_at_by_iteration", None)
                if isinstance(by_iteration, dict):
                    answer_started_at = str(by_iteration.get(int(iteration)) or "").strip() or None
                if not answer_started_at:
                    answer_started_at = self._delta_cache_started_at(
                        marker="answer",
                        artifact_name_prefix=f"react.final_answer.{iteration}.",
                    )
                if not answer_started_at:
                    answer_started_at = getattr(self.scratchpad, "_latest_streamed_final_answer_started_at", None)
                if answer_started_at:
                    entry = record_assistant_completion_attempt(
                        scratchpad=self.scratchpad,
                        answer_text=(decision.get("final_answer") or ""),
                        ts=answer_started_at,
                        iteration=iteration,
                        working_summary_text=working_summary_text,
                    )
                    timeline = getattr(self.ctx_browser, "timeline", None) if self.ctx_browser else None
                    block_factory = getattr(timeline, "block", None)
                    contribute = getattr(self.ctx_browser, "contribute", None) if self.ctx_browser else None
                    if entry and callable(block_factory) and callable(contribute):
                        entries = getattr(self.scratchpad, "assistant_completion_attempts", []) or []
                        attempt_blocks = build_assistant_completion_attempt_blocks(
                            runtime=self.ctx_browser.runtime_ctx,
                            entry=entry,
                            attempt_index=len(entries),
                            block_factory=block_factory,
                        )
                        if attempt_blocks:
                            contribute(blocks=attempt_blocks)
                    session_cfg = getattr(getattr(self.ctx_browser, "runtime_ctx", None), "session", None)
                    working_summary_enabled = bool(getattr(session_cfg, "working_summary_enabled", True))
                    if entry and working_summary_enabled and str(working_summary_text or "").strip():
                        entries = getattr(self.scratchpad, "assistant_completion_attempts", []) or []
                        summary_blocks = build_working_summary_attempt_blocks(
                            runtime=self.ctx_browser.runtime_ctx,
                            summary_text=working_summary_text,
                            attempt_index=len(entries),
                            attempt_count=len(entries),
                            iteration=iteration,
                            ts=answer_started_at,
                            block_factory=self.ctx_browser.timeline.block,
                        )
                        if summary_blocks:
                            self.ctx_browser.contribute(blocks=summary_blocks)
            except Exception:
                self.log.log(traceback.format_exc(), level="ERROR")

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
                        f"[react.v3] external event arrived during decision; forcing another round "
                        f"(seen={latest_seen_after_decision} visible={visible_external_event_seq})",
                        level="INFO",
                    )
                except Exception:
                    pass
            else:
                handler_closed = None
                try_close = getattr(self.ctx_browser, "try_close_external_event_handler", None) if self.ctx_browser else None
                if callable(try_close):
                    handler_closed = await try_close()
                if handler_closed is False:
                    state["retry_decision"] = True
                    state["exit_reason"] = None
                    state["final_answer"] = None
                    state["suggested_followups"] = []
                    try:
                        self.log.log("[react.v3] event-bus close gate deferred final answer; forcing another round", level="INFO")
                    except Exception:
                        pass
                else:
                    final_answer_text = (decision.get("final_answer") or "").strip()
                    state["exit_reason"] = "steer" if bool(state.get("steer_finalize_mode")) else action
                    state["final_answer"] = final_answer_text
                    state["suggested_followups"] = decision.get("suggested_followups") or []
                    if working_summary_text:
                        state["working_summary"] = working_summary_text
                        try:
                            self.scratchpad.react_working_summary = working_summary_text
                        except Exception:
                            pass
                    try:
                        sf = state.get("suggested_followups") or []
                        self.log.log(
                            f"[react.v3] decision followups: count={len(sf)}",
                            level="INFO",
                        )
                    except Exception:
                        pass

        try:
            if notes and not bundle_mode:
                notes_started_at = None
                by_iteration = getattr(self.scratchpad, "_react_notes_started_at_by_iteration", None)
                if isinstance(by_iteration, dict):
                    notes_started_at = str(by_iteration.get(int(iteration)) or "").strip() or None
                if not notes_started_at:
                    notes_started_at = getattr(self.scratchpad, "_latest_streamed_notes_started_at", None)
                if not notes_started_at:
                    notes_started_at = self._delta_cache_started_at(
                        marker="timeline_text",
                        artifact_name_prefix=f"timeline_text.react.decision.{iteration}.",
                    )
                ReactRound.note(
                    ctx_browser=self.ctx_browser,
                    notes=notes,
                    tool_call_id=pending_tool_call_id,
                    tool_id=tool_id,
                    action=action,
                    iteration=iteration,
                    ts=notes_started_at,
                )
        except Exception:
            pass
        decision_event_title = f"ReAct Round ({iteration}). Decision Node"
        decision_event_data: Dict[str, Any]
        if bundle_mode:
            bundle_items = state.get("last_decision_bundle") if isinstance(state.get("last_decision_bundle"), list) else []
            bsize = len(bundle_items)
            decision_event_data = dict(decision) if isinstance(decision, dict) else {}
            decision_event_data["bundle"] = [it for it in bundle_items if isinstance(it, dict)]
            decision_event_data["bundle_size"] = bsize
            if bsize > 1:
                decision_event_title = f"ReAct Round ({iteration}). Decision Node (bundle of {bsize} actions)"
        else:
            decision_event_data = decision if decision else {"error": "no_decision"}
        await emit_event(
            comm=self.comm,
            etype="solver.react.decision",
            title=decision_event_title,
            step=f"react({iteration}).decision",
            data=decision_event_data,
            agent=f"solver.react({iteration}).decision",
        )
        try:
            self.scratchpad.register_agentic_response(
                f"solver.react.decision ({iteration})",
                decision,
            )
        except Exception as exc:
            self.log.log(f"[react.v3] register_agentic_response failed: {exc}", level="ERROR")

        state["exec_code_streamer"] = exec_streamer_widget
        state["record_streamers"] = record_streamers
        state["timeline_streamer"] = timeline_streamer
        state["pending_exec_id"] = exec_id
        state["pending_tool_call_id"] = None if bundle_mode else pending_tool_call_id
        if action == "call_tool":
            state["pending_tool_origin_iteration"] = iteration
        else:
            state.pop("pending_tool_origin_iteration", None)
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
        async def _merge_pending_sources(current_state: Dict[str, Any]) -> None:
            pending_sources = current_state.pop("pending_sources", None)
            if not pending_sources:
                return
            try:
                self.log.log(
                    f"[react.v3] merge_sources: pending_sources={len(pending_sources or [])}",
                    level="INFO",
                )
                await self._merge_with_pool(
                    collections=[pending_sources],
                    workdir=pathlib.Path(current_state["workdir"]),
                    outdir=pathlib.Path(current_state["outdir"]),
                    record_streamers=current_state.get("record_streamers"),
                    timeline_streamer=current_state.get("timeline_streamer"),
                )
            except Exception:
                self.log.log(traceback.format_exc())

        pending_bundle = state.pop("pending_tool_bundle", None)
        if isinstance(pending_bundle, list) and pending_bundle:
            bundle_size = len(pending_bundle)
            for action_index, item in enumerate(pending_bundle):
                if await self._apply_steer_interrupt_if_requested(state, checkpoint="tool.before_execute"):
                    return state
                decision = item.get("decision") if isinstance(item, dict) else None
                tool_call_id = (item.get("tool_call_id") or "").strip() if isinstance(item, dict) else ""
                try:
                    origin_iteration = int(item.get("iteration")) if isinstance(item, dict) and item.get("iteration") is not None else int(state.get("pending_tool_origin_iteration"))
                except Exception:
                    origin_iteration = max(0, int(state.get("iteration") or 0) - 1)
                if not isinstance(decision, dict):
                    continue
                state["last_decision"] = decision
                state["pending_tool_call_id"] = tool_call_id or None
                state["pending_tool_origin_iteration"] = origin_iteration
                notes = (decision.get("notes") or "").strip()
                tool_call = decision.get("tool_call") or {}
                tool_id = (tool_call.get("tool_id") or "").strip()
                if notes:
                    try:
                        ReactRound.note(
                            ctx_browser=self.ctx_browser,
                            notes=notes,
                            tool_call_id=tool_call_id,
                            tool_id=tool_id,
                            action="call_tool",
                            iteration=origin_iteration,
                        )
                    except Exception:
                        pass
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
                await _merge_pending_sources(state)
                await self._emit_react_action_step(
                    iteration=int(origin_iteration),
                    action_index=int(action_index),
                    bundle_size=int(bundle_size),
                    decision=decision,
                    tool_call_id=tool_call_id,
                    status=("failed" if state.get("exit_reason") == "error" else "completed"),
                )
                if state.get("exit_reason"):
                    return state
            state["pending_tool_call_id"] = None
            state.pop("pending_tool_origin_iteration", None)
            pending_final = state.pop("pending_final_answer_after_bundle", None)
            if isinstance(pending_final, dict):
                final_action = str(pending_final.get("action") or "complete").strip() or "complete"
                state["last_decision"] = pending_final
                state["exit_reason"] = final_action
                state["final_answer"] = str(pending_final.get("final_answer") or "").strip()
                state["suggested_followups"] = pending_final.get("suggested_followups") or []
            return state

        if await self._apply_steer_interrupt_if_requested(state, checkpoint="tool.before_execute"):
            return state
        try:
            state.setdefault("pending_tool_origin_iteration", max(0, int(state.get("iteration") or 0) - 1))
        except Exception:
            state.setdefault("pending_tool_origin_iteration", 0)
        single_action_tool_call_id = (state.get("pending_tool_call_id") or "").strip() if isinstance(state.get("pending_tool_call_id"), str) else ""
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
        await _merge_pending_sources(state)
        try:
            origin_iteration_single = int(state.get("pending_tool_origin_iteration") or 0)
        except Exception:
            origin_iteration_single = 0
        single_decision = state.get("last_decision") if isinstance(state.get("last_decision"), dict) else {}
        await self._emit_react_action_step(
            iteration=origin_iteration_single,
            action_index=0,
            bundle_size=1,
            decision=single_decision,
            tool_call_id=single_action_tool_call_id,
            status=("failed" if state.get("exit_reason") == "error" else "completed"),
        )
        state.pop("pending_tool_origin_iteration", None)
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

        # Persist the compact model-visible finalize marker. Keep only stable
        # budget/open-plan state, not the full announce with memory/workspace
        # sections.
        try:
            runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None) if self.ctx_browser else None
            turn_id = self.scratchpad.turn_id or getattr(runtime_ctx, "turn_id", "") or ""
            final_text = ""
            if turn_id:
                try:
                    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_announce_text
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
                        mode="turn_finalize_budget",
                    ).strip()
                except Exception:
                    self.log.log(traceback.format_exc())
            if turn_id and final_text:
                post_blocks.append({
                    "type": "react.turn.finalize",
                    "author": "react",
                    "turn_id": turn_id,
                    "ts": time.time(),
                    "mime": "text/plain",
                    "path": f"ar:{turn_id}.react.turn.finalize",
                    "text": final_text,
                    "meta": {"model_visible": True, "sections": ["BUDGET", "OPEN PLANS"]},
                })
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
            self.log.log(f"[react.v3] completion_hooks: {ex}", level="ERROR")

        # Emit citations used in this turn (files already emitted on host)
        try:
            if self.hosting_service and self.ctx_browser and self.ctx_browser.timeline:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import extract_sources_used_from_blocks
                blocks = self.ctx_browser.timeline.get_turn_blocks()
                used_sids = extract_sources_used_from_blocks(blocks)
                pool = list(self.ctx_browser.timeline.sources_pool or [])
                try:
                    self.log.log(
                        "[react.v3] citations.used_sids.from_blocks "
                        f"block_count={len(blocks or [])} "
                        f"used_sids={list(used_sids or [])}",
                        level="INFO",
                    )
                except Exception:
                    pass
                if used_sids:
                    sid_set = set(used_sids)
                    citations = []
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
                            f"[react.v3] emit_citations: skipped non-citable sids={sorted(set(non_citable))}",
                            level="INFO",
                        )
                    self.log.log(
                        f"[react.v3] emit_citations: used_sids={sorted(sid_set)} "
                        f"pool={len(pool)} citations={len(citations)}",
                        level="INFO",
                    )
                    if citations:
                        await self.hosting_service.emit_solver_artifacts(files=[], citations=citations)
                else:
                    self.log.log("[react.v3] emit_citations: no used_sids detected", level="INFO")
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
                f"[react.v3] merge_sources: existing={len(self.ctx_browser.sources_pool or [])} "
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
                    f"[react.v3] merge_sources: merged={len(merged)}",
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
        block_completion_texts: List[str] = []
        try:
            timeline = getattr(self.ctx_browser, "timeline", None) if self.ctx_browser else None
            if timeline is not None:
                from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import extract_assistant_completion_texts_from_blocks
                block_completion_texts = extract_assistant_completion_texts_from_blocks(timeline.get_turn_blocks())
        except Exception:
            block_completion_texts = []
        scratchpad_completion_texts = assistant_completion_texts(self.scratchpad)
        completion_texts = block_completion_texts or scratchpad_completion_texts
        state_answer_text = (state.get("final_answer") or "").strip()
        final_answers = list(completion_texts or [])
        if not final_answers and state_answer_text:
            final_answers = [state_answer_text]
        final_answer_text = (
            (final_answers[-1] if final_answers else "")
            or state_answer_text
        )
        try:
            state_answer_len = len(state_answer_text)
            if block_completion_texts:
                selected_source = "turn_blocks"
            elif scratchpad_completion_texts:
                selected_source = "assistant_completion_attempts"
            else:
                selected_source = "state.final_answer"
            self.log.log(
                "[react.v3] result.final_answer.selected "
                f"source={selected_source} "
                f"block_completions={len(block_completion_texts)} "
                f"completion_attempts={len(scratchpad_completion_texts)} "
                f"final_answers={len(final_answers)} "
                f"selected_len={len(final_answer_text or '')} "
                f"state_len={state_answer_len} "
                f"exit_reason={state.get('exit_reason') or ''} "
                f"iteration={int(state.get('iteration') or 0)} "
                f"max_iterations={int(state.get('max_iterations') or 0)}",
                level="INFO",
            )
        except Exception:
            pass

        react_result = ReactResult(
            ok=True,
            out=artifacts_out,
            sources_pool=sources_pool,
            final_answers=final_answers,
            final_answer=final_answer_text,
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
