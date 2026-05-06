# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import Timeline
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import (
    build_plan_ack_block,
    build_plan_block,
    create_plan_snapshot,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_timeline_rendering_with_attachment_and_tool_blocks():
    ctx = RuntimeCtx(turn_id="turn_123", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text="[TURN turn_123]"),
        tl._block(
            type="user.prompt",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_123.user.prompt",
            text="[USER MESSAGE]\n[path: ar:turn_123.user.prompt]\nhello",
        ),
        tl._block(
            type="user.attachment.meta",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/menu.pdf",
            text="[USER ATTACHMENT] menu.pdf | application/pdf\n[path: fi:turn_123.user.attachments/menu.pdf]\n[physical_path: turn_123/attachments/menu.pdf]",
            meta={"physical_path": "turn_123/attachments/menu.pdf"},
        ),
        tl._block(
            type="user.attachment",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/menu.pdf",
            mime="application/pdf",
            base64="QUJD",
        ),
        tl._block(
            type="user.attachment.meta",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/notes.txt",
            text="[USER ATTACHMENT] notes.txt | text/plain\n[path: fi:turn_123.user.attachments/notes.txt]\n[physical_path: turn_123/attachments/notes.txt]",
            meta={"physical_path": "turn_123/attachments/notes.txt"},
        ),
        tl._block(
            type="user.attachment.text",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/notes.txt",
            text="[USER ATTACHMENT TEXT]\n[path: fi:turn_123.user.attachments/notes.txt]\nhello notes",
        ),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.abc.call",
            text='{"tool_id":"web_tools.web_search","tool_call_id":"abc"}',
        ),
        # react.write (display + file)
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.write1.call",
            text='{"tool_id":"react.write","tool_call_id":"write1","params":{"path":"turn_123/files/draft.md","kind":"file"}}',
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.write1.result",
            text='{"artifact_path":"fi:turn_123.files/draft.md","physical_path":"turn_123/files/draft.md","tool_call_id":"write1","kind":"file","visibility":"external"}',
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="text/markdown",
            path="fi:turn_123.files/draft.md",
            text="# Draft\\n",
        ),
        # exec tool call referencing the file written above
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.exec1.call",
            text='{"tool_id":"exec_tools.execute_code_python","tool_call_id":"exec1","params":{"code":"open(\'turn_123/files/draft.md\').read()"}}',
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_blocks = [b for b in rendered if b.get("type") == "text"]
    doc_blocks = [b for b in rendered if b.get("type") == "document"]
    assert all(b.get("type") in {"text", "document", "image"} for b in rendered)

    assert any("TURN turn_123" in b.get("text", "") for b in text_blocks)
    assert any("[path: ar:turn_123.user.prompt]" in b.get("text", "") for b in text_blocks)
    assert any("turn_123/attachments/menu.pdf" in b.get("text", "") for b in text_blocks)
    assert any("fi:turn_123.user.attachments/notes.txt" in b.get("text", "") for b in text_blocks)
    assert any("hello notes" in b.get("text", "") for b in text_blocks)
    assert any("fi:turn_123.files/draft.md" in b.get("text", "") for b in text_blocks)
    assert doc_blocks, "Expected a document block for the PDF attachment"


