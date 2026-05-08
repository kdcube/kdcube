# SPDX-License-Identifier: MIT

import json

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
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.session import (
    TruncationConfig,
    _bound_ttl_replacement,
    apply_cache_ttl_pruning,
)


def _blk(*, btype: str, text: str, turn_id: str, ts: str = "2026-02-09T00:00:00Z") -> dict:
    return {
        "type": btype,
        "text": text,
        "turn_id": turn_id,
        "ts": ts,
    }


@pytest.mark.asyncio
async def test_compaction_inserts_summary_and_keeps_cut_block(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        prefix_blocks = kwargs.get("blocks") or []
        assert any(
            b.get("path") == email_result_path
            and not b.get("hidden")
            and "email body" in (b.get("text") or "")
            for b in prefix_blocks
        )
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
async def test_compaction_summary_renders_as_prior_memory_checkpoint():
    runtime = RuntimeCtx(turn_id="turn_current", max_tokens=1000)
    tl = Timeline(runtime=runtime, svc=object())
    tl.blocks = [
        {
            "type": "conv.range.summary",
            "author": "system",
            "turn_id": "turn_old",
            "path": "su:turn_old.conv.range.summary",
            "text": "Goal: old work\nOutcome: old result",
            "meta": {
                "covered_turn_ids": ["turn_a", "turn_b"],
                "compacted_range_start_ts": "2026-02-01T10:00:00Z",
                "compacted_range_end_ts": "2026-02-02T11:00:00Z",
                "conversation_first_message_ts": "2026-02-01T10:00:00Z",
                "split_turn_id": "turn_b",
            },
        },
        _blk(btype="turn.header", text="[TURN turn_current]", turn_id="turn_current"),
        _blk(btype="user.prompt", text="continue", turn_id="turn_current"),
    ]

    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[COMPACTED PRIOR CONVERSATION MEMORY]" in text
    assert "[path: su:turn_old.conv.range.summary]" in text
    assert "covered_turns: turn_a, turn_b" in text
    assert "compacted_time_range: 2026-02-01T10:00:00Z -> 2026-02-02T11:00:00Z" in text
    assert "conversation_first_message_ts: 2026-02-01T10:00:00Z" in text
    assert "split_turn_id: turn_b" in text
    assert "origin: model-generated compaction of older timeline blocks removed from the visible stream" in text
    assert "use: treat this as prior conversation state" in text
    assert "Goal: old work" in text
    assert "[END COMPACTED PRIOR CONVERSATION MEMORY]" in text
    assert "TURN turn_current" in text


@pytest.mark.asyncio
async def test_compaction_summary_caps_covered_turns_list():
    runtime = RuntimeCtx(turn_id="turn_current", max_tokens=1000)
    tl = Timeline(runtime=runtime, svc=object())
    tl.blocks = [
        {
            "type": "conv.range.summary",
            "author": "system",
            "turn_id": "turn_old",
            "path": "su:turn_old.conv.range.summary",
            "text": "Goal: old work",
            "meta": {
                "covered_turn_ids": [f"turn_{idx}" for idx in range(12)],
            },
        },
        _blk(btype="turn.header", text="[TURN turn_current]", turn_id="turn_current"),
        _blk(btype="user.prompt", text="continue", turn_id="turn_current"),
    ]

    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "covered_turns: turn_0, turn_1, ... turn_10, turn_11 (count=12)" in text
    assert "turn_2, turn_3, turn_4" not in text


@pytest.mark.asyncio
async def test_compaction_summary_metadata_keeps_temporal_bounds(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_current", max_tokens=200)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(
            btype="turn.header",
            text="[TURN turn_old]",
            turn_id="turn_old",
            ts="2026-02-01T09:59:00Z",
        ),
        _blk(
            btype="user.prompt",
            text="first user message",
            turn_id="turn_old",
            ts="2026-02-01T10:00:00Z",
        ),
        _blk(
            btype="assistant.completion",
            text="old reply" * 30,
            turn_id="turn_old",
            ts="2026-02-01T10:01:00Z",
        ),
        _blk(
            btype="turn.header",
            text="[TURN turn_current]",
            turn_id="turn_current",
            ts="2026-02-03T12:00:00Z",
        ),
        _blk(
            btype="user.prompt",
            text="current ask",
            turn_id="turn_current",
            ts="2026-02-03T12:01:00Z",
        ),
    ]

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=40,
        keep_recent_turns=0,
        force=True,
    )

    summary_block = next(b for b in updated if b.get("type") == "conv.range.summary")
    meta = summary_block.get("meta") or {}
    assert meta.get("compacted_range_start_ts") == "2026-02-01T09:59:00Z"
    assert meta.get("compacted_range_end_ts") == "2026-02-01T10:01:00Z"
    assert meta.get("conversation_first_message_ts") == "2026-02-01T10:00:00Z"


@pytest.mark.asyncio
async def test_current_turn_split_does_not_create_prior_summary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        raise AssertionError("current-turn-only compaction must not summarize a fake prior history")

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_2", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())

    # Single turn with multiple blocks so cut falls inside the current turn.
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

    assert not [b for b in updated if b.get("type") == "conv.range.summary"]
    checkpoints = [b for b in updated if b.get("type") == "react.current_turn.compaction_checkpoint"]
    assert checkpoints
    assert "PREFIX" in (checkpoints[-1].get("text") or "")
    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")
    assert "[MID-TURN COMPACTION 1]" in text
    assert "semantic_progress:" in text


