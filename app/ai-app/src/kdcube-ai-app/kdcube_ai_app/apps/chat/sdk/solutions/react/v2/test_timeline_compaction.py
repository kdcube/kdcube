# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import (
    build_plan_block,
    create_plan_snapshot,
    latest_plan_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import (
    Timeline,
    resolve_artifact_from_timeline,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.session import apply_cache_ttl_pruning


def _blk(*, btype: str, text: str, turn_id: str) -> dict:
    return {
        "type": btype,
        "text": text,
        "turn_id": turn_id,
        "ts": "2026-02-09T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_compaction_inserts_summary_and_keeps_cut_block(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_1", max_tokens=200)
    tl = Timeline(runtime=runtime, svc=object())

    blocks = [
        _blk(btype="turn.header", text="[TURN turn_0]", turn_id="turn_0"),
        _blk(btype="user.prompt", text="user asks", turn_id="turn_0"),
        _blk(btype="assistant.completion", text="assistant replies", turn_id="turn_0"),
        _blk(btype="react.tool.result", text="tool-result", turn_id="turn_0"),
        _blk(btype="turn.header", text="[TURN turn_1]", turn_id="turn_1"),
        _blk(btype="user.prompt", text="new ask", turn_id="turn_1"),
        _blk(btype="assistant.completion", text="new reply", turn_id="turn_1"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=20,
        keep_recent_turns=0,
        force=True,
    )

    summary_blocks = [b for b in updated if b.get("type") == "conv.range.summary"]
    assert summary_blocks, "summary block not inserted"
    summary_idx = updated.index(summary_blocks[0])
    # Cut-point block should remain immediately after the summary
    assert summary_idx + 1 < len(updated)
    assert updated[summary_idx + 1].get("type") in {
        "turn.header",
        "user.prompt",
        "assistant.completion",
        "react.tool.call",
    }


@pytest.mark.asyncio
async def test_split_turn_prefix_summary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_2", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())

    # Single turn with multiple blocks so cut falls inside the same turn
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_2]", turn_id="turn_2"),
        _blk(btype="user.prompt", text="user asks" * 30, turn_id="turn_2"),
        _blk(btype="assistant.completion", text="assistant replies" * 30, turn_id="turn_2"),
        _blk(btype="react.tool.call", text="{...}", turn_id="turn_2"),
        _blk(btype="react.tool.result", text="tool-result", turn_id="turn_2"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=80,
        force=True,
    )

    summary_blocks = [b for b in updated if b.get("type") == "conv.range.summary"]
    assert summary_blocks, "summary block not inserted"
    summary_text = summary_blocks[0].get("text") or ""
    assert "Turn Context (split turn)" in summary_text


def test_blocks_for_persist_trims_before_summary():
    runtime = RuntimeCtx(turn_id="turn_3")
    tl = Timeline(runtime=runtime, svc=None)
    tl.blocks = [
        _blk(btype="turn.header", text="[TURN turn_1]", turn_id="turn_1"),
        {"type": "conv.range.summary", "turn_id": "turn_2", "text": "SUMMARY"},
        _blk(btype="turn.header", text="[TURN turn_2]", turn_id="turn_2"),
    ]
    persisted = tl._blocks_for_persist()
    assert persisted[0].get("type") == "conv.range.summary"
    assert len(persisted) == 2


@pytest.mark.asyncio
async def test_no_compaction_when_under_limit():
    runtime = RuntimeCtx(turn_id="turn_4", max_tokens=1000)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_4]", turn_id="turn_4"),
        _blk(btype="user.prompt", text="short", turn_id="turn_4"),
    ]
    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=1000,
        force=False,
    )
    assert updated == blocks


@pytest.mark.asyncio
async def test_compaction_after_existing_summary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_5", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_0]", turn_id="turn_0"),
        {"type": "conv.range.summary", "turn_id": "turn_1", "text": "OLD_SUMMARY"},
        _blk(btype="turn.header", text="[TURN turn_2]", turn_id="turn_2"),
        _blk(btype="user.prompt", text="ask" * 50, turn_id="turn_2"),
        _blk(btype="assistant.completion", text="reply" * 50, turn_id="turn_2"),
        _blk(btype="turn.header", text="[TURN turn_5]", turn_id="turn_5"),
    ]
    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=40,
        keep_recent_turns=0,
        force=True,
    )
    summary_blocks = [b for b in updated if b.get("type") == "conv.range.summary"]
    assert len(summary_blocks) >= 2
    assert updated[1].get("text") == "OLD_SUMMARY"


