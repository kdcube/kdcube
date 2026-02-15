# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import Timeline
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx


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

    # Patch summarizers used by Timeline
    import kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline as tl_mod

    monkeypatch.setattr(tl_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(tl_mod, "summarize_turn_prefix_progressive", _fake_prefix)

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

    import kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline as tl_mod

    monkeypatch.setattr(tl_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(tl_mod, "summarize_turn_prefix_progressive", _fake_prefix)

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

    import kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline as tl_mod

    monkeypatch.setattr(tl_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(tl_mod, "summarize_turn_prefix_progressive", _fake_prefix)

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

    import kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline as tl_mod

    monkeypatch.setattr(tl_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(tl_mod, "summarize_turn_prefix_progressive", _fake_prefix)

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

    import kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline as tl_mod

    monkeypatch.setattr(tl_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(tl_mod, "summarize_turn_prefix_progressive", _fake_prefix)

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
