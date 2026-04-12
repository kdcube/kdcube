# SPDX-License-Identifier: MIT

import os
import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime import ReactSolverV2
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.round import ReactRound


class _LogStub:
    def log(self, *args, **kwargs):
        return None


def _solver_stub() -> ReactSolverV2:
    solver = ReactSolverV2.__new__(ReactSolverV2)
    solver.log = _LogStub()
    solver.ctx_browser = None
    solver._latest_external_event_seq_seen = 0
    solver._last_decision_visible_external_event_seq = 0
    solver._last_consumed_external_event_seq = 0
    solver._steer_interrupt_requested = False
    solver._latest_steer_seq_seen = 0
    solver._last_handled_steer_seq = 0
    solver._latest_steer_text = ""
    solver._active_phase_task = None
    solver._active_phase_name = ""
    solver._active_phase_cancelled_by_steer = False
    solver._active_phase_cancel_requested_at = 0.0
    solver._active_generation_iteration = None
    solver._active_generation_raw_chunks = []
    solver._interrupted_generation_snapshot = None
    solver._active_phase_event_watch_task = None
    solver.scratchpad = SimpleNamespace(turn_id="turn-1")
    solver.comm = SimpleNamespace(service_event=_noop_async)
    solver.tools_subsystem = None
    solver.svc = None
    return solver


async def _noop_async(*args, **kwargs):
    return None


def test_route_after_decision_exits_when_exit_reason_is_set():
    solver = _solver_stub()
    state = {
        "exit_reason": "max_iterations",
        "last_decision": {"action": "call_tool"},
    }

    assert solver._route_after_decision(state) == "exit"


@pytest.mark.asyncio
async def test_decision_node_short_circuits_when_exit_reason_is_set():
    solver = _solver_stub()
    called = {"value": False}

    async def _impl(state, iteration):
        called["value"] = True
        return state

    solver._decision_node_impl = _impl
    state = {
        "exit_reason": "error",
        "iteration": 0,
        "max_iterations": 5,
    }

    out = await solver._decision_node(state)

    assert out is state
    assert called["value"] is False


@pytest.mark.asyncio
async def test_tool_execution_node_drains_external_events_after_execute(monkeypatch):
    solver = _solver_stub()
    calls = {"drain": 0}

    async def _fake_execute(*, react, state):
        del react
        state["executed"] = True
        return state

    async def _fake_drain(*, call_hooks):
        assert call_hooks is True
        calls["drain"] += 1
        return 1

    monkeypatch.setattr(ReactRound, "execute", _fake_execute)
    solver._drain_external_events = _fake_drain

    state = {"last_decision": {"tool_call": {"tool_id": "web_tools.web_search"}}}
    out = await solver._tool_execution_node(state)

    assert out["executed"] is True
    assert calls["drain"] == 1


@pytest.mark.asyncio
async def test_tool_execution_node_short_circuits_on_pending_steer(monkeypatch):
    solver = _solver_stub()
    solver._steer_interrupt_requested = True
    solver._latest_steer_seq_seen = 7

    class _Timeline:
        last_external_event_seq = 7

    solver.ctx_browser = SimpleNamespace(
        timeline=_Timeline(),
        current_turn_blocks=lambda: [],
    )

    marks = {"count": 0}

    async def _fake_mark(*, max_sequence):
        assert max_sequence == 7
        marks["count"] += 1
        solver._last_consumed_external_event_seq = max_sequence
        return 1

    async def _fake_execute(*, react, state):
        del react, state
        raise AssertionError("tool execution should not happen after steer")

    solver._mark_external_events_consumed_up_to = _fake_mark
    monkeypatch.setattr(ReactRound, "execute", _fake_execute)

    state = {"last_decision": {"tool_call": {"tool_id": "web_tools.web_search"}}}
    out = await solver._tool_execution_node(state)

    assert out.get("exit_reason") is None
    assert out["final_answer"] is None
    assert out["retry_decision"] is True
    assert out["steer_finalize_mode"] is True
    assert marks["count"] == 1


