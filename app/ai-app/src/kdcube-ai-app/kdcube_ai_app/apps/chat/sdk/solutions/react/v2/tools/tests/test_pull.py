# SPDX-License-Identifier: MIT

import base64
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.pull import handle_react_pull
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser


def _latest_payload(ctx: FakeBrowser) -> dict:
    blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert blocks
    return json.loads(blocks[-1]["text"])


@pytest.mark.asyncio
async def test_pull_materializes_turn_file_subtree_from_fi_paths(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/projectA/settings.json","physical_path":"turn_prev/files/projectA/settings.json"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "path": "fi:turn_prev.files/projectA/settings.json",
                "text": '{"theme": "dark"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"fi:turn_prev.files/projectA/src/app.py","physical_path":"turn_prev/files/projectA/src/app.py"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/x-python",
                "path": "fi:turn_prev.files/projectA/src/app.py",
                "text": 'print("ok")\n',
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["fi:turn_prev.files/projectA"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_files")

    payload = _latest_payload(ctx)
    pulled = payload["pulled"]
    assert {"logical_path": "fi:turn_prev.files/projectA/settings.json", "physical_path": "turn_prev/files/projectA/settings.json", "kind": "files"} in pulled
    assert {"logical_path": "fi:turn_prev.files/projectA/src/app.py", "physical_path": "turn_prev/files/projectA/src/app.py", "kind": "files"} in pulled
    assert payload["invalid"] == []
    assert (outdir / "turn_prev" / "files" / "projectA" / "settings.json").read_text(encoding="utf-8") == '{"theme": "dark"}'
    assert (outdir / "turn_prev" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == 'print("ok")\n'


@pytest.mark.asyncio
async def test_pull_materializes_exact_attachment_ref(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    payload = base64.b64encode(b"XLSXDATA").decode("utf-8")
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "path": "fi:turn_prev.user.attachments/template.xlsx",
                "base64": payload,
                "turn_id": "turn_prev",
                "meta": {
                    "physical_path": "turn_prev/attachments/template.xlsx",
                    "size_bytes": 8,
                },
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["fi:turn_prev.user.attachments/template.xlsx"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_attachment")

    payload = _latest_payload(ctx)
    assert payload["invalid"] == []
    assert payload["missing"] == []
    assert payload["pulled"] == [{
        "logical_path": "fi:turn_prev.user.attachments/template.xlsx",
        "physical_path": "turn_prev/attachments/template.xlsx",
        "kind": "attachment",
    }]
    assert (outdir / "turn_prev" / "attachments" / "template.xlsx").read_bytes() == b"XLSXDATA"


@pytest.mark.asyncio
async def test_pull_rejects_attachment_prefix_pull(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["fi:turn_prev.user.attachments/binaries"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_bad_attachment")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == []
    assert payload["missing"] == []
    assert payload["errors"] == []
    assert payload["invalid"] == [{
        "path": "fi:turn_prev.user.attachments/binaries",
        "reason": "attachment_pulls_require_exact_file_ref",
    }]