@pytest.mark.asyncio
async def test_split_turn_compaction_preserves_round_ledger(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_job", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())
    email_result_path = "tc:turn_job.tc_email.result"
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_job]", turn_id="turn_job", ts="2026-05-08T09:00:00Z"),
        _blk(btype="user.prompt", text="scheduled email digest", turn_id="turn_job", ts="2026-05-08T09:00:01Z"),
        _blk(btype="react.thinking", text="Need to fetch emails, then generate a report from exact data.", turn_id="turn_job", ts="2026-05-08T09:00:01Z"),
        _blk(btype="react.note", text="[K] Email result path must be processed exactly, not guessed.", turn_id="turn_job", ts="2026-05-08T09:00:01Z"),
        {
            "type": "react.tool.call",
            "turn_id": "turn_job",
            "call_id": "tc_email",
            "path": "tc:turn_job.tc_email.call",
            "mime": "application/json",
            "ts": "2026-05-08T09:00:02Z",
            "text": json.dumps({
                "tool_id": "email.process_user_emails",
                "tool_call_id": "tc_email",
                "params": {"mailbox": "INBOX", "max_messages": 50},
            }),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_email",
            "path": email_result_path,
            "mime": "application/json",
            "text": json.dumps({"ok": True, "messages": ["email body " * 2000 for _ in range(40)]}),
            "meta": {"tool_call_id": "tc_email", "tool_id": "email.process_user_emails"},
        },
        {
            "type": "react.tool.call",
            "turn_id": "turn_job",
            "call_id": "tc_read",
            "path": "tc:turn_job.tc_read.call",
            "mime": "application/json",
            "ts": "2026-05-08T09:01:00Z",
            "text": json.dumps({
                "tool_id": "react.read",
                "tool_call_id": "tc_read",
                "params": {"paths": [email_result_path]},
            }),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_read",
            "path": "tc:turn_job.tc_read.result",
            "mime": "application/json",
            "text": json.dumps({"paths": [{"path": email_result_path, "tokens": 89167}], "total_tokens": 89167}),
            "meta": {"tool_call_id": "tc_read", "tool_id": "react.read"},
        },
        _blk(btype="assistant.completion", text="continue", turn_id="turn_job", ts="2026-05-08T09:02:00Z"),
    ]
    monkeypatch.setattr(tl, "_find_compaction_cut_point", lambda *args, **kwargs: (8, 0, True))

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=80,
        force=True,
    )

    assert not [b for b in updated if b.get("type") == "conv.range.summary"]
    assert not [b for b in updated if b.get("type") == "react.rounds.compacted"]
    result_blocks = [b for b in updated if b.get("path") == email_result_path]
    assert result_blocks
    assert result_blocks[0].get("hidden") is True
    assert result_blocks[0].get("text")
    persisted_blocks = tl._blocks_for_persist()
    persisted_result = [b for b in persisted_blocks if b.get("path") == email_result_path]
    assert persisted_result and persisted_result[0].get("text") == result_blocks[0].get("text")
    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[MID-TURN COMPACTION 1]" in text
    assert "[USER MESSAGE]" in text
    assert "scheduled email digest" in text
    assert "semantic_progress:" in text
    assert "PREFIX" in text
    assert "engineering_ledger:" in text
    assert "tool_call_id: tc_email" in text
    assert "call: tc:turn_job.tc_email.call" in text
    assert "result: tc:turn_job.tc_email.result" in text
    assert "tool: email.process_user_emails" in text
    assert "tool: react.read" in text
    assert email_result_path in text
    assert "result_tokens_estimate:" in text
    assert "position: current-turn prefix compacted here" in text
    assert "exact source blocks remain in timeline.json" in text
    assert text.index("[USER MESSAGE]") < text.index("[MID-TURN COMPACTION 1]")
    assert text.index("[MID-TURN COMPACTION 1]") < text.index("[ASSISTANT MESSAGE]")


