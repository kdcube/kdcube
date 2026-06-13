# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import STRATEGY_COMPATIBILITY_MATRIX
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.action_overseer import RoundActionOverseer
from kdcube_ai_app.apps.chat.sdk.streaming.stream_policy import StreamPolicyViolation


def _traits(tool_id: str):
    return {
        "web_tools.web_search": {"strategy": ["exploration"]},
        "react.read": {"strategy": ["exploration"]},
        "react.write": {"strategy": ["exploitation"]},
        "memory.record_memory": {"strategy": ["neutral"]},
    }.get(tool_id, {})


@pytest.mark.asyncio
async def test_action_gate_buffers_then_flushes_when_first_tool_identity_is_allowed():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)

    await gate.emit_delta(text="buffered")
    assert emitted == []

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="web_tools.web_search",
        action_gate=gate,
    )

    assert emitted == [{"text": "buffered"}]
    await gate.emit_delta(text="live")
    assert emitted[-1] == {"text": "live"}


@pytest.mark.asyncio
async def test_final_answer_gate_opens_after_neutral_tool():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    neutral_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    neutral_answer_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta, lane="final_answer")
    final_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)
    final_answer_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta, lane="final_answer")

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="memory.record_memory",
        action_gate=neutral_gate,
        answer_gate=neutral_answer_gate,
    )
    await final_answer_gate.emit_delta(marker="answer", text="Saved.")
    await overseer.observe_action_signal(
        action_index=1,
        action="complete",
        tool_id="",
        action_gate=final_gate,
        answer_gate=final_answer_gate,
    )

    assert {"marker": "answer", "text": "Saved."} in emitted


@pytest.mark.asyncio
async def test_final_answer_gate_is_denied_after_non_neutral_tool():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    search_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    final_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)
    final_answer_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta, lane="final_answer")

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="web_tools.web_search",
        action_gate=search_gate,
    )
    await final_answer_gate.emit_delta(marker="answer", text="Premature.")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=1,
            action="complete",
            tool_id="",
            action_gate=final_gate,
            answer_gate=final_answer_gate,
        )

    assert exc.value.code == "multi_action_bundle_final_answer_after_non_neutral"
    assert overseer.accepted_actions()[0].tool_id == "web_tools.web_search"
    assert overseer.rejected_actions() == [
        {
            "index": 1,
            "action": "complete",
            "code": "multi_action_bundle_final_answer_after_non_neutral",
            "extra": {
                "index": 1,
                "action": "complete",
                "first_index": 0,
                "first_tool_id": "web_tools.web_search",
                "first_strategy": ["exploration"],
            },
        }
    ]
    assert {"marker": "answer", "text": "Premature."} not in emitted


@pytest.mark.asyncio
async def test_final_action_is_classified_as_exploitation_but_may_follow_neutral():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    final_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    final_answer_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta, lane="final_answer")

    observed = await overseer.observe_action_signal(
        action_index=0,
        action="complete",
        tool_id="",
        action_gate=final_gate,
        answer_gate=final_answer_gate,
    )

    assert observed.strategies == {"exploitation"}


@pytest.mark.asyncio
async def test_unknown_tool_is_denied_with_exploration_tool():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    search_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    unknown_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="web_tools.web_search",
        action_gate=search_gate,
    )
    await unknown_gate.emit_delta(text="unknown buffered")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=1,
            action="call_tool",
            tool_id="custom_tools.inspect",
            action_gate=unknown_gate,
        )

    assert exc.value.code == "multi_action_bundle_strategy_incompatible"
    assert exc.value.extra["strategy"] == ["unknown"]
    assert {"text": "unknown buffered"} not in emitted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_strategy,later_strategy",
    [
        (first, later)
        for first, row in STRATEGY_COMPATIBILITY_MATRIX.items()
        for later in row
    ],
)
async def test_overseer_applies_ordered_strategy_matrix(first_strategy: str, later_strategy: str):
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    def traits(tool_id: str):
        if tool_id.endswith("_first"):
            return {} if first_strategy == "unknown" else {"strategy": [first_strategy]}
        if tool_id.endswith("_later"):
            return {} if later_strategy == "unknown" else {"strategy": [later_strategy]}
        return {}

    overseer = RoundActionOverseer(resolve_traits=traits)
    first_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    later_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await first_gate.emit_delta(text="first")
    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="matrix_tools.action_first",
        action_gate=first_gate,
    )
    await later_gate.emit_delta(text="later")

    if STRATEGY_COMPATIBILITY_MATRIX[first_strategy][later_strategy]:
        await overseer.observe_action_signal(
            action_index=1,
            action="call_tool",
            tool_id="matrix_tools.action_later",
            action_gate=later_gate,
        )
        assert {"text": "later"} in emitted
    else:
        with pytest.raises(StreamPolicyViolation) as exc:
            await overseer.observe_action_signal(
                action_index=1,
                action="call_tool",
                tool_id="matrix_tools.action_later",
                action_gate=later_gate,
            )
        assert exc.value.code == "multi_action_bundle_strategy_incompatible"
        assert exc.value.extra["first_strategy"] == [first_strategy]
        assert exc.value.extra["strategy"] == [later_strategy]
        assert {"text": "later"} not in emitted

    assert {"text": "first"} in emitted


