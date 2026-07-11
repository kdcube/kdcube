# SPDX-License-Identifier: MIT

from __future__ import annotations

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
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
    SUBAGENT_CHARTER_EVENT_KIND,
    SUBAGENT_CONTRIBUTION_EVENT_KIND,
    SUBAGENT_EVENT_SOURCE_ID,
    SUBAGENT_FAILED_EVENT_KIND,
    ParentLaneAddress,
    contribution_refs_for_parent,
    publish_subagent_event,
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


class _FakeCtxClient:
    """Minimal ctx client: timeline artifacts round-trip through memory."""

    class _Store:
        async def get_blob_bytes(self, uri_or_path):
            raise FileNotFoundError(uri_or_path)

    def __init__(self):
        self.store = self._Store()
        self.saved = []  # (kind, conversation_id, content)

    async def save_artifact(self, *, kind, conversation_id, content, **kwargs):
        self.saved.append({"kind": kind, "conversation_id": conversation_id, "content": content})
        return {"ok": True}

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


def test_parse_charter_requires_goal_and_clamps_budget():
    charter, err = parse_charter({
        "charter": {
            "goal": "Research X",
            "deliverables": ["files/report.md"],
            "max_rounds": 500,
            "contribute": "the report ref",
        },
        "model": "claude-sonnet-4-6",
    })
    assert err == ""
    assert charter.goal == "Research X"
    assert charter.max_rounds == MAX_SUBAGENT_MAX_ROUNDS
    assert charter.model == "claude-sonnet-4-6"

    missing, err = parse_charter({"charter": {"deliverables": ["x"]}})
    assert missing is None and err == "missing_goal"

    flat, err = parse_charter({"goal": "flat form"})
    assert err == "" and flat.max_rounds == DEFAULT_SUBAGENT_MAX_ROUNDS

    text = flat.charter_text()
    assert "[SUBAGENT CHARTER]" in text and "flat form" in text


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


def test_fork_marker_block_names_child_and_charter():
    marker = build_fork_marker_block(
        parent_turn_id="turn_3",
        child_conversation_id="childconv",
        child_turn_id="turn_c1",
        charter_summary="Research X",
        deliverables=["files/report.md"],
        max_rounds=8,
        tool_call_id="tc1",
    )
    assert marker["type"] == FORK_MARKER_BLOCK_TYPE
    assert "conv_childconv" in marker["text"]
    assert "Research X" in marker["text"]
    assert marker["meta"]["child_conversation_id"] == "childconv"
    assert marker["meta"]["max_rounds"] == 8
    assert marker["call_id"] == "tc1"


# ---------------------------------------------------------------- events


@pytest.mark.asyncio
async def test_charter_event_is_authored_passive_external_event():
    redis = _FakeRedis()
    lane = _lane(redis, "conv_child")
    charter = SubagentCharter(goal="Research X", max_rounds=5)
    event = await publish_subagent_event(
        lane_source=lane,
        semantic_type=SUBAGENT_CHARTER_EVENT_KIND,
        text=charter.charter_text(),
        facts={"charter": charter.to_dict()},
        author="agent:conv_parent/turn_3",
        target_turn_id="turn_c1",
    )
    stored = await lane.get_event(event.message_id)
    assert stored is not None
    # transport kind is uniformly external_event; the semantic type is nested
    assert stored.kind == "external_event"
    nested = (stored.payload or {}).get("event") or {}
    assert nested.get("type") == SUBAGENT_CHARTER_EVENT_KIND
    assert nested.get("event_source_id") == SUBAGENT_EVENT_SOURCE_ID
    assert nested.get("reactive") is False
    assert stored.source == "agent:conv_parent/turn_3"
    assert stored.target_turn_id == "turn_c1"
    # passive: the stored task envelope carries no request to run
    assert "request" not in (stored.task_payload or {})


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
        state=_tool_state("react.delegate", {"charter": {"goal": "nested"}}),
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
        state=_tool_state("react.delegate", {"charter": {"goal": "goal"}}),
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
            "charter": {"goal": "Research X", "max_rounds": 4, "deliverables": ["files/r.md"]},
        }),
        tool_call_id="tc5",
    )
    result = state["last_tool_result"]
    assert result["status"] == "started"
    assert result["child_conversation_ref"] == "conv_sub_abc"
    assert launches and launches[0].charter.goal == "Research X"
    assert launches[0].charter.max_rounds == 4
    marker = next(b for b in browser.blocks if b.get("type") == FORK_MARKER_BLOCK_TYPE)
    assert marker["meta"]["child_conversation_id"] == "sub_abc"


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
        self.comm_context = None
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