@pytest.mark.asyncio
async def test_mid_turn_engineering_ledger_groups_outputs_by_tool_call(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "Grouped progress summary."

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_job", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_job]", turn_id="turn_job", ts="2026-05-08T09:00:00Z"),
        _blk(btype="user.prompt", text="run tools", turn_id="turn_job", ts="2026-05-08T09:00:01Z"),
        {
            "type": "react.tool.call",
            "turn_id": "turn_job",
            "call_id": "tc_exec",
            "path": "tc:turn_job.tc_exec.call",
            "mime": "application/json",
            "text": json.dumps({"tool_id": "exec_tools.execute_code_python", "tool_call_id": "tc_exec", "params": {"code": "write files"}}),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_exec",
            "path": "tc:turn_job.tc_exec.result",
            "mime": "application/json",
            "text": json.dumps({
                "ok": True,
                "artifact_type": "files",
                "files": [
                    {"artifact_path": "fi:turn_job.outputs/a.pdf"},
                    {"artifact_path": "fi:turn_job.outputs/b.csv"},
                    {"artifact_path": "fi:turn_job.outputs/c.json"},
                ],
            }),
            "meta": {"tool_call_id": "tc_exec", "tool_id": "exec_tools.execute_code_python"},
        },
        {"type": "react.tool.result", "turn_id": "turn_job", "call_id": "tc_exec", "path": "fi:turn_job.outputs/a.pdf", "mime": "application/pdf", "meta": {"tool_call_id": "tc_exec"}},
        {"type": "react.tool.result", "turn_id": "turn_job", "call_id": "tc_exec", "path": "fi:turn_job.outputs/b.csv", "mime": "text/csv", "meta": {"tool_call_id": "tc_exec"}},
        {"type": "react.tool.result", "turn_id": "turn_job", "call_id": "tc_exec", "path": "fi:turn_job.outputs/c.json", "mime": "application/json", "meta": {"tool_call_id": "tc_exec"}},
        {
            "type": "react.tool.call",
            "turn_id": "turn_job",
            "call_id": "tc_web",
            "path": "tc:turn_job.tc_web.call",
            "mime": "application/json",
            "text": json.dumps({"tool_id": "web_tools.web_search", "tool_call_id": "tc_web", "params": {"query": "news"}}),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_web",
            "path": "tc:turn_job.tc_web.result",
            "mime": "application/json",
            "text": json.dumps({"ok": True, "sources_used": [1, 2, 3], "results": [{"sid": 1}, {"sid": 2}, {"sid": 3}]}),
            "meta": {"tool_call_id": "tc_web", "tool_id": "web_tools.web_search", "sources_used": [1, 2, 3]},
        },
        {
            "type": "react.tool.call",
            "turn_id": "turn_job",
            "call_id": "tc_render",
            "path": "tc:turn_job.tc_render.call",
            "mime": "application/json",
            "text": json.dumps({"tool_id": "rendering_tools.write_pdf", "tool_call_id": "tc_render", "params": {"path": "outputs/report.pdf"}}),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_render",
            "path": "tc:turn_job.tc_render.result",
            "mime": "application/json",
            "text": json.dumps({"ok": True, "artifact_path": "fi:turn_job.outputs/report.pdf", "mime": "application/pdf"}),
            "meta": {"tool_call_id": "tc_render", "tool_id": "rendering_tools.write_pdf"},
        },
        {"type": "react.tool.result", "turn_id": "turn_job", "call_id": "tc_render", "path": "fi:turn_job.outputs/report.pdf", "mime": "application/pdf", "meta": {"tool_call_id": "tc_render"}},
        _blk(btype="assistant.completion", text="continue", turn_id="turn_job", ts="2026-05-08T09:02:00Z"),
    ]
    monkeypatch.setattr(tl, "_find_compaction_cut_point", lambda *args, **kwargs: (len(blocks) - 1, 0, True))

    await tl.sanitize_context_blocks(system_text="sys", blocks=blocks, max_tokens=80, force=True)
    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "tool_call_id: tc_exec" in text
    assert "tool: exec_tools.execute_code_python" in text
    assert "files:" in text
    assert "fi:turn_job.outputs/a.pdf mime=application/pdf" in text
    assert "fi:turn_job.outputs/b.csv mime=text/csv" in text
    assert "fi:turn_job.outputs/c.json mime=application/json" in text
    assert "tool_call_id: tc_web" in text
    assert "tool: web_tools.web_search" in text
    assert "so:sources_pool[1-3]" in text
    assert "tool_call_id: tc_render" in text
    assert "tool: rendering_tools.write_pdf" in text
    assert "fi:turn_job.outputs/report.pdf mime=application/pdf" in text
    assert text.index("tool_call_id: tc_exec") < text.index("tool_call_id: tc_web") < text.index("tool_call_id: tc_render")


@pytest.mark.asyncio
async def test_render_shows_only_latest_mid_turn_compaction_checkpoint():
    runtime = RuntimeCtx(turn_id="turn_job", max_tokens=1000)
    tl = Timeline(runtime=runtime, svc=object())
    tl.blocks = [
        _blk(btype="turn.header", text="[TURN turn_job]", turn_id="turn_job", ts="2026-05-08T09:00:00Z"),
        _blk(btype="user.prompt", text="run the job", turn_id="turn_job", ts="2026-05-08T09:00:01Z"),
        {
            "type": "react.current_turn.compaction_checkpoint",
            "turn_id": "turn_job",
            "path": "ar:turn_job.react.mid_turn.compaction.1",
            "text": "[MID-TURN COMPACTION 1]\nold checkpoint\n[/MID-TURN COMPACTION 1]",
            "meta": {"current_turn_compaction_checkpoint": True, "marker_index": 1},
        },
        {
            "type": "react.current_turn.compaction_checkpoint",
            "turn_id": "turn_job",
            "path": "ar:turn_job.react.mid_turn.compaction.2",
            "text": "[MID-TURN COMPACTION 2]\nlatest checkpoint\n[/MID-TURN COMPACTION 2]",
            "meta": {"current_turn_compaction_checkpoint": True, "marker_index": 2},
        },
        _blk(btype="assistant.completion", text="done", turn_id="turn_job", ts="2026-05-08T09:02:00Z"),
    ]

    rendered = await tl.render(cache_last=False, system_text="sys", include_sources=False)
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[MID-TURN COMPACTION 1]" not in text
    assert "[MID-TURN COMPACTION 2]" in text
    assert "latest checkpoint" in text


