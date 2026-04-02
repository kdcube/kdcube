# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external import handle_external_tool
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


class _FakeExecStreamer:
    def __init__(self, code: str):
        self._code = code
        self.subsystem_language = "python"

    def get_code(self):
        return self._code

    def set_code(self, code: str):
        self._code = code


class _HostingRecorder:
    def __init__(self):
        self.host_calls = []
        self.emit_calls = []

    async def host_files_to_conversation(self, **kwargs):
        self.host_calls.append(kwargs)
        files = kwargs.get("files") or []
        if not files:
            return []
        artifact = files[0]
        value = artifact.get("value") if isinstance(artifact.get("value"), dict) else {}
        return [{
            "rn": "ef:test:artifact:secret.txt",
            "hosted_uri": "s3://bucket/secret.txt",
            "key": "artifact/secret.txt",
            "physical_path": value.get("path") or "",
        }]

    async def emit_solver_artifacts(self, *, files, citations):
        self.emit_calls.append({"files": files, "citations": citations})


@pytest.mark.asyncio
async def test_external_exec_path_rewrite_notice(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"tool_id": "exec_tools.execute_code_python", "params": {
        "contract": [{"filename": "turn_exec/files/out.txt", "description": "test output"}],
        "prog_name": "snippet.py",
    }}},
             "outdir": str(tmp_path),
             "workdir": str(tmp_path),
             "exec_code_streamer": _FakeExecStreamer("open('files/x.txt').read()")}

    captured = {}

    async def _fake_execute_tool(**kwargs):
        captured["params"] = kwargs["tool_execution_context"]["params"]
        return {"items": []}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e1")
    assert "turn_exec/files/x.txt" in captured["params"]["code"]


@pytest.mark.asyncio
async def test_rendering_tool_accepts_generic_outdir_fi_path(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "rendering_tools.write_pdf",
                "params": {"path": "fi:logs/out.pdf", "content": "<html><body>x</body></html>"},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    captured = {}

    async def _fake_execute_tool(**kwargs):
        captured["params"] = kwargs["tool_execution_context"]["params"]
        outdir = kwargs["outdir"]
        target = outdir / kwargs["tool_execution_context"]["params"]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\n")
        return {"output": kwargs["tool_execution_context"]["params"]["path"], "summary": ""}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e2")

    assert captured["params"]["path"] == "logs/out.pdf"
    assert any(
        "\"artifact_path\": \"fi:logs/out.pdf\"" in (b.get("text") or "")
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    )
    assert not any(
        b.get("type") == "react.notice" and "path_rewritten" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_exec_internal_file_is_not_hosted_but_keeps_file_path(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "contract": [{
                        "filename": "turn_exec/files/secret.txt",
                        "description": "Agent-only output.",
                        "visibility": "internal",
                    }],
                    "prog_name": "secret_exec",
                },
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
        "exec_code_streamer": _FakeExecStreamer("print('ok')"),
    }

    async def _fake_execute_tool(**kwargs):
        target = tmp_path / "turn_exec" / "files" / "secret.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("top secret\n", encoding="utf-8")
        return {
            "items": [{
                "artifact_id": "secret",
                "output": {
                    "type": "file",
                    "path": "turn_exec/files/secret.txt",
                    "filename": "secret.txt",
                    "mime": "text/plain",
                    "text": "top secret\n",
                    "description": "Agent-only output.",
                    "visibility": "internal",
                },
                "artifact_kind": "file",
                "summary": "",
                "filepath": "turn_exec/files/secret.txt",
                "visibility": "internal",
            }]
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting)
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e3")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    meta_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("path") == "tc:turn_exec.e3.result"
        and (b.get("mime") or "").strip() == "application/json"
    ]
    assert meta_blocks
    meta_text = meta_blocks[-1].get("text") or ""
    assert "\"visibility\": \"internal\"" in meta_text
    assert "\"artifact_path\": \"fi:turn_exec.files/secret.txt\"" in meta_text
    assert "\"physical_path\": \"turn_exec/files/secret.txt\"" in meta_text
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.files/secret.txt"
        and (b.get("text") or "") == "top secret\n"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_exec_requires_pull_for_unmaterialized_historical_file(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "contract": [{
                        "filename": "turn_exec/files/out.txt",
                        "description": "test output",
                    }],
                    "prog_name": "snippet.py",
                },
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
        "exec_code_streamer": _FakeExecStreamer("print(open('turn_old/files/a.txt').read())"),
    }

    called = {"execute": False}

    async def _fake_execute_tool(**kwargs):
        called["execute"] = True
        return {"items": []}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_pull")

    assert called["execute"] is False
    assert out.get("retry_decision") is True
    notices = [b for b in ctx.timeline.blocks if b.get("type") == "react.notice"]
    assert any("exec_requires_pull" in (b.get("text") or "") for b in notices)
    result_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert result_blocks
    assert "pre_exec_pull_required" in (result_blocks[-1].get("text") or "")
