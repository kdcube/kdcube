# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import (
    parse_react_decision_bundle_from_raw,
    react_decision_stream_v2,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.runtime import ReactSolverV2
from kdcube_ai_app.apps.chat.sdk.solutions.react.round import ReactRound


class _LogStub:
    def log(self, *args, **kwargs):
        return None


class _ExecStreamerStub:
    def __init__(self, *, complete: bool):
        self.complete = complete

    def is_complete(self):
        return self.complete


def _solver_stub() -> ReactSolverV2:
    solver = ReactSolverV2.__new__(ReactSolverV2)
    solver.log = _LogStub()
    solver.ctx_browser = SimpleNamespace(
        runtime_ctx=SimpleNamespace(turn_id="turn-1"),
        timeline=SimpleNamespace(last_external_event_seq=0),
    )
    solver.comm = SimpleNamespace(delta=_noop_async, service_event=_noop_async)
    solver.tools_subsystem = None
    solver.multi_action_mode = "safe_fanout"
    solver._merge_with_pool = _noop_async
    solver._drain_external_events = _noop_async
    solver._apply_steer_interrupt_if_requested = _always_false
    solver._run_cancellable_phase = _run_phase
    return solver


async def _noop_async(*args, **kwargs):
    return None


async def _always_false(*args, **kwargs):
    return False


async def _run_phase(*, phase, coro):
    del phase
    return False, await coro


class _FakeDecisionService:
    def __init__(self, text: str, chunk_size: int = 31):
        self.text = text
        self.chunk_size = chunk_size

    def get_client(self, _role):
        return object()

    def describe_client(self, _client, role=None):
        return type("Cfg", (), {"provider": "fake", "model_name": role or "fake"})()

    async def stream_model_text_tracked(
        self,
        _client,
        messages,
        on_delta,
        on_complete,
        temperature,
        max_tokens,
        client_cfg,
        debug,
        role,
        debug_citations=False,
    ):
        del messages, temperature, max_tokens, client_cfg, debug, role, debug_citations
        for idx in range(0, len(self.text), self.chunk_size):
            await on_delta(self.text[idx: idx + self.chunk_size])
        await on_complete({})
        return {"text": self.text, "service_error": None}


def test_parse_react_decision_bundle_from_repeated_channels():
    raw = """
<channel:thinking>searching</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"search","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:ReactDecisionOutV2>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"read","tool_call":{"tool_id":"react.read","params":{"paths":["so:sources_pool[1]"]}}}
```</channel:ReactDecisionOutV2>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["candidate_count"] == 2
    assert parsed["errors"] == []
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "web_tools.web_search",
        "react.read",
    ]


def test_parse_react_decision_bundle_from_repeated_full_round_sequences():
    raw = """
<channel:thinking>search one</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"search one","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:ReactDecisionOutV2>
<channel:code></channel:code>
<channel:thinking>search two</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"search two","tool_call":{"tool_id":"web_tools.web_fetch","params":{"url":"https://example.com"}}}
```</channel:ReactDecisionOutV2>
<channel:code></channel:code>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["candidate_count"] == 2
    assert parsed["errors"] == []
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "web_tools.web_search",
        "web_tools.web_fetch",
    ]


def test_parse_react_decision_bundle_ignores_literal_channel_mentions_in_thinking():
    raw = """
<channel:thinking>
Explain the literal syntax `<channel:ReactDecisionOutV2>...</channel:ReactDecisionOutV2>` to the user.
</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"search one","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:ReactDecisionOutV2>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["candidate_count"] == 1
    assert parsed["errors"] == []
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "web_tools.web_search",
    ]


def test_parse_react_decision_bundle_from_multiple_fenced_blocks_in_single_channel():
    json_raw = """
```json
{"action":"call_tool","notes":"search","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```
```json
{"action":"call_tool","notes":"search 2","tool_call":{"tool_id":"web_tools.web_fetch","params":{"url":"https://example.com"}}}
```
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=None, json_raw=json_raw)

    assert parsed["candidate_count"] == 2
    assert parsed["errors"] == []
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "web_tools.web_search",
        "web_tools.web_fetch",
    ]


@pytest.mark.asyncio
async def test_decision_stream_recovers_repeated_action_channels_as_bundle():
    raw = """
<thinking>legacy hidden thought must not poison channel parsing</thinking>
<channel:thinking>writing two files</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"write alpha","tool_call":{"tool_id":"react.write","params":{"path":"outputs/a.md","channel":"canvas","content":"alpha"}}}
```</channel:ReactDecisionOutV2>
<channel:code></channel:code>
<channel:thinking>second accidental thinking block</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"write beta","tool_call":{"tool_id":"react.write","params":{"path":"outputs/b.md","channel":"canvas","content":"beta"}}}
```</channel:ReactDecisionOutV2>
<channel:code></channel:code>
"""
    packet = await react_decision_stream_v2(
        svc=_FakeDecisionService(raw, chunk_size=17),
        agent_name="solver.react.v2.decision.v2.strong",
        adapters=[],
        multi_action_mode="safe_fanout",
        user_blocks=[{"type": "text", "text": "write two files"}],
    )

    assert (packet["log"] or {}).get("error") is None
    assert (packet["log"] or {}).get("bundle_candidate_count") == 2
    assert [d["tool_call"]["params"]["path"] for d in packet["agent_response_bundle"]] == [
        "outputs/a.md",
        "outputs/b.md",
    ]