@pytest.mark.asyncio
async def test_empty_split_turn_prefix_summary_compacts_prior_history_only(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "HISTORY SUMMARY"

    async def _empty_prefix(*args, **kwargs):
        return None

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _empty_prefix)

    runtime = RuntimeCtx(turn_id="turn_2", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_1]", turn_id="turn_1"),
        _blk(btype="user.prompt", text="old ask" * 30, turn_id="turn_1"),
        _blk(btype="assistant.completion", text="old reply" * 30, turn_id="turn_1"),
        _blk(btype="turn.header", text="[TURN turn_2]", turn_id="turn_2"),
        _blk(btype="user.prompt", text="current ask" * 30, turn_id="turn_2"),
        _blk(btype="assistant.completion", text="current reply" * 30, turn_id="turn_2"),
    ]
    monkeypatch.setattr(tl, "_find_compaction_cut_point", lambda *args, **kwargs: (5, 3, True))

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=80,
        force=True,
    )

    summary_idx = next(i for i, b in enumerate(updated) if b.get("type") == "conv.range.summary")
    summary_text = updated[summary_idx].get("text") or ""
    assert "HISTORY SUMMARY" in summary_text
    assert "Turn Context (split turn)" not in summary_text
    assert updated[summary_idx + 1].get("turn_id") == "turn_2"
    assert updated[summary_idx + 1].get("type") == "turn.header"


@pytest.mark.asyncio
async def test_split_inside_non_current_turn_compacts_full_turn_without_prefix_summary(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "HISTORY SUMMARY"

    async def _prefix_should_not_run(*args, **kwargs):
        raise AssertionError("turn-prefix summary should only run for the current turn")

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _prefix_should_not_run)

    runtime = RuntimeCtx(turn_id="turn_3", max_tokens=120)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = [
        _blk(btype="turn.header", text="[TURN turn_1]", turn_id="turn_1"),
        _blk(btype="user.prompt", text="old ask" * 30, turn_id="turn_1"),
        _blk(btype="assistant.completion", text="old reply" * 30, turn_id="turn_1"),
        _blk(btype="turn.header", text="[TURN turn_2]", turn_id="turn_2"),
        _blk(btype="user.prompt", text="middle ask" * 30, turn_id="turn_2"),
        _blk(btype="assistant.completion", text="middle reply" * 30, turn_id="turn_2"),
        _blk(btype="turn.header", text="[TURN turn_3]", turn_id="turn_3"),
        _blk(btype="user.prompt", text="current ask" * 10, turn_id="turn_3"),
    ]
    monkeypatch.setattr(tl, "_find_compaction_cut_point", lambda *args, **kwargs: (5, 3, True))

    updated = await tl.sanitize_context_blocks(
        system_text="sys",
        blocks=blocks,
        max_tokens=80,
        force=True,
    )

    summary_idx = next(i for i, b in enumerate(updated) if b.get("type") == "conv.range.summary")
    assert "HISTORY SUMMARY" in (updated[summary_idx].get("text") or "")
    assert updated[summary_idx + 1].get("turn_id") == "turn_3"
    assert updated[summary_idx + 1].get("type") == "turn.header"


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


def test_turn_prefix_serializer_caps_large_tool_result_without_hiding():
    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import (
        _serialize_context_blocks_for_compaction,
    )

    payload = {
        "ok": True,
        "messages": [
            {
                "message_id": f"msg_{idx}",
                "thread_id": f"thr_{idx}",
                "from": "Sender <sender@example.com>",
                "subject": f"Subject {idx}",
                "date": "Fri, 08 May 2026 10:00:00 +0000",
                "body_excerpt": "body " * 2000,
            }
            for idx in range(8)
        ],
    }
    text = _serialize_context_blocks_for_compaction([
        {
            "type": "react.tool.result",
            "turn_id": "turn_job",
            "call_id": "tc_email",
            "path": "tc:turn_job.tc_email.result",
            "mime": "application/json",
            "text": json.dumps(payload),
            "meta": {"tool_call_id": "tc_email", "tool_id": "email.process_user_emails"},
        }
    ])

    assert "hidden=true" not in text
    assert "[TRUNCATED LARGE TOOL RESULT FOR COMPACTION SUMMARY]" in text
    assert "shape:" in text
    assert "sample:" in text
    assert '"messages"' in text
    assert '"message_id": "msg_0"' in text
    assert "ctx_tools.fetch_ctx(path=\"tc:turn_job.tc_email.result\")" in text
    assert "msg_7" not in text


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


