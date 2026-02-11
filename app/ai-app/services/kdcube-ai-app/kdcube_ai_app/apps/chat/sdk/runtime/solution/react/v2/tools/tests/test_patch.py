# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.patch import handle_react_patch
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


@pytest.mark.asyncio
async def test_patch_copies_old_file_on_rewrite(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_new", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)

    old_path = tmp_path / "turn_old" / "files"
    old_path.mkdir(parents=True, exist_ok=True)
    (old_path / "a.txt").write_text("old", encoding="utf-8")

    state = {
        "last_decision": {"tool_call": {"params": {"path": "turn_old/files/a.txt", "patch": "new", "kind": "display"}}},
        "outdir": str(tmp_path),
    }

    await handle_react_patch(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="p1")

    new_path = tmp_path / "turn_new" / "files" / "a.txt"
    assert new_path.exists()
    assert new_path.read_text(encoding="utf-8") == "new"
    assert any(b.get("type") == "react.notice" for b in ctx.timeline.blocks)
