# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.solution_workspace import rehost_files_from_timeline
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.tests.helpers import FakeBrowser


@pytest.mark.asyncio
async def test_rehost_files_from_timeline_base64(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_ctx", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/old.txt","physical_path":"turn_prev/files/old.txt"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/plain",
                "path": "fi:turn_prev.files/old.txt",
                "text": "hello",
                "turn_id": "turn_prev",
            },
        ]
    }
    class _Settings:
        STORAGE_PATH = str(tmp_path)
    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()
    res = await rehost_files_from_timeline(ctx_browser=ctx, paths=["turn_prev/files/old.txt"], outdir=tmp_path)
    assert "turn_prev/files/old.txt" in res.get("rehosted", [])
    assert (tmp_path / "turn_prev" / "files" / "old.txt").exists()