@pytest.mark.asyncio
async def test_compaction_caps_preserved_internal_notes(monkeypatch):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_prefix(*args, **kwargs):
        return "PREFIX"

    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_live", max_tokens=80)
    tl = Timeline(runtime=runtime, svc=object())
    old_turn_blocks = [
        _blk(btype="turn.header", text="[TURN turn_old]", turn_id="turn_old"),
        _blk(btype="user.prompt", text="old ask" * 20, turn_id="turn_old"),
    ]
    for idx in range(45):
        old_turn_blocks.append(
            {
                "type": "react.note",
                "author": "react",
                "turn_id": "turn_old",
                "ts": f"2026-02-09T00:{idx:02d}:00Z",
                "path": f"fi:turn_old.files/memory/note-{idx}.md",
                "text": f"[K] note {idx}",
                "meta": {"channel": "internal"},
            }
        )
    blocks = [
        *old_turn_blocks,
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
    assert len(preserved_notes) == 32
    preserved_texts = {(b.get("text") or "").strip() for b in preserved_notes}
    assert "[K] note 44" in preserved_texts
    assert "[K] note 0" not in preserved_texts


@pytest.mark.asyncio
async def test_compaction_rewrites_preferences_into_summary(monkeypatch):
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
            "path": "fi:turn_old.files/memory/preferences.md",
            "text": "[P] User prefers direct answers with no product pitch unless asked.",
            "meta": {"channel": "internal"},
        },
        {
            "type": "react.note",
            "author": "react",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "fi:turn_old.files/memory/preferences.md",
            "text": "[P] User prefers concise answers and product positioning only when relevant.",
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

    summary_block = next(b for b in updated if b.get("type") == "conv.range.summary")
    summary_text = (summary_block.get("text") or "").strip()
    assert "[INTERNAL MEMORY DIGEST]" in summary_text
    assert "Active conversation preferences:" in summary_text
    assert "product positioning only when relevant" in summary_text
    assert "no product pitch unless asked" not in summary_text


@pytest.mark.asyncio
async def test_compaction_preserves_external_turn_events_after_summary(monkeypatch):
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
            "type": "user.followup",
            "author": "user",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:01:00Z",
            "path": "ar:turn_old.external.followup.evt_1",
            "text": "also include the quantum angle",
            "meta": {"message_id": "evt_1", "target_turn_id": "turn_old"},
        },
        {
            "type": "user.steer",
            "author": "user",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "ar:turn_old.external.steer.evt_2",
            "text": "stop the broader scan and wrap up what you have",
            "meta": {"message_id": "evt_2", "target_turn_id": "turn_old"},
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
    preserved_events = [
        b
        for i, b in enumerate(updated)
        if i > summary_idx
        and isinstance(b, dict)
        and b.get("type") in {"user.followup.preserved", "user.steer.preserved"}
    ]
    assert len(preserved_events) == 2
    assert {b.get("type") for b in preserved_events} == {"user.followup.preserved", "user.steer.preserved"}
    assert all((b.get("meta") or {}).get("preserved_by_compaction") is True for b in preserved_events)

    persisted = tl._blocks_for_persist()
    assert any(b.get("type") == "user.followup.preserved" for b in persisted)
    assert any(b.get("type") == "user.steer.preserved" for b in persisted)


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
        keep_recent_intact_turns=0,
    )

    assert res.get("status") in {"pruned", "pruned_light", "no_effect"}
    note_block = next(b for b in tl.blocks if b.get("type") == "react.note")
    assert not note_block.get("hidden")
    assert "fi:turn_old.files/memory/key-artifacts.md" not in (res.get("hidden_paths") or [])


def test_cache_ttl_pruning_keeps_working_summary_visible():
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
            "type": "conv.working.summary",
            "author": "assistant",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "ws:turn_old.conv.working.summary.attempt.1",
            "text": "Goal: old task\nOutcome: first useful result",
            "meta": {"kind": "working_summary", "summary_scope": "completion_attempt"},
        },
        {
            "type": "conv.working.summary",
            "author": "assistant",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:30Z",
            "path": "ws:turn_old.conv.working.summary",
            "text": "Goal: old task\nOutcome: final useful result",
            "meta": {"kind": "working_summary"},
        },
        {
            "type": "assistant.completion",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:03:00Z",
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
        keep_recent_intact_turns=0,
    )

    assert res.get("status") in {"pruned", "pruned_light", "no_effect"}
    summary_blocks = [b for b in tl.blocks if b.get("type") == "conv.working.summary"]
    assert len(summary_blocks) == 2
    assert all(not b.get("hidden") for b in summary_blocks)
    assert "ws:turn_old.conv.working.summary.attempt.1" not in (res.get("hidden_paths") or [])
    assert "ws:turn_old.conv.working.summary" not in (res.get("hidden_paths") or [])


def test_cache_ttl_pruning_preserves_react_read_large_binary_marker_shape():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    marker_path = "fi:turn_src.outputs/large-report.pdf"
    tl.blocks = [
        {
            "type": "turn.header",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:00Z",
            "text": "[TURN turn_old]",
        },
        {
            "type": "react.tool.call",
            "turn_id": "turn_old",
            "call_id": "r_read",
            "path": "tc:turn_old.r_read.call",
            "text": json.dumps({
                "tool_id": "react.read",
                "tool_call_id": "r_read",
                "params": {"paths": [marker_path]},
            }),
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_old",
            "call_id": "r_read",
            "path": marker_path,
            "mime": "text/markdown",
            "text": "\n".join([
                "[LARGE READ NOT MATERIALIZED]",
                f"path: {marker_path}",
                "bytes: 24000000",
                "visible_read_limit_bytes: 10485760",
                "exact_content: recoverable by logical path",
            ]),
        },
    ]
    tl.cache_last_touch_at = 0

    res = apply_cache_ttl_pruning(
        timeline=tl,
        ttl_seconds=1,
        buffer_seconds=0,
        keep_recent_turns=0,
        keep_recent_intact_turns=0,
    )

    assert res.get("status") in {"pruned", "pruned_light", "no_effect"}
    marker = next(b for b in tl.blocks if b.get("path") == marker_path)
    assert marker.get("hidden") is True
    replacement = marker.get("replacement_text") or ""
    assert "LARGE READ NOT MATERIALIZED" in replacement
    assert f"path: {marker_path}" in replacement
    assert "bytes: 24000000" in replacement
    assert "visible_read_limit_bytes: 10485760" in replacement


