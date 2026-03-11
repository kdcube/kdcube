# SPDX-License-Identifier: MIT

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.patch import handle_react_patch
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


class _FakeComm:
    def __init__(self):
        self.service = {
            "request_id": "req_1",
            "tenant": "tenant_1",
            "project": "project_1",
            "user": "user_1",
            "conversation_id": "conv_1",
            "user_type": "human",
        }
        self.user_id = "user_1"
        self.user_type = "human"


class _FakeHostingService:
    def __init__(self):
        self.calls = []
        self.emitted = []

    async def host_files_to_conversation(self, **kwargs):
        self.calls.append(kwargs)
        artifact = (kwargs.get("files") or [{}])[0]
        value = artifact.get("value") or {}
        return [{
            "hosted_uri": "hosted://artifact/a.txt",
            "rn": "rn:artifact:a.txt",
            "key": "artifact/a.txt",
            "physical_path": value.get("path") or "",
        }]

    async def emit_solver_artifacts(self, *, files, citations):
        self.emitted.append({"files": files, "citations": citations})


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


@pytest.mark.asyncio
async def test_patch_applies_unified_diff_and_rewrites_headers(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_new", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)

    old_path = tmp_path / "turn_old" / "files"
    old_path.mkdir(parents=True, exist_ok=True)
    source_file = old_path / "a.txt"
    source_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    patch_text = "\n".join([
        "--- a/a.txt",
        "+++ b/a.txt",
        "@@ -1,3 +1,4 @@",
        " alpha",
        "+inserted",
        " beta",
        " gamma",
        "",
    ])
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "path": "turn_old/files/a.txt",
                    "channel": "canvas",
                    "patch": patch_text,
                    "kind": "display",
                }
            }
        },
        "outdir": str(tmp_path),
    }

    await handle_react_patch(react=FakeReact(), ctx_browser=ctx, state=state, tool_call_id="p2")

    new_path = tmp_path / "turn_new" / "files" / "a.txt"
    assert new_path.read_text(encoding="utf-8") == "alpha\ninserted\nbeta\ngamma\n"

    result_blocks = [b for b in ctx.timeline.blocks if b.get("type") == "react.tool.result" and b.get("mime") == "text/markdown"]
    assert result_blocks
    rendered_patch = result_blocks[-1].get("text") or ""
    assert str(source_file) in rendered_patch
    assert str(new_path) in rendered_patch


@pytest.mark.asyncio
async def test_patch_file_kind_hosts_and_emits_file(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_new", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    hosting = _FakeHostingService()
    comm = _FakeComm()

    old_path = tmp_path / "turn_old" / "files"
    old_path.mkdir(parents=True, exist_ok=True)
    (old_path / "a.txt").write_text("old", encoding="utf-8")

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "path": "turn_old/files/a.txt",
                    "channel": "canvas",
                    "patch": "new",
                    "kind": "file",
                }
            }
        },
        "outdir": str(tmp_path),
    }

    await handle_react_patch(
        react=FakeReact(hosting_service=hosting, comm=comm),
        ctx_browser=ctx,
        state=state,
        tool_call_id="p3",
    )

    assert hosting.calls
    hosted_artifact = hosting.calls[0]["files"][0]
    assert hosted_artifact["value"]["type"] == "file"
    assert hosted_artifact["value"]["path"] == "turn_new/files/a.txt"
    assert hosting.emitted

    json_blocks = [b for b in ctx.timeline.blocks if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"]
    assert json_blocks
    payload = json.loads(json_blocks[-1]["text"])
    assert payload["ok"] is True