@pytest.mark.asyncio
async def test_decision_node_short_circuits_on_pending_steer_before_decision():
    solver = _solver_stub()
    solver._steer_interrupt_requested = True
    solver._latest_steer_seq_seen = 3
    solver.ctx_browser = SimpleNamespace(
        timeline=SimpleNamespace(last_external_event_seq=3),
        current_turn_blocks=lambda: [],
    )

    async def _fake_mark(*, max_sequence):
        assert max_sequence == 3
        solver._last_consumed_external_event_seq = max_sequence
        return 1

    async def _impl(state, iteration):
        del state, iteration
        raise AssertionError("decision impl should not run after steer")

    solver._mark_external_events_consumed_up_to = _fake_mark
    solver._decision_node_impl = _impl
    state = {
        "iteration": 0,
        "max_iterations": 5,
    }

    out = await solver._decision_node(state)

    assert out.get("exit_reason") is None
    assert out["final_answer"] is None
    assert out["retry_decision"] is True
    assert out["steer_finalize_mode"] is True


@pytest.mark.asyncio
async def test_decision_node_cancels_inflight_phase_on_steer():
    solver = _solver_stub()
    solver.ctx_browser = SimpleNamespace(
        timeline=SimpleNamespace(last_external_event_seq=0),
        current_turn_blocks=lambda: [],
    )

    async def _impl(state, iteration):
        del state, iteration
        await asyncio.sleep(30)
        raise AssertionError("cancel expected")

    solver._decision_node_impl = _impl
    state = {
        "iteration": 0,
        "max_iterations": 5,
    }

    task = asyncio.create_task(solver._decision_node(state))
    await asyncio.sleep(0.05)
    solver.ctx_browser.timeline.last_external_event_seq = 9
    await solver.on_timeline_event(
        type="steer",
        event=SimpleNamespace(sequence=9, text="stop now", message_id="evt_9"),
        blocks=[],
    )
    out = await task

    assert out.get("exit_reason") is None
    assert out["retry_decision"] is True
    assert out["steer_finalize_mode"] is True
    assert out["steer_interrupt"]["cancelled_phase"] == "decision"


@pytest.mark.asyncio
async def test_on_timeline_event_ignores_steer_for_other_turn():
    solver = _solver_stub()
    interrupted = {"count": 0}

    async def _fake_interrupt():
        interrupted["count"] += 1
        return True

    solver._interrupt_active_phase_for_steer = _fake_interrupt

    handled = await solver.on_timeline_event(
        type="steer",
        event=SimpleNamespace(
            sequence=14,
            text="stop",
            message_id="evt_14",
            target_turn_id="turn-other",
            active_turn_id_at_ingress="turn-other",
            owner_turn_id="turn-other",
        ),
        blocks=[],
    )

    assert handled is False
    assert interrupted["count"] == 0
    assert solver._steer_interrupt_requested is False
    assert solver._latest_steer_seq_seen == 0


