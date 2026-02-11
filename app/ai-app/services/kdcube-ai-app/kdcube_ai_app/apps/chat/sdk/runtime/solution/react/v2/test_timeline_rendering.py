# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import Timeline
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx


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
            path="tc:turn_123.tool_calls.abc.in.json",
            text='{"tool_id":"web_tools.web_search","tool_call_id":"abc"}',
        ),
        # react.write (display + file)
        tl._block(
            type="react.tool.call",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.tool_calls.write1.in.json",
            text='{"tool_id":"react.write","tool_call_id":"write1","params":{"path":"turn_123/files/draft.md","kind":"file"}}',
        ),
        tl._block(
            type="react.tool.result",
            author="agent",
            turn_id=ctx.turn_id,
            ts=ctx.started_at,
            mime="application/json",
            path="tc:turn_123.tool_calls.write1.out.json",
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
            path="tc:turn_123.tool_calls.exec1.in.json",
            text='{"tool_id":"exec_tools.execute_code_python","tool_call_id":"exec1","params":{"code":"open(\'turn_123/files/draft.md\').read()"}}',
        ),
    ])

    rendered = _run(tl.render(cache_last=True))
    text_blocks = [b for b in rendered if b.get("type") == "text"]
    doc_blocks = [b for b in rendered if b.get("type") == "document"]
    assert all(b.get("type") in {"text", "document", "image"} for b in rendered)

    assert any("[TURN turn_123]" in b.get("text", "") for b in text_blocks)
    assert any("[path: ar:turn_123.user.prompt]" in b.get("text", "") for b in text_blocks)
    assert any("turn_123/attachments/menu.pdf" in b.get("text", "") for b in text_blocks)
    assert any("fi:turn_123.user.attachments/notes.txt" in b.get("text", "") for b in text_blocks)
    assert any("hello notes" in b.get("text", "") for b in text_blocks)
    assert any("fi:turn_123.files/draft.md" in b.get("text", "") for b in text_blocks)
    assert doc_blocks, "Expected a document block for the PDF attachment"
