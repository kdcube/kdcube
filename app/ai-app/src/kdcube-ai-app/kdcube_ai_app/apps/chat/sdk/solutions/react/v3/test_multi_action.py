# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import STRATEGY_COMPATIBILITY_MATRIX
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


def _adapter_with_strategy(*strategies: str):
    return {"doc": {"tool_traits": {"strategy": list(strategies)}}}


def _matrix_tool(strategy: str, index: int) -> dict:
    return {
        "action": "call_tool",
        "notes": f"{strategy} {index}",
        "tool_call": {
            "tool_id": f"matrix_tools.{strategy}_{index}",
            "params": {},
        },
    }


def _matrix_adapters(*strategies: str) -> dict:
    return {
        f"matrix_tools.{strategy}_{index}": (
            {} if strategy == "unknown" else _adapter_with_strategy(strategy)
        )
        for index, strategy in enumerate(strategies)
    }


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
<channel:action>```json
{"action":"call_tool","notes":"search","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:action>
<channel:action>```json
{"action":"call_tool","notes":"read","tool_call":{"tool_id":"react.read","params":{"paths":["so:sources_pool[1]"]}}}
```</channel:action>
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
<channel:action>```json
{"action":"call_tool","notes":"search one","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:action>
<channel:code></channel:code>
<channel:thinking>search two</channel:thinking>
<channel:action>```json
{"action":"call_tool","notes":"search two","tool_call":{"tool_id":"web_tools.web_fetch","params":{"url":"https://example.com"}}}
```</channel:action>
<channel:code></channel:code>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["candidate_count"] == 2
    assert parsed["errors"] == []
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "web_tools.web_search",
        "web_tools.web_fetch",
    ]


def test_parse_react_decision_json_with_embedded_fence_text():
    raw = """
<channel:action>```json
{"action":"call_tool","notes":"write","tool_call":{"tool_id":"react.write","params":{"path":"outputs/report.html","channel":"canvas","content":"<pre>```html\\n<div>ok</div>\\n```</pre>"}}}
```
</channel:action>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["errors"] == []
    assert parsed["candidate_count"] == 1
    assert parsed["decisions"][0]["tool_call"]["tool_id"] == "react.write"


def test_parse_react_decision_bundle_not_poisoned_by_prior_fenced_content():
    raw = """
<channel:action>```json
{"action":"call_tool","notes":"write","tool_call":{"tool_id":"react.write","params":{"path":"outputs/a.md","content":"```html\\n<div>one</div>\\n```"}}}
```</channel:action>
<channel:action>```json
{"action":"call_tool","notes":"search","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"two"}}}
```</channel:action>
"""
    parsed = parse_react_decision_bundle_from_raw(full_raw=raw, json_raw=None)

    assert parsed["errors"] == []
    assert parsed["candidate_count"] == 2
    assert [d["tool_call"]["tool_id"] for d in parsed["decisions"]] == [
        "react.write",
        "web_tools.web_search",
    ]


@pytest.mark.asyncio
async def test_decision_stream_parses_html_content_inside_structured_channel():
    html = """<!DOCTYPE html>
