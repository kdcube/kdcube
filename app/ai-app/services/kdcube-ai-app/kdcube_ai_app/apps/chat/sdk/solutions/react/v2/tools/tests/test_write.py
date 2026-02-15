# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.write import handle_react_write
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


@pytest.mark.asyncio
async def test_write_rewrites_old_turn_path_and_notice(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_cur", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"path": "turn_old/files/draft.md", "content": "hi", "kind": "display"}}},
             "outdir": str(tmp_path)}

    await handle_react_write(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="c1")

    assert any(b.get("type") == "react.notice" for b in ctx.timeline.blocks)
    assert any("turn_cur/files/draft.md" in (b.get("text") or "") for b in ctx.timeline.blocks)


@pytest.mark.asyncio
async def test_write_rewrites_logical_path(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_cur", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"path": "fi:turn_old.files/draft.md", "content": "hi", "kind": "display"}}},
             "outdir": str(tmp_path)}

    await handle_react_write(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="c1")

    assert any(b.get("type") == "react.notice" for b in ctx.timeline.blocks)
    assert any("turn_cur/files/draft.md" in (b.get("text") or "") for b in ctx.timeline.blocks)


@pytest.mark.asyncio
async def test_write_internal_channel_creates_note_block(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_cur", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"path": "turn_cur/files/note.md", "channel": "internal", "content": "keep this", "kind": "file"}}},
             "outdir": str(tmp_path)}

    await handle_react_write(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="c2")

    note_blocks = [b for b in ctx.timeline.blocks if b.get("type") == "react.note"]
    assert note_blocks, "internal channel should create react.note block"
    assert any((b.get("meta") or {}).get("channel") == "internal" for b in note_blocks)
    assert any("\"visibility\": \"internal\"" in (b.get("text") or "") for b in ctx.timeline.blocks)