@pytest.mark.asyncio
async def test_decision_stream_keeps_valid_repeated_action_and_reports_malformed_sibling():
    raw = """
<channel:thinking>writing files</channel:thinking>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":"write alpha","tool_call":{"tool_id":"react.write","params":{"path":"outputs/a.md","channel":"canvas","content":"alpha"}}}
```</channel:ReactDecisionOutV2>
<channel:ReactDecisionOutV2>```json
{"action":"call_tool","notes":
```</channel:ReactDecisionOutV2>
<channel:code></channel:code>
"""
    packet = await react_decision_stream_v2(
        svc=_FakeDecisionService(raw, chunk_size=19),
        agent_name="solver.react.v2.decision.v2.strong",
        adapters=[],
        multi_action_mode="safe_fanout",
        user_blocks=[{"type": "text", "text": "write two files"}],
    )

    assert (packet["log"] or {}).get("error") is None
    assert (packet["log"] or {}).get("bundle_candidate_count") == 2
    assert [d["tool_call"]["params"]["path"] for d in packet["agent_response_bundle"]] == [
        "outputs/a.md",
    ]
    assert (packet["log"] or {}).get("bundle_errors") == ["instance:1:no_json_candidate"]
    assert (packet["log"] or {}).get("bundle_error_items") == [
        {
            "index": 1,
            "error": "no_json_candidate",
            "raw_preview": '{"action":"call_tool","notes":',
        }
    ]


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_accepts_safe_tools():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "search",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "one"},
            },
        },
        {
            "action": "call_tool",
            "notes": "read",
            "tool_call": {
                "tool_id": "react.read",
                "params": {"paths": ["so:sources_pool[1]"]},
            },
        },
        {
            "action": "call_tool",
            "notes": "write",
            "tool_call": {
                "tool_id": "react.write",
                "params": {"path": "outputs/brief.md", "channel": "canvas", "content": "Brief.", "kind": "display"},
            },
        },
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": {}},
    )

    assert error is None
    assert extra is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "web_tools.web_search",
        "react.read",
        "react.write",
    ]


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_accepts_renderer_fanout():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "render deck",
            "tool_call": {
                "tool_id": "rendering_tools.write_pptx",
                "params": {
                    "path": "outputs/news.pptx",
                    "content": "<section><h1>News</h1></section>",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "render document",
            "tool_call": {
                "tool_id": "rendering_tools.write_docx",
                "params": {
                    "path": "outputs/news.docx",
                    "content": "# News\n\nSummary.",
                },
            },
        },
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "rendering_tools.write_pptx": {},
            "rendering_tools.write_docx": {},
        },
    )

    assert error is None
    assert extra is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "rendering_tools.write_pptx",
        "rendering_tools.write_docx",
    ]


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_drops_unsafe_tool_but_keeps_valid_sibling():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "search",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "one"},
            },
        },
        {
            "action": "call_tool",
            "notes": "exec",
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {"prog_name": "demo"},
            },
        },
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": {}},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["web_tools.web_search"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_unsafe_tool",
                "tool_id": "exec_tools.execute_code_python",
                "extra": {"tool_id": "exec_tools.execute_code_python"},
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_allows_single_complete_exec_with_code():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "exec",
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "contract": [{"filename": "outputs/out.txt", "description": "output"}],
                    "prog_name": "demo.py",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "write",
            "tool_call": {
                "tool_id": "react.write",
                "params": {"path": "outputs/brief.md", "channel": "canvas", "content": "Brief.", "kind": "display"},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"exec_tools.execute_code_python": {}},
        allow_single_exec_with_code=True,
    )

    assert error is None
    assert extra is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "exec_tools.execute_code_python",
        "react.write",
    ]


def test_validate_decision_packet_channel_consistency_allows_multi_action_with_stray_code_for_per_item_handling():
    solver = _solver_stub()
    packet = {
        "channels": {
            "code": {"text": "print('x')"},
        }
    }
    bundle = [
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "web_tools.web_search", "params": {"q": "one"}},
        },
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "react.read", "params": {"paths": ["so:sources_pool[1]"]}},
        },
    ]

    error, extra = solver._validate_decision_packet_channel_consistency(packet=packet, bundle=bundle)

    assert error is None
    assert extra is None