def test_compaction_digest_includes_hidden_block():
    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import build_compaction_digest

    hidden_block = {
        "type": "react.tool.result",
        "path": "fi:turn_9.files/secret.txt",
        "meta": {"hidden": True, "replacement_text": "HIDDEN"},
        "turn_id": "turn_9",
    }
    digest = build_compaction_digest([hidden_block])
    hidden = digest.get("hidden_blocks") or []
    assert hidden and hidden[0].get("replacement_text") == "HIDDEN"


@pytest.mark.asyncio
async def test_compaction_preserves_tool_call_boundary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_tool", max_tokens=60)
    tl = Timeline(runtime=runtime, svc=object())

    blocks = [
        _blk(btype="turn.header", text="[TURN turn_tool]", turn_id="turn_tool"),
        _blk(btype="user.prompt", text="ask" * 10, turn_id="turn_tool"),
        _blk(btype="react.tool.call", text="call", turn_id="turn_tool"),
        _blk(btype="react.tool.result", text="result", turn_id="turn_tool"),
        _blk(btype="assistant.completion", text="reply" * 10, turn_id="turn_tool"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=30,
        keep_recent_turns=0,
        force=True,
    )
    summary_blocks = [b for b in updated if b.get("type") == "conv.range.summary"]
    assert summary_blocks, "summary block not inserted"
    summary_idx = updated.index(summary_blocks[0])
    # Ensure tool.result isn't the first retained block (boundary should avoid cutting inside tool call/result)
    assert updated[summary_idx + 1].get("type") != "react.tool.result"


@pytest.mark.asyncio
async def test_compaction_keeps_cache_points_after_render(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_cache", max_tokens=60)
    tl = Timeline(runtime=runtime, svc=object())
    tl.blocks = [
        _blk(btype="turn.header", text="[TURN turn_a]", turn_id="turn_a"),
        _blk(btype="user.prompt", text="ask" * 20, turn_id="turn_a"),
        _blk(btype="assistant.completion", text="reply" * 20, turn_id="turn_a"),
        _blk(btype="turn.header", text="[TURN turn_cache]", turn_id="turn_cache"),
        _blk(btype="user.prompt", text="current ask" * 5, turn_id="turn_cache"),
    ]

    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    # At least one cache marker should exist
    assert any(isinstance(b, dict) and b.get("cache") for b in rendered)


@pytest.mark.asyncio
async def test_compaction_preserves_latest_active_plan(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_current", max_tokens=80)
    tl = Timeline(runtime=runtime, svc=object())
    active_snap = create_plan_snapshot(
        plan={"steps": ["gather sources", "draft report"]},
        turn_id="turn_old",
        created_ts="2026-02-09T00:00:00Z",
    )
    active_plan_block = build_plan_block(
        snap=active_snap,
        turn_id="turn_old",
        ts="2026-02-09T00:00:00Z",
    )
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_old]", turn_id="turn_old"),
        _blk(btype="user.prompt", text="old ask" * 20, turn_id="turn_old"),
        active_plan_block,
        _blk(btype="assistant.completion", text="old reply" * 20, turn_id="turn_old"),
        _blk(btype="turn.header", text="[TURN turn_current]", turn_id="turn_current"),
        _blk(btype="user.prompt", text="new ask" * 5, turn_id="turn_current"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=30,
        keep_recent_turns=0,
        force=True,
    )

    latest = latest_plan_snapshot(updated)
    assert latest is not None
    assert latest.plan_id == active_snap.plan_id
    assert latest.is_active()

    summary_idx = next(i for i, b in enumerate(updated) if b.get("type") == "conv.range.summary")
    assert any(
        isinstance(b, dict)
        and b.get("type") == "react.plan"
        and summary_idx < idx
        and (b.get("meta") or {}).get("preserved_by_compaction")
        for idx, b in enumerate(updated)
    )


@pytest.mark.asyncio
async def test_compaction_carries_historical_plan_refs(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_current", max_tokens=90)
    tl = Timeline(runtime=runtime, svc=object())

    old_snap = create_plan_snapshot(
        plan={"steps": ["collect metrics", "compare trends"]},
        turn_id="turn_old",
        created_ts="2026-02-09T00:00:00Z",
    )
    current_snap = create_plan_snapshot(
        plan={"steps": ["draft answer"]},
        turn_id="turn_live",
        created_ts="2026-02-10T00:00:00Z",
    )
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_old]", turn_id="turn_old"),
        build_plan_block(snap=old_snap, turn_id="turn_old", ts="2026-02-09T00:00:00Z"),
        {
            "type": "react.plan.ack",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:01:00Z",
            "path": "ar:turn_old.react.plan.ack.1",
            "text": "✓ 1. collect metrics",
            "meta": {"iteration": 1},
        },
        {
            "type": "react.notes",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "ar:turn_old.react.notes.tc_old",
            "text": "Need to revisit the trend break later.",
            "meta": {"tool_call_id": "tc_old"},
        },
        _blk(btype="assistant.completion", text="old reply" * 20, turn_id="turn_old"),
        _blk(btype="turn.header", text="[TURN turn_live]", turn_id="turn_live"),
        build_plan_block(snap=current_snap, turn_id="turn_live", ts="2026-02-10T00:00:00Z"),
        _blk(btype="assistant.completion", text="current reply", turn_id="turn_live"),
        _blk(btype="turn.header", text="[TURN turn_current]", turn_id="turn_current"),
        _blk(btype="user.prompt", text="new ask" * 5, turn_id="turn_current"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=35,
        keep_recent_turns=0,
        force=True,
    )

    history_blocks = [b for b in updated if b.get("type") == "react.plan.history"]
    assert history_blocks, "historical plan refs block not inserted"
    history_text = history_blocks[0].get("text") or ""
    assert "collect metrics" in history_text
    assert "react.plan.history" in history_blocks[0].get("path", "")
    assert f"snapshot_ref: ar:plan.latest:{old_snap.plan_id}" in history_text

    persisted = tl._blocks_for_persist()
    snapshot_ref = f"ar:plan.latest:{old_snap.plan_id}"
    snapshot_art = resolve_artifact_from_timeline({"blocks": persisted, "sources_pool": []}, snapshot_ref)

    assert snapshot_art and old_snap.plan_id in (snapshot_art.get("text") or "")
    assert "latest_note_preview: Need to revisit the trend break later." in history_text


@pytest.mark.asyncio
async def test_compaction_preserves_internal_notes_after_summary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_live", max_tokens=80)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_old]", turn_id="turn_old"),
        _blk(btype="user.prompt", text="old ask" * 20, turn_id="turn_old"),
        {
            "type": "react.note",
            "author": "react",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:01:00Z",
            "path": "fi:turn_old.files/memory/key-artifacts.md",
            "text": "[K] fi:turn_old.files/src/app/auth/service.py - invite flow implementation",
            "meta": {"channel": "internal"},
        },
        _blk(btype="assistant.completion", text="old reply" * 20, turn_id="turn_old"),
        _blk(btype="turn.header", text="[TURN turn_live]", turn_id="turn_live"),
        _blk(btype="user.prompt", text="new ask" * 5, turn_id="turn_live"),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=30,
        keep_recent_turns=0,
        force=True,
    )

    summary_idx = next(i for i, b in enumerate(updated) if b.get("type") == "conv.range.summary")
    preserved_notes = [
        b for i, b in enumerate(updated)
        if i > summary_idx and isinstance(b, dict) and b.get("type") == "react.note.preserved"
    ]
    assert preserved_notes, "internal notes should be preserved after summary"
    assert "[K]" in (preserved_notes[0].get("text") or "")
    assert (preserved_notes[0].get("meta") or {}).get("preserved_by_compaction") is True

    persisted = tl._blocks_for_persist()
    assert any(b.get("type") == "react.note.preserved" for b in persisted)


def test_cache_ttl_pruning_keeps_internal_notes_visible():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    tl.blocks = [
        {
            "type": "turn.header",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:00Z",
            "text": "[TURN turn_old]",
        },
        {
            "type": "react.note",
            "author": "react",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:01:00Z",
            "path": "fi:turn_old.files/memory/key-artifacts.md",
            "text": "[K] fi:turn_old.files/src/app/auth/service.py - invite flow implementation",
            "meta": {"channel": "internal"},
        },
        {
            "type": "assistant.completion",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "ar:turn_old.assistant.completion",
            "text": "done",
        },
    ]
    tl.cache_last_touch_at = 0

    res = apply_cache_ttl_pruning(
        timeline=tl,
        ttl_seconds=1,
        buffer_seconds=0,
        keep_recent_turns=0,
    )

    assert res.get("status") in {"pruned", "pruned_light", "no_effect"}
    note_block = next(b for b in tl.blocks if b.get("type") == "react.note")
    assert not note_block.get("hidden")
    assert "fi:turn_old.files/memory/key-artifacts.md" not in (res.get("hidden_paths") or [])
