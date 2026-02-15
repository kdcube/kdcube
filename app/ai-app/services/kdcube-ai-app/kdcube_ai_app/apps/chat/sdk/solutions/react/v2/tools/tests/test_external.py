# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external import handle_external_tool
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


@pytest.mark.asyncio
async def test_external_exec_path_rewrite_notice(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"tool_id": "exec_tools.execute_code_python", "params": {"code": "open('files/x.txt').read()"}}},
             "outdir": str(tmp_path),
             "workdir": str(tmp_path)}

    async def _fake_execute_tool(**_kwargs):
        return {}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    await handle_external_tool(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="e1")
    assert any(b.get("type") == "react.notice" and "exec_path_rewritten" in (b.get("text") or "") for b in ctx.timeline.blocks)
