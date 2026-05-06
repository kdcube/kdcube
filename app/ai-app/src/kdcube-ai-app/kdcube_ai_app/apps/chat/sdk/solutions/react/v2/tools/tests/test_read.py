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
async def test_read_duplicate_visible_content_returns_visible_ref(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "note.md").write_text("already visible", encoding="utf-8")

    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:logs/note.md"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_visible_1")
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_visible_2")

    summaries = []
    for b in ctx.timeline.blocks:
        if (
            b.get("type") != "react.tool.result"
            or b.get("path") != "tc:turn_read.r_visible_2.result"
            or b.get("mime") != "application/json"
        ):
            continue
        payload = json.loads(b.get("text") or "{}")
        if "paths" in payload:
            summaries.append(payload)

    assert summaries
    assert summaries[-1]["exists_in_visible_context"] == ["fi:logs/note.md"]
    ref = summaries[-1]["visible_context_refs"]["fi:logs/note.md"]
    assert ref["path"] == "fi:logs/note.md"
    assert ref["tool_result_path"] == "tc:turn_read.r_visible_1.result"


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
