# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import base64
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


def test_multimodal_token_estimate_counts_model_message_data_blocks():
    ctx = RuntimeCtx(turn_id="turn_mm", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)

    image_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    pdf_b64 = base64.b64encode(
        b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF"
    ).decode("ascii")

    tokens = tl._estimate_model_message_tokens([
        {"type": "image", "data": image_b64, "media_type": "image/png"},
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_b64,
            },
        },
    ])

    assert tokens >= 4101


def test_multimodal_token_estimate_counts_visible_timeline_blocks_only():
    ctx = RuntimeCtx(turn_id="turn_mm", started_at="2026-02-09T00:00:00Z")
    tl = Timeline(runtime=ctx)
    pdf_b64 = base64.b64encode(
        b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF"
    ).decode("ascii")

    visible = tl._estimate_block_tokens(
        tl._block(
            type="user.attachment",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/pdf",
            path="fi:turn_mm.user.attachments/doc.pdf",
            text="[USER ATTACHMENT] doc.pdf",
            base64=pdf_b64,
        )
    )
    hidden = tl._estimate_block_tokens(
        tl._block(
            type="user.attachment",
            author="user",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/pdf",
            path="fi:turn_mm.user.attachments/doc.pdf",
            base64=pdf_b64,
            meta={"hidden": True, "replacement_text": "[HIDDEN] doc.pdf"},
        )
    )

    assert visible >= 4100
    assert hidden < visible


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


def test_large_tool_result_is_rendered_as_preview_with_shape():
    ctx = RuntimeCtx(
        turn_id="turn_large",
        started_at="2026-05-08T00:00:00Z",
        tool_result_preview_max_text_symbols=600,
    )
    tl = Timeline(runtime=ctx)
    payload = {
        "ok": True,
        "new_count": 50,
        "messages": [
            {
                "message_id": "m1",
                "from": "Sender <sender@example.com>",
                "subject": "Large newsletter",
                "date": "Fri, 08 May 2026 00:00:00 +0000",
                "snippet": "hello",
                "body_excerpt": "A" * 5000,
            },
            {
                "message_id": "m2",
                "from": "Other <other@example.com>",
                "subject": "Second",
                "date": "Fri, 08 May 2026 00:01:00 +0000",
                "snippet": "hello",
                "body_excerpt": "B" * 5000,
            },
        ],
    }
    raw_text = json.dumps(payload, ensure_ascii=False)

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text=""),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_large.tc_email.call",
            text=json.dumps({
                "tool_id": "email.process_user_emails",
                "tool_call_id": "tc_email",
                "params": {"account": "lena@nestlogic.com", "limit": 50},
            }),
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_large.tc_email.result",
            text=raw_text,
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    joined = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert tl.blocks[-1]["text"] == raw_text
    assert "[TOOL RESULT PREVIEW TRUNCATED]" in joined
    assert "mime: application/json" in joined
    assert "payload:" in joined
    assert "full_text_chars:" in joined
    assert "shape_depth: 4" in joined
    assert '"messages":' in joined
    assert '"type": "list[2]"' in joined
    assert "ctx_tools.fetch_ctx(path=\"tc:turn_large.tc_email.result\")" in joined
    assert len(joined) < len(raw_text)
    assert "A" * 1000 not in joined


def test_large_text_artifact_is_rendered_as_bounded_preview():
    ctx = RuntimeCtx(
        turn_id="turn_artifact",
        started_at="2026-05-08T00:00:00Z",
        tool_result_preview_max_text_symbols=140,
    )
    tl = Timeline(runtime=ctx)
    artifact_text = "\n".join(
        f"line {idx}: " + ("X" * 40)
        for idx in range(1, 30)
    )

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text=""),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_artifact.tc_write.call",
            text=json.dumps({
                "tool_id": "react.write",
                "tool_call_id": "tc_write",
                "params": {"path": "outputs/report.md", "channel": "canvas", "kind": "display"},
            }),
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="text/markdown",
            path="fi:turn_artifact.outputs/report.md",
            text=artifact_text,
            meta={
                "tool_call_id": "tc_write",
                "text_symbols": len(artifact_text),
                "size_bytes": len(artifact_text.encode("utf-8")),
                "line_count": 29,
                "physical_path": "turn_artifact/outputs/report.md",
            },
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    joined = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert tl.blocks[-1]["text"] == artifact_text
    assert "[ARTIFACT PREVIEW TRUNCATED]" in joined
    assert "logical_path: fi:turn_artifact.outputs/report.md" in joined
    assert "physical_path: turn_artifact/outputs/report.md" in joined
    assert "preview_lines: [1-" in joined
    assert "line_numbers: true" in joined
    assert "Use react.rg on the file to find relevant regions before editing." in joined
    assert "line 29:" not in joined
    assert len(joined) < len(artifact_text)


