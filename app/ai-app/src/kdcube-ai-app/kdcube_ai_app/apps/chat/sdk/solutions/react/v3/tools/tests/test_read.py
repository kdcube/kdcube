# SPDX-License-Identifier: MIT

import pytest
import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.read import handle_react_read
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser


@pytest.mark.asyncio
async def test_read_missing_paths_notice(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:turn_read.files/missing.md"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r1")
    assert any(b.get("type") == "react.notice" for b in ctx.timeline.blocks)


@pytest.mark.asyncio
async def test_read_returns_latest_version(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    path = "fi:turn_read.files/report.md"
    # older version
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "application/json",
        "text": '{"artifact_path":"fi:turn_read.files/report.md","physical_path":"turn_read/files/report.md"}',
        "turn_id": "turn_read",
    })
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "text/markdown",
        "path": path,
        "text": "old",
        "turn_id": "turn_read",
    })
    # newer version
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "application/json",
        "text": '{"artifact_path":"fi:turn_read.files/report.md","physical_path":"turn_read/files/report.md"}',
        "turn_id": "turn_read",
    })
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "text/markdown",
        "path": path,
        "text": "new",
        "turn_id": "turn_read",
    })
    state = {"last_decision": {"tool_call": {"params": {"paths": [path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r2")
    assert any(b.get("text") == "new" for b in ctx.timeline.blocks if b.get("type") == "react.tool.result")


@pytest.mark.asyncio
async def test_read_supports_outdir_relative_fi_paths(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "docker.err.log").write_text("boom", encoding="utf-8")

    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:logs/docker.err.log"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r3")

    assert any(
        b.get("path") == "fi:logs/docker.err.log" and b.get("text") == "boom"
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )


@pytest.mark.asyncio
async def test_read_tc_result_prefers_inline_payload_over_meta(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    source_path = "tc:turn_src.pref1.result"

    ctx.timeline.blocks.extend([
        {
            "type": "react.tool.result",
            "mime": "application/json",
            "path": source_path,
            "text": (
                '{"artifact_path":"tc:turn_src.pref1.result","mime":"application/json",'
                '"kind":"file","visibility":"internal","tool_call_id":"pref1"}'
            ),
            "turn_id": "turn_src",
            "call_id": "pref1",
            "meta": {"tool_call_id": "pref1"},
        },
        {
            "type": "react.tool.result",
            "mime": "application/json",
            "path": source_path,
            "text": (
                '{"ok": true, "current": {"location": {"value": "Wuppertal"}}, '
                '"summary": "Current preferences:\\n- location: Wuppertal"}'
            ),
            "turn_id": "turn_src",
            "call_id": "pref1",
            "meta": {"tool_call_id": "pref1"},
        },
    ])

    state = {"last_decision": {"tool_call": {"params": {"paths": [source_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r4")

    assert any(
        b.get("call_id") == "r4"
        and b.get("path") == source_path
        and '"location": {"value": "Wuppertal"}' in (b.get("text") or "")
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )


@pytest.mark.asyncio
async def test_read_large_tc_result_returns_recovery_marker_not_payload(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), max_tokens=80_000)
    ctx = FakeBrowser(runtime)
    source_path = "tc:turn_src.tc_big.result"
    large_text = json.dumps({"ok": True, "messages": ["email body " * 2000 for _ in range(80)]})

    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "application/json",
        "path": source_path,
        "text": large_text,
        "turn_id": "turn_src",
        "call_id": "tc_big",
        "meta": {"tool_call_id": "tc_big", "tool_id": "email.process_user_emails"},
    })

    state = {"last_decision": {"tool_call": {"params": {"paths": [source_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_large")

    read_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("call_id") == "r_large"
    ]
    assert not any(b.get("text") == large_text for b in read_blocks)
    marker = next(b for b in read_blocks if b.get("path") == source_path)
    assert "LARGE READ NOT MATERIALIZED" in marker["text"]
    assert "ctx_tools.fetch_ctx" in marker["text"]

    status = next(
        json.loads(b["text"])
        for b in read_blocks
        if b.get("path") == "tc:turn_read.r_large.result" and b.get("mime") == "application/json"
    )
    assert status["paths"][0]["status"] == "too_large_for_visible_context"
    assert status["large_paths"][0]["path"] == source_path


@pytest.mark.asyncio
async def test_read_turn_index_reconstructs_semantic_inventory_from_turn_log(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_current", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "author": "assistant",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T19:37:00Z",
                "path": "ws:turn_prev.conv.working.summary.attempt.1",
                "text": "Goal: Create ZIP with Anthropic April invoices.\nOutcome: ZIP failed at hosted artifact boundary.",
                "mime": "text/markdown",
                "meta": {"kind": "working_summary", "summary_scope": "completion_attempt"},
            },
            {
                "type": "user.followup",
                "author": "user",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T19:27:38Z",
                "path": "ar:turn_prev.external.followup.msg_1",
                "text": "Retry the Anthropic April invoice ZIP workflow.",
                "meta": {"event_kind": "followup", "message_id": "msg_1", "sequence": 12, "source": "telegram"},
            },
            {
                "type": "react.tool.call",
                "turn_id": "turn_prev",
                "call_id": "tc_scan",
                "path": "tc:turn_prev.tc_scan.call",
                "mime": "application/json",
                "text": json.dumps({"tool_id": "email.process_user_emails", "params": {"query": "Anthropic April invoices"}}),
            },
            {
                "type": "react.tool.result",
                "turn_id": "turn_prev",
                "call_id": "tc_scan",
                "path": "tc:turn_prev.tc_scan.result",
                "mime": "application/json",
                "text": json.dumps({"ok": True, "message": "found 10 Anthropic April emails and current attachment IDs"}),
            },
            {
                "type": "react.tool.result",
                "turn_id": "turn_prev",
                "call_id": "tc_mat",
                "path": "tc:turn_prev.tc_mat.result",
                "mime": "application/json",
                "text": json.dumps({
                    "artifact_path": "fi:turn_prev.outputs/email-attachments/Invoice_1.pdf",
                    "mime": "application/pdf",
                    "kind": "file",
                    "description": "Anthropic invoice PDF materialized from Gmail",
                }),
                "meta": {"tool_call_id": "tc_mat", "tool_id": "email.materialize_email_attachments"},
            },
            {
                "type": "react.tool.result",
                "turn_id": "turn_prev",
                "call_id": "tc_mat",
                "path": "fi:turn_prev.outputs/email-attachments/Invoice_1.pdf",
                "mime": "application/pdf",
                "meta": {"tool_call_id": "tc_mat", "tool_id": "email.materialize_email_attachments"},
            },
            {
                "type": "assistant.completion",
                "author": "assistant",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T19:37:19Z",
                "path": "ar:turn_prev.assistant.completion",
                "text": "All 20 Anthropic invoice PDFs were materialized, but ZIP failed. [[S:1]]",
                "meta": {"sources_used": [1]},
            },
        ],
        "sources_pool": [
            {"sid": 1, "title": "Anthropic invoice source", "url": "https://example.test/invoice", "text": "invoice source row"}
        ],
    }

    index_path = "ar:turn_prev.react.turn.index"
    state = {"last_decision": {"tool_call": {"params": {"paths": [index_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_index")

    index_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("path") == index_path
    )
    text = index_block.get("text") or ""
    assert "[TURN INDEX]" in text
    assert "latest working summary: ws:turn_prev.conv.working.summary" in text
    assert "user followup: ar:turn_prev.external.followup.msg_1" in text
    assert "Retry the Anthropic April invoice ZIP workflow" in text
    assert "tool: email.process_user_emails" in text
    assert "found 10 Anthropic April emails" in text
    assert "fi:turn_prev.outputs/email-attachments/Invoice_1.pdf" in text
    assert "Anthropic invoice PDF materialized from Gmail" in text
    assert "source: so:sources_pool[1]" in text

    status_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("path") == "tc:turn_current.r_index.result"
    )
    status = json.loads(status_block["text"])
    assert status["paths"][0]["path"] == index_path
    assert status["paths"][0]["source_turn_id"] == "turn_prev"
