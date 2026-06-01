# SPDX-License-Identifier: MIT

import base64
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
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
    assert payload["pulled"][0]["logical_root"] == "fi:turn_prev.files/projectA"
    assert payload["pulled"][0]["physical_root"] == "turn_prev/files/projectA"
    assert payload["pulled"][0]["file_count"] == 2
    assert "settings.json" in payload["pulled"][0]["tree"]
    assert "src/" in payload["pulled"][0]["tree"]
    assert "app.py" in payload["pulled"][0]["tree"]
    assert payload["pulled"][0]["path_rule"] == {
        "logical": "fi:turn_prev.files/projectA/<path shown in tree>",
        "physical": "turn_prev/files/projectA/<path shown in tree>",
    }
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    artifact_root = outdir / "workdir"
    assert (artifact_root / "turn_prev" / "files" / "projectA" / "settings.json").read_text(encoding="utf-8") == '{"theme": "dark"}'
    assert (artifact_root / "turn_prev" / "files" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == 'print("ok")\n'


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
    assert payload["pulled"] == [{
        "logical_path": "fi:turn_prev.user.attachments/template.xlsx",
        "physical_path": "turn_prev/attachments/template.xlsx",
        "file_count": 1,
    }]
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    assert (outdir / "workdir" / "turn_prev" / "attachments" / "template.xlsx").read_bytes() == b"XLSXDATA"


@pytest.mark.asyncio
async def test_pull_materializes_cross_conversation_snapshot_ref(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(
        turn_id="turn_pull",
        outdir=str(outdir),
        workdir=str(tmp_path / "work"),
        conversation_id="conv_1",
    )
    ctx = FakeBrowser(runtime)
    logical = "fi:conv_conv_2.turn_prev.snapshots/wizard/current.yaml"
    physical = "conv_conv_2/turn_prev/snapshots/wizard/current.yaml"
    stored_logical = "fi:turn_prev.snapshots/wizard/current.yaml"
    stored_physical = "turn_prev/snapshots/wizard/current.yaml"
    ctx._turn_logs[("conv_2", "turn_prev")] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": json.dumps({"artifact_path": stored_logical, "physical_path": stored_physical}),
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/yaml",
                "path": stored_logical,
                "text": "state: current\n",
                "turn_id": "turn_prev",
                "meta": {"physical_path": stored_physical},
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
                    "paths": [logical],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_snapshot")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": logical,
        "physical_path": physical,
        "file_count": 1,
    }]
    assert "missing" not in payload
    assert (outdir / "workdir" / physical).read_text(encoding="utf-8") == "state: current\n"


@pytest.mark.asyncio
async def test_pull_materializes_hosted_internal_output_not_preview(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    physical = "turn_prev/outputs/analysis/zip_contents.json"
    logical = "fi:turn_prev.outputs/analysis/zip_contents.json"
    hosted_key = (
        "cb/tenants/demo/projects/demo/attachments/user-1/conv-1/"
        "turn_prev/turn_prev/outputs/analysis/zip_contents.json"
    )
    full_payload = b'{"full": true, "items": [1, 2, 3]}\n'
    blob = tmp_path / hosted_key
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(full_payload)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": json.dumps({
                    "artifact_path": logical,
                    "physical_path": physical,
                    "mime": "application/json",
                    "kind": "file",
                    "visibility": "internal",
                    "size_bytes": len(full_payload),
                    "key": hosted_key,
                }),
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "path": logical,
                "text": "[TEXT FILE PREVIEW]\ntruncated preview, not the artifact bytes\n",
                "turn_id": "turn_prev",
                "meta": {
                    "physical_path": physical,
                    "key": hosted_key,
                    "size_bytes": len(full_payload),
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
                    "paths": [logical],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_internal_output")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": logical,
        "physical_path": physical,
        "file_count": 1,
    }]
    assert "missing" not in payload
    assert "errors" not in payload
    assert (outdir / "workdir" / physical).read_bytes() == full_payload


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
    assert payload["invalid"] == [{
        "path": "fi:turn_prev.user.attachments/binaries",
        "reason": "attachment_pulls_require_exact_file_ref",
    }]
    assert "missing" not in payload
    assert "errors" not in payload
