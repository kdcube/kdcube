# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.call import get_react_tools_catalog
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import (
    DEFAULT_SUBAGENT_MAX_ROUNDS,
    MAX_SUBAGENT_MAX_ROUNDS,
    SubagentCharter,
    parse_charter,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import (
    EventLaneState,
    wake_ignore_reason,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.child_turn import (
    SUBAGENT_ACCOUNTING_AGENT,
    SubagentChildTurnContext,
    apply_child_runtime_overrides,
    bind_child_turn_accounting,
    charter_turn_context,
    publish_child_completion,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
    SUBAGENT_CHARTER_EVENT_KIND,
    SUBAGENT_CONTRIBUTION_EVENT_KIND,
    SUBAGENT_CONVERGED_EVENT_KIND,
    SUBAGENT_EVENT_SOURCE_ID,
    SUBAGENT_FAILED_EVENT_KIND,
    ParentLaneAddress,
    contribution_refs_for_parent,
    publish_subagent_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.schedule import (
    SUBAGENT_CALL_CONTEXT_KEY,
    build_child_task_payload,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.fork import (
    FORK_HEADER_BLOCK_TYPE,
    FORK_MARKER_BLOCK_TYPE,
    build_fork_marker_block,
    build_fork_projection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.launch import SubagentLaunchRequest
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.contribute import handle_react_contribute
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.delegate import handle_react_delegate
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.browser import ContextBrowser


class _FakeRedis:
    def __init__(self):
        self._kv = {}
        self._streams = {}
        self._stream_seq = {}

    async def incr(self, key):
        self._kv[key] = int(self._kv.get(key, 0)) + 1
        return self._kv[key]

    async def xadd(self, key, fields):
        seq = int(self._stream_seq.get(key, 0)) + 1
        self._stream_seq[key] = seq
        stream_id = f"{seq}-0"
        self._streams.setdefault(key, []).append((stream_id, dict(fields or {})))
        return stream_id

    async def xrange(self, key, min="-", max="+", count=None):
        out = []
        for stream_id, fields in list(self._streams.get(key, [])):
            if min not in ("-", None, ""):
                exclusive = str(min).startswith("(")
                floor = str(min)[1:] if exclusive else str(min)
                if exclusive:
                    if stream_id <= floor:
                        continue
                elif stream_id < floor:
                    continue
            if max not in ("+", None, "") and stream_id > str(max):
                continue
            out.append((stream_id, dict(fields)))
            if count is not None and len(out) >= int(count):
                break
        return out

    async def setex(self, key, ttl, value):
        del ttl
        self._kv[key] = value

    async def set(self, key, value, ex=None, nx=False):
        del ex
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def get(self, key):
        return self._kv.get(key)

    async def delete(self, key):
        self._kv.pop(key, None)

    async def rpush(self, key, value):
        self._kv.setdefault(key, [])
        self._kv[key].append(value)
        return len(self._kv[key])

    async def lrange(self, key, start, stop):
        items = list(self._kv.get(key) or [])
        if stop == -1:
            return items[start:]
        return items[start:stop + 1]


class _FakeCtxClient:
    """Minimal ctx client: timeline artifacts round-trip through memory."""

    class _Store:
        async def get_blob_bytes(self, uri_or_path):
            raise FileNotFoundError(uri_or_path)

    def __init__(self):
        self.store = self._Store()
        self.saved = []  # (kind, conversation_id, content)
        self.deleted_turns = []

    async def save_artifact(self, *, kind, conversation_id, content, **kwargs):
        self.saved.append({
            "kind": kind,
            "conversation_id": conversation_id,
            "content": content,
            "turn_id": kwargs.get("turn_id"),
        })
        return {"ok": True}

    async def delete_turn(self, *, conversation_id, turn_id, **kwargs):
        self.deleted_turns.append({"conversation_id": conversation_id, "turn_id": turn_id, **kwargs})
        self.saved = [
            row for row in self.saved
            if not (row["conversation_id"] == conversation_id and row.get("turn_id") == turn_id)
        ]
        return {"deleted_messages": 1}

    async def recent(self, *, kinds=(), conversation_id=None, **kwargs):
        wanted = {str(k).split(":", 1)[-1] for k in (kinds or ())}
        for row in reversed(self.saved):
            if row["kind"] in wanted and row["conversation_id"] == conversation_id:
                return {"items": [{"payload": row["content"]}]}
        return {"items": []}

    async def fetch_latest_feedback_reactions(self, *args, **kwargs):
        return {"items": []}


def _lane(redis, conversation_id):
    return build_conversation_external_event_source(
        redis=redis,
        tenant="tenant",
        project="project",
        conversation_id=conversation_id,
        user_id="user_1",
        agent_id="main",
    )


def _parent_browser(tmp_path, source, *, conversation_id="conv_parent", turn_id="turn_parent"):
    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        conversation_id=conversation_id,
        turn_id=turn_id,
        bundle_id="bundle@1",
        started_at="2026-07-11T10:00:00Z",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        external_event_source=source,
    )
    browser = ContextBrowser(ctx_client=_FakeCtxClient(), runtime_ctx=runtime)
    return runtime, browser


# ---------------------------------------------------------------- catalog


def test_catalog_gates_subagent_tools_by_role():
    base = {t["id"] for t in get_react_tools_catalog()}
    parent = {t["id"] for t in get_react_tools_catalog(subagent_role="parent")}
    child = {t["id"] for t in get_react_tools_catalog(subagent_role="child")}
    assert "react.delegate" not in base and "react.contribute" not in base
    assert "react.delegate" in parent and "react.contribute" not in parent
    assert "react.contribute" in child and "react.delegate" not in child


# ---------------------------------------------------------------- charter


def test_parse_charter_takes_the_prompt_string_and_the_alias():
    charter, err = parse_charter({
        "charter": "Research X.\nSend back the report file ref plus a 5-line summary.",
        "agent_alias": "strong_agent",
    })
    assert err == ""
    assert charter.goal.startswith("Research X.")
    assert charter.agent_alias == "strong_agent"
    # the round budget is config's business: absent config = the default
    assert charter.max_rounds == DEFAULT_SUBAGENT_MAX_ROUNDS

    text = charter.charter_text()
    assert "[SUBAGENT CHARTER]" in text and "Research X." in text


def test_parse_charter_budget_comes_from_config_never_the_params():
    charter, err = parse_charter(
        {"charter": "Research X", "max_rounds": 500},
        max_rounds=6,
    )
    assert err == "" and charter.max_rounds == 6

    # the configured value itself stays capped
    capped, _ = parse_charter({"charter": "Research X"}, max_rounds=500)
    assert capped.max_rounds == MAX_SUBAGENT_MAX_ROUNDS


def test_parse_charter_missing_prompt_is_rejected():
    missing, err = parse_charter({"charter": {"deliverables": ["x"]}})
    assert missing is None and err == "missing_goal"
    missing, err = parse_charter({"charter": "   "})
    assert missing is None and err == "missing_goal"


def test_parse_charter_tolerates_the_earlier_object_form():
    """A legacy {goal, deliverables, contribute, model} object folds into the
    prompt string; `model` reads as the alias (dual-read)."""
    charter, err = parse_charter({
        "charter": {
            "goal": "Research X",
            "deliverables": ["files/report.md"],
            "contribute": "the report ref",
        },
        "model": "strong_agent",
    })
    assert err == ""
    assert "Research X" in charter.goal
    assert "files/report.md" in charter.goal
    assert "the report ref" in charter.goal
    assert charter.agent_alias == "strong_agent"

    flat, err = parse_charter({"goal": "flat form"})
    assert err == "" and "flat form" in flat.goal


def test_charter_round_trips_alias_dual_read_single_write():
    stored = SubagentCharter.from_dict({"goal": "g", "model": "fast_agent"})
    assert stored.agent_alias == "fast_agent"
    # freshly written charters carry agent_alias only
    data = SubagentCharter(goal="g", agent_alias="fast_agent").to_dict()
    assert data["agent_alias"] == "fast_agent"
    assert "model" not in data


def test_charter_summary_line_is_the_first_sentence():
    charter = SubagentCharter(
        goal="Research the market. Then compile a long report.\nMore detail here.",
    )
    assert charter.summary_line() == "Research the market."
    long_first = SubagentCharter(goal="A" * 300)
    assert len(long_first.summary_line()) <= 140


# ---------------------------------------------------------------- fork


def test_fork_projection_copies_summaries_then_current_turn_with_qualified_file_refs():
    range_summary = {
        "type": "conv.range.summary",
        "turn_id": "turn_1",
        "path": "conv:su:turn_1.conv.range.summary",
        "text": "compacted memory",
    }
    ws_old = {
        "type": "conv.working.summary",
        "turn_id": "turn_2",
        "path": "conv:ws:turn_2.conv.working.summary.attempt.1",
        "text": "turn 2 summary",
    }
    file_result = {
        "type": "react.tool.result",
        "turn_id": "turn_3",
        "path": "conv:fi:turn_3.files/report.md",
        "text": "report body mentioning conv:fi:turn_3.files/report.md",
        "refs": ["conv:fi:turn_3.files/report.md"],
    }
    artifact_block = {
        "type": "react.notes",
        "turn_id": "turn_3",
        "path": "conv:ar:turn_3.react.notes.1",
        "text": "note",
    }
    parent_blocks = [range_summary, ws_old, {"type": "turn.header", "turn_id": "turn_3"}, file_result, artifact_block]
    current = [{"type": "turn.header", "turn_id": "turn_3"}, file_result, artifact_block]

    seed = build_fork_projection(
        parent_blocks=parent_blocks,
        parent_current_turn_blocks=current,
        parent_conversation_id="parentconv",
        parent_turn_id="turn_3",
        child_conversation_id="childconv",
    )

    types = [b["type"] for b in seed]
    # range summary FIRST (the persist window starts at it), then the header.
    assert types[0] == "conv.range.summary"
    assert types[1] == FORK_HEADER_BLOCK_TYPE
    assert "conv.working.summary" in types
    # current-turn blocks follow the summaries
    assert types[-1] == "react.notes"

    by_type = {b["type"]: b for b in seed}
    copied_file = next(b for b in seed if b["type"] == "react.tool.result")
    # every conversation-scoped ref in the copy names its home conversation...
    assert copied_file["path"] == "conv:fi:conv_parentconv.turn_3.files/report.md"
    assert copied_file["refs"] == ["conv:fi:conv_parentconv.turn_3.files/report.md"]
    # ...including legacy conversation-local refs inside the copied text,
    # which get pinned to the parent at copy time
    assert copied_file["text"] == "report body mentioning conv:fi:conv_parentconv.turn_3.files/report.md"
    # conv:ar: paths carry their home conversation segment too (they resolve
    # inside the copied timeline; the segment records provenance)
    assert by_type["react.notes"]["path"] == "conv:ar:conv_parentconv.turn_3.react.notes.1"
    # source blocks are not mutated
    assert file_result["path"] == "conv:fi:turn_3.files/report.md"
    assert file_result["text"] == "report body mentioning conv:fi:turn_3.files/report.md"
    header = by_type[FORK_HEADER_BLOCK_TYPE]
    assert "conv_parentconv" in header["text"]


def test_fork_projection_is_idempotent_for_qualified_refs():
    """Blocks whose refs are already conversation-qualified (qualified at
    birth, or copied from an earlier fork) are carried verbatim."""
    foreign = {
        "type": "react.tool.result",
        "turn_id": "turn_9",
        "path": "conv:fi:conv_grandparent.turn_9.files/spec.md",
        "text": "spec at conv:fi:conv_grandparent.turn_9.files/spec.md",
        "refs": ["conv:fi:conv_grandparent.turn_9.files/spec.md"],
    }
    own_qualified = {
        "type": "react.tool.result",
        "turn_id": "turn_3",
        "path": "conv:fi:conv_parentconv.turn_3.files/report.md",
        "text": "see conv:fi:conv_parentconv.turn_3.files/report.md",
        "refs": ["conv:fi:conv_parentconv.turn_3.files/report.md"],
    }
    seed = build_fork_projection(
        parent_blocks=[],
        parent_current_turn_blocks=[foreign, own_qualified],
        parent_conversation_id="parentconv",
        parent_turn_id="turn_3",
        child_conversation_id="childconv",
    )
    copied = [b for b in seed if b["type"] == "react.tool.result"]
    assert copied[0]["path"] == foreign["path"]
    assert copied[0]["refs"] == foreign["refs"]
    assert copied[0]["text"] == foreign["text"]
    assert copied[1]["path"] == own_qualified["path"]
    assert copied[1]["refs"] == own_qualified["refs"]
    assert copied[1]["text"] == own_qualified["text"]


def test_fork_marker_block_names_child_charter_and_helper_identity():
    marker = build_fork_marker_block(
        parent_turn_id="turn_3",
        child_conversation_id="childconv",
        child_turn_id="turn_c1",
        charter_summary="Research X",
        max_rounds=8,
        agent_alias="strong_agent",
        agent_class="strong",
        tool_call_id="tc1",
    )
    assert marker["type"] == FORK_MARKER_BLOCK_TYPE
    assert "conv_childconv" in marker["text"]
    assert "Research X" in marker["text"]
    assert "strong_agent [strong]" in marker["text"]
    assert marker["meta"]["child_conversation_id"] == "childconv"
    assert marker["meta"]["max_rounds"] == 8
    assert marker["meta"]["agent_alias"] == "strong_agent"
    assert marker["meta"]["agent_class"] == "strong"
    assert marker["call_id"] == "tc1"


# ---------------------------------------------------------------- events


@pytest.mark.asyncio
async def test_subagent_event_default_is_passive_external_event():
    redis = _FakeRedis()
    lane = _lane(redis, "conv_parent")
    event = await publish_subagent_event(
        lane_source=lane,
        semantic_type=SUBAGENT_CONTRIBUTION_EVENT_KIND,
        text="[SUBAGENT CONTRIBUTION]\npartial result",
        facts={"child_conversation_id": "sub_child"},
        author="agent:conv_sub_child/turn_c1",
        target_turn_id="turn_parent",
    )
    stored = await lane.get_event(event.message_id)
    assert stored is not None
    # transport kind is uniformly external_event; the semantic type is nested
    assert stored.kind == "external_event"
    nested = (stored.payload or {}).get("event") or {}
    assert nested.get("type") == SUBAGENT_CONTRIBUTION_EVENT_KIND
    assert nested.get("event_source_id") == SUBAGENT_EVENT_SOURCE_ID
    assert nested.get("reactive") is False
    assert stored.source == "agent:conv_sub_child/turn_c1"
    assert stored.target_turn_id == "turn_parent"
    # passive: the stored task envelope carries no request to run
    assert "request" not in (stored.task_payload or {})


@pytest.mark.asyncio
async def test_charter_event_is_promotable_but_never_reactive():
    redis = _FakeRedis()
    lane = _lane(redis, "sub_child")
    charter = SubagentCharter(goal="Research X", max_rounds=5)
    parent = ParentLaneAddress(
        tenant="tenant",
        project="project",
        user_id="user_1",
        conversation_id="conv_parent",
        turn_id="turn_parent",
        agent_id="main",
    )
    task_payload = build_child_task_payload(
        parent_payload=None,
        charter=charter,
        parent=parent,
        child_conversation_id="sub_child",
        child_turn_id="turn_c1",
        subagent_context={"kind": "charter"},
    )
    event = await publish_subagent_event(
        lane_source=lane,
        semantic_type=SUBAGENT_CHARTER_EVENT_KIND,
        text=charter.charter_text(),
        facts={"charter": charter.to_dict()},
        author="agent:conv_conv_parent/turn_parent",
        target_turn_id="turn_c1",
        task_payload=task_payload,
    )
    stored = await lane.get_event(event.message_id)
    assert stored is not None
    # promotable: the stored task envelope carries the run request...
    request = (stored.task_payload or {}).get("request") or {}
    events = request.get("external_events") or []
    assert events and events[0]["type"] == SUBAGENT_CHARTER_EVENT_KIND
    # ...while the lane occurrence stays non-reactive (no live-turn credit)
    nested = (stored.payload or {}).get("event") or {}
    assert nested.get("reactive") is False


def test_contribution_refs_are_conversation_qualified():
    refs = contribution_refs_for_parent(
        refs=[
            "conv:fi:turn_c1.files/out.md",
            "conv:fi:conv_child.turn_c1.files/already.md",
            "conv:ar:turn_c1.react.notes.1",
            "mem:record:abc",
        ],
        child_conversation_id="child",
    )
    assert refs == [
        "conv:fi:conv_child.turn_c1.files/out.md",
        "conv:fi:conv_child.turn_c1.files/already.md",
        "conv:ar:conv_child.turn_c1.react.notes.1",
        "mem:record:abc",
    ]


# ---------------------------------------------------------------- contribute tool


class _StubChildBrowser:
    def __init__(self, runtime_ctx):
        self.runtime_ctx = runtime_ctx
        self.blocks = []
        self.timeline = SimpleNamespace(blocks=self.blocks)

    def contribute(self, *, blocks):
        self.blocks.extend(blocks)

    def contribute_notice(self, **kwargs):
        self.blocks.append({"type": "react.notice", **kwargs})


def _tool_state(tool_id, params):
    return {
        "last_decision": {
            "action": "call_tool",
            "tool_call": {"tool_id": tool_id, "params": params},
        }
    }


@pytest.mark.asyncio
async def test_contribute_authors_parent_lane_event_that_folds_into_live_parent_turn(tmp_path):
    redis = _FakeRedis()
    parent_lane = _lane(redis, "conv_parent")
    _, parent_browser = _parent_browser(tmp_path, parent_lane)
    await parent_browser.load_timeline()
    try:
        child_stamp = {
            "child_conversation_id": "sub_child",
            "forked_from_conversation_id": "conv_parent",
            "forked_from_turn_id": "turn_parent",
            "charter_goal": "Research X",
        }
        child_ctx = SimpleNamespace(
            conversation_id="sub_child",
            turn_id="turn_c1",
            subagent_parent_lane=parent_lane,
            subagent_parent={
                "tenant": "tenant",
                "project": "project",
                "user_id": "user_1",
                "conversation_id": "conv_parent",
                "turn_id": "turn_parent",
                "agent_id": "main",
            },
            subagent_stamp=dict(child_stamp),
        )
        child_browser = _StubChildBrowser(child_ctx)
        state = _tool_state("react.contribute", {
            "report": "Draft ready; see the ref.",
            "refs": ["conv:fi:turn_c1.files/draft.md"],
        })
        state = await handle_react_contribute(
            ctx_browser=child_browser, state=state, tool_call_id="tc9",
        )
        result = state["last_tool_result"]
        assert result["status"] == "delivered"
        assert result["refs"] == ["conv:fi:conv_sub_child.turn_c1.files/draft.md"]
        # child records its own call + result blocks
        assert any(b.get("type") == "react.tool.call" for b in child_browser.blocks)
        assert any(b.get("type") == "react.tool.result" for b in child_browser.blocks)

        # the contribution's structured facts carry the envelope stamp
        lane_events = await parent_lane.read_since(None)
        contribution_event = lane_events[-1]
        assert (contribution_event.payload or {}).get("subagent") == child_stamp

        # the parent folds the contribution as a visible block + cursor advance
        before_seq = int(parent_browser.timeline.last_external_event_seq or 0)
        changed = await parent_browser._fold_external_events(call_hooks=False)
        assert changed >= 1
        blocks = parent_browser.timeline.get_turn_blocks()
        contribution = next(
            b for b in blocks
            if (b.get("meta") or {}).get("event_type") == SUBAGENT_CONTRIBUTION_EVENT_KIND
        )
        assert "Draft ready" in str(contribution.get("text") or "")
        assert "conv:fi:conv_sub_child.turn_c1.files/draft.md" in str(contribution.get("text") or "")
        assert int(parent_browser.timeline.last_external_event_seq or 0) > before_seq

        # a second fold pass adds nothing (applied exactly once)
        assert await parent_browser._fold_external_events(call_hooks=False) == 0
    finally:
        await parent_browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_contribute_outside_a_subagent_is_rejected():
    child_ctx = SimpleNamespace(
        conversation_id="conv_x",
        turn_id="turn_x",
        subagent_parent_lane=None,
        subagent_parent=None,
    )
    browser = _StubChildBrowser(child_ctx)
    state = await handle_react_contribute(
        ctx_browser=browser,
        state=_tool_state("react.contribute", {"report": "hello"}),
        tool_call_id="tc1",
    )
    assert state["last_tool_result"]["code"] == "contribute_unavailable"


# ---------------------------------------------------------------- delegate tool guards


@pytest.mark.asyncio
async def test_delegate_refuses_depth_beyond_one():
    ctx = SimpleNamespace(
        conversation_id="sub_child",
        turn_id="turn_c1",
        subagent_depth=1,
        subagent_spawner=object(),
        tenant="tenant",
        project="project",
        user_id="user_1",
        agent_id="main",
    )
    browser = _StubChildBrowser(ctx)
    state = await handle_react_delegate(
        ctx_browser=browser,
        state=_tool_state("react.delegate", {"charter": "nested"}),
        tool_call_id="tc2",
    )
    assert state["last_tool_result"]["code"] == "delegate_depth_limit"


@pytest.mark.asyncio
async def test_delegate_without_wired_spawner_is_rejected():
    ctx = SimpleNamespace(
        conversation_id="conv_parent",
        turn_id="turn_parent",
        subagent_depth=0,
        subagent_spawner=None,
        tenant="tenant",
        project="project",
        user_id="user_1",
        agent_id="main",
    )
    browser = _StubChildBrowser(ctx)
    state = await handle_react_delegate(
        ctx_browser=browser,
        state=_tool_state("react.delegate", {"charter": "goal"}),
        tool_call_id="tc3",
    )
    assert state["last_tool_result"]["code"] == "delegate_unavailable"


@pytest.mark.asyncio
async def test_delegate_missing_goal_is_rejected():
    ctx = SimpleNamespace(
        conversation_id="conv_parent",
        turn_id="turn_parent",
        subagent_depth=0,
        subagent_spawner=object(),
        tenant="tenant",
        project="project",
        user_id="user_1",
        agent_id="main",
    )
    browser = _StubChildBrowser(ctx)
    state = await handle_react_delegate(
        ctx_browser=browser,
        state=_tool_state("react.delegate", {"charter": {"deliverables": ["x"]}}),
        tool_call_id="tc4",
    )
    assert state["last_tool_result"]["code"] == "delegate_missing_goal"


@pytest.mark.asyncio
async def test_delegate_spawns_and_marks_fork_on_parent_timeline():
    launches = []

    class _Spawner:
        async def spawn(self, request):
            launches.append(request)
            from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.launch import (
                SubagentLaunchTicket,
            )

            return SubagentLaunchTicket(
                child_conversation_id="sub_abc",
                child_turn_id="turn_c1",
            )

    ctx = SimpleNamespace(
        conversation_id="conv_parent",
        turn_id="turn_parent",
        subagent_depth=0,
        subagent_spawner=_Spawner(),
        subagent_defaults={"max_rounds": 4},
        tenant="tenant",
        project="project",
        user_id="user_1",
        agent_id="main",
    )
    browser = _StubChildBrowser(ctx)
    browser.current_turn_blocks = lambda: []
    state = await handle_react_delegate(
        ctx_browser=browser,
        state=_tool_state("react.delegate", {
            "charter": "Research X. Send back files/r.md.",
            "agent_alias": "strongest_agent",
        }),
        tool_call_id="tc5",
    )
    result = state["last_tool_result"]
    assert result["status"] == "scheduled"
    assert result["child_conversation_ref"] == "conv_sub_abc"
    assert launches and launches[0].charter.goal.startswith("Research X.")
    # the budget is the configured one, never a model-provided count
    assert launches[0].charter.max_rounds == 4
    marker = next(b for b in browser.blocks if b.get("type") == FORK_MARKER_BLOCK_TYPE)
    assert marker["meta"]["child_conversation_id"] == "sub_abc"
    # the unconfigured strongest_agent resolves to the smartest configured
    # alias, recorded on the marker for the announce
    assert marker["meta"]["agent_alias"] == "strong_agent"
    assert marker["meta"]["agent_class"] == "strong"


# ---------------------------------------------------------------- spawner (child run)


class _StubLogger:
    def log(self, *args, **kwargs):
        pass


class _StubWorkflowBase:
    def __init__(self, tmp_path):
        self.redis = _FakeRedis()
        self.ctx_client = _FakeCtxClient()
        self.logger = _StubLogger()
        self.model_service = object()
        self.store = None
        self.bundle_props = {}
        self.comm = SimpleNamespace(
            emitter=SimpleNamespace(),
            tenant="tenant",
            project="project",
            user_id="user_1",
            user_type="privileged",
            service={"tenant": "tenant", "project": "project", "user": "user_1"},
            conversation={"session_id": "sess", "conversation_id": "conv_parent", "turn_id": "turn_parent"},
        )
        self.comm_context = SimpleNamespace(
            user={
                "user_type": "privileged",
                "user_id": "user_1",
                "username": "elena",
                "timezone": "UTC",
                "roles": ["kdcube:role:super-admin"],
            },
            routing=SimpleNamespace(session_id="sess_parent"),
        )
        self.runtime_ctx = RuntimeCtx(
            tenant="tenant",
            project="project",
            user_id="user_1",
            user_type="privileged",
            conversation_id="conv_parent",
            turn_id="turn_parent",
            bundle_id="bundle@1",
            started_at="2026-07-11T10:00:00Z",
            outdir=str(tmp_path / "parent_out"),
            workdir=str(tmp_path / "parent_work"),
        )


def _launch_request(fork_blocks=None):
    return SubagentLaunchRequest(
        charter=SubagentCharter(goal="Research X", max_rounds=4),
        parent=ParentLaneAddress(
            tenant="tenant",
            project="project",
            user_id="user_1",
            conversation_id="conv_parent",
            turn_id="turn_parent",
            agent_id="main",
        ),
        fork_blocks=list(fork_blocks or []),
        allowed_plugins=["some_plugin"],
        parent_depth=0,
        tool_call_id="tc7",
    )


async def _parent_lane_semantic_types(redis):
    lane = _lane(redis, "conv_parent")
    events = await lane.read_since(None)
    return [((e.payload or {}).get("event") or {}).get("type") for e in events]


def _enqueued_wakeups(redis):
    out = []
    for key, value in redis._kv.items():
        if not isinstance(value, list):
            continue
        for item in value:
            try:
                data = json.loads(item)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("kind") == "external_event_lane_wakeup":
                out.append((key, data))
    return out


class _FakeAtomicQueueManager:
    """The gateway admission seam: writes lane events + wakeup like the
    atomic Lua script (all-or-nothing), or rejects with a reason."""

    def __init__(self, redis, *, admit=True, reason="queue_size_exceeded"):
        self.redis = redis
        self.admit = admit
        self.reason = reason
        self.lane_calls = []
        self.wake_calls = []

    @staticmethod
    def _queue_key(user_type):
        return f"kdcube:test:prompt:queue:{getattr(user_type, 'value', user_type)}"

    async def enqueue_chat_task_with_lane_events_atomic(
        self, user_type, chat_task_data, session, context, endpoint, *,
        lane_log_key, lane_events,
    ):
        self.lane_calls.append({
            "user_type": user_type,
            "chat_task_data": chat_task_data,
            "session": session,
            "context": context,
            "endpoint": endpoint,
            "lane_log_key": lane_log_key,
            "lane_events": list(lane_events or []),
        })
        if not self.admit:
            return False, self.reason, {}
        stream_ids = []
        for item in lane_events or []:
            event = dict(item.get("event") or {})
            stream_id = await self.redis.xadd(lane_log_key, {"message_id": event.get("message_id")})
            await self.redis.set(item["event_key"], json.dumps(event, ensure_ascii=False))
            stream_ids.append(stream_id)
        await self.redis.rpush(
            self._queue_key(user_type), json.dumps(chat_task_data, ensure_ascii=False),
        )
        return True, "admitted", {"lane_stream_ids": stream_ids}

    async def enqueue_chat_task_atomic(self, user_type, chat_task_data, session, context, endpoint):
        self.wake_calls.append({
            "user_type": user_type,
            "chat_task_data": chat_task_data,
            "session": session,
            "context": context,
            "endpoint": endpoint,
        })
        if not self.admit:
            return False, self.reason, {}
        await self.redis.rpush(
            self._queue_key(user_type), json.dumps(chat_task_data, ensure_ascii=False),
        )
        return True, "admitted", {}


class _StubWorkflow(_StubWorkflowBase):
    """A delegate-side workflow: any attempt to build/run a child in-proc fails."""

    def build_react(self, scratchpad, **kwargs):
        raise AssertionError("v2 delegate must not build a child agent in-proc")


def _fork_blocks():
    return [{
        "type": FORK_HEADER_BLOCK_TYPE,
        "turn_id": "turn_parent",
        "path": "conv:ar:turn_parent.subagent.fork.header",
        "text": "[FORK] context",
        "meta": {},
    }]


@pytest.mark.asyncio
async def test_spawn_refuses_depth_beyond_one(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import (
        ReactSubagentSpawner,
    )

    workflow = _StubWorkflow(tmp_path)
    spawner = ReactSubagentSpawner(
        workflow=workflow,
        build_template={},
        queue_manager=_FakeAtomicQueueManager(workflow.redis),
    )
    deep = _launch_request()
    deep.parent_depth = 1
    with pytest.raises(RuntimeError):
        await spawner.spawn(deep)


@pytest.mark.asyncio
async def test_spawn_persists_seed_and_schedules_promotable_charter(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import (
        ReactSubagentSpawner,
    )

    workflow = _StubWorkflow(tmp_path)
    queue = _FakeAtomicQueueManager(workflow.redis)
    spawner = ReactSubagentSpawner(
        workflow=workflow, build_template={}, queue_manager=queue,
    )
    fork_blocks = _fork_blocks()

    ticket = await spawner.spawn(_launch_request(fork_blocks))

    assert ticket.status == "scheduled"
    child_id = ticket.child_conversation_id

    # the fork seed was persisted as the child conversation's timeline, with
    # the queryable forked_from backref promoted out of the header meta
    seeded = next(
        row for row in workflow.ctx_client.saved
        if row["conversation_id"] == child_id and row["kind"] == "conv.timeline.v1"
    )
    assert seeded["content"]["forked_from"] == {
        "conversation_id": "conv_parent",
        "turn_id": "turn_parent",
    }
    assert any(b.get("type") == FORK_HEADER_BLOCK_TYPE for b in seeded["content"]["blocks"])
    assert seeded["content"]["blocks"][0]["meta"]["child_conversation_id"] == child_id

    # the charter is authored onto the CHILD lane WITH the run request
    child_lane = _lane(workflow.redis, child_id)
    child_events = await child_lane.read_since(None)
    child_types = [((e.payload or {}).get("event") or {}).get("type") for e in child_events]
    assert child_types == [SUBAGENT_CHARTER_EVENT_KIND]
    charter_event = child_events[0]
    assert charter_event.source == "agent:conv_conv_parent/turn_parent"
    assert charter_event.target_turn_id == ticket.child_turn_id
    task_payload = charter_event.task_payload or {}
    assert task_payload["routing"]["conversation_id"] == child_id
    assert task_payload["routing"]["turn_id"] == ticket.child_turn_id
    request_events = (task_payload.get("request") or {}).get("external_events") or []
    assert request_events and request_events[0]["type"] == SUBAGENT_CHARTER_EVENT_KIND
    sub_ctx = (task_payload.get("bundle_call_context") or {}).get(SUBAGENT_CALL_CONTEXT_KEY) or {}
    assert sub_ctx["depth"] == 1
    assert sub_ctx["charter"]["goal"] == "Research X"
    assert sub_ctx["allowed_plugins"] == ["some_plugin"]
    # visibility defaults silent when the agent's subagents config says nothing
    assert sub_ctx["visibility"] == "silent"
    # the charter's structured facts carry the envelope stamp
    charter_stamp = (charter_event.payload or {}).get("subagent") or {}
    assert charter_stamp == {
        "child_conversation_id": child_id,
        "forked_from_conversation_id": "conv_parent",
        "forked_from_turn_id": "turn_parent",
        "charter_goal": "Research X",
    }

    # the kickoff is the promotion: one lane wakeup rides the processor queue
    wakeups = _enqueued_wakeups(workflow.redis)
    assert len(wakeups) == 1
    _queue_key, wake = wakeups[0]
    assert wake["event_lane"]["conversation_id"] == child_id
    assert wake["event_lane"]["event_id"] == charter_event.message_id

    # lane event + wakeup went through the atomic admission as ONE call,
    # with the session/actor derived from the parent's user identity and
    # the delegate source marker
    assert len(queue.lane_calls) == 1
    call = queue.lane_calls[0]
    assert call["endpoint"] == "react.delegate"
    assert call["lane_events"][0]["event"]["message_id"] == charter_event.message_id
    session = call["session"]
    assert session.user_id == "user_1"
    assert session.user_type.value == "privileged"
    assert session.session_id == child_id
    assert session.request_context.user_agent == "react.subagent.delegate"
    assert sub_ctx["parent_session_id"] == "sess_parent"

    # nothing ran in-proc, and nothing reached the parent lane at spawn time
    assert await _parent_lane_semantic_types(workflow.redis) == []


@pytest.mark.asyncio
async def test_spawn_carries_thread_visibility_from_agent_defaults(tmp_path):
    """``react.agents.<id>.subagents.visibility: thread`` (resolved into the
    subagent defaults at spawner install) travels with the assignment."""
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import (
        ReactSubagentSpawner,
    )

    workflow = _StubWorkflow(tmp_path)
    workflow.runtime_ctx.subagent_defaults = {"model": "claude-haiku-4-5", "visibility": "thread"}
    queue = _FakeAtomicQueueManager(workflow.redis)
    spawner = ReactSubagentSpawner(workflow=workflow, build_template={}, queue_manager=queue)

    ticket = await spawner.spawn(_launch_request(_fork_blocks()))

    child_lane = _lane(workflow.redis, ticket.child_conversation_id)
    charter_event = (await child_lane.read_since(None))[0]
    sub_ctx = ((charter_event.task_payload or {}).get("bundle_call_context") or {}).get(
        SUBAGENT_CALL_CONTEXT_KEY
    ) or {}
    assert sub_ctx["visibility"] == "thread"
    assert charter_turn_context(charter_event.task_payload).visibility == "thread"


@pytest.mark.asyncio
async def test_spawn_rejected_by_backpressure_leaves_no_child_state(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import (
        ReactSubagentSpawner,
        SubagentEnqueueRejected,
    )

    workflow = _StubWorkflow(tmp_path)
    queue = _FakeAtomicQueueManager(workflow.redis, admit=False, reason="queue_size_exceeded")
    spawner = ReactSubagentSpawner(
        workflow=workflow, build_template={}, queue_manager=queue,
    )

    with pytest.raises(SubagentEnqueueRejected) as exc_info:
        await spawner.spawn(_launch_request(_fork_blocks()))
    assert exc_info.value.reason == "queue_size_exceeded"

    # the atomic script wrote nothing, and the seed was cleaned up: a
    # rejected delegate leaves no child state
    assert _enqueued_wakeups(workflow.redis) == []
    assert workflow.ctx_client.deleted_turns
    assert not any(
        row["kind"] == "conv.timeline.v1" and str(row["conversation_id"]).startswith("sub")
        for row in workflow.ctx_client.saved
    )


@pytest.mark.asyncio
async def test_delegate_reports_queue_saturation_as_structured_rejection():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.schedule import (
        SubagentEnqueueRejected,
    )

    class _RejectedSpawner:
        async def spawn(self, request):
            raise SubagentEnqueueRejected("hard_limit_exceeded")

    ctx = SimpleNamespace(
        conversation_id="conv_parent",
        turn_id="turn_parent",
        subagent_depth=0,
        subagent_spawner=_RejectedSpawner(),
        tenant="tenant",
        project="project",
        user_id="user_1",
        agent_id="main",
    )
    browser = _StubChildBrowser(ctx)
    browser.current_turn_blocks = lambda: []
    state = await handle_react_delegate(
        ctx_browser=browser,
        state=_tool_state("react.delegate", {"charter": {"goal": "Research X"}}),
        tool_call_id="tc6",
    )
    result = state["last_tool_result"]
    assert result["code"] == "delegate_queue_saturated"
    assert "hard_limit_exceeded" in result["message"]


# ---------------------------------------------------------------- child turn


def _child_context(**overrides):
    kwargs = dict(
        charter=SubagentCharter(goal="Research X", max_rounds=4),
        parent=ParentLaneAddress(
            tenant="tenant",
            project="project",
            user_id="user_1",
            conversation_id="conv_parent",
            turn_id="turn_parent",
            agent_id="main",
        ),
        depth=1,
        child_conversation_id="sub_x",
        child_turn_id="turn_c1",
        parent_session_id="sess_parent",
        parent_user={"user_type": "privileged", "user_id": "user_1"},
        allowed_plugins=["some_plugin"],
    )
    kwargs.update(overrides)
    return SubagentChildTurnContext(**kwargs)


def test_charter_task_payload_round_trips_through_the_promoter_shape():
    from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.schedule import (
        build_completion_task_payload,
    )

    context = _child_context()
    task_payload = build_child_task_payload(
        parent_payload=None,
        charter=context.charter,
        parent=context.parent,
        child_conversation_id="sub_x",
        child_turn_id="turn_c1",
        subagent_context=context.to_dict(),
    )
    model = ExternalEventPayload.model_validate(task_payload)
    parsed = charter_turn_context(model)
    assert parsed is not None
    assert parsed.charter.goal == "Research X"
    assert parsed.charter.max_rounds == 4
    assert parsed.parent.conversation_id == "conv_parent"
    assert parsed.parent_session_id == "sess_parent"
    assert parsed.allowed_plugins == ["some_plugin"]
    assert parsed.depth == 1

    # a completion payload describes a NORMAL parent turn: no assignment
    completion = build_completion_task_payload(
        child_payload=model,
        semantic_type=SUBAGENT_CONVERGED_EVENT_KIND,
        text="done",
        facts={},
        parent=context.parent,
        parent_session_id="sess_parent",
        parent_user=context.parent_user,
    )
    completion_model = ExternalEventPayload.model_validate(completion)
    assert charter_turn_context(completion_model) is None
    assert completion["routing"]["conversation_id"] == "conv_parent"
    assert completion["routing"]["session_id"] == "sess_parent"
    assert completion["user"]["user_id"] == "user_1"


def test_apply_child_runtime_overrides_sets_budget_depth_and_parent_lane():
    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        user_id="user_1",
        conversation_id="sub_x",
        turn_id="turn_c1",
        agent_id="main",
        max_iterations=15,
    )
    context = _child_context()
    apply_child_runtime_overrides(
        runtime,
        context,
        bundle_props={},
        subagent_defaults={"model": "claude-haiku-4-5"},
        redis=_FakeRedis(),
    )
    # budget: the charter budget IS the iteration budget, no reactive credit
    assert runtime.max_iterations == 4
    assert runtime.reactive_event_iteration_credit_enabled is False
    # depth + parent address wired for react.contribute
    assert runtime.subagent_depth == 1
    assert runtime.subagent_parent["conversation_id"] == "conv_parent"
    assert runtime.subagent_parent_lane is not None
    assert runtime.subagent_parent_lane.conversation_id == "conv_parent"
    # the envelope stamp is available for mid-turn subagent traffic
    assert runtime.subagent_stamp == {
        "child_conversation_id": "sub_x",
        "forked_from_conversation_id": "conv_parent",
        "forked_from_turn_id": "turn_parent",
        "charter_goal": "Research X",
    }
    # configured subagent default model lands on the user-model role
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import USER_MODEL_TARGET_ROLE

    assert runtime.agent_role_models[USER_MODEL_TARGET_ROLE]["model"] == "claude-haiku-4-5"


def test_bind_child_turn_accounting_stamps_task_identity():
    from kdcube_ai_app.infra import accounting

    fresh = accounting.AccountingContext()
    accounting._set_context(fresh)
    try:
        bind_child_turn_accounting(_child_context())
        assert accounting.get_context().get("agent") == SUBAGENT_ACCOUNTING_AGENT
        enrichment = accounting.get_enrichment()
        assert enrichment.get("metadata", {}).get("subagent", {}).get(
            "parent_conversation_id"
        ) == "conv_parent"
        # the parent backref is FIRST-CLASS on every child accounting event:
        # context keys, exported to the event root (queryable without
        # metadata scans)
        assert accounting.get_context().get("parent_conversation_id") == "conv_parent"
        assert accounting.get_context().get("parent_turn_id") == "turn_parent"
        assert "parent_conversation_id" in accounting.CONTEXT_EXPORT_KEYS
        assert "parent_turn_id" in accounting.CONTEXT_EXPORT_KEYS
    finally:
        accounting.clear_context()


def test_child_payload_carries_the_parent_economics_identity():
    """The economics boundary reads its subject from the payload's user block
    (identity_authority / roles / permissions / user_id) with actor
    tenant/project; the child and continuation payloads must carry the
    parent's, verbatim — same subject, same lane, same bypass decision."""
    from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.schedule import (
        build_completion_task_payload,
    )

    parent_payload = ExternalEventPayload.model_validate({
        "meta": {"task_id": "task_parent", "created_at": 1.0, "instance_id": "inst-1"},
        "routing": {
            "bundle_id": "bundle@1",
            "session_id": "sess_parent",
            "conversation_id": "conv_parent",
            "turn_id": "turn_parent",
        },
        "actor": {"tenant_id": "tenant", "project_id": "project"},
        "user": {
            "user_type": "registered",
            "user_id": "user_1",
            "username": "elena",
            "email": "user@example.test",
            "fingerprint": "fp_1",
            "roles": ["kdcube:role:member"],
            "permissions": ["chat:write"],
            "timezone": "Europe/Berlin",
            "identity_authority": {
                "platform_user_id": "user_1",
                "platform_roles": ["kdcube:role:member"],
            },
        },
        "request": {"external_events": [], "request_id": "req_parent"},
        "config": {"values": {"tenant": "tenant", "project": "project"}},
        "accounting": {"envelope": {"request_id": "req_parent", "metadata": {}}},
    })
    context = _child_context()
    child = build_child_task_payload(
        parent_payload=parent_payload,
        charter=context.charter,
        parent=context.parent,
        child_conversation_id="sub_x",
        child_turn_id="turn_c1",
        subagent_context=context.to_dict(),
    )
    parent_user = parent_payload.user.model_dump()
    assert child["user"] == parent_user
    assert child["actor"] == {"tenant_id": "tenant", "project_id": "project"}
    # the exact fields the run() authority projection reads
    for key in ("identity_authority", "roles", "permissions", "user_id", "user_type"):
        assert child["user"][key] == parent_user[key]
    # config values travel too (the child's ConfigRequest = the parent's)
    assert child["config"] == parent_payload.config.model_dump()

    completion = build_completion_task_payload(
        child_payload=ExternalEventPayload.model_validate(child),
        semantic_type=SUBAGENT_CONVERGED_EVENT_KIND,
        text="done",
        facts={"child_conversation_id": "sub_x", "child_turn_id": "turn_c1"},
        parent=context.parent,
        parent_session_id="sess_parent",
        parent_user=parent_user,
    )
    assert completion["user"] == parent_user
    assert completion["actor"] == {"tenant_id": "tenant", "project_id": "project"}


class _RecordingEmitter:
    """Captures what ChatCommunicator.emit forwards to the relay."""

    def __init__(self):
        self.calls = []

    async def emit(self, **kwargs):
        self.calls.append(kwargs)


def _child_base_comm(emitter=None):
    from kdcube_ai_app.apps.chat.emitters import ChatCommunicator

    return ChatCommunicator(
        emitter=emitter or _RecordingEmitter(),
        tenant="tenant",
        project="project",
        user_id="user_1",
        user_type="privileged",
        service={"request_id": "req", "tenant": "tenant", "project": "project", "user": "user_1"},
        conversation={"session_id": "sub_x", "conversation_id": "sub_x", "turn_id": "turn_c1"},
        room="sub_x",
        target_sid=None,
    )


def _stamp():
    return {
        "child_conversation_id": "sub_x",
        "forked_from_conversation_id": "conv_parent",
        "forked_from_turn_id": "turn_parent",
        "charter_goal": "Research X",
    }


def test_child_comm_policy_default_visibility_is_silent():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.comm_policy import (
        DenyAllEventFilter,
        build_subagent_child_comm,
        normalize_subagent_visibility,
    )

    # the knob's vocabulary: anything that is not "thread" resolves silent
    assert normalize_subagent_visibility(None) == "silent"
    assert normalize_subagent_visibility("") == "silent"
    assert normalize_subagent_visibility("THREAD") == "thread"
    assert normalize_subagent_visibility("loud") == "silent"

    child = build_subagent_child_comm(_child_base_comm())
    assert isinstance(child.event_filter, DenyAllEventFilter)
    assert child.event_filter.allow_event(type="chat.delta") is False
    assert child.target_sid is None
    assert child.conversation["conversation_id"] == "sub_x"


@pytest.mark.asyncio
async def test_child_comm_silent_mode_emits_nothing():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.comm_policy import (
        build_subagent_child_comm,
    )

    emitter = _RecordingEmitter()
    child = build_subagent_child_comm(
        _child_base_comm(emitter),
        subagent=_stamp(),
        parent_session_id="sess_parent",
    )
    await child.start(message="charter")
    await child.delta(text="chunk", index=0)
    await child.complete(data={"ok": True})
    assert emitter.calls == []


@pytest.mark.asyncio
async def test_child_comm_thread_mode_stamps_every_emission_and_routes_to_parent_room():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.comm_policy import (
        build_subagent_child_comm,
    )

    emitter = _RecordingEmitter()
    stamp = _stamp()
    child = build_subagent_child_comm(
        _child_base_comm(emitter),
        visibility="thread",
        parent_session_id="sess_parent",
        subagent=stamp,
    )
    await child.start(message="charter")
    await child.step(step="workflow_start", status="started")
    await child.delta(text="chunk", index=0)
    await child.event(agent="main", type="chat.followups", data={}, auto_markdown=False)
    await child.service_event(
        type="accounting.usage", step="accounting", status="completed", auto_markdown=False,
    )
    await child.complete(data={"ok": True})
    await child.error(message="boom")

    assert len(emitter.calls) == 7
    for call in emitter.calls:
        env = call["data"]
        # every emission carries the subagent envelope stamp
        assert env["subagent"] == stamp
        # delivery: the PARENT conversation's room, session-broadcast
        assert call["session_id"] == "sess_parent"
        assert call["room"] == "sess_parent"
        assert call["target_sid"] is None
        # event identity stays the CHILD's
        conv = env["conversation"]
        assert conv["conversation_id"] == "sub_x"
        assert conv["turn_id"] == "turn_c1"
        assert conv["session_id"] == "sess_parent"
    assert [c["data"]["type"] for c in emitter.calls] == [
        "chat.start", "chat.step", "chat.delta", "chat.followups",
        "accounting.usage", "chat.complete", "chat.error",
    ]


@pytest.mark.asyncio
async def test_child_comm_thread_mode_without_parent_session_stays_silent():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.comm_policy import (
        DenyAllEventFilter,
        build_subagent_child_comm,
    )

    emitter = _RecordingEmitter()
    child = build_subagent_child_comm(
        _child_base_comm(emitter), visibility="thread", subagent=_stamp(),
    )
    assert isinstance(child.event_filter, DenyAllEventFilter)
    await child.start(message="charter")
    assert emitter.calls == []


def test_child_context_round_trips_visibility_and_defaults_silent():
    default_ctx = _child_context()
    assert default_ctx.visibility == "silent"
    assert default_ctx.to_dict()["visibility"] == "silent"

    thread_ctx = SubagentChildTurnContext.from_dict(
        _child_context(visibility="thread").to_dict()
    )
    assert thread_ctx.visibility == "thread"

    # unknown values normalize back to the silent default
    junk_ctx = SubagentChildTurnContext.from_dict(
        {**_child_context().to_dict(), "visibility": "loud"}
    )
    assert junk_ctx.visibility == "silent"


def test_processor_lifecycle_comm_follows_the_child_policy():
    """The processor's chat.start / workflow_start / complete / error around
    a child task ride the same policy as the child's own stream: thread ⇒
    stamped to the parent room; silent ⇒ filtered; continuation ⇒ plain."""
    from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.comm_policy import (
        DenyAllEventFilter,
        SubagentThreadComm,
    )

    def _payload(context=None):
        call_context = {}
        if context is not None:
            call_context[SUBAGENT_CALL_CONTEXT_KEY] = context.to_dict()
        return SimpleNamespace(bundle_call_context=call_context)

    base = _child_base_comm()

    threaded = EnhancedChatRequestProcessor._apply_subagent_comm_policy(
        _payload(_child_context(visibility="thread")), base,
    )
    assert isinstance(threaded, SubagentThreadComm)
    assert threaded.room == "sess_parent"
    assert threaded.subagent_stamp == _stamp()
    assert threaded.conversation["conversation_id"] == "sub_x"

    silent = EnhancedChatRequestProcessor._apply_subagent_comm_policy(
        _payload(_child_context()), base,
    )
    assert isinstance(silent.event_filter, DenyAllEventFilter)

    # a parent continuation turn carries no assignment: the comm is untouched
    plain = EnhancedChatRequestProcessor._apply_subagent_comm_policy(_payload(), base)
    assert plain is base


# ---------------------------------------------------------------- completion


@pytest.mark.asyncio
async def test_converged_completion_is_promotable_on_parent_lane():
    redis = _FakeRedis()
    context = _child_context()
    runtime = SimpleNamespace(
        conversation_id="sub_x", turn_id="turn_c1", subagent_parent_lane=None,
    )

    queue = _FakeAtomicQueueManager(redis)
    event = await publish_child_completion(
        redis=redis,
        runtime_ctx=runtime,
        context=context,
        child_payload=None,
        ok=True,
        final_answer="Charter complete.",
        queue_manager=queue,
    )

    types = await _parent_lane_semantic_types(redis)
    assert types == [SUBAGENT_CONVERGED_EVENT_KIND]
    parent_lane = _lane(redis, "conv_parent")
    stored = await parent_lane.get_event(event.message_id)
    nested = ((stored.payload or {}).get("event") or {}).get("payload") or {}
    assert "Charter complete." in str((nested.get("event") or {}).get("final_answer") or "")
    # the completion's structured facts carry the envelope stamp
    assert (stored.payload or {}).get("subagent") == {
        "child_conversation_id": "sub_x",
        "forked_from_conversation_id": "conv_parent",
        "forked_from_turn_id": "turn_parent",
        "charter_goal": "Research X",
    }
    # promotable: the task payload describes the parent's continuation turn
    task_payload = stored.task_payload or {}
    assert task_payload["routing"]["conversation_id"] == "conv_parent"
    assert task_payload["routing"]["session_id"] == "sess_parent"
    request_events = (task_payload.get("request") or {}).get("external_events") or []
    assert request_events and request_events[0]["type"] == SUBAGENT_CONVERGED_EVENT_KIND
    # the completion names the helper turn (spend attribution key)
    completion_facts = (request_events[0].get("payload") or {}).get("event") or {}
    assert completion_facts.get("child_conversation_id") == "sub_x"
    assert completion_facts.get("child_turn_id") == "turn_c1"
    # ...and one wakeup rides the processor queue for it
    wakeups = _enqueued_wakeups(redis)
    assert len(wakeups) == 1
    assert wakeups[0][1]["event_lane"]["conversation_id"] == "conv_parent"
    assert wakeups[0][1]["event_lane"]["event_id"] == stored.message_id

    # the process-local exactly-once registry recorded the report
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.child_turn import (
        completion_already_published,
    )

    assert completion_already_published("sub_x", "turn_c1") is True
    assert completion_already_published("sub_never", "turn_never") is False


@pytest.mark.asyncio
async def test_failed_completion_is_authored_and_promotable():
    redis = _FakeRedis()
    context = _child_context()
    runtime = SimpleNamespace(
        conversation_id="sub_x", turn_id="turn_c1", subagent_parent_lane=None,
    )

    event = await publish_child_completion(
        redis=redis,
        runtime_ctx=runtime,
        context=context,
        child_payload=None,
        ok=False,
        reason="decision model unavailable",
        queue_manager=_FakeAtomicQueueManager(redis),
    )

    types = await _parent_lane_semantic_types(redis)
    assert types == [SUBAGENT_FAILED_EVENT_KIND]
    parent_lane = _lane(redis, "conv_parent")
    stored = await parent_lane.get_event(event.message_id)
    assert "decision model unavailable" in str(stored.text or "")
    assert ((stored.payload or {}).get("subagent") or {}).get("child_conversation_id") == "sub_x"
    task_payload = stored.task_payload or {}
    request_events = (task_payload.get("request") or {}).get("external_events") or []
    assert request_events and request_events[0]["type"] == SUBAGENT_FAILED_EVENT_KIND
    assert len(_enqueued_wakeups(redis)) == 1


@pytest.mark.asyncio
async def test_converged_without_final_answer_is_authored_as_failed():
    redis = _FakeRedis()
    context = _child_context()
    runtime = SimpleNamespace(
        conversation_id="sub_x", turn_id="turn_c1", subagent_parent_lane=None,
    )
    await publish_child_completion(
        redis=redis,
        runtime_ctx=runtime,
        context=context,
        child_payload=None,
        ok=True,
        final_answer="",
        queue_manager=_FakeAtomicQueueManager(redis),
    )
    assert await _parent_lane_semantic_types(redis) == [SUBAGENT_FAILED_EVENT_KIND]


@pytest.mark.asyncio
async def test_completion_survives_a_rejected_wakeup():
    """The lane publish is unconditional; a backpressure-rejected wakeup
    leaves the completion resting in the lane (folded on the parent's next
    turn) — degraded liveness, zero loss."""
    redis = _FakeRedis()
    context = _child_context()
    runtime = SimpleNamespace(
        conversation_id="sub_x", turn_id="turn_c1", subagent_parent_lane=None,
    )
    queue = _FakeAtomicQueueManager(redis, admit=False, reason="hard_limit_exceeded")
    event = await publish_child_completion(
        redis=redis,
        runtime_ctx=runtime,
        context=context,
        child_payload=None,
        ok=True,
        final_answer="Charter complete.",
        queue_manager=queue,
    )
    # the completion IS in the lane, promotable-by-shape and foldable...
    assert await _parent_lane_semantic_types(redis) == [SUBAGENT_CONVERGED_EVENT_KIND]
    parent_lane = _lane(redis, "conv_parent")
    stored = await parent_lane.get_event(event.message_id)
    assert (stored.task_payload or {}).get("request")
    assert wake_ignore_reason(stored, EventLaneState()) == ""
    # ...and no wakeup was enqueued (the admission declined it)
    assert _enqueued_wakeups(redis) == []
    assert len(queue.wake_calls) == 1


# ------------------------------------------------- promote only if unconsumed


@pytest.mark.asyncio
async def test_completion_wake_promotes_only_if_unconsumed():
    redis = _FakeRedis()
    context = _child_context()
    runtime = SimpleNamespace(
        conversation_id="sub_x", turn_id="turn_c1", subagent_parent_lane=None,
    )
    event = await publish_child_completion(
        redis=redis,
        runtime_ctx=runtime,
        context=context,
        child_payload=None,
        ok=True,
        final_answer="Charter complete.",
        queue_manager=_FakeAtomicQueueManager(redis),
    )
    parent_lane = _lane(redis, "conv_parent")
    stored = await parent_lane.get_event(event.message_id)

    # no live parent turn touched it: the wake promotes
    assert wake_ignore_reason(stored, EventLaneState()) == ""

    # a live parent turn folded it (fold totality marks consumption on the
    # event): the wake is acked, never double-started
    await parent_lane.mark_consumed_up_to(
        max_sequence=int(stored.sequence or 0), turn_id="turn_parent",
    )
    folded = await parent_lane.get_event(event.message_id)
    assert folded.consumed_at is not None
    assert folded.consumed_by_turn_id == "turn_parent"
    assert wake_ignore_reason(folded, EventLaneState()) == "event_already_consumed"

    # the lane's processed-event cursor alone also acks a non-reactive event
    state = EventLaneState(last_processed_event_timestamp="2099-01-01T00:00:00Z")
    assert wake_ignore_reason(stored, state) == "wake_already_processed"

    # an already-promoted duplicate wake is acked too
    stored.promoted_at = 1.0
    assert wake_ignore_reason(stored, EventLaneState()) == "event_already_promoted"



# ---------------------------------------------------------------------------
# Delegate catalog entry: cache-pure static spec
# ---------------------------------------------------------------------------


def test_delegate_spec_is_static_and_names_no_models():
    """The spec is part of the cached system instruction: identical for every
    user, free of model names, provider names, and per-config text."""
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.delegate import TOOL_SPEC

    rendered = json.dumps(TOOL_SPEC)
    for token in ("claude", "anthropic", "haiku", "sonnet", "opus", "ReAct"):
        assert token not in rendered
    # the alias vocabulary itself stays out of the spec — it lives in announce
    for alias in ("fast_agent", "strong_agent", "strongest_agent"):
        assert alias not in rendered
    assert "agent_alias" in TOOL_SPEC["args"]
    assert "charter" in TOOL_SPEC["args"]
    # the spec points at the announce section for the situational half
    assert "DELEGATION" in TOOL_SPEC["args"]["agent_alias"]


def test_catalog_delegate_entry_is_the_static_spec():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.call import get_react_tools_catalog
    from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.delegate import TOOL_SPEC

    entry = next(
        c for c in get_react_tools_catalog(subagent_role="parent")
        if c["id"] == "react.delegate"
    )
    assert entry["purpose"] == TOOL_SPEC["purpose"]
    assert entry["args"] == TOOL_SPEC["args"]
    assert entry["tool_traits"]["strategy"] == ["neutral"]


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


def test_alias_map_ships_defaults_and_admin_entries_win():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import subagent_alias_map

    shipped = subagent_alias_map({})
    assert shipped["fast_agent"]["model"] == "claude-haiku-4-5-20251001"
    assert shipped["fast_agent"]["class"] == "regular"
    assert shipped["fast_agent"]["caption"] == "quick focused work"
    assert shipped["strong_agent"]["model"] == "claude-sonnet-4-6"
    assert shipped["strong_agent"]["class"] == "strong"
    assert "strongest_agent" not in shipped

    merged = subagent_alias_map({
        "models": {
            "strong_agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
            "strongest_agent": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
                "class": "strongest",
                "caption": "the heaviest reasoning",
            },
            # legacy tier labels become aliases; class derives from the label
            "fast": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        }
    })
    assert merged["strong_agent"]["model"] == "claude-opus-4-6"
    assert merged["strong_agent"]["class"] == "strong"  # shipped class kept
    assert merged["strongest_agent"]["class"] == "strongest"
    assert merged["strongest_agent"]["caption"] == "the heaviest reasoning"
    assert merged["fast"]["class"] == "regular"
    assert merged["fast_agent"]["model"] == "claude-haiku-4-5-20251001"


def test_unconfigured_alias_falls_back_to_the_smartest_configured():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import (
        resolve_subagent_alias,
        subagent_alias_map,
    )

    aliases = subagent_alias_map({})
    # strongest_agent belongs to the vocabulary but ships unconfigured
    name, pick = resolve_subagent_alias("strongest_agent", aliases)
    assert name == "strong_agent"
    assert pick == {"provider": "anthropic", "model": "claude-sonnet-4-6"}

    name, pick = resolve_subagent_alias("fast_agent", aliases)
    assert name == "fast_agent"
    assert pick == {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}

    configured = subagent_alias_map({
        "models": {
            "strongest_agent": {
                "provider": "anthropic", "model": "claude-opus-4-6", "class": "strongest",
            },
        }
    })
    name, pick = resolve_subagent_alias("made_up_alias", configured)
    assert name == "strongest_agent"
    assert pick["model"] == "claude-opus-4-6"


def test_resolve_child_model_speaks_aliases():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.child_turn import (
        resolve_child_model,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import (
        SubagentCharter,
    )

    defaults = {"model": "strong_agent"}
    pick = resolve_child_model(
        SubagentCharter(goal="g", agent_alias="fast_agent"),
        bundle_props={}, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}

    # alias-less charter runs on the configured default alias
    pick = resolve_child_model(
        SubagentCharter(goal="g"),
        bundle_props={}, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-sonnet-4-6"}

    # unconfigured strongest_agent runs as the smartest configured alias
    pick = resolve_child_model(
        SubagentCharter(goal="g", agent_alias="strongest_agent"),
        bundle_props={}, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-sonnet-4-6"}

    # an unknown alias resolves the same way (spawn never fails on naming)
    pick = resolve_child_model(
        SubagentCharter(goal="g", agent_alias="galactic"),
        bundle_props={}, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-sonnet-4-6"}

    # a stored charter under the earlier field spelling keeps resolving
    pick = resolve_child_model(
        SubagentCharter.from_dict({"goal": "g", "model": "fast_agent"}),
        bundle_props={}, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}

    # a direct model name from the admin-allowed list keeps resolving, silently
    props = {
        "react": {
            "default_agent": {
                "supported_models": [
                    {"model": "claude-opus-4-6", "provider": "anthropic", "label": "Opus"},
                ],
            }
        }
    }
    pick = resolve_child_model(
        SubagentCharter(goal="g", agent_alias="claude-opus-4-6"),
        bundle_props=props, agent_id="main", subagent_defaults=defaults,
    )
    assert pick == {"provider": "anthropic", "model": "claude-opus-4-6"}

    # alias-less charter with no configured default: the child inherits the
    # parent's role models
    pick = resolve_child_model(
        SubagentCharter(goal="g"),
        bundle_props={}, agent_id="main", subagent_defaults={},
    )
    assert pick is None


def test_configured_max_rounds_reads_the_subagents_block():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import (
        configured_max_rounds,
    )

    assert configured_max_rounds({}) == DEFAULT_SUBAGENT_MAX_ROUNDS
    assert configured_max_rounds(None) == DEFAULT_SUBAGENT_MAX_ROUNDS
    assert configured_max_rounds({"max_rounds": 12}) == 12
    assert configured_max_rounds({"max_rounds": 500}) == MAX_SUBAGENT_MAX_ROUNDS
    assert configured_max_rounds({"max_rounds": 0}) == DEFAULT_SUBAGENT_MAX_ROUNDS


# ---------------------------------------------------------------------------
# Announce: the DELEGATION section
# ---------------------------------------------------------------------------


def _parent_delegation_ctx(**overrides):
    kwargs = dict(
        subagent_depth=0,
        subagent_spawner=object(),
        subagent_model_facts={
            "own": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
            "own_alias": "fast_agent",
            "own_class": "regular",
            "aliases": [
                {
                    "alias": "fast_agent",
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5-20251001",
                    "class": "regular",
                    "caption": "quick focused work",
                },
                {
                    "alias": "strong_agent",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "class": "strong",
                    "caption": "deep reasoning and synthesis",
                },
            ],
            "default_alias": "strong_agent",
        },
    )
    kwargs.update(overrides)
    return SimpleNamespace(**kwargs)


def test_announce_delegation_section_lists_self_class_and_aliases():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_delegation_lines,
    )

    lines = build_announce_delegation_lines(
        runtime_ctx=_parent_delegation_ctx(), timeline_blocks=[],
    )
    text = "\n".join(lines)
    assert lines[0] == "[DELEGATION]"
    # self-identity speaks the SAME alias vocabulary as the helper list
    assert "you are: fast_agent [regular]" in text
    assert "fast_agent [regular]: quick focused work" in text
    assert "strong_agent [strong] (default): deep reasoning and synthesis" in text
    # the announce speaks aliases and classes; model names stay out of it
    assert "claude" not in text
    # no spawned subagents: no delegations block
    assert "delegations in this conversation" not in text


def test_announce_delegation_section_omits_an_unmatched_self_identity():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_delegation_lines,
    )

    ctx = _parent_delegation_ctx()
    facts = dict(ctx.subagent_model_facts)
    facts.pop("own_alias")
    facts.pop("own_class")
    ctx.subagent_model_facts = facts
    text = "\n".join(build_announce_delegation_lines(runtime_ctx=ctx, timeline_blocks=[]))
    assert "you are:" not in text
    assert "fast_agent" in text


def _subagent_event_block(event_type, child_id):
    return {
        "type": event_type,
        "turn_id": "turn_parent",
        "meta": {
            "event_type": event_type,
            "payload": {"child_conversation_id": child_id},
        },
    }


def test_announce_delegation_section_tracks_live_delegations():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_delegation_lines,
    )

    marker_running = build_fork_marker_block(
        parent_turn_id="turn_parent",
        child_conversation_id="sub_run",
        child_turn_id="turn_r1",
        charter_summary="Research the market",
        max_rounds=8,
        agent_alias="strong_agent",
        agent_class="strong",
    )
    marker_done = build_fork_marker_block(
        parent_turn_id="turn_parent",
        child_conversation_id="sub_done",
        child_turn_id="turn_d1",
        charter_summary="Draft the appendix",
        max_rounds=8,
        agent_alias="fast_agent",
        agent_class="regular",
    )
    blocks = [
        marker_running,
        marker_done,
        _subagent_event_block(SUBAGENT_CONTRIBUTION_EVENT_KIND, "sub_run"),
        _subagent_event_block(SUBAGENT_CONTRIBUTION_EVENT_KIND, "sub_run"),
        _subagent_event_block(SUBAGENT_CONVERGED_EVENT_KIND, "sub_done"),
    ]
    text = "\n".join(build_announce_delegation_lines(
        runtime_ctx=_parent_delegation_ctx(), timeline_blocks=blocks,
    ))
    assert "delegations in this conversation:" in text
    assert "Research the market — strong_agent [strong] — contributed 2" in text
    assert "Draft the appendix — fast_agent [regular] — converged" in text

    failed = blocks + [_subagent_event_block(SUBAGENT_FAILED_EVENT_KIND, "sub_run")]
    text = "\n".join(build_announce_delegation_lines(
        runtime_ctx=_parent_delegation_ctx(), timeline_blocks=failed,
    ))
    assert "Research the market — strong_agent [strong] — failed" in text


def test_announce_delegation_section_absent_for_children_and_non_parents():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_delegation_lines,
        build_announce_text,
    )

    # a subagent (child) gets no section, spawner or not
    child = _parent_delegation_ctx(subagent_depth=1)
    assert build_announce_delegation_lines(runtime_ctx=child, timeline_blocks=[]) == []
    # an agent without delegation wired gets none either
    plain = _parent_delegation_ctx(subagent_spawner=None)
    assert build_announce_delegation_lines(runtime_ctx=plain, timeline_blocks=[]) == []

    # end to end through the full announce render
    child_announce = build_announce_text(
        iteration=0,
        max_iterations=8,
        started_at=None,
        timezone="UTC",
        timeline_blocks=[],
        runtime_ctx=child,
    )
    assert "[DELEGATION]" not in child_announce
    parent_announce = build_announce_text(
        iteration=0,
        max_iterations=8,
        started_at=None,
        timezone="UTC",
        timeline_blocks=[],
        runtime_ctx=_parent_delegation_ctx(),
    )
    assert "[DELEGATION]" in parent_announce
    assert "strong_agent [strong] (default)" in parent_announce


def test_announce_delegation_terminal_rows_expire_after_their_turn():
    """Lifecycle: unresolved delegations render every round (live
    obligations); a terminal one renders only during the turn its completion
    folded — afterwards the outcome is ordinary timeline history."""
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_delegation_lines,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
        SUBAGENT_CONVERGED_EVENT_KIND,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.fork import (
        FORK_MARKER_BLOCK_TYPE,
    )

    def _marker(child_id, caption):
        return {
            "type": FORK_MARKER_BLOCK_TYPE,
            "meta": {
                "child_conversation_id": child_id,
                "charter_summary": caption,
                "agent_alias": "strong_agent",
                "agent_class": "strong",
            },
        }

    def _converged(child_id, turn):
        return {
            "type": SUBAGENT_CONVERGED_EVENT_KIND,
            "turn": turn,
            "meta": {
                "event_type": SUBAGENT_CONVERGED_EVENT_KIND,
                "payload": {"child_conversation_id": child_id},
            },
        }

    blocks = [
        _marker("sub_running", "Long research."),
        _marker("sub_done_old", "Old drafting."),
        _converged("sub_done_old", "turn_earlier"),
        _marker("sub_done_now", "Fresh drafting."),
        _converged("sub_done_now", "turn_current"),
    ]
    ctx = _parent_delegation_ctx(turn_id="turn_current")
    text = "\n".join(
        build_announce_delegation_lines(runtime_ctx=ctx, timeline_blocks=blocks)
    )
    # live obligation: always visible
    assert "Long research." in text and "running" in text
    # completion folded THIS turn: visible once
    assert "Fresh drafting." in text and "converged" in text
    # completion folded an earlier turn: expired from the announce
    assert "Old drafting." not in text


def test_tools_block_opens_with_a_derived_roster():
    """The catalog section header carries a machine-derived roster (count +
    ids) the model can verify completeness against; generated from the same
    list it heads, so it can never disagree with the entries below."""
    from kdcube_ai_app.apps.chat.sdk.solutions.react.call import get_react_tools_catalog
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_tools_block

    catalog = get_react_tools_catalog(subagent_role="parent")
    block = build_tools_block(catalog, header="[AVAILABLE REACT TOOLS]")
    assert f"This catalog: {len(catalog)} tools —" in block
    for tool in catalog:
        assert str(tool["id"]) in block.split("═")[0]  # every id in the roster head
    assert "react.delegate" in block.split("═")[0]