@pytest.mark.asyncio
async def test_spawner_refuses_depth_and_runs_child_that_fails_authoring_failed_event(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import ReactSubagentSpawner
    from kdcube_ai_app.infra import accounting

    seen = {}

    class _StubWorkflow(_StubWorkflowBase):
        def build_react(self, scratchpad, **kwargs):
            seen["accounting_context"] = dict(accounting.get_context() or {})
            seen["accounting_enrichment"] = dict(accounting.get_enrichment() or {})
            seen["build_kwargs"] = kwargs
            raise RuntimeError("decision model unavailable")

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.browser.get_exec_workspace_root",
        lambda: tmp_path,
    )
    workflow = _StubWorkflow(tmp_path)
    spawner = ReactSubagentSpawner(
        workflow=workflow,
        build_template={"mod_tools_spec": None, "story_snapshots_enabled": False},
    )

    # depth guard at the spawner boundary too
    deep = _launch_request()
    deep.parent_depth = 1
    with pytest.raises(RuntimeError):
        await spawner.spawn(deep)

    fork_blocks = [{
        "type": FORK_HEADER_BLOCK_TYPE,
        "turn_id": "turn_parent",
        "path": "conv:ar:turn_parent.subagent.fork.header",
        "text": "[FORK] context",
        "meta": {},
    }]
    await spawner._run_child(
        request=_launch_request(fork_blocks),
        redis=workflow.redis,
        child_conversation_id="sub_test1",
        child_turn_id="turn_c1",
    )

    # charter authored onto the CHILD lane
    child_lane = _lane(workflow.redis, "sub_test1")
    child_events = await child_lane.read_since(None)
    child_types = [((e.payload or {}).get("event") or {}).get("type") for e in child_events]
    assert SUBAGENT_CHARTER_EVENT_KIND in child_types
    charter_event = child_events[child_types.index(SUBAGENT_CHARTER_EVENT_KIND)]
    assert charter_event.source == "agent:conv_conv_parent/turn_parent"

    # the fork seed was persisted as the child conversation's timeline
    assert any(
        row["conversation_id"] == "sub_test1" and row["kind"] == "conv.timeline.v1"
        for row in workflow.ctx_client.saved
    )
    seeded = next(
        row for row in workflow.ctx_client.saved
        if row["conversation_id"] == "sub_test1" and row["kind"] == "conv.timeline.v1"
    )
    assert any(b.get("type") == FORK_HEADER_BLOCK_TYPE for b in seeded["content"]["blocks"])
    assert seeded["content"]["blocks"][0]["meta"]["child_conversation_id"] == "sub_test1"

    # the failure was authored to the PARENT lane, never silenced
    parent_types = await _parent_lane_semantic_types(workflow.redis)
    assert SUBAGENT_FAILED_EVENT_KIND in parent_types

    # accounting: child spend accounts under the child conversation with the
    # identifiable subagent tag
    assert seen["accounting_context"].get("conversation_id") == "sub_test1"
    assert seen["accounting_context"].get("turn_id") == "turn_c1"
    assert seen["accounting_context"].get("agent") == "react.subagent"
    assert seen["accounting_enrichment"].get("metadata", {}).get("subagent", {}).get(
        "parent_conversation_id"
    ) == "conv_parent"

    # the child was built through the SAME path with the child overrides
    kwargs = seen["build_kwargs"]
    assert kwargs["ctx_browser_override"].runtime_ctx.conversation_id == "sub_test1"
    assert kwargs["ctx_browser_override"].runtime_ctx.subagent_depth == 1
    assert kwargs["comm_override"] is not None


@pytest.mark.asyncio
async def test_spawner_child_budget_and_silence_and_converged_event(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.react_subagents import (
        ReactSubagentSpawner,
        _DenyAllEventFilter,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
        SUBAGENT_CONVERGED_EVENT_KIND,
    )

    captured = {}

    class _StubReact:
        def __init__(self):
            self.persisted = False

        async def run(self, *, allowed_plugins, allowed_tool_names_by_alias=None):
            captured["allowed_plugins"] = list(allowed_plugins or [])
            return SimpleNamespace(ok=True, final_answer="Charter complete.", error=None)

        async def persist_workspace(self):
            self.persisted = True

    class _StubWorkflow(_StubWorkflowBase):
        def build_react(self, scratchpad, **kwargs):
            captured["kwargs"] = kwargs
            captured["scratchpad_text"] = scratchpad.user_text
            return _StubReact()

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.react.browser.get_exec_workspace_root",
        lambda: tmp_path,
    )
    workflow = _StubWorkflow(tmp_path)
    spawner = ReactSubagentSpawner(workflow=workflow, build_template={})

    await spawner._run_child(
        request=_launch_request(),
        redis=workflow.redis,
        child_conversation_id="sub_test2",
        child_turn_id="turn_c1",
    )

    kwargs = captured["kwargs"]
    child_ctx = kwargs["ctx_browser_override"].runtime_ctx
    # budget: the charter budget IS the iteration budget, with no reactive credit
    assert child_ctx.max_iterations == 4
    assert child_ctx.reactive_event_iteration_credit_enabled is False
    # depth + parent address wired for react.contribute
    assert child_ctx.subagent_depth == 1
    assert child_ctx.subagent_parent["conversation_id"] == "conv_parent"
    assert child_ctx.subagent_parent_lane is not None
    # silent-until-contribution: the child's communicator denies every event
    silent_comm = kwargs["comm_override"]
    assert isinstance(silent_comm.event_filter, _DenyAllEventFilter)
    assert silent_comm.target_sid is None
    # the charter is the child's task text
    assert "[SUBAGENT CHARTER]" in captured["scratchpad_text"]
    # tool selection inherited from the parent run
    assert captured["allowed_plugins"] == ["some_plugin"]

    # converged authored to the parent lane with the final answer
    lane = _lane(workflow.redis, "conv_parent")
    events = await lane.read_since(None)
    types = [((e.payload or {}).get("event") or {}).get("type") for e in events]
    assert SUBAGENT_CONVERGED_EVENT_KIND in types
    converged = events[types.index(SUBAGENT_CONVERGED_EVENT_KIND)]
    nested = ((converged.payload or {}).get("event") or {}).get("payload") or {}
    assert "Charter complete." in str((nested.get("event") or {}).get("final_answer") or "")

    # the child conversation's timeline was persisted at completion
    assert any(
        row["conversation_id"] == "sub_test2" and row["kind"] == "conv.timeline.v1"
        for row in workflow.ctx_client.saved
    )
