# SPDX-License-Identifier: MIT

"""Regression: content on the fence lines of a <channel:action> JSON block.

A locally served model first emitted

    <channel:action>```json {
      "action": "complete", ...
    } ```</channel:action>

and later emitted the opening fence, complete object, and closing fence on one
physical line. The char-level streaming layer accepted both layouts and
streamed final_answer to the user; post-hoc fence stripping broke them, the
Action failed validation, and a retry produced a duplicate answer. Post-hoc
parsing must accept every container layout the streaming layer accepts.
"""

import json

from kdcube_ai_app.apps.chat.sdk.streaming.workspace_streamer import (
    _strip_structured_fences as strip_v1,
)
from kdcube_ai_app.apps.chat.sdk.streaming.workspace_streamer_v3 import (
    _strip_structured_fences as strip_v3,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.agents.decision import (
    parse_react_decision_bundle_from_raw,
    parse_single_react_decision_from_channel_text,
)

FENCED = (
    '```json {\n'
    '  "action": "complete",\n'
    '  "notes": "",\n'
    '  "final_answer": "I can help you with a wide range of tasks",\n'
    '  "suggested_followups": ["Draft a document"]\n'
    '} ```'
)
FENCED_ONE_LINE = (
    '```json { "action": "complete", "notes": "", "tool_call": null, '
    '"final_answer": "I can help you with a wide range of tasks", '
    '"suggested_followups": ["Draft a document"] } ```'
)
RAW = (
    '<channel:thinking>\nElena asked what I can do.\n</channel:thinking>\n'
    f'<channel:action>{FENCED}</channel:action>\n'
    '<channel:code></channel:code>'
)


def test_fence_strip_keeps_opening_fence_line_content():
    for fn in (strip_v3, strip_v1):
        obj = json.loads(fn(FENCED))
        assert obj["action"] == "complete"


def test_fence_strip_keeps_entire_json_on_one_fence_line():
    for fn in (strip_v3, strip_v1):
        obj = json.loads(fn(FENCED_ONE_LINE))
        assert obj["action"] == "complete"
        assert obj["final_answer"].startswith("I can help you")


def test_fence_strip_clean_shapes_unchanged():
    for fn in (strip_v3, strip_v1):
        assert fn('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert fn('```\n{"a": 1}\n```') == '{"a": 1}'
        assert fn('{"a": 1}') == '{"a": 1}'
        assert fn('``` {"a": 1}\n```') == '{"a": 1}'


def test_bundle_parse_recovers_incident_raw():
    bundle = parse_react_decision_bundle_from_raw(
        full_raw=RAW, json_raw=strip_v3(FENCED),
    )
    assert bundle["decisions"], bundle
    decision = bundle["decisions"][0]
    assert decision["action"] == "complete"
    assert decision["final_answer"].startswith("I can help you")
    assert not bundle["errors"]


def test_bundle_parse_recovers_one_line_action_without_provider_raw():
    bundle = parse_react_decision_bundle_from_raw(
        full_raw=None,
        json_raw=strip_v3(FENCED_ONE_LINE),
    )
    assert bundle["decisions"], bundle
    decision = bundle["decisions"][0]
    assert decision["action"] == "complete"
    assert decision["final_answer"].startswith("I can help you")
    assert not bundle["errors"]


def test_single_instance_parse_accepts_fenced_instance_text():
    decision, error = parse_single_react_decision_from_channel_text(FENCED)
    assert error is None, error
    assert decision["action"] == "complete"


# --- keystone: per-lane streamed state feeds the salvage as FACT ---

import asyncio

from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.action_overseer import (
    RoundActionOverseer,
)


def test_streamed_state_reflects_real_gate_emission():
    async def run():
        emitted = []

        async def real(**kw):
            emitted.append(kw.get("text", ""))

        ov = RoundActionOverseer(resolve_traits=lambda **k: {})
        # allowed final_answer lane → user saw it, text captured verbatim
        g = ov.gate_for(action_index=0, emit_delta=real, lane="final_answer")
        await g.emit_delta(text="I can help ")
        await g.emit_delta(text="with tasks")
        await g.allow()
        st = ov.streamed_state()
        assert st["answer_streamed"] is True
        assert st["answer_text"] == "I can help with tasks"

        # denied lane → user saw nothing, no salvage
        ov2 = RoundActionOverseer(resolve_traits=lambda **k: {})
        g2 = ov2.gate_for(action_index=0, emit_delta=real, lane="final_answer")
        await g2.emit_delta(text="ghost answer")
        await g2.deny()
        st2 = ov2.streamed_state()
        assert st2["answer_streamed"] is False
        assert st2["answer_text"] == ""

    asyncio.run(run())