def test_persist_interrupted_generation_uses_mainstream_raw_snapshot():
    solver = _solver_stub()
    captured = {}

    def _contribute(*, blocks):
        captured["blocks"] = list(blocks or [])

    solver.ctx_browser = SimpleNamespace(
        runtime_ctx=SimpleNamespace(turn_id="turn-1", started_at="2026-04-12T10:00:00Z"),
        timeline=SimpleNamespace(block=lambda **kwargs: kwargs),
        contribute=_contribute,
        current_turn_blocks=lambda: [],
    )
    solver._interrupted_generation_snapshot = {
        "raw_text": "<channel:thinking>thinking</channel:thinking><channel:code>print(1)</channel:code>",
        "iteration": 2,
        "captured_at": "2026-04-12T10:01:00Z",
    }

    state = {}
    solver._persist_interrupted_generation(
        state=state,
        checkpoint="decision.after",
        cancelled_phase="decision",
    )

    assert state["interrupted_generation_persisted"] is True
    blocks = captured["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "react.decision.raw"
    assert blocks[0]["mime"] == "text/plain"
    assert blocks[0]["meta"]["interrupted"] is True
    assert blocks[0]["meta"]["checkpoint"] == "decision.after"
    assert blocks[0]["text"].startswith("<channel:thinking>")


def test_mk_timeline_streamer_hides_provisional_final_answer_by_default():
    solver = _solver_stub()
    solver.comm = SimpleNamespace(delta=_noop_async)
    solver.ctx_browser = None

    _fn, streamer = solver._mk_timeline_streamer("decision")

    target_names = [t.get("name") for t in streamer.targets]
    assert "notes" in target_names
    assert "plan" in target_names
    assert "final_answer" not in target_names


def test_mk_timeline_streamer_uses_existing_answer_index_for_final_answer_stream():
    solver = _solver_stub()
    solver.comm = SimpleNamespace(delta=_noop_async)
    solver.ctx_browser = None
    solver.scratchpad = SimpleNamespace(turn_id="turn-1", _react_answer_delta_idx=3)

    _fn, streamer = solver._mk_timeline_streamer("decision", stream_final_answer=True)

    assert streamer.next_index("final_answer") == 3


@pytest.mark.asyncio
async def test_tool_execution_node_cancels_inflight_phase_on_steer(monkeypatch):
    solver = _solver_stub()
    solver.ctx_browser = SimpleNamespace(
        timeline=SimpleNamespace(last_external_event_seq=0),
        current_turn_blocks=lambda: [],
    )

    async def _fake_execute(*, react, state):
        del react, state
        await asyncio.sleep(30)
        raise AssertionError("cancel expected")

    monkeypatch.setattr(ReactRound, "execute", _fake_execute)

    state = {"last_decision": {"tool_call": {"tool_id": "web_tools.web_search"}}}
    task = asyncio.create_task(solver._tool_execution_node(state))
    await asyncio.sleep(0.05)
    solver.ctx_browser.timeline.last_external_event_seq = 11
    await solver.on_timeline_event(
        type="steer",
        event=SimpleNamespace(sequence=11, text="stop tool", message_id="evt_11"),
        blocks=[],
    )
    out = await task

    assert out.get("exit_reason") is None
    assert out["retry_decision"] is True
    assert out["steer_finalize_mode"] is True
    assert out["steer_interrupt"]["cancelled_phase"] == "tool_execution"


@pytest.mark.asyncio
async def test_decision_node_direct_phase_watcher_interrupts_without_browser_listener():
    solver = _solver_stub()

    class _EventSource:
        def __init__(self):
            self._sent = False

        async def wait_for_events_after(self, cursor, *, block_ms, limit):
            del cursor, block_ms, limit
            await asyncio.sleep(0.05)
            if self._sent:
                await asyncio.sleep(30)
                return []
            self._sent = True
            return [
                SimpleNamespace(
                    kind="steer",
                    sequence=13,
                    text="stop now",
                    message_id="evt_13",
                    stream_id="1775871766336-0",
                )
            ]

    class _Timeline:
        last_external_event_seq = 0
        last_external_event_id = ""

    class _Browser:
        def __init__(self, react, source):
            self.timeline = _Timeline()
            self.runtime_ctx = SimpleNamespace(external_event_source=source)
            self.react = react
            self.ensure_calls = 0

        async def ensure_external_event_listener(self):
            self.ensure_calls += 1

        async def apply_external_events(self, events, *, call_hooks):
            assert call_hooks is True
            for event in events:
                self.timeline.last_external_event_seq = int(getattr(event, "sequence", 0) or 0)
                self.timeline.last_external_event_id = str(getattr(event, "stream_id", "") or "")
                await self.react.on_timeline_event(type=str(event.kind), event=event, blocks=[{"type": "user.steer"}])
            return len(events)

        def current_turn_blocks(self):
            return []

    event_source = _EventSource()
    solver.ctx_browser = _Browser(solver, event_source)

    async def _impl(state, iteration):
        del state, iteration
        await asyncio.sleep(30)
        raise AssertionError("cancel expected")

    solver._decision_node_impl = _impl
    state = {
        "iteration": 0,
        "max_iterations": 5,
    }

    out = await solver._decision_node(state)

    assert solver.ctx_browser.ensure_calls >= 1
    assert out.get("exit_reason") is None
    assert out["retry_decision"] is True
    assert out["steer_finalize_mode"] is True
    assert out["steer_interrupt"]["cancelled_phase"] == "decision"




@pytest.mark.asyncio
async def test_decision_complete_waits_for_exit_grace_and_retries_on_new_external_event(monkeypatch):
    solver = _solver_stub()

    class _Timeline:
        last_external_event_seq = 4

    class _Browser:
        def __init__(self):
            self.timeline = _Timeline()
            self.runtime_ctx = SimpleNamespace(
                workspace_implementation="git",
                bundle_id="bundle.test",
            )
            self.sources_pool = []

        async def wait_and_drain_external_events(self, *, call_hooks, block_ms, limit):
            assert call_hooks is True
            assert block_ms == 250
            assert limit == 100
            self.timeline.last_external_event_seq = 5
            return 1

        async def drain_external_events(self, *, call_hooks):
            del call_hooks
            return 0

        def announce(self, *, blocks):
            del blocks

        @property
        def feedback_updates(self):
            return []

        @property
        def feedback_updates_integrated(self):
            return False

        def contribute_notice(self, *args, **kwargs):
            del args, kwargs

        @property
        def timeline_visible_paths(self):
            return []

    solver.ctx_browser = _Browser()
    solver._latest_external_event_seq_seen = 4
    solver._last_consumed_external_event_seq = 4
    solver._drain_external_events = _noop_async

    async def _fake_retry_with_compaction(**kwargs):
        del kwargs
        return {
            "agent_response": {
                "action": "complete",
                "final_answer": "done",
                "tool_call": None,
                "notes": "",
            }
        }

    async def _fake_emit_event(**kwargs):
        del kwargs

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry.retry_with_compaction",
        _fake_retry_with_compaction,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.emit_event",
        _fake_emit_event,
    )

    async def _fake_react_decision_stream_v2(**kwargs):
        del kwargs
        return {}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.react_decision_stream_v2",
        _fake_react_decision_stream_v2,
    )

    async def _fake_update_announce(**kwargs):
        del kwargs

    solver._update_announce = _fake_update_announce
    solver._mk_mainstream = lambda phase: _noop_async
    solver._mk_exec_code_streamer = lambda phase, idx, execution_id=None: (_noop_async, None)
    solver._mk_content_streamers = lambda phase, sources_list=None, artifact_name=None: ([], [])
    solver._mk_timeline_streamer = lambda *args, **kwargs: (_noop_async, None)
    solver._append_react_timing = lambda **kwargs: None
    solver._adapters_index = lambda adapters: {}
    solver._short_json = lambda obj, max_len=800: str(obj)
    solver._protocol_violation_message = lambda **kwargs: "protocol"
    solver.scratchpad = SimpleNamespace(turn_id="turn-1", register_agentic_response=lambda *args, **kwargs: None)

    old = os.environ.get("REACT_EXTERNAL_EVENT_EXIT_GRACE_MS")
    os.environ["REACT_EXTERNAL_EVENT_EXIT_GRACE_MS"] = "250"
    try:
        state = {
            "iteration": 0,
            "max_iterations": 15,
            "adapters": [],
            "outdir": "/tmp/out",
            "workdir": "/tmp/work",
            "turn_id": "turn-1",
            "decision_retries": 0,
            "max_decision_retries": 2,
        }
        out = await solver._decision_node_impl(state, 0)
    finally:
        if old is None:
            os.environ.pop("REACT_EXTERNAL_EVENT_EXIT_GRACE_MS", None)
        else:
            os.environ["REACT_EXTERNAL_EVENT_EXIT_GRACE_MS"] = old

    assert out["retry_decision"] is True
    assert out.get("exit_reason") is None


