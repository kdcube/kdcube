# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.memsearch import handle_react_memsearch


class FakeBrowser:
    def __init__(self, runtime_ctx: RuntimeCtx):
        self.runtime_ctx = runtime_ctx
        self.timeline = Timeline(runtime=runtime_ctx, svc=None)
        self._turn_logs = {}

    def contribute(self, blocks, persist=True):
        self.timeline.blocks.extend(blocks or [])

    def contribute_notice(self, *, code, message, extra=None, call_id=None, meta=None):
        block = {
            "type": "react.notice",
            "call_id": call_id,
            "text": f"{code}:{message}",
            "meta": extra or {},
            "turn_id": self.runtime_ctx.turn_id or "",
        }
        if meta:
            block["meta"] = {**block.get("meta", {}), **meta}
        self.contribute([block])

    async def get_turn_log(self, turn_id: str):
        return self._turn_logs.get(turn_id, {})


def _latest_summary_payload(ctx: FakeBrowser) -> dict:
    blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert blocks
    return json.loads(blocks[-1]["text"])


@pytest.mark.asyncio
async def test_memsearch_attachment_target_includes_external_followup_attachments(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_current",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
        user_id="user_1",
    )
    ctx = FakeBrowser(runtime)

    async def _search(**kwargs):
        return "turn_prev", [{
            "turn_id": "turn_prev",
            "score": 0.91,
            "sim": 0.88,
            "rec": 0.97,
            "matched_via_role": "user",
            "source_query": "brief",
            "ts": "2026-04-26T10:00:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "user.followup",
                "turn_id": "turn_prev",
                "ts": "2026-04-26T10:00:00Z",
                "path": "ar:turn_prev.external.followup.msg_1",
                "text": "See the attached brief",
                "meta": {"message_id": "msg_1", "event_kind": "followup", "sequence": 1},
            },
            {
                "type": "user.attachment.meta",
                "turn_id": "turn_prev",
                "ts": "2026-04-26T10:00:00Z",
                "path": "fi:turn_prev.external.followup.attachments/msg_1/brief.txt",
                "text": "{\"kind\":\"file\"}",
                "meta": {
                    "filename": "brief.txt",
                    "mime": "text/plain",
                    "hosted_uri": "s3://bucket/brief.txt",
                    "continuation_kind": "followup",
                    "event_kind": "followup",
                    "message_id": "msg_1",
                    "sequence": 1,
                },
            },
            {
                "type": "user.attachment.text",
                "turn_id": "turn_prev",
                "ts": "2026-04-26T10:00:00Z",
                "path": "fi:turn_prev.external.followup.attachments/msg_1/brief.txt",
                "text": "Attachment content from followup",
                "meta": {
                    "filename": "brief.txt",
                    "mime": "text/plain",
                    "message_id": "msg_1",
                },
            },
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "brief",
                    "targets": ["attachment"],
                    "top_k": 3,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms1")

    hits = out["last_tool_result"]
    assert len(hits) == 1
    snippets = hits[0]["snippets"]
    assert len(snippets) == 1
    assert snippets[0]["role"] == "attachment"
    assert snippets[0]["path"] == "fi:turn_prev.external.followup.attachments/msg_1/brief.txt"
    assert snippets[0]["text"] == "Attachment content from followup"
    assert snippets[0]["meta"]["message_id"] == "msg_1"

    summary = _latest_summary_payload(ctx)
    assert summary["hits"][0]["snippets"] == [{
        "path": "fi:turn_prev.external.followup.attachments/msg_1/brief.txt",
        "role": "attachment",
        "ts": "2026-04-26T10:00:00Z",
    }]


@pytest.mark.asyncio
async def test_memsearch_summary_target_includes_working_summary_blocks(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_current",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
        user_id="user_1",
    )
    ctx = FakeBrowser(runtime)
    captured_search = {}

    async def _search(**kwargs):
        captured_search.update(kwargs)
        return "turn_prev", [{
            "turn_id": "turn_prev",
            "score": 0.9,
            "sim": 0.89,
            "rec": 0.92,
            "matched_via_role": "assistant",
            "source_query": "Anthropic invoices zip",
            "ts": "2026-05-05T19:37:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "author": "assistant",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T19:37:00Z",
                "path": "ws:turn_prev.conv.working.summary.attempt.1",
                "text": "Goal: Create ZIP with all Anthropic April 2026 invoice PDFs.\nOutcome: Failed at hosted artifact boundary.",
                "meta": {
                    "kind": "working_summary",
                    "summary_scope": "completion_attempt",
                    "assistant_completion_attempt_index": 1,
                },
            }
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "Anthropic invoices zip",
                    "targets": ["summary"],
                    "top_k": 3,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms2")

    assert captured_search["targets"] == [{"where": "assistant", "query": "Anthropic invoices zip"}]
    hits = out["last_tool_result"]
    assert len(hits) == 1
    snippets = hits[0]["snippets"]
    assert len(snippets) == 1
    assert snippets[0]["role"] == "summary"
    assert snippets[0]["path"] == "ws:turn_prev.conv.working.summary.attempt.1"
    assert "Anthropic April 2026" in snippets[0]["text"]

    summary = _latest_summary_payload(ctx)
    assert summary["hits"][0]["snippets"] == [{
        "path": "ws:turn_prev.conv.working.summary.attempt.1",
        "role": "summary",
        "ts": "2026-05-05T19:37:00Z",
    }]
