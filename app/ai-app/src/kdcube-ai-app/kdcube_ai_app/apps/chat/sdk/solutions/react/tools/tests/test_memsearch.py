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

    async def get_turn_log(self, turn_id: str, conversation_id: str | None = None):
        return self._turn_logs.get((conversation_id or "", turn_id), self._turn_logs.get(turn_id, {}))

    async def search_turn_catalog(self, **kwargs):
        raise AssertionError(f"unexpected search_turn_catalog call: {kwargs!r}")


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
    assert captured_search["scope"] == "conversation"
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


@pytest.mark.asyncio
async def test_memsearch_notes_target_includes_internal_note_blocks(tmp_path):
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
            "matched_via_role": "artifact",
            "source_query": "renderer refs",
            "ts": "2026-05-05T19:37:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.note",
                "author": "react",
                "turn_id": "turn_prev",
                "ts": "2026-05-05T19:37:00Z",
                "path": "fi:turn_prev.outputs/internal_notes/rendering.md",
                "text": "[K] fi:turn_prev.outputs/report.html - source for rendered PDF\n[D] Renderer refs point at text source artifacts.",
                "meta": {"channel": "internal", "note_tags": ["K", "D"]},
            }
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "renderer refs",
                    "targets": ["notes"],
                    "top_k": 3,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms_notes")

    assert captured_search["targets"] == [{"where": "notes", "query": "renderer refs"}]
    hits = out["last_tool_result"]
    assert len(hits) == 1
    snippets = hits[0]["snippets"]
    assert len(snippets) == 1
    assert snippets[0]["role"] == "notes"
    assert snippets[0]["path"] == "fi:turn_prev.outputs/internal_notes/rendering.md"
    assert "[K]" in snippets[0]["text"]
    assert "[D]" in snippets[0]["text"]

    summary = _latest_summary_payload(ctx)
    assert summary["hits"][0]["snippets"] == [{
        "path": "fi:turn_prev.outputs/internal_notes/rendering.md",
        "role": "notes",
        "ts": "2026-05-05T19:37:00Z",
    }]


@pytest.mark.asyncio
async def test_memsearch_ordinal_mode_uses_turn_catalog_without_query(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_current",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
        user_id="user_1",
    )
    ctx = FakeBrowser(runtime)
    captured_catalog = {}

    async def _search_turn_catalog(**kwargs):
        captured_catalog.update(kwargs)
        return [{
            "turn_id": "turn_second",
            "turn_index_path": "ar:turn_second.react.turn.index",
            "working_summary_path": "ws:turn_second.conv.working.summary",
            "user_path": "ar:turn_second.user.prompt",
            "assistant_path": "ar:turn_second.assistant.completion",
            "ordinal": 2,
            "total_turns": 8,
            "started_at": "2026-05-03T01:17:11Z",
            "ended_at": "2026-05-03T01:18:30Z",
            "working_summary_text": "Goal: Find two exciting recent medicine stories. Outcome: Answered with sources.",
            "working_summary_ts": "2026-05-03T01:18:30Z",
            "first_user_text": "le'ts then check the 2 most exciting news in medicine for last 2 weeks",
            "first_user_ts": "2026-05-03T01:17:11Z",
            "about": "Goal: Find two exciting recent medicine stories.",
        }]

    ctx.search_turn_catalog = _search_turn_catalog

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "",
                    "targets": ["summary", "user"],
                    "mode": "ordinal",
                    "ordinal": 2,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms3")

    assert captured_catalog["ordinal"] == 2
    assert captured_catalog["scope"] == "conversation"
    assert captured_catalog["days"] == 3650
    hits = out["last_tool_result"]
    assert len(hits) == 1
    assert hits[0]["turn_id"] == "turn_second"
    assert hits[0]["ordinal"] == 2
    assert hits[0]["total_turns"] == 8
    assert hits[0]["turn_index_path"] == "ar:turn_second.react.turn.index"
    assert [sn["role"] for sn in hits[0]["snippets"]] == ["summary", "user"]

    summary = _latest_summary_payload(ctx)
    assert summary["mode"] == "ordinal"
    assert summary["hits"][0]["ordinal"] == 2
    assert summary["hits"][0]["snippets"] == [
        {
            "path": "ws:turn_second.conv.working.summary",
            "role": "summary",
            "ts": "2026-05-03T01:18:30Z",
        },
        {
            "path": "ar:turn_second.user.prompt",
            "role": "user",
            "ts": "2026-05-03T01:17:11Z",
        },
    ]