def test_timeline_does_not_emit_non_pdf_binary_attachment_as_model_document():
    ctx = RuntimeCtx(turn_id="turn_123", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text="[TURN turn_123]"),
        tl._block(
            type="user.attachment.meta",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/template.xlsx",
            text="[USER ATTACHMENT] template.xlsx | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            meta={
                "filename": "template.xlsx",
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "physical_path": "turn_123/attachments/template.xlsx",
            },
        ),
        tl._block(
            type="user.attachment",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_123.user.attachments/template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            base64="WFNTREFUQQ==",
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    assert not any(
        b.get("type") == "document"
        and b.get("media_type") != "application/pdf"
        for b in rendered
    )
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")
    assert "template.xlsx" in text
    assert "BINARY FILE NOT ATTACHED DIRECTLY TO MODEL" in text


def test_timeline_renders_plan_calls_but_hides_internal_plan_snapshots():
    ctx = RuntimeCtx(turn_id="turn_plan", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)

    plan = create_plan_snapshot(
        plan={"steps": ["collect metrics", "compare trends"]},
        turn_id="turn_plan",
        created_ts=ctx.started_at,
    )
    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text="[TURN turn_plan]"),
        tl._block(
            type="react.notes",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_plan.react.notes.tc_plan",
            text="Create the investigation plan.",
        ),
        tl._block(
            type="react.tool.call",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_plan.tc_plan.call",
            text=json.dumps(
                {
                    "tool_id": "react.plan",
                    "tool_call_id": "tc_plan",
                    "params": {"mode": "new", "steps": ["collect metrics", "compare trends"]},
                    "new_plan_id": plan.plan_id,
                    "new_snapshot_ref": f"ar:plan.latest:{plan.plan_id}",
                },
                ensure_ascii=False,
            ),
        ),
        tl._block(
            type="react.tool.result",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="text/markdown",
            path="tc:turn_plan.tc_plan.result",
            text="\n".join([
                "mode: new",
                f"plan_id: {plan.plan_id}",
                f"latest_snapshot_ref: ar:plan.latest:{plan.plan_id}",
            ]),
            meta={"tool_call_id": "tc_plan", "render_role": "summary"},
        ),
        build_plan_block(snap=plan, turn_id="turn_plan", ts=ctx.started_at),
        build_plan_ack_block(
            ack_items=[{"step": 1, "status": "done", "text": "collect metrics"}],
            turn_id="turn_plan",
            ts=ctx.started_at,
            iteration=1,
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_dump = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[TOOL CALL tc_plan].call react.plan" in text_dump
    assert '"mode": "new"' in text_dump
    assert '"steps": [' in text_dump
    assert "[TOOL RESULT tc_plan].summary react.plan" in text_dump
    assert f"plan_id: {plan.plan_id}" in text_dump
    assert f"latest_snapshot_ref: ar:plan.latest:{plan.plan_id}" in text_dump
    assert "[AI Agent say]: Create the investigation plan." in text_dump
    assert '"origin_turn_id": "turn_plan"' not in text_dump
    assert "ar:turn_plan.react.plan.ack.1" not in text_dump


def test_timeline_renders_interrupted_raw_generation_even_when_raw_hidden():
    ctx = RuntimeCtx(turn_id="turn_raw", started_at="2026-04-12T00:00:00Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.append(
        tl._block(
            type="react.decision.raw",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_raw.react.decision.raw.interrupted.1",
            text="<channel:thinking>draft</channel:thinking><channel:code>print(1)</channel:code>",
            meta={
                "interrupted": True,
                "reason": "steer.interrupted",
                "checkpoint": "decision.after",
                "cancelled_phase": "decision",
            },
        )
    )

    rendered = _run(tl.render(cache_last=True))
    text_dump = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[REACT DECISION RAW INTERRUPTED]" in text_dump
    assert "checkpoint: decision.after" in text_dump
    assert "cancelled_phase: decision" in text_dump
    assert "<channel:thinking>draft</channel:thinking>" in text_dump


def test_timeline_renders_failed_protocol_attempt_as_a_round():
    ctx = RuntimeCtx(turn_id="turn_fail", started_at="2026-04-16T00:00:00Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text="[TURN turn_fail]"),
        tl._block(
            type="react.notes",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_fail.react.notes.tc_fail_1",
            text="Wrong round. The agent wrote invalid content into the action channel, so this round executed no action.",
            meta={
                "tool_id": "__protocol_violation__",
                "tool_call_id": "tc_fail_1",
                "action": "call_tool",
                "iteration": 0,
            },
        ),
        tl._block(
            type="react.decision.raw",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_fail.react.decision.raw.0",
            text="```json\n{\"action\":\"call_tool\"}\n```\n```json\n{\"action\":\"call_tool\"}\n```",
            meta={
                "reason": "schema_error",
                "tool_call_id": "tc_fail_1",
                "iteration": 0,
            },
        ),
        tl._block(
            type="react.notice",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_fail.tc_fail_1.notice",
            text=json.dumps(
                {
                    "code": "protocol_violation.ReactDecisionOutV2_schema_error",
                    "message": "Bad Protocol. The agent output in <channel:ReactDecisionOutV2> could not be parsed, so no action was executed for this round.",
                },
                ensure_ascii=False,
            ),
            meta={"tool_call_id": "tc_fail_1"},
        ),
        tl._block(
            type="react.notes",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_fail.react.notes.tc_ok_2",
            text="Searching the web.",
            meta={
                "tool_id": "web_tools.web_search",
                "tool_call_id": "tc_ok_2",
                "action": "call_tool",
                "iteration": 1,
            },
        ),
        tl._block(
            type="react.tool.call",
            author="react",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_fail.tc_ok_2.call",
            text=json.dumps(
                {
                    "tool_id": "web_tools.web_search",
                    "tool_call_id": "tc_ok_2",
                    "params": {"queries": ["latest neuroscience news"]},
                },
                ensure_ascii=False,
            ),
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_dump = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "┌──────── ROUND 1 ────────┐" in text_dump
    assert "┌──────── ROUND 2 ────────┐" in text_dump
    assert "[PROTOCOL VIOLATION]" in text_dump
    assert "protocol_violation.ReactDecisionOutV2_schema_error" in text_dump
    assert "Wrong round. The agent wrote invalid content into the action channel" in text_dump
    assert "[TOOL CALL tc_ok_2].call web_tools.web_search" in text_dump


def test_timeline_renders_mid_round_followup_inside_active_round():
    ctx = RuntimeCtx(turn_id="turn_follow", started_at="2026-04-16T18:04:21Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text="[TURN turn_follow]"),
        tl._block(
            type="user.prompt",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_follow.user.prompt",
            text="Find top news and put it into pptx.",
        ),
        {
            **tl._block(
                type="react.round.start",
                author="react",
                turn_id=ctx.turn_id,
                ts="2026-04-16T18:04:36Z",
                path="ar:turn_follow.react.round.start.tc_fail_1",
                text="thinking",
                meta={"tool_call_id": "tc_fail_1", "iteration": 0},
            ),
            "call_id": "tc_fail_1",
        },
        tl._block(
            type="user.followup",
            author="user",
            turn_id=ctx.turn_id,
            ts="2026-04-16T18:04:36Z",
            path="ar:turn_follow.external.followup.evt_1",
            text="sorry, pdf",
            meta={"target_turn_id": "turn_follow", "event_kind": "followup"},
        ),
        tl._block(
            type="react.notes",
            author="react",
            turn_id=ctx.turn_id,
            ts="2026-04-16T18:04:37Z",
            path="ar:turn_follow.react.notes.tc_fail_1",
            text="Wrong round. The agent wrote invalid content into the action channel, so this round executed no action.",
            meta={
                "tool_id": "__protocol_violation__",
                "tool_call_id": "tc_fail_1",
                "action": "call_tool",
                "iteration": 0,
            },
        ),
        tl._block(
            type="react.notice",
            author="react",
            turn_id=ctx.turn_id,
            ts="2026-04-16T18:04:37Z",
            mime="application/json",
            path="tc:turn_follow.tc_fail_1.notice",
            text=json.dumps(
                {
                    "code": "protocol_violation.ReactDecisionOutV2_schema_error",
                    "message": "Bad Protocol. The agent output in <channel:ReactDecisionOutV2> could not be parsed, so no action was executed for this round.",
                },
                ensure_ascii=False,
            ),
            meta={"tool_call_id": "tc_fail_1"},
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_dump = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "┌──────── ROUND 1 ────────┐" in text_dump
    assert "[AI Agent thinking...]" in text_dump
    assert "  [FOLLOWUP DURING TURN]" in text_dump
    assert "  sorry, pdf" in text_dump
    assert text_dump.index("[AI Agent thinking...]") < text_dump.index("[FOLLOWUP DURING TURN]") < text_dump.index("[AI Agent say]: Wrong round.")


def test_read_result_says_requested_path_is_already_visible():
    ctx = RuntimeCtx(turn_id="turn_read", started_at="2026-05-05T20:40:29Z")
    tl = Timeline(runtime=ctx)

    tl.blocks.extend([
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts="2026-05-05T20:40:55Z",
            mime="application/json",
            path="tc:turn_read.tc_read.call",
            text=json.dumps(
                {
                    "tool_id": "react.read",
                    "tool_call_id": "tc_read",
                    "params": {"paths": ["sk:productivity.email"]},
                },
                ensure_ascii=False,
            ),
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts="2026-05-05T20:40:56Z",
            mime="application/json",
            path="tc:turn_read.tc_read.result",
            text=json.dumps(
                {
                    "paths": [],
                    "total_tokens": 0,
                    "exists_in_visible_context": ["sk:productivity.email"],
                    "visible_context_refs": {
                        "sk:productivity.email": {
                            "path": "sk:productivity.email",
                            "tool_call_id": "tc_prev_read",
                            "tool_result_path": "tc:turn_prev.tc_prev_read.result",
                            "visible_at": "[TOOL RESULT tc_prev_read].artifact react.read",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            meta={"tool_call_id": "tc_read", "tool_id": "react.read"},
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_dump = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[TOOL RESULT tc_read].result react.read" in text_dump
    assert "Already visible in current context; no new content was loaded for these paths:" in text_dump
    assert (
        "- sk:productivity.email (already visible at [TOOL RESULT tc_prev_read].artifact react.read; "
        "see tc:turn_prev.tc_prev_read.result)"
    ) in text_dump


def test_render_does_not_print_cache_trace_by_default(capsys):
    ctx = RuntimeCtx(turn_id="turn_quiet", started_at="2026-05-05T20:40:29Z")
    tl = Timeline(runtime=ctx)
    tl.blocks.append(
        tl._block(
            type="user.prompt",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="ar:turn_quiet.user.prompt",
            text="hello",
        )
    )

    _run(tl.render(cache_last=True))
    captured = capsys.readouterr()

    assert "[cache_trace:" not in captured.out


def test_hidden_old_blocks_render_as_minimal_retrieval_refs():
    ctx = RuntimeCtx(turn_id="turn_current", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)
    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id="turn_old", ts="2026-02-08T00:00:00Z", text="[TURN turn_old]"),
        tl._block(
            type="user.prompt",
            author="user",
            turn_id="turn_old",
            ts="2026-02-08T00:00:01Z",
            path="ar:turn_old.user.prompt",
            text="long old user request",
        ),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id="turn_old",
            ts="2026-02-08T00:00:02Z",
            mime="application/json",
            path="tc:turn_old.tc_abc.call",
            text='{"tool_id":"email.process_user_emails","tool_call_id":"tc_abc","params":{"mailbox":"INBOX"}}',
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id="turn_old",
            ts="2026-02-08T00:00:03Z",
            mime="application/json",
            path="tc:turn_old.tc_abc.result",
            text='{"tool_id":"email.process_user_emails","tool_call_id":"tc_abc","result":{"messages":[]}}',
        ),
        tl._block(
            type="assistant.completion",
            author="assistant",
            turn_id="turn_old",
            ts="2026-02-08T00:00:04Z",
            path="ar:turn_old.assistant.completion",
            text="old answer",
        ),
        tl._block(
            type="conv.working.summary",
            author="assistant",
            turn_id="turn_old",
            ts="2026-02-08T00:00:04Z",
            path="ws:turn_old.conv.working.summary",
            text=(
                "Goal: Fetch old email.\n"
                "Outcome: Completed from cached data.\n"
                "Key facts:\n"
                "- Gmail scan returned no messages.\n"
                "Refs:\n"
                "- user: ar:turn_old.user.prompt\n"
                "- result: tc:turn_old.tc_abc.result"
            ),
            meta={"kind": "working_summary"},
        ),
        tl._block(
            type="system.message",
            author="system",
            turn_id="turn_old",
            ts="2026-02-08T00:00:05Z",
            path="ar:turn_old.system.message.cache_pruned",
            text="Context was pruned because the session TTL was exceeded.",
            meta={"kind": "cache_ttl_pruned"},
        ),
    ])
    for path in [
        "ar:turn_old.user.prompt",
        "tc:turn_old.tc_abc.call",
        "tc:turn_old.tc_abc.result",
        "ar:turn_old.assistant.completion",
        "ar:turn_old.system.message.cache_pruned",
    ]:
        tl.hide_paths([path], "[TRUNCATED] verbose replacement")

    rendered = _run(tl.render(cache_last=False))
    text = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[WORKING SUMMARY]" in text
    assert "[path: ws:turn_old.conv.working.summary]" in text
    assert "Goal: Fetch old email." in text
    assert "Outcome: Completed from cached data." in text
    assert "- result: tc:turn_old.tc_abc.result" in text
    assert "[pruned user message]" not in text
    assert "[pruned tool call]" not in text
    assert "[pruned tool result]" not in text
    assert "[ASSISTANT MESSAGE]" not in text
    assert "read=react.read" not in text
    assert "ROUND 1" not in text
    assert "Params:" not in text
    assert "cache_pruned" not in text