def test_cache_ttl_pruning_keeps_external_turn_events_visible():
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
            "type": "user.followup",
            "author": "user",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:01:00Z",
            "path": "ar:turn_old.external.followup.evt_1",
            "text": "also include the quantum angle",
            "meta": {"message_id": "evt_1", "target_turn_id": "turn_old"},
        },
        {
            "type": "user.steer",
            "author": "user",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:02:00Z",
            "path": "ar:turn_old.external.steer.evt_2",
            "text": "stop and summarize",
            "meta": {"message_id": "evt_2", "target_turn_id": "turn_old"},
        },
        {
            "type": "assistant.completion",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:03:00Z",
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
    followup_block = next(b for b in tl.blocks if b.get("type") == "user.followup")
    steer_block = next(b for b in tl.blocks if b.get("type") == "user.steer")
    assert not followup_block.get("hidden")
    assert not steer_block.get("hidden")
    hidden_paths = res.get("hidden_paths") or []
    assert "ar:turn_old.external.followup.evt_1" not in hidden_paths
    assert "ar:turn_old.external.steer.evt_2" not in hidden_paths


@pytest.mark.asyncio
async def test_render_compacts_when_pruned_skeleton_has_too_many_message_blocks(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    async def _fake_summary(*args, **kwargs):
        return "compacted rendered skeleton"

    async def _fake_prefix(*args, **kwargs):
        return "compacted turn prefix"

    monkeypatch.setattr(summary_mod, "summarize_context_blocks_progressive", _fake_summary)
    monkeypatch.setattr(summary_mod, "summarize_turn_prefix_progressive", _fake_prefix)

    runtime = RuntimeCtx(turn_id="turn_cur", max_tokens=80000)
    tl = Timeline(runtime=runtime, svc=object())
    blocks = []
    for idx in range(760):
        tid = f"turn_{idx}"
        blocks.append({
            "type": "turn.header",
            "turn_id": tid,
            "text": f"TURN {tid}",
        })
        blocks.append({
            "type": "user.prompt",
            "author": "user",
            "turn_id": tid,
            "path": f"ar:{tid}.user.prompt",
            "text": "x",
            "hidden": True,
            "replacement_text": f"[pruned user] path=ar:{tid}.user.prompt hint=\"x\"",
        })
    tl.blocks = blocks

    rendered = await tl.render(cache_last=False, include_sources=False, include_announce=False)

    assert any(b.get("type") == "conv.range.summary" for b in tl.blocks)
    assert len(rendered) < 760
    assert "compacted rendered skeleton" in rendered[0]["text"]


def test_cache_ttl_pruning_collapses_old_prune_notices():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    tl.blocks = [
        {
            "type": "system.message",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:00Z",
            "path": "ar:turn_old.system.message.cache_pruned",
            "text": "Context was pruned because the session TTL was exceeded. " * 20,
            "meta": {"kind": "cache_ttl_pruned"},
        },
    ]
    tl.cache_last_touch_at = 0

    res = apply_cache_ttl_pruning(
        timeline=tl,
        ttl_seconds=1,
        buffer_seconds=0,
        keep_recent_turns=0,
        keep_recent_intact_turns=0,
    )

    assert res.get("status") in {"pruned", "pruned_light"}
    block = tl.blocks[0]
    assert block.get("hidden") is True
    assert "cache prune notice hidden" in block.get("replacement_text", "")
    assert "session TTL was exceeded" not in block.get("replacement_text", "")


def test_hide_paths_preserves_explicit_replacement_text():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    path = "tc:turn_old.tc_large.result"
    tl.blocks = [
        {
            "type": "react.tool.result",
            "turn_id": "turn_old",
            "path": path,
            "text": '{"ok": true, "messages": []}',
            "meta": {"tool_call_id": "tc_large", "tool_id": "email.process_user_emails"},
        },
    ]
    replacement = "very verbose replacement " * 1000

    res = tl.hide_paths([path], replacement)

    assert res["status"] == "ok"
    stored = tl.blocks[0].get("replacement_text", "")
    assert stored == replacement


def test_ttl_replacement_bound_caps_automatic_prune_replacement():
    path = "tc:turn_old.tc_large.result"
    block = {
        "type": "react.tool.result",
        "turn_id": "turn_old",
        "path": path,
        "text": '{"ok": true, "messages": []}',
        "meta": {"tool_call_id": "tc_large", "tool_id": "email.process_user_emails"},
    }
    replacement = "\n".join(
        [
            "[TRUNCATED]",
            f"path={path}",
            "tool_id=email.process_user_emails",
            "payload=" + ("very verbose replacement " * 1000),
        ]
    )

    stored = _bound_ttl_replacement(
        block=block,
        replacement=replacement,
        cfg=TruncationConfig(replacement_max_tokens=40),
    )

    assert len(stored) < len(replacement)
    assert path in stored
    assert "email.process_user_emails" in stored


def test_ttl_replacement_bound_caps_material_growth_below_absolute_cap():
    path = "tc:turn_old.tc_small.result"
    block = {
        "type": "react.tool.result",
        "turn_id": "turn_old",
        "path": path,
        "text": '{"ok": true}',
        "meta": {"tool_call_id": "tc_small", "tool_id": "email.process_user_emails"},
    }
    replacement = "\n".join(
        [
            "[TRUNCATED]",
            f"path={path}",
            "tool_id=email.process_user_emails",
            "payload=" + ("growth " * 240),
        ]
    )

    stored = _bound_ttl_replacement(
        block=block,
        replacement=replacement,
        cfg=TruncationConfig(replacement_max_tokens=240),
    )

    assert len(stored) < len(replacement)
    assert path in stored
    assert "email.process_user_emails" in stored


@pytest.mark.asyncio
async def test_cache_ttl_pruning_renders_compact_turn_status_for_old_internal_blocks():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    finalize_text = """
╔═══════════════════════════════════╗
║  Turn completed with these stats  ║
╚═══════════════════════════════════╝

[BUDGET]
  iterations  ██░░░░░░░░  9 remaining
  time_elapsed_in_turn   23s

[OPEN PLANS]
  - plans: none

[WORKSPACE]
  implementation: git
  current_turn_root: turn_old/
  current_turn_publish: pending
  last_published_turn: turn_prev (succeeded)
"""
    tl.blocks = [
        {
            "type": "turn.header",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:00Z",
            "text": "TURN turn_old",
        },
        {
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:20Z",
            "text": finalize_text,
        },
        {
            "type": "react.state",
            "author": "react",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.state",
            "mime": "application/json",
            "text": '{"iteration": 2, "max_iterations": 12, "exit_reason": "complete", "error": null}',
        },
        {
            "type": "react.exit",
            "author": "react",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.exit",
            "mime": "application/json",
            "text": '{"reason": "complete"}',
        },
        {
            "type": "react.workspace.publish",
            "author": "react.workspace",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.workspace.publish",
            "mime": "application/json",
            "text": '{"status": "succeeded", "turn_id": "turn_old", "workspace_implementation": "git"}',
        },
    ]
    tl.cache_last_touch_at = 0

    res = apply_cache_ttl_pruning(
        timeline=tl,
        ttl_seconds=1,
        buffer_seconds=0,
        keep_recent_turns=0,
        keep_recent_intact_turns=0,
    )
    rendered = await tl.render(cache_last=False, include_sources=False, include_announce=False)
    text = "\n".join(str(block.get("text") or "") for block in rendered if isinstance(block, dict))

    assert res.get("status") in {"pruned", "pruned_light"}
    assert "[TURN STATUS]" in text
    assert "rounds: 3/12" in text
    assert "exit_reason: complete" in text
    assert "time_elapsed_in_turn: 23s" in text
    assert "current_turn_root: turn_old/" in text
    assert "last_published_turn: turn_prev (succeeded)" in text
    assert "Turn completed with these stats" not in text
    assert "[pruned react state]" not in text
    assert "[pruned react workspace publish]" not in text
    assert "refs:" not in text
    assert "ar:turn_old.react.turn.finalize" not in text
    assert "ar:turn_old.react.state" not in text
    assert "ar:turn_old.react.exit" not in text
    assert "ar:turn_old.react.workspace.publish" not in text


@pytest.mark.asyncio
async def test_cache_ttl_pruning_suppresses_old_round_scaffolding_and_duplicate_assistant_path():
    runtime = RuntimeCtx(turn_id="turn_cur")
    tl = Timeline(runtime=runtime, svc=None)
    assistant_path = "ar:turn_old.assistant.completion"
    tl.blocks = [
        {
            "type": "turn.header",
            "turn_id": "turn_old",
            "ts": "2026-02-09T00:00:00Z",
            "text": "TURN turn_old",
        },
        {
            "type": "react.round.start",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.round.start.tc_1",
            "text": "thinking",
            "meta": {"tool_call_id": "tc_1"},
        },
        {
            "type": "react.notes",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.notes.tc_1",
            "text": "Retrying email tool after user applied fixes.",
            "meta": {"tool_call_id": "tc_1"},
        },
        {
            "type": "react.thinking",
            "turn_id": "turn_old",
            "path": "ar:turn_old.react.thinking.1",
            "text": "The tool now returns 403, which means runtime binding is fixed.",
        },
        {
            "type": "react.notice",
            "turn_id": "turn_old",
            "path": "tc:turn_old.tc_1.notice",
            "mime": "application/json",
            "text": '{"code": "tool_result_error", "message": "HTTP Error 403: Forbidden"}',
            "meta": {"tool_call_id": "tc_1"},
        },
        {
            "type": "react.tool.call",
            "turn_id": "turn_old",
            "path": "tc:turn_old.tc_1.call",
            "text": '{"tool_id": "email.process_user_emails", "tool_call_id": "tc_1", "params": {"mailbox": "INBOX"}}',
            "meta": {"tool_call_id": "tc_1"},
        },
        {
            "type": "react.tool.result",
            "turn_id": "turn_old",
            "path": "tc:turn_old.tc_1.result",
            "text": '{"tool_id": "email.process_user_emails", "tool_call_id": "tc_1", "error": "HTTP Error 403: Forbidden"}',
            "meta": {"tool_call_id": "tc_1"},
        },
        {
            "type": "assistant.completion",
            "turn_id": "turn_old",
            "path": assistant_path,
            "text": "Progress: HTTP 403 means the tool is now reaching Gmail but lacks authorization.",
        },
        {
            "type": "stage.suggested_followups",
            "turn_id": "turn_old",
            "path": "ar:turn_old.stage.suggested_followups",
            "text": "[STAGE: SUGGESTED FOLLOW-UPS]\nitems: Reauthorize Gmail",
        },
    ]
    tl.cache_last_touch_at = 0

    res = apply_cache_ttl_pruning(
        timeline=tl,
        ttl_seconds=1,
        buffer_seconds=0,
        keep_recent_turns=0,
        keep_recent_intact_turns=0,
    )
    rendered = await tl.render(cache_last=False, include_sources=False, include_announce=False)
    text = "\n".join(str(block.get("text") or "") for block in rendered if isinstance(block, dict))

    assert res.get("status") in {"pruned", "pruned_light"}
    assert "ROUND 1" not in text
    assert "[AI Agent say]" not in text
    assert "[AI Agent thinking...]" not in text
    assert "[pruned react thinking]" not in text
    assert "[pruned react notice]" not in text
    assert "[pruned stage suggested_followups]" not in text
    assert "[pruned " not in text
    assert "[PRUNED TURN DATA]" in text
    assert text.count("[PRUNED TURN DATA]") == 1
    assert "tool_call:" in text
    assert "tool_result:" in text
    assert f"assistant: path={assistant_path}" in text
    assert "[ASSISTANT MESSAGE]" not in text
    assert text.count(assistant_path) == 1


@pytest.mark.asyncio
async def test_turn_prefix_summary_empty_model_output_returns_none(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.streaming.streaming as streaming_mod
    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    class _NoopAccounting:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _blank_stream(*args, **kwargs):
        return {
            "agent_response": "",
            "log": {"raw_data": "", "error": None, "service_error": None},
        }

    monkeypatch.setattr(summary_mod, "with_accounting", lambda *args, **kwargs: _NoopAccounting())
    monkeypatch.setattr(streaming_mod, "stream_agent_to_json", _blank_stream)

    summary = await summary_mod.summarize_turn_prefix_progressive(
        svc=object(),
        blocks=[
            _blk(btype="user.prompt", text="please create the invoice zip", turn_id="turn_1"),
            _blk(btype="assistant.completion", text="started by loading the email skill", turn_id="turn_1"),
        ],
    )

    assert summary is None


def test_compaction_serializer_marks_external_turn_events():
    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import (
        _serialize_context_blocks_for_compaction,
    )

    text = _serialize_context_blocks_for_compaction(
        [
            {
                "type": "user.followup",
                "author": "user",
                "turn_id": "turn_old",
                "text": "also include the quantum angle",
            },
            {
                "type": "user.steer",
                "author": "user",
                "turn_id": "turn_old",
                "text": "stop and summarize",
            },
        ]
    )

    assert "[User Followup During Turn]:" in text
    assert "also include the quantum angle" in text
    assert "[User Steer During Turn]:" in text
    assert "stop and summarize" in text


def test_compaction_serializer_marks_working_summary_not_assistant():
    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import (
        _serialize_context_blocks_for_compaction,
    )

    text = _serialize_context_blocks_for_compaction(
        [
            {
                "type": "conv.working.summary",
                "author": "assistant",
                "turn_id": "turn_old",
                "path": "ws:turn_old.conv.working.summary.attempt.1",
                "text": "Goal: create invoice ZIP\nOutcome: materialized files but ZIP failed",
                "meta": {
                    "kind": "working_summary",
                    "summary_scope": "completion_attempt",
                    "assistant_completion_path": "ar:turn_old.assistant.completion",
                },
            },
        ]
    )

    assert "[Working Summary]:" in text
    assert "ws:turn_old.conv.working.summary.attempt.1" in text
    assert "assistant_completion_path" in text
    assert "[Assistant]:" not in text


def test_compaction_serializer_accepts_numeric_metadata_fields():
    from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary import (
        _serialize_context_blocks_for_compaction,
    )

    text = _serialize_context_blocks_for_compaction(
        [
            {
                "type": "react.tool.result",
                "turn_id": "turn_old",
                "ts": 1778032340.800,
                "path": "tc:turn_old.tc_result.result",
                "call_id": 12345,
                "tool_id": "email.process_user_emails",
                "text": "",
            },
        ]
    )

    assert "[Tool result]:" in text
    assert "ts=1778032340.8" in text
    assert "call_id=12345" in text
    assert "path=tc:turn_old.tc_result.result" in text


@pytest.mark.asyncio
async def test_context_compaction_prompt_injects_relevant_working_summaries(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.streaming.streaming as streaming_mod
    import kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conv_progressive_summary as summary_mod

    class _NoopAccounting:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    captured = {}

    async def _capture_stream(*args, **kwargs):
        messages = kwargs.get("messages") or []
        captured["prompt"] = messages[0].content if messages else ""
        return {
            "agent_response": "SUMMARY",
            "log": {"raw_data": "SUMMARY", "error": None, "service_error": None},
        }

    monkeypatch.setattr(summary_mod, "with_accounting", lambda *args, **kwargs: _NoopAccounting())
    monkeypatch.setattr(streaming_mod, "stream_agent_to_json", _capture_stream)

    summary = await summary_mod.summarize_context_blocks_progressive(
        svc=object(),
        blocks=[
            _blk(btype="user.prompt", text="please send all April invoices", turn_id="turn_old"),
        ],
        working_summary_blocks=[
            {
                "type": "conv.working.summary",
                "author": "assistant",
                "turn_id": "turn_old",
                "path": "ws:turn_old.conv.working.summary.attempt.1",
                "text": "Goal: invoice retrieval\nOutcome: found 20 Anthropic PDFs",
                "meta": {"summary_scope": "completion_attempt"},
            },
        ],
    )

    prompt = captured.get("prompt") or ""
    assert summary == "SUMMARY"
    assert "<working-summaries>" in prompt
    assert "[Working Summary]:" in prompt
    assert "found 20 Anthropic PDFs" in prompt
    assert "[Assistant]: Goal: invoice retrieval" not in prompt