def test_validate_decision_packet_channel_consistency_allows_multi_action_with_complete_exec_and_code():
    solver = _solver_stub()
    packet = {
        "channels": {
            "code": {"text": "print('x')"},
        }
    }
    bundle = [
        {
            "action": "call_tool",
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "contract": [{"filename": "outputs/out.txt", "description": "output"}],
                    "prog_name": "x",
                },
            },
        },
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "react.write", "params": {"path": "outputs/a.md", "channel": "canvas", "content": "a"}},
        },
    ]

    error, extra = solver._validate_decision_packet_channel_consistency(
        packet=packet,
        bundle=bundle,
        exec_streamer=_ExecStreamerStub(complete=True),
    )

    assert error is None
    assert extra is None


def test_validate_decision_packet_channel_consistency_allows_multi_action_with_incomplete_exec_for_per_item_handling():
    solver = _solver_stub()
    packet = {
        "channels": {
            "code": {"text": "print('x')"},
        }
    }
    bundle = [
        {
            "action": "call_tool",
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "contract": [{"filename": "outputs/out.txt", "description": "output"}],
                    "prog_name": "x",
                },
            },
        },
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "react.write", "params": {"path": "outputs/a.md", "channel": "canvas", "content": "a"}},
        },
    ]

    error, extra = solver._validate_decision_packet_channel_consistency(
        packet=packet,
        bundle=bundle,
        exec_streamer=_ExecStreamerStub(complete=False),
    )

    assert error is None
    assert extra is None


def test_validate_decision_packet_channel_consistency_allows_single_exec_with_code():
    solver = _solver_stub()
    packet = {
        "channels": {
            "code": {"text": "print('x')"},
        }
    }
    bundle = [
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "exec_tools.execute_code_python", "params": {"contract": [], "prog_name": "x"}},
        },
    ]

    error, extra = solver._validate_decision_packet_channel_consistency(packet=packet, bundle=bundle)

    assert error is None
    assert extra is None


def test_validate_decision_packet_channel_consistency_rejects_non_exec_with_code():
    solver = _solver_stub()
    packet = {
        "channels": {
            "code": {"text": "print('x')"},
        }
    }
    bundle = [
        {
            "action": "call_tool",
            "tool_call": {"tool_id": "web_tools.web_search", "params": {"q": "one"}},
        },
    ]

    error, extra = solver._validate_decision_packet_channel_consistency(packet=packet, bundle=bundle)

    assert error == "code_channel_without_single_exec"
    assert extra == {"action": "call_tool", "tool_id": "web_tools.web_search"}


@pytest.mark.asyncio
async def test_tool_execution_node_runs_pending_bundle_sequentially(monkeypatch):
    solver = _solver_stub()
    executed: list[tuple[str, str]] = []
    noted: list[tuple[str, str]] = []
    merged: list[int] = []

    async def _fake_merge(*, collections, workdir, outdir, record_streamers, timeline_streamer):
        del workdir, outdir, record_streamers, timeline_streamer
        merged.append(len(collections[0]))

    async def _fake_execute(*, react, state):
        del react
        decision = state["last_decision"]
        tool_id = decision["tool_call"]["tool_id"]
        call_id = state.get("pending_tool_call_id") or ""
        executed.append((tool_id, call_id))
        state["pending_sources"] = [{"sid": len(executed)}]
        return state

    def _fake_note(*, ctx_browser, notes, tool_call_id, tool_id, action, iteration):
        del ctx_browser, action, iteration
        noted.append((tool_id, tool_call_id))

    solver._merge_with_pool = _fake_merge
    monkeypatch.setattr(ReactRound, "execute", _fake_execute)
    monkeypatch.setattr(ReactRound, "note", _fake_note)

    state = {
        "workdir": ".",
        "outdir": ".",
        "pending_tool_bundle": [
            {
                "decision": {
                    "action": "call_tool",
                    "notes": "search",
                    "tool_call": {"tool_id": "web_tools.web_search", "params": {"q": "one"}},
                },
                "tool_call_id": "tc_first",
            },
            {
                "decision": {
                    "action": "call_tool",
                    "notes": "read",
                    "tool_call": {"tool_id": "react.read", "params": {"paths": ["so:sources_pool[1]"]}},
                },
                "tool_call_id": "tc_second",
            },
        ],
    }

    out = await solver._tool_execution_node(state)

    assert executed == [
        ("web_tools.web_search", "tc_first"),
        ("react.read", "tc_second"),
    ]
    assert noted == [
        ("web_tools.web_search", "tc_first"),
        ("react.read", "tc_second"),
    ]
    assert merged == [1, 1]
    assert out["pending_tool_call_id"] is None
