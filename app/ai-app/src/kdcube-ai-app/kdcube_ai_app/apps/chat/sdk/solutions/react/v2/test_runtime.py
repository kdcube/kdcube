# SPDX-License-Identifier: MIT

import os
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

    assert out["exit_reason"] == "steer"
    assert out["final_answer"] is None
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

    assert out["exit_reason"] == "steer"
    assert out["final_answer"] is None


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