@pytest.mark.asyncio
async def test_first_unknown_tool_is_admitted_but_later_exploration_is_denied():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    unknown_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    search_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await unknown_gate.emit_delta(text="unknown first")
    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="custom_tools.inspect",
        action_gate=unknown_gate,
    )
    await search_gate.emit_delta(text="search second")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=1,
            action="call_tool",
            tool_id="web_tools.web_search",
            action_gate=search_gate,
        )

    assert {"text": "unknown first"} in emitted
    assert exc.value.code == "multi_action_bundle_strategy_incompatible"
    assert exc.value.extra["first_strategy"] == ["unknown"]
    assert {"text": "search second"} not in emitted


@pytest.mark.asyncio
async def test_third_action_is_denied_even_when_strategy_compatible():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    search_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    second_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="web_tools.web_search",
        action_gate=search_gate,
    )
    await overseer.observe_action_signal(
        action_index=1,
        action="call_tool",
        tool_id="react.read",
        action_gate=second_gate,
    )
    third_gate = overseer.gate_for(action_index=2, emit_delta=emit_delta)
    await third_gate.emit_delta(text="third buffered")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=2,
            action="call_tool",
            tool_id="react.read",
            action_gate=third_gate,
        )

    assert exc.value.code == "multi_action_bundle_too_many_actions"
    assert exc.value.extra["max_actions"] == 2
    assert {"text": "third buffered"} not in emitted


@pytest.mark.asyncio
async def test_unknown_tool_is_denied_with_exploitation_tool():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    write_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    unknown_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await overseer.observe_action_signal(
        action_index=0,
        action="call_tool",
        tool_id="react.write",
        action_gate=write_gate,
    )
    await unknown_gate.emit_delta(text="unknown buffered")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=1,
            action="call_tool",
            tool_id="custom_tools.inspect",
            action_gate=unknown_gate,
        )

    assert exc.value.code == "multi_action_bundle_strategy_incompatible"
    assert exc.value.extra["strategy"] == ["unknown"]
    assert {"text": "unknown buffered"} not in emitted


@pytest.mark.asyncio
async def test_non_neutral_tool_is_denied_after_final_answer():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    final_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    final_answer_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta, lane="final_answer")
    write_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await overseer.observe_action_signal(
        action_index=0,
        action="complete",
        tool_id="",
        action_gate=final_gate,
        answer_gate=final_answer_gate,
    )
    await write_gate.emit_delta(text="write output")

    with pytest.raises(StreamPolicyViolation) as exc:
        await overseer.observe_action_signal(
            action_index=1,
            action="call_tool",
            tool_id="react.write",
            action_gate=write_gate,
        )

    assert exc.value.code == "multi_action_bundle_non_neutral_after_final_answer"
    assert {"text": "write output"} not in emitted


@pytest.mark.asyncio
async def test_neutral_tool_is_allowed_after_final_answer():
    emitted = []

    async def emit_delta(**kwargs):
        emitted.append(kwargs)

    overseer = RoundActionOverseer(resolve_traits=_traits)
    final_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta)
    final_answer_gate = overseer.gate_for(action_index=0, emit_delta=emit_delta, lane="final_answer")
    neutral_gate = overseer.gate_for(action_index=1, emit_delta=emit_delta)

    await overseer.observe_action_signal(
        action_index=0,
        action="complete",
        tool_id="",
        action_gate=final_gate,
        answer_gate=final_answer_gate,
    )
    await neutral_gate.emit_delta(text="neutral output")
    await overseer.observe_action_signal(
        action_index=1,
        action="call_tool",
        tool_id="memory.record_memory",
        action_gate=neutral_gate,
    )

    assert {"text": "neutral output"} in emitted