@pytest.mark.asyncio
async def test_steer_finalize_enables_agent_final_answer_stream_and_marks_it_emitted(monkeypatch):
    solver = _solver_stub()
    timeline_streamer_calls = []

    class _Timeline:
        last_external_event_seq = 0

    class _Browser:
        def __init__(self):
            self.timeline = _Timeline()
            self.runtime_ctx = SimpleNamespace(
                workspace_implementation="git",
                bundle_id="bundle.test",
            )
            self.sources_pool = []

        async def wait_and_drain_external_events(self, *, call_hooks, block_ms, limit):
            assert call_hooks is True
            assert limit == 100
            return 0

        async def drain_external_events(self, *, call_hooks):
            del call_hooks
            return 0

        def announce(self, *, blocks):
            del blocks

        @property
        def feedback_updates(self):
            return []

        @property
        def feedback_updates_integrated(self):
            return False

        def contribute_notice(self, *args, **kwargs):
            del args, kwargs

        @property
        def timeline_visible_paths(self):
            return []

    solver.ctx_browser = _Browser()
    solver.comm = SimpleNamespace(delta=_noop_async, service_event=_noop_async)
    solver._latest_external_event_seq_seen = 0
    solver._last_consumed_external_event_seq = 0
    solver._drain_external_events = _noop_async

    async def _fake_retry_with_compaction(**kwargs):
        del kwargs
        return {
            "agent_response": {
                "action": "complete",
                "final_answer": "Stopped. Here is the partial result so far.",
                "tool_call": None,
                "notes": "",
                "suggested_followups": [],
            }
        }

    async def _fake_emit_event(**kwargs):
        del kwargs

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry.retry_with_compaction",
        _fake_retry_with_compaction,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.emit_event",
        _fake_emit_event,
    )

    async def _fake_react_decision_stream_v2(**kwargs):
        del kwargs
        return {}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.react_decision_stream_v2",
        _fake_react_decision_stream_v2,
    )

    async def _fake_update_announce(**kwargs):
        del kwargs

    solver._update_announce = _fake_update_announce
    solver._mk_mainstream = lambda phase: _noop_async
    solver._mk_exec_code_streamer = lambda phase, idx, execution_id=None: (_noop_async, None)
    solver._mk_content_streamers = lambda phase, sources_list=None, artifact_name=None: ([], [])

    class _TimelineStreamer:
        def has_started(self, name: str) -> bool:
            return name == "final_answer"

        def next_index(self, name: str) -> int:
            assert name == "final_answer"
            return 7

    def _fake_mk_timeline_streamer(*args, **kwargs):
        timeline_streamer_calls.append(dict(kwargs))
        return _noop_async, _TimelineStreamer()

    solver._mk_timeline_streamer = _fake_mk_timeline_streamer
    solver._append_react_timing = lambda **kwargs: None
    solver._adapters_index = lambda adapters: {}
    solver._short_json = lambda obj, max_len=800: str(obj)
    solver._protocol_violation_message = lambda **kwargs: "protocol"
    solver.scratchpad = SimpleNamespace(
        turn_id="turn-1",
        register_agentic_response=lambda *args, **kwargs: None,
        _react_answer_delta_idx=3,
    )

    state = {
        "iteration": 0,
        "max_iterations": 15,
        "adapters": [],
        "outdir": "/tmp/out",
        "workdir": "/tmp/work",
        "turn_id": "turn-1",
        "decision_retries": 0,
        "max_decision_retries": 2,
        "steer_finalize_mode": True,
        "steer_finalize_rounds_remaining": 1,
    }

    out = await solver._decision_node_impl(state, 0)

    assert out["exit_reason"] == "steer"
    assert out["final_answer"] == "Stopped. Here is the partial result so far."
    assert timeline_streamer_calls
    assert timeline_streamer_calls[0]["stream_final_answer"] is True
    assert getattr(solver.scratchpad, "_final_answer_delta_emitted", False) is True
    assert getattr(solver.scratchpad, "_react_answer_delta_idx", 0) == 7