<html>
<head>
<style>
@page { size: A4 portrait; margin: 20mm; }
:root { --primary: #1e3a8a; --accent: #3b82f6; }
.card { border: 1px solid #e2e8f0; }
</style>
</head>
<body>
<pre>```html
<div class="card">embedded fenced sample</div>
```</pre>
<p>Report body</p>
</body>
</html>"""
    decision = {
        "action": "call_tool",
        "notes": "Writing HTML source",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "turn_2026-05-19-01-01-49-177/outputs/science_news/report.html",
                "channel": "canvas",
                "content": html,
                "kind": "file",
            },
        },
    }
    raw = (
        "<channel:thinking>Preparing report artifacts.</channel:thinking>\n"
        "I have the data. Now I will write the HTML source.\n"
        "<channel:action>```json\n"
        f"{json.dumps(decision, ensure_ascii=False, indent=2)}\n"
        "```\n</channel:action>\n"
        "<channel:code></channel:code>\n"
    )

    packet = await react_decision_stream_v2(
        svc=_FakeDecisionService(raw, chunk_size=23),
        agent_name="solver.react.v2.decision.v2.strong",
        adapters=[],
        multi_action_mode="safe_fanout",
        user_blocks=[{"type": "text", "text": "write report"}],
    )

    assert (packet["log"] or {}).get("error") is None
    assert (packet["log"] or {}).get("bundle_errors") == []
    tool_call = packet["agent_response"]["tool_call"]
    assert tool_call["tool_id"] == "react.write"
    assert tool_call["params"]["content"] == html


def test_parse_react_decision_bundle_ignores_literal_channel_mentions_in_thinking():
    raw = """
<channel:thinking>
Explain the literal syntax `<channel:action>...</channel:action>` to the user.
</channel:thinking>
<channel:action>```json
{"action":"call_tool","notes":"search one","tool_call":{"tool_id":"web_tools.web_search","params":{"q":"one"}}}
```</channel:action>
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


def test_stream_policy_recovery_keeps_accepted_search_and_drops_later_final_answer():
    solver = _solver_stub()
    search_decision = {
        "action": "call_tool",
        "notes": "Attempting forbidden pair",
        "tool_call": {
            "tool_id": "web_tools.web_search",
            "params": {"queries": ["kdcube"], "n": 3},
        },
    }
    denied_final = {
        "action": "complete",
        "notes": "",
        "final_answer": "done",
    }
    raw_search = "```json\n" + json.dumps(search_decision) + "\n```"
    packet = {
        "agent_response": denied_final,
        "agent_response_bundle": [search_decision, denied_final],
        "log": {"error": None},
    }

    recovered = solver._stream_policy_recovery_packet(
        decision_packet=packet,
        decision_stream_instances={0: {"raw_chunks": [raw_search]}},
        accepted_actions=[SimpleNamespace(index=0)],
        rejected_items=[
            {
                "index": 1,
                "action": "complete",
                "code": "multi_action_bundle_final_answer_after_non_neutral",
                "extra": {
                    "first_index": 0,
                    "first_tool_id": "web_tools.web_search",
                    "first_strategy": ["exploration"],
                },
            }
        ],
    )

    assert recovered is not None
    assert recovered["agent_response"]["tool_call"]["tool_id"] == "web_tools.web_search"
    assert len(recovered["agent_response_bundle"]) == 1
    assert recovered["agent_response_bundle"][0]["action"] == "call_tool"
    assert recovered["agent_response_bundle"][0]["tool_call"]["params"]["queries"] == ["kdcube"]
    assert recovered["log"]["error"] is None
    assert recovered["log"]["stream_policy_recovered"] is True
    assert recovered["log"]["stream_policy_rejected_items"] == [
        {
            "index": 1,
            "action": "complete",
            "code": "multi_action_bundle_final_answer_after_non_neutral",
            "extra": {
                "first_index": 0,
                "first_tool_id": "web_tools.web_search",
                "first_strategy": ["exploration"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_decision_stream_recovers_repeated_action_channels_as_bundle():
    raw = """
<thinking>legacy hidden thought must not poison channel parsing</thinking>
<channel:thinking>writing two files</channel:thinking>
<channel:action>```json
{"action":"call_tool","notes":"write alpha","tool_call":{"tool_id":"react.write","params":{"path":"outputs/a.md","channel":"canvas","content":"alpha"}}}
```</channel:action>
<channel:code></channel:code>
<channel:thinking>second accidental thinking block</channel:thinking>
<channel:action>```json
{"action":"call_tool","notes":"write beta","tool_call":{"tool_id":"react.write","params":{"path":"outputs/b.md","channel":"canvas","content":"beta"}}}
```</channel:action>
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
<channel:action>```json
{"action":"call_tool","notes":"write alpha","tool_call":{"tool_id":"react.write","params":{"path":"outputs/a.md","channel":"canvas","content":"alpha"}}}
```</channel:action>
<channel:action>```json
{"action":"call_tool","notes":
```</channel:action>
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
async def test_prepare_safe_multi_action_bundle_accepts_strategy_compatible_exploration_tools():
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
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "web_tools.web_search",
        "react.read",
    ]
    assert extra is None


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_rejects_third_action_even_when_strategy_compatible():
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
            "notes": "inspect",
            "tool_call": {
                "tool_id": "custom_tools.inspect",
                "params": {},
            },
        },
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "web_tools.web_search": _adapter_with_strategy("exploration"),
            "custom_tools.inspect": {},
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "web_tools.web_search",
        "react.read",
    ]
    assert extra == {
        "rejected": [
            {
                "index": 2,
                "code": "multi_action_bundle_too_many_actions",
                "tool_id": "custom_tools.inspect",
                "extra": {
                    "index": 2,
                    "max_actions": 2,
                },
            }
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_strategy,later_strategy",
    [
        (first, later)
        for first, row in STRATEGY_COMPATIBILITY_MATRIX.items()
        for later in row
    ],
)
async def test_prepare_safe_multi_action_bundle_applies_ordered_strategy_matrix(
    first_strategy: str,
    later_strategy: str,
):
    solver = _solver_stub()
    expected_pair_allowed = STRATEGY_COMPATIBILITY_MATRIX[first_strategy][later_strategy]
    bundle = [
        _matrix_tool(first_strategy, 0),
        _matrix_tool(later_strategy, 1),
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id=_matrix_adapters(first_strategy, later_strategy),
    )

    assert error is None
    assert [item["tool_call"]["tool_id"] for item in accepted] == [
        f"matrix_tools.{first_strategy}_0",
        *([f"matrix_tools.{later_strategy}_1"] if expected_pair_allowed else []),
    ]
    if expected_pair_allowed:
        assert extra is None
    else:
        assert extra == {
            "rejected": [
                {
                    "index": 1,
                    "code": "multi_action_bundle_strategy_incompatible",
                    "tool_id": f"matrix_tools.{later_strategy}_1",
                    "extra": {
                        "tool_id": f"matrix_tools.{later_strategy}_1",
                        "strategy": [later_strategy],
                        "first_index": 0,
                        "first_tool_id": f"matrix_tools.{first_strategy}_0",
                        "first_strategy": [first_strategy],
                    },
                }
            ]
        }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_checks_candidate_against_all_prior_accepted_actions():
    solver = _solver_stub()
    solver.MAX_ACTIONS_PER_ROUND = 3
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
            "notes": "record memory",
            "tool_call": {
                "tool_id": "memory.record_memory",
                "params": {"text": "prefers short answers"},
            },
        },
        {
            "action": "call_tool",
            "notes": "write",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/brief.md",
                    "channel": "canvas",
                    "content": "Brief.",
                    "kind": "display",
                },
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "web_tools.web_search": _adapter_with_strategy("exploration"),
            "memory.record_memory": _adapter_with_strategy("neutral"),
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "web_tools.web_search",
        "memory.record_memory",
    ]
    assert extra == {
        "rejected": [
            {
                "index": 2,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "react.write",
                "extra": {
                    "tool_id": "react.write",
                    "strategy": ["exploitation"],
                    "first_index": 0,
                    "first_tool_id": "web_tools.web_search",
                    "first_strategy": ["exploration"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_rejects_unknown_with_exploration():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "search",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {},
            },
        },
        {
            "action": "call_tool",
            "notes": "unmarked inspect",
            "tool_call": {
                "tool_id": "custom_tools.inspect",
                "params": {},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "web_tools.web_search": _adapter_with_strategy("exploration"),
            "custom_tools.inspect": {},
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["web_tools.web_search"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "custom_tools.inspect",
                "extra": {
                    "tool_id": "custom_tools.inspect",
                    "strategy": ["unknown"],
                    "first_index": 0,
                    "first_tool_id": "web_tools.web_search",
                    "first_strategy": ["exploration"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_rejects_unknown_with_neutral():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "record note",
            "tool_call": {
                "tool_id": "memory.record_memory",
                "params": {},
            },
        },
        {
            "action": "call_tool",
            "notes": "unmarked inspect",
            "tool_call": {
                "tool_id": "custom_tools.inspect",
                "params": {},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "memory.record_memory": _adapter_with_strategy("neutral"),
            "custom_tools.inspect": {},
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["memory.record_memory"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "custom_tools.inspect",
                "extra": {
                    "tool_id": "custom_tools.inspect",
                    "strategy": ["unknown"],
                    "first_index": 0,
                    "first_tool_id": "memory.record_memory",
                    "first_strategy": ["neutral"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_rejects_unknown_with_exploitation():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "render",
            "tool_call": {
                "tool_id": "rendering_tools.write_pptx",
                "params": {},
            },
        },
        {
            "action": "call_tool",
            "notes": "unmarked inspect",
            "tool_call": {
                "tool_id": "custom_tools.inspect",
                "params": {},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "rendering_tools.write_pptx": _adapter_with_strategy("exploitation"),
            "custom_tools.inspect": {},
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["rendering_tools.write_pptx"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "custom_tools.inspect",
                "extra": {
                    "tool_id": "custom_tools.inspect",
                    "strategy": ["unknown"],
                    "first_index": 0,
                    "first_tool_id": "rendering_tools.write_pptx",
                    "first_strategy": ["exploitation"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_rejects_unknown_with_unknown():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "unmarked one",
            "tool_call": {
                "tool_id": "custom_tools.one",
                "params": {},
            },
        },
        {
            "action": "call_tool",
            "notes": "unmarked two",
            "tool_call": {
                "tool_id": "custom_tools.two",
                "params": {},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "custom_tools.one": {},
            "custom_tools.two": {},
        },
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["custom_tools.one"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "custom_tools.two",
                "extra": {
                    "tool_id": "custom_tools.two",
                    "strategy": ["unknown"],
                    "first_index": 0,
                    "first_tool_id": "custom_tools.one",
                    "first_strategy": ["unknown"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_allows_skill_read_with_independent_action():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "load skill",
            "tool_call": {
                "tool_id": "react.read",
                "params": {"paths": ["sk:public.pdf-press"]},
            },
        },
        {
            "action": "call_tool",
            "notes": "write report",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/report.html",
                    "channel": "canvas",
                    "content": "<html></html>",
                    "kind": "display",
                },
            },
        },
    ]
    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["react.read"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_strategy_incompatible",
                "tool_id": "react.write",
                "extra": {
                    "tool_id": "react.write",
                    "strategy": ["exploitation"],
                    "first_index": 0,
                    "first_tool_id": "react.read",
                    "first_strategy": ["exploration"],
                },
            }
        ]
    }


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
            "rendering_tools.write_pptx": _adapter_with_strategy("exploitation"),
            "rendering_tools.write_docx": _adapter_with_strategy("exploitation"),
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
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
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
        adapters_by_id={"exec_tools.execute_code_python": _adapter_with_strategy("exploration", "exploitation")},
        allow_single_exec_with_code=True,
    )

    assert error is None
    assert extra is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "exec_tools.execute_code_python",
        "react.write",
    ]


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_allows_complete_exec_with_neutral_tool():
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
            "notes": "record memory",
            "tool_call": {
                "tool_id": "memory.record_memory",
                "params": {"text": "prefers short answers"},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={
            "exec_tools.execute_code_python": _adapter_with_strategy("exploration", "exploitation"),
            "memory.record_memory": _adapter_with_strategy("neutral"),
        },
        allow_single_exec_with_code=True,
    )

    assert error is None
    assert extra is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == [
        "exec_tools.execute_code_python",
        "memory.record_memory",
    ]


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_carries_final_answer_after_neutral_tool():
    solver = _solver_stub()
    final_decision = {
        "action": "complete",
        "final_answer": "Saved.",
        "suggested_followups": [],
    }
    bundle = [
        {
            "action": "call_tool",
            "notes": "record memory",
            "tool_call": {
                "tool_id": "memory.record_memory",
                "params": {"text": "prefers short answers"},
            },
        },
        final_decision,
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"memory.record_memory": _adapter_with_strategy("neutral")},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["memory.record_memory"]
    assert extra == {"final_decision": final_decision, "final_index": 1}


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_allows_neutral_tool_after_final_answer():
    solver = _solver_stub()
    final_decision = {
        "action": "complete",
        "final_answer": "Saved.",
        "suggested_followups": [],
    }
    bundle = [
        final_decision,
        {
            "action": "call_tool",
            "notes": "record memory",
            "tool_call": {
                "tool_id": "memory.record_memory",
                "params": {"text": "prefers short answers"},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"memory.record_memory": _adapter_with_strategy("neutral")},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["memory.record_memory"]
    assert extra == {"final_decision": final_decision, "final_index": 0}


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_drops_final_answer_after_non_neutral_tool():
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
            "action": "complete",
            "final_answer": "Done.",
            "suggested_followups": [],
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
    )

    assert error is None
    assert [d["tool_call"]["tool_id"] for d in accepted] == ["web_tools.web_search"]
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_final_answer_after_non_neutral",
                "extra": {
                    "first_index": 0,
                    "first_tool_id": "web_tools.web_search",
                    "first_strategy": ["exploration"],
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_drops_non_neutral_tool_after_final_answer():
    solver = _solver_stub()
    final_decision = {
        "action": "complete",
        "final_answer": "Done.",
        "suggested_followups": [],
    }
    bundle = [
        final_decision,
        {
            "action": "call_tool",
            "notes": "search",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "one"},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
    )

    assert error is None
    assert accepted == []
    assert extra == {
        "rejected": [
            {
                "index": 1,
                "code": "multi_action_bundle_non_neutral_after_final_answer",
                "tool_id": "web_tools.web_search",
                "extra": {
                    "tool_id": "web_tools.web_search",
                    "strategy": ["exploration"],
                    "first_index": 0,
                    "first_action": "complete",
                },
            }
        ],
        "final_decision": final_decision,
        "final_index": 0,
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_suppresses_tool_final_answer_but_keeps_tool():
    solver = _solver_stub()
    bundle = [
        {
            "action": "call_tool",
            "notes": "search",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "one"},
            },
            "final_answer": "done",
        },
        {
            "action": "call_tool",
            "notes": "search again",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "two"},
            },
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
    )

    assert error is None
    assert [d["tool_call"]["params"]["q"] for d in accepted] == ["one", "two"]
    assert accepted[0]["final_answer"] == ""
    assert extra == {
        "suppressed": [
            {
                "index": 0,
                "code": "final_answer_with_tool_call",
                "tool_id": "web_tools.web_search",
                "extra": {
                    "suppressed_field": "final_answer",
                    "suppressed_text_symbols": 4,
                    "tool_id": "web_tools.web_search",
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_prepare_safe_multi_action_bundle_keeps_two_explorations_and_drops_later_final_answer():
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
            "notes": "search again",
            "tool_call": {
                "tool_id": "web_tools.web_search",
                "params": {"q": "two"},
            },
        },
        {
            "action": "complete",
            "final_answer": "done",
            "suggested_followups": [],
        },
    ]

    accepted, error, extra = await solver._prepare_safe_multi_action_bundle(
        bundle=bundle,
        adapters_by_id={"web_tools.web_search": _adapter_with_strategy("exploration")},
    )

    assert error is None
    assert [d["tool_call"]["params"]["q"] for d in accepted] == ["one", "two"]
    assert extra == {
        "rejected": [
            {
                "index": 2,
                "code": "multi_action_bundle_too_many_actions",
                "extra": {
                    "index": 2,
                    "max_actions": 2,
                },
            }
        ]
    }


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