def test_large_internal_note_and_code_are_rendered_as_bounded_previews():
    ctx = RuntimeCtx(
        turn_id="turn_large_internal",
        started_at="2026-05-08T00:00:00Z",
        tool_result_preview_max_text_symbols=120,
    )
    tl = Timeline(runtime=ctx)
    html_text = "<!doctype html>\n" + "\n".join(f"<button>Button {idx}</button>" for idx in range(80))
    code_text = "html = '''\n" + html_text + "\n'''\nprint(len(html))"

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text=""),
        tl._block(
            type="react.note",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_large_internal.outputs/app.html",
            text=html_text,
        ),
        tl._block(
            type="react.tool.code",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            path="fi:turn_large_internal.code.tc_exec",
            text=code_text,
            meta={"tool_call_id": "tc_exec"},
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    joined = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert tl.blocks[1]["text"] == html_text
    assert tl.blocks[2]["text"] == code_text
    assert "[INTERNAL NOTE PREVIEW TRUNCATED]" in joined
    assert "[CODE PREVIEW TRUNCATED]" in joined
    assert "Use react.rg on the file to find relevant regions before editing." in joined
    assert "Button 79" not in joined
    assert len(joined) < len(html_text) + len(code_text)


def test_source_result_render_includes_items_stats():
    ctx = RuntimeCtx(
        turn_id="turn_sources",
        started_at="2026-05-08T00:00:00Z",
        tool_result_preview_max_text_symbols=200,
    )
    tl = Timeline(runtime=ctx)
    payload = [
        {
            "sid": 1,
            "url": "https://example.com/security",
            "title": "Security Article",
            "text": "short preview",
            "content": "full source content " * 80,
        }
    ]

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text=""),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_sources.tc_search.call",
            text=json.dumps({
                "tool_id": "web_tools.web_search",
                "tool_call_id": "tc_search",
                "params": {"queries": ["security"]},
            }),
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="so:sources_pool[1]",
            text=json.dumps(payload, ensure_ascii=False),
            meta={
                "tool_call_id": "tc_search",
                "items_stats": {
                    "kind": "sources_pool",
                    "items_count": 1,
                    "content_rows": 1,
                    "total_content_symbols": len(payload[0]["content"]),
                },
            },
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    joined = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "items_stats:" in joined
    assert '"items_count": 1' in joined
    assert '"content_rows": 1' in joined
    assert "[TOOL RESULT PREVIEW TRUNCATED]" not in joined
    assert "full source content full source content" in joined


def test_react_read_output_is_not_capped_again_by_tool_result_preview():
    ctx = RuntimeCtx(
        turn_id="turn_read",
        started_at="2026-05-08T00:00:00Z",
        tool_result_preview_max_text_symbols=120,
    )
    tl = Timeline(runtime=ctx)
    read_preview = "\n".join([
        "A" * 300,
        "",
        "[READ PREVIEW TRUNCATED]",
        "path: tc:turn_src.tc_big.result",
        "visible_text_symbols: 300",
    ])

    tl.blocks.extend([
        tl._block(type="turn.header", author="system", turn_id=ctx.turn_id, ts=ctx.started_at, text=""),
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_read.r_big.call",
            text=json.dumps({
                "tool_id": "react.read",
                "tool_call_id": "r_big",
                "params": {"paths": ["tc:turn_src.tc_big.result"]},
            }),
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="text/plain",
            path="tc:turn_src.tc_big.result",
            text=read_preview,
            meta={"tool_call_id": "r_big", "tool_id": "react.read", "read_preview_truncated": True},
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    joined = "\n".join(b.get("text", "") for b in rendered if b.get("type") == "text")

    assert "[READ PREVIEW TRUNCATED]" in joined
    assert "[TOOL RESULT PREVIEW TRUNCATED]" not in joined
    assert "A" * 200 in joined


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