@pytest.mark.asyncio
async def test_decision_node_always_enables_final_answer_timeline_stream(monkeypatch):
    solver = _solver_stub()
    timeline_streamer_calls = []

    class _Timeline:
        last_external_event_seq = 0

    class _Browser:
        def __init__(self):
            self.timeline = _Timeline()
            self.runtime_ctx = SimpleNamespace(
                workspace_implementation="git",
                bundle_id="bundle.test",
            )
            self.sources_pool = []

        async def wait_and_drain_external_events(self, *, call_hooks, block_ms, limit):
            del call_hooks, block_ms, limit
            return 0

        async def drain_external_events(self, *, call_hooks):
            del call_hooks
            return 0

        def announce(self, *, blocks):
            del blocks

        @property
        def feedback_updates(self):
            return []

        @property
        def feedback_updates_integrated(self):
            return False

        def contribute_notice(self, *args, **kwargs):
            del args, kwargs

        @property
        def timeline_visible_paths(self):
            return []

    solver.ctx_browser = _Browser()
    solver.comm = SimpleNamespace(delta=_noop_async, service_event=_noop_async)
    solver._latest_external_event_seq_seen = 0
    solver._last_consumed_external_event_seq = 0
    solver._drain_external_events = _noop_async

    async def _fake_retry_with_compaction(**kwargs):
        del kwargs
        return {
            "agent_response": {
                "action": "complete",
                "final_answer": "Done.",
                "tool_call": None,
                "notes": "",
                "suggested_followups": [],
            }
        }

    async def _fake_emit_event(**kwargs):
        del kwargs

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry.retry_with_compaction",
        _fake_retry_with_compaction,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.emit_event",
        _fake_emit_event,
    )

    async def _fake_react_decision_stream_v2(**kwargs):
        del kwargs
        return {}

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime.react_decision_stream_v2",
        _fake_react_decision_stream_v2,
    )

    async def _fake_update_announce(**kwargs):
        del kwargs

    solver._update_announce = _fake_update_announce
    solver._mk_mainstream = lambda phase: _noop_async
    solver._mk_exec_code_streamer = lambda phase, idx, execution_id=None: (_noop_async, None)
    solver._mk_content_streamers = lambda phase, sources_list=None, artifact_name=None: ([], [])

    class _TimelineStreamer:
        def has_started(self, name: str) -> bool:
            return False

        def next_index(self, name: str) -> int:
            assert name == "final_answer"
            return 0

    def _fake_mk_timeline_streamer(*args, **kwargs):
        timeline_streamer_calls.append(dict(kwargs))
        return _noop_async, _TimelineStreamer()

    solver._mk_timeline_streamer = _fake_mk_timeline_streamer
    solver._append_react_timing = lambda **kwargs: None
    solver._adapters_index = lambda adapters: {}
    solver._short_json = lambda obj, max_len=800: str(obj)
    solver._protocol_violation_message = lambda **kwargs: "protocol"
    solver.scratchpad = SimpleNamespace(
        turn_id="turn-1",
        register_agentic_response=lambda *args, **kwargs: None,
    )

    state = {
        "iteration": 0,
        "max_iterations": 15,
        "adapters": [],
        "outdir": "/tmp/out",
        "workdir": "/tmp/work",
        "turn_id": "turn-1",
        "decision_retries": 0,
        "max_decision_retries": 2,
        "steer_finalize_mode": False,
    }

    await solver._decision_node_impl(state, 0)

    assert timeline_streamer_calls
    assert timeline_streamer_calls[0]["stream_final_answer"] is True
