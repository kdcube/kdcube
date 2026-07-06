# SPDX-License-Identifier: MIT

"""Cold-cache governance: snapshot/delta classification, the cold-turn marker,
the user-held selection-change policy, and pending-delta promotion.

Governing principle: the USER pays for the cache, so the USER decides when a
cache-colding change lands; admin config supplies defaults/bounds only. The
marker attributes the cache-rebuild premium as one identifiable component
within the turn's spend sum — a turn's cost is always the sum of the spendings
inside it.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
    USER_MODEL_TARGET_ROLE,
    clamp_cache_policy,
    classify_selection_change,
    effective_selection_change_policy,
    react_selection_change_policy,
    selection_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import (
    UserAgentSelectionStore,
    agent_selection_key,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    build_announce_cold_turn_lines,
    build_announce_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.tests.test_user_selection_store import (  # noqa: E501
    _FakePool,  # the WRITING fake: deferred writes + promotion persist
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.tests.test_agent_selection_apply import (  # noqa: E501
    _Logger,
    _tool_cfg,
)

_PROPS = {
    "react": {
        "default_agent": {
            "supported_models": [
                {"model": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6"},
                {"model": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Haiku 4.5"},
            ],
        },
    },
}


def _stub(*, rows=None, warm=False, snapshot=None, conversation_id="conv-1", bundle_props=None):
    stub = SimpleNamespace()
    pool = _FakePool()
    # Alias the caller's dict so assertions observe the store's writes.
    shared = rows if rows is not None else {}
    pool.rows = shared
    pool.con._rows = shared
    stub.pg_pool = pool
    stub.logger = _Logger()
    stub.bundle_props = dict(bundle_props if bundle_props is not None else _PROPS)
    stub.runtime_ctx = SimpleNamespace(
        tenant="acme",
        project="demo",
        user_id="u1",
        bundle_id="bundle@1-0",
        agent_id="main",
        conversation_id=conversation_id,
        cold_turn_marker=None,
    )
    timeline = SimpleNamespace(agent_selection_snapshot=snapshot)
    stub.ctx_browser = SimpleNamespace(timeline=timeline)
    stub._conversation_cache_is_warm = lambda tl, _w=warm: _w
    stub.events = []

    async def _emit(evt):
        stub.events.append(evt)

    stub._emit = _emit
    return stub


def _row(disabled=None, model=None, cache_policy=None, pending=None):
    value = {"schema_version": 1, "disabled": disabled or {}}
    if model:
        value["model"] = model
    if cache_policy:
        value["cache_policy"] = cache_policy
    if pending:
        value["pending"] = pending
    return {"value_json": json.dumps(value), "subsystem": "agents", "created_at": "", "updated_at": ""}


# ── classification ────────────────────────────────────────────────────────────


def test_classify_selection_change_classes_and_reasons():
    base = selection_snapshot({"tools": {"gmail": True}}, {"provider": "anthropic", "model": "m1"})
    assert classify_selection_change(base, dict(base))["changed"] is False

    model_only = classify_selection_change(base, selection_snapshot({"tools": {"gmail": True}}, None))
    assert model_only["classes"] == ["model_switch"]
    assert model_only["prev_model"] == {"provider": "anthropic", "model": "m1"}

    caps = classify_selection_change(
        base,
        selection_snapshot(
            {"tools": {"gmail": True}, "mcp": {"knowledge": True}, "skills": ["public.a"]},
            {"provider": "anthropic", "model": "m1"},
        ),
    )
    assert caps["classes"] == ["capability_toggle"]
    assert set(caps["reasons"]) == {"mcp_toggle", "skill_toggle"}

    both = classify_selection_change(base, selection_snapshot({}, None))
    assert set(both["classes"]) == {"model_switch", "capability_toggle"}


# ── admin policy config + clamp ───────────────────────────────────────────────


def test_selection_change_policy_defaults_and_config_forms():
    default = react_selection_change_policy({}, "main")
    assert default["model_switch"] == "confirm"
    assert default["capability_toggle"] == "confirm"
    assert default["allowed"] == ["accept", "confirm", "defer_cold", "defer_conversation"]

    props = {"react": {"default_agent": {"cache": {"selection_change_policy": "accept"}}}}
    assert react_selection_change_policy(props, "main")["capability_toggle"] == "accept"

    props = {"react": {"default_agent": {"cache": {"selection_change_policy": {
        "model_switch": "confirm",
        "capability_toggle": "accept",
        "allowed": ["accept", "confirm"],
    }}}}}
    admin = react_selection_change_policy(props, "main")
    assert admin["allowed"] == ["accept", "confirm"]

    # User value wins inside the allowed set; outside it, the admin default holds.
    effective = effective_selection_change_policy(props, "main", {"model_switch": "accept"})
    assert effective["model_switch"] == "accept"
    effective = effective_selection_change_policy(props, "main", {"model_switch": "defer_cold"})
    assert effective["model_switch"] == "confirm"

    assert clamp_cache_policy(
        {"model_switch": "defer_cold", "capability_toggle": "accept", "bogus": "x"},
        props, "main",
    ) == {"capability_toggle": "accept"}


# ── the cold-turn marker at the choke point ───────────────────────────────────


@pytest.mark.asyncio
async def test_warm_delta_with_accept_policy_fires_marker_and_updates_snapshot():
    prev = selection_snapshot({}, None)
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        disabled={"tools": {"gmail": True}},
        cache_policy={"capability_toggle": "accept"},
    )}
    stub = _stub(rows=rows, warm=True, snapshot=prev)
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())

    assert "gmail" not in out_tools.allowed_plugins
    marker = stub.runtime_ctx.cold_turn_marker
    assert marker and marker["warm"] is True
    assert marker["reasons"] == ["tool_toggle"]
    snapshot = stub.ctx_browser.timeline.agent_selection_snapshot
    assert snapshot["disabled"] == {"tools": {"gmail": True}}
    assert any(line.startswith("kdcube.react.cache") for _, line in stub.logger.lines)


@pytest.mark.asyncio
async def test_first_turn_adopts_without_marker():
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(disabled={"tools": {"gmail": True}})}
    stub = _stub(rows=rows, warm=True, snapshot=None)
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert stub.runtime_ctx.cold_turn_marker is None
    assert stub.ctx_browser.timeline.agent_selection_snapshot["disabled"] == {"tools": {"gmail": True}}


@pytest.mark.asyncio
async def test_cold_delta_marker_not_warm():
    prev = selection_snapshot({}, None)
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        disabled={"tools": {"gmail": True}},
        cache_policy={"capability_toggle": "accept"},
    )}
    stub = _stub(rows=rows, warm=False, snapshot=prev)
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    marker = stub.runtime_ctx.cold_turn_marker
    assert marker and marker["warm"] is False
    # ANNOUNCE stays silent for a change that applied on a cold conversation.
    assert build_announce_cold_turn_lines(runtime_ctx=RuntimeCtx(cold_turn_marker=marker)) == []


# ── user-held policies at the choke point ─────────────────────────────────────


@pytest.mark.asyncio
async def test_defer_conversation_pins_snapshot_on_warm_delta():
    prev = selection_snapshot({}, None)
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        disabled={"tools": {"gmail": True}},
        cache_policy={"capability_toggle": "defer_conversation"},
    )}
    stub = _stub(rows=rows, warm=True, snapshot=prev)
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    # Pinned: the active delta does not apply; the snapshot stays authoritative.
    assert "gmail" in out_tools.allowed_plugins
    assert stub.runtime_ctx.cold_turn_marker is None
    assert stub.ctx_browser.timeline.agent_selection_snapshot == prev


@pytest.mark.asyncio
async def test_defer_cold_applies_free_when_cache_is_cold():
    prev = selection_snapshot({}, None)
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        disabled={"tools": {"gmail": True}},
        cache_policy={"capability_toggle": "defer_cold"},
    )}
    # Warm: pinned.
    stub = _stub(rows=dict(rows), warm=True, snapshot=dict(prev))
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert "gmail" in out_tools.allowed_plugins
    # Cold: adopts (free by definition), marker records warm=False.
    stub = _stub(rows=dict(rows), warm=False, snapshot=dict(prev))
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert "gmail" not in out_tools.allowed_plugins
    assert stub.runtime_ctx.cold_turn_marker["warm"] is False


# ── pending promotion ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_next_conversation_promotes_in_a_different_conversation():
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        pending={
            "disabled": {"tools": {"gmail": True}},
            "apply": "next_conversation",
            "since_conversation_id": "conv-0",
        },
    )}
    stub = _stub(rows=rows, warm=True, snapshot=None, conversation_id="conv-1")
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert "gmail" not in out_tools.allowed_plugins
    stored = json.loads(rows[("u1", "bundle@1-0", agent_selection_key("main"))]["value_json"])
    assert "pending" not in stored
    assert stored["disabled"] == {"tools": {"gmail": True}}


@pytest.mark.asyncio
async def test_pending_next_conversation_waits_in_the_same_conversation():
    rows = {("u1", "bundle@1-0", agent_selection_key("main")): _row(
        pending={
            "disabled": {"tools": {"gmail": True}},
            "apply": "next_conversation",
            "since_conversation_id": "conv-1",
        },
    )}
    stub = _stub(rows=rows, warm=True, snapshot=None, conversation_id="conv-1")
    out_tools, _ = await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert "gmail" in out_tools.allowed_plugins
    stored = json.loads(rows[("u1", "bundle@1-0", agent_selection_key("main"))]["value_json"])
    assert stored["pending"]["apply"] == "next_conversation"


@pytest.mark.asyncio
async def test_pending_when_cold_promotes_only_on_cold_cache():
    def rows():
        return {("u1", "bundle@1-0", agent_selection_key("main")): _row(
            pending={
                "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
                "apply": "when_cold",
                "since_conversation_id": "conv-1",
            },
        )}

    warm_rows = rows()
    stub = _stub(rows=warm_rows, warm=True, snapshot=None)
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert USER_MODEL_TARGET_ROLE not in stub.runtime_ctx.agent_role_models

    cold_rows = rows()
    stub = _stub(rows=cold_rows, warm=False, snapshot=None)
    await BaseWorkflow.apply_user_agent_selection(stub, _tool_cfg(), AgentSkillConfig())
    assert stub.runtime_ctx.agent_role_models[USER_MODEL_TARGET_ROLE]["model"] == "claude-haiku-4-5-20251001"
    stored = json.loads(cold_rows[("u1", "bundle@1-0", agent_selection_key("main"))]["value_json"])
    assert "pending" not in stored


# ── store: deferred writes + policy merge ─────────────────────────────────────


@pytest.mark.asyncio
async def test_store_deferred_write_parks_pending_and_keeps_active():
    store = UserAgentSelectionStore(pg_pool=_FakePool(), tenant="acme", project="demo")
    record = await store.set_selection(
        user_id="u1", bundle_id="b", agent_id="main",
        patch={"tools": {"gmail": True}},
        apply="next_conversation",
        conversation_id="conv-9",
        cache_policy={"capability_toggle": "defer_conversation"},
    )
    assert record["disabled"] == {}
    assert record["pending"]["disabled"] == {"tools": {"gmail": True}}
    assert record["pending"]["since_conversation_id"] == "conv-9"
    assert record["cache_policy"] == {"capability_toggle": "defer_conversation"}

    promoted = await store.promote_pending(user_id="u1", bundle_id="b", agent_id="main")
    assert promoted["disabled"] == {"tools": {"gmail": True}}
    assert promoted["pending"] is None
    assert promoted["cache_policy"] == {"capability_toggle": "defer_conversation"}


# ── warmness helper + announce line ───────────────────────────────────────────


def test_conversation_cache_is_warm_reads_the_persisted_signal():
    stub = SimpleNamespace(runtime_ctx=SimpleNamespace(session=None))
    is_warm = BaseWorkflow._conversation_cache_is_warm
    assert is_warm(stub, None) is False
    assert is_warm(stub, SimpleNamespace(cache_last_touch_at=None, cache_last_ttl_seconds=300)) is False
    assert is_warm(stub, SimpleNamespace(cache_last_touch_at=int(time.time()), cache_last_ttl_seconds=300)) is True
    assert is_warm(stub, SimpleNamespace(cache_last_touch_at=int(time.time()) - 3600, cache_last_ttl_seconds=300)) is False


def test_announce_cold_turn_line_renders_for_warm_marker_only():
    marker = {
        "reason": "model_switch",
        "reasons": ["model_switch"],
        "prev_model": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "new_model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
        "warm": True,
    }
    lines = build_announce_cold_turn_lines(runtime_ctx=RuntimeCtx(cold_turn_marker=marker))
    assert lines[0] == "[CACHE]"
    assert "model_switch (claude-sonnet-4-6 -> claude-haiku-4-5-20251001)" in lines[1]
    assert "re-caches at full input rates this turn" in lines[1]

    announce = build_announce_text(
        iteration=0, max_iterations=8, started_at=None, timezone="UTC",
        timeline_blocks=[], runtime_ctx=RuntimeCtx(cold_turn_marker=marker),
    )
    assert "[CACHE]" in announce
    assert "[CACHE]" not in build_announce_text(
        iteration=0, max_iterations=8, started_at=None, timezone="UTC",
        timeline_blocks=[], runtime_ctx=RuntimeCtx(),
    )