@pytest.mark.asyncio
async def test_memsearch_semantic_with_temporal_bounds_passes_timestamp_filters(tmp_path):
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
            "score": 0.8,
            "sim": 0.75,
            "rec": 0.9,
            "matched_via_role": "assistant",
            "source_query": "invoice",
            "ts": "2026-03-12T10:00:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "author": "assistant",
                "turn_id": "turn_prev",
                "ts": "2026-03-12T10:00:00Z",
                "path": "ws:turn_prev.conv.working.summary.attempt.1",
                "text": "Goal: retrieve March invoices.",
                "meta": {},
            }
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "invoice",
                    "targets": ["summary"],
                    "from": "2026-03-01T00:00:00Z",
                    "to": "2026-04-01T00:00:00Z",
                    "top_k": 2,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms4")

    assert captured_search["days"] == 3650
    assert captured_search["timestamp_filters"] == [
        {"op": ">=", "value": "2026-03-01T00:00:00Z"},
        {"op": "<", "value": "2026-04-01T00:00:00Z"},
    ]
    assert out["last_tool_result"][0]["turn_id"] == "turn_prev"


@pytest.mark.asyncio
async def test_memsearch_semantic_user_scope_is_forwarded(tmp_path):
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
        return "turn_cross", [{
            "conversation_id": "conv_2",
            "turn_id": "turn_cross",
            "score": 0.8,
            "sim": 0.75,
            "rec": 0.9,
            "matched_via_role": "assistant",
            "source_query": "invoice",
            "ts": "2026-03-12T10:00:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs[("conv_2", "turn_cross")] = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "turn_id": "turn_cross",
                "ts": "2026-03-12T10:00:00Z",
                "path": "ws:turn_cross.conv.working.summary",
                "text": "Goal: retrieve March invoices.",
                "meta": {},
            }
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "invoice",
                    "targets": ["summary"],
                    "scope": "user",
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms_user")

    assert captured_search["scope"] == "user"
    assert out["last_tool_result"][0]["turn_id"] == "turn_cross"
    assert out["last_tool_result"][0]["conversation_id"] == "conv_2"
    assert out["last_tool_result"][0]["snippets"][0]["conversation_id"] == "conv_2"
    summary = _latest_summary_payload(ctx)
    assert summary["hits"][0]["conversation_id"] == "conv_2"
    assert summary["hits"][0]["snippets"][0]["conversation_id"] == "conv_2"


@pytest.mark.asyncio
async def test_memsearch_scopes_cross_conversation_fi_refs(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_current",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
        user_id="user_1",
    )
    ctx = FakeBrowser(runtime)

    async def _search(**kwargs):
        return "turn_cross", [{
            "conversation_id": "conv_2",
            "turn_id": "turn_cross",
            "score": 0.8,
            "sim": 0.75,
            "rec": 0.9,
            "matched_via_role": "user",
            "source_query": "wizard snapshot",
            "ts": "2026-03-12T10:00:00Z",
        }]

    ctx.search = _search
    ctx._turn_logs[("conv_2", "turn_cross")] = {
        "blocks": [
            {
                "type": "react.note",
                "turn_id": "turn_cross",
                "ts": "2026-03-12T10:00:00Z",
                "path": "fi:turn_cross.snapshots/wizard/current.yaml",
                "text": "state: needs_triage\n",
                "meta": {},
            }
        ],
        "sources_pool": [],
    }

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "wizard snapshot",
                    "targets": ["notes"],
                    "scope": "user",
                }
            }
        }
    }

    await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms_cross")

    summary = _latest_summary_payload(ctx)
    assert summary["hits"][0]["snippets"] == [{
        "path": "fi:conv_conv_2.turn_cross.snapshots/wizard/current.yaml",
        "role": "notes",
        "ts": "2026-03-12T10:00:00Z",
    }]
    text_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "text/markdown"
    ]
    assert text_blocks[-1]["path"] == "fi:conv_conv_2.turn_cross.snapshots/wizard/current.yaml"
    assert "conversation_id" not in text_blocks[-1].get("meta", {})


@pytest.mark.asyncio
async def test_memsearch_timeline_mode_reports_ignored_generic_query(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_current",
        outdir=str(tmp_path / "out"),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
        user_id="user_1",
    )
    ctx = FakeBrowser(runtime)
    captured_catalog = {}

    async def _search_turn_catalog(**kwargs):
        captured_catalog.update(kwargs)
        return [{
            "turn_id": "turn_1",
            "turn_index_path": "ar:turn_1.react.turn.index",
            "working_summary_path": "ws:turn_1.conv.working.summary",
            "ordinal": 1,
            "total_turns": 1,
            "started_at": "2026-05-06T10:00:00Z",
            "working_summary_text": "Goal: discuss memory recovery. Outcome: designed memsearch timeline lookup.",
            "working_summary_ts": "2026-05-06T10:05:00Z",
        }]

    ctx.search_turn_catalog = _search_turn_catalog

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "query": "conversation topics discussed",
                    "targets": ["summary"],
                    "mode": "timeline",
                    "order": "asc",
                    "top_k": 10,
                }
            }
        }
    }

    out = await handle_react_memsearch(ctx_browser=ctx, state=state, tool_call_id="ms5")

    assert captured_catalog["order"] == "asc"
    assert out["last_tool_result"][0]["ignored_query"] == "conversation topics discussed"
    assert out["last_tool_result"][0]["source_query"] == ""
    summary = _latest_summary_payload(ctx)
    assert summary["mode"] == "timeline"
    assert "query ignored in timeline catalog mode" in summary["warnings"][0]
