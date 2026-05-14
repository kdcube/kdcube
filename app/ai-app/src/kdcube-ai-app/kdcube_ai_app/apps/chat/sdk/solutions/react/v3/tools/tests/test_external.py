# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external import handle_external_tool
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser, FakeReact


class _FakeExecStreamer:
    def __init__(self, code: str):
        self._code = code
        self.subsystem_language = "python"
        self.execution_id = None

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
        filename = (value.get("filename") or "secret.txt").strip()
        path = value.get("path") or ""
        return [{
            "rn": f"ef:test:artifact:{filename}",
            "hosted_uri": f"s3://bucket/{filename}",
            "key": f"artifact/{filename}",
            "physical_path": path,
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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

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
async def test_rendering_tool_normalizes_visible_current_turn_shorthand_ref(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "turn_id": "turn_exec",
        "path": "fi:turn_exec.outputs/report.html",
        "mime": "text/html",
        "text": "<html><body>source</body></html>",
        "meta": {
            "artifact_path": "fi:turn_exec.outputs/report.html",
            "physical_path": "turn_exec/outputs/report.html",
            "visibility": "external",
        },
    })
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "rendering_tools.write_pdf",
                "params": {
                    "path": "outputs/report.pdf",
                    "content": "ref:outputs/report.html",
                    "format": "html",
                },
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    captured = {}

    async def _fake_execute_tool(**kwargs):
        captured["params"] = kwargs["tool_execution_context"]["params"]
        outdir = kwargs["outdir"]
        target = outdir / "turn_exec/outputs/report.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\n")
        return {"output": "turn_exec/outputs/report.pdf", "summary": ""}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_ref")

    assert captured["params"]["content"] == "<html><body>source</body></html>"
    assert not state.get("retry_decision")
    notices = [b for b in ctx.timeline.blocks if b.get("type") == "react.notice"]
    assert any("protocol_warning.ref_path_normalized" in (b.get("text") or "") for b in notices)
    assert any("ref:fi:turn_exec.outputs/report.html" in (b.get("text") or "") for b in notices)


@pytest.mark.asyncio
async def test_rendering_tool_stats_resolve_split_artifact_outdir(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "rendering_tools.write_pdf",
                "params": {"path": "outputs/report.pdf", "content": "<html><body>ok</body></html>"},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }

    async def _fake_execute_tool(**kwargs):
        target = tmp_path / "workdir" / kwargs["tool_execution_context"]["params"]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\n" + (b"x" * 2048))
        return {"output": kwargs["tool_execution_context"]["params"]["path"], "summary": ""}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="pdf_ok")

    meta_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("path") == "tc:turn_exec.pdf_ok.result"
        and (b.get("mime") or "").strip() == "application/json"
    ]
    assert meta_blocks
    meta_text = meta_blocks[-1].get("text") or ""
    assert '"artifact_path": "fi:turn_exec.outputs/report.pdf"' in meta_text
    assert '"status": "error"' not in meta_text
    assert '"file_not_found"' not in meta_text


@pytest.mark.asyncio
async def test_large_inline_renderer_source_is_passed_to_tool(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    large_content = "<html>" + ("x" * 5000) + "</html>"
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "rendering_tools.write_pdf",
                "params": {
                    "path": "outputs/report.pdf",
                    "content": large_content,
                },
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
        target.write_bytes(b"%PDF-1.4\n" + (b"x" * 2048))
        return {"output": kwargs["tool_execution_context"]["params"]["path"], "summary": ""}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="pdf_inline")

    assert captured["params"]["content"] == large_content
    assert not out.get("retry_decision")
    assert not any(
        b.get("type") == "react.notice"
        and "too_large" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/report.pdf"
        and b.get("mime") == "application/pdf"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_call_error_is_visible_on_result_block(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "browser_tools.open_page",
                "params": {"url": "fi:turn_exec.outputs/missing.html"},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }

    async def _fake_runtime_execute_tool(**kwargs):
        # This mocks runtime.execution.execute_tool(), not the raw browser tool.
        # Raw tool callables return {ok,error,ret}; execute_tool unwraps that
        # into ReAct's normalized {status,output,summary,error,call_error}.
        return {
            "status": "error",
            "output": None,
            "summary": "ERROR [FileNotFoundError] at browser_tools.open_page: missing.html",
            "error": None,
            "call_error": {
                "code": "FileNotFoundError",
                "message": "missing.html",
                "where": "browser_tools.open_page",
                "managed": False,
            },
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_runtime_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="browser_err")

    assert out["last_tool_result"][0]["error"]["code"] == "FileNotFoundError"
    result_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("path") == "tc:turn_exec.browser_err.result"
        and (b.get("mime") or "").strip() == "application/json"
    ]
    assert result_blocks
    text = result_blocks[-1].get("text") or ""
    assert '"status": "error"' in text
    assert '"code": "FileNotFoundError"' in text
    assert '"message": "missing.html"' in text


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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

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
async def test_external_tool_declared_files_are_hosted_and_emitted(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "email.materialize_email_attachments",
                "params": {"message_ids_json": "[\"m1\"]"},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    first = tmp_path / "turn_exec" / "outputs" / "email-attachments" / "acct" / "m1" / "invoice.pdf"
    second = tmp_path / "turn_exec" / "outputs" / "email-attachments" / "acct" / "m1" / "terms.txt"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"%PDF-1.4\n")
    second.write_text("terms\n", encoding="utf-8")

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "artifact_type": "files",
                "files": [
                    {
                        "artifact_path": "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf",
                        "logical_path": "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf",
                        "physical_path": "turn_exec/outputs/email-attachments/acct/m1/invoice.pdf",
                        "filename": "invoice.pdf",
                        "mime_type": "application/pdf",
                        "size_bytes": first.stat().st_size,
                        "visibility": "external",
                    },
                    {
                        "path": "turn_exec/outputs/email-attachments/acct/m1/terms.txt",
                        "filename": "terms.txt",
                        "mime": "text/plain",
                        "visibility": "external",
                    },
                ],
            },
            "summary": "",
        }

    class _Comm:
        user_id = "u1"
        user_type = "admin"
        service = {
            "tenant": "tenant1",
            "project": "project1",
            "user": "u1",
            "user_type": "admin",
            "conversation_id": "conv1",
            "request_id": "req1",
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting, comm=_Comm())
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="email_files")

    assert len(hosting.host_calls) == 2
    assert len(hosting.emit_calls) == 2
    assert len(out["last_tool_result"]) == 3
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf"
        and (b.get("meta") or {}).get("hosted_uri") == "s3://bucket/invoice.pdf"
        for b in ctx.timeline.blocks
    )
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/email-attachments/acct/m1/terms.txt"
        and (b.get("meta") or {}).get("hosted_uri") == "s3://bucket/terms.txt"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_internal_declared_files_keep_paths_without_hosting(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "email.materialize_email_attachments",
                "params": {"message_ids_json": "[\"m1\"]", "visibility": "internal"},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    target = tmp_path / "turn_exec" / "outputs" / "email-attachments" / "acct" / "m1" / "invoice.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4\n")

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "artifact_type": "files",
                "files": [{
                    "artifact_path": "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf",
                    "logical_path": "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf",
                    "physical_path": "turn_exec/outputs/email-attachments/acct/m1/invoice.pdf",
                    "filename": "invoice.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": target.stat().st_size,
                    "visibility": "internal",
                }],
            },
            "summary": "",
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting)
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="email_internal_files")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    assert len(out["last_tool_result"]) == 2
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf"
        and (b.get("meta") or {}).get("physical_path") == "turn_exec/outputs/email-attachments/acct/m1/invoice.pdf"
        and (b.get("meta") or {}).get("visibility") == "internal"
        for b in ctx.timeline.blocks
    )
    assert any(
        b.get("type") == "react.tool.result"
        and (b.get("text") or "").find('"artifact_path": "fi:turn_exec.outputs/email-attachments/acct/m1/invoice.pdf"') >= 0
        and (b.get("text") or "").find('"physical_path": "turn_exec/outputs/email-attachments/acct/m1/invoice.pdf"') >= 0
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_self_hosted_declared_files_are_not_rehosted(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "email.materialize_email_attachments",
                "params": {},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "artifact_type": "files",
                "files": [{
                    "type": "file",
                    "hosted": True,
                    "emitted": True,
                    "hosted_uri": "s3://bucket/invoice.pdf",
                    "rn": "rn:invoice",
                    "key": "artifact/invoice.pdf",
                    "physical_path": "turn_exec/outputs/email-attachments/acct/m1/invoice.pdf",
                    "filename": "invoice.pdf",
                    "mime_type": "application/pdf",
                    "visibility": "external",
                }],
            },
            "summary": "",
        }

    class _Comm:
        user_id = "u1"
        user_type = "admin"
        service = {"tenant": "tenant1", "project": "project1", "user": "u1", "conversation_id": "conv1"}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting, comm=_Comm())
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="hosted_files")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    assert len(out["last_tool_result"]) == 2
    assert any(
        b.get("type") == "react.tool.result"
        and (b.get("meta") or {}).get("hosted_uri") == "s3://bucket/invoice.pdf"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_self_hosted_internal_image_is_not_emitted_but_is_multimodal(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "browser_tools.status",
                "params": {"screenshot": True},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    target = tmp_path / "turn_exec" / "outputs" / "browser_screenshots" / "123_main.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "ret": {
                    "artifact_type": "files",
                    "screenshot": {
                        "path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "logical_path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "artifact_path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "physical_path": "turn_exec/outputs/browser_screenshots/123_main.png",
                        "filename": "123_main.png",
                        "mime": "image/png",
                        "visibility": "internal",
                        "hosted": True,
                        "emitted": False,
                        "hosted_uri": "s3://bucket/123_main.png",
                    },
                    "files": [{
                        "path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "logical_path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "artifact_path": "fi:turn_exec.outputs/browser_screenshots/123_main.png",
                        "physical_path": "turn_exec/outputs/browser_screenshots/123_main.png",
                        "filename": "123_main.png",
                        "mime": "image/png",
                        "visibility": "internal",
                        "hosted": True,
                        "emitted": False,
                        "hosted_uri": "s3://bucket/123_main.png",
                    }],
                },
            },
            "summary": "",
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting)
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="browser_screen")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    assert len(out["last_tool_result"]) == 2
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/browser_screenshots/123_main.png"
        and b.get("mime") == "image/png"
        and b.get("base64")
        and (b.get("meta") or {}).get("visibility") == "internal"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_large_internal_image_is_metadata_only(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "browser_tools.status",
                "params": {"screenshot": True},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }
    target = tmp_path / "turn_exec" / "outputs" / "browser_screenshots" / "large.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 800_000)

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "ret": {
                    "artifact_type": "files",
                    "files": [{
                        "path": "fi:turn_exec.outputs/browser_screenshots/large.png",
                        "logical_path": "fi:turn_exec.outputs/browser_screenshots/large.png",
                        "artifact_path": "fi:turn_exec.outputs/browser_screenshots/large.png",
                        "physical_path": "turn_exec/outputs/browser_screenshots/large.png",
                        "filename": "large.png",
                        "mime": "image/png",
                        "visibility": "internal",
                        "hosted": True,
                        "emitted": False,
                    }],
                },
            },
            "summary": "",
        }

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact(hosting_service=_HostingRecorder())
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="browser_large_screen")

    assert not any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/browser_screenshots/large.png"
        and b.get("base64")
        for b in ctx.timeline.blocks
    )
    assert any(
        b.get("type") == "react.tool.result"
        and b.get("path") == "fi:turn_exec.outputs/browser_screenshots/large.png"
        and (b.get("meta") or {}).get("multimodal_status") == "too_large_for_visible_context"
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_tool_plain_files_field_is_not_hosted_without_marker(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "some.bundle_tool",
                "params": {},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "files": [{
                    "physical_path": "turn_exec/outputs/report.pdf",
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                }],
            },
            "summary": "",
        }

    class _Comm:
        user_id = "u1"
        user_type = "admin"
        service = {"tenant": "tenant1", "project": "project1", "user": "u1", "conversation_id": "conv1"}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting, comm=_Comm())
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="plain_files")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    assert len(out["last_tool_result"]) == 1


@pytest.mark.asyncio
async def test_external_tool_rejects_non_artifact_type_file_markers(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path), conversation_id="conv1")
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "tool_id": "some.bundle_tool",
                "params": {},
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
    }

    async def _fake_execute_tool(**kwargs):
        return {
            "output": {
                "ok": True,
                "kdcube_result_type": "files",
                "artifact_kind": "files",
                "artifacts": {
                    "files": [{
                        "physical_path": "turn_exec/outputs/report.pdf",
                        "filename": "report.pdf",
                        "mime_type": "application/pdf",
                    }]
                },
            },
            "summary": "",
        }

    class _Comm:
        user_id = "u1"
        user_type = "admin"
        service = {"tenant": "tenant1", "project": "project1", "user": "u1", "conversation_id": "conv1"}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    hosting = _HostingRecorder()
    react = FakeReact(hosting_service=hosting, comm=_Comm())
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="legacy_files")

    assert hosting.host_calls == []
    assert hosting.emit_calls == []
    assert len(out["last_tool_result"]) == 1


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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

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


@pytest.mark.asyncio
async def test_external_exec_falls_back_to_decision_packet_code_channel(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    code_text = "print('from decision packet')\n"
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
        "last_decision_raw": {
            "channels": {
                "code": {
                    "text": code_text,
                }
            }
        },
        "outdir": str(tmp_path),
        "workdir": str(tmp_path),
        "exec_code_streamer": _FakeExecStreamer(""),
    }

    captured = {}

    async def _fake_execute_tool(**kwargs):
        captured["params"] = kwargs["tool_execution_context"]["params"]
        return {"items": []}

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_packet")

    assert captured["params"]["code"] == code_text


@pytest.mark.asyncio
async def test_external_exec_rejects_contaminated_code_channel(monkeypatch, tmp_path):
    runtime = RuntimeCtx(turn_id="turn_exec", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    contaminated = (
        "` was mentioned in thinking before the real code.\n"
        "</thinking>\n"
        "print('this must not run')\n"
    )
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
        "exec_code_streamer": _FakeExecStreamer(contaminated),
    }

    async def _fake_execute_tool(**kwargs):
        raise AssertionError("contaminated code should be rejected before execution")

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v3.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_contaminated")

    assert out["retry_decision"] is True
    assert out["last_tool_result"][0]["error"]["code"] == "exec_code_contaminated"
    assert any(
        b.get("type") == "react.notice"
        and "exec_code_contaminated" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_external_exec_missing_code_adds_tool_result_error(tmp_path):
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
        "exec_code_streamer": _FakeExecStreamer(""),
    }

    react = FakeReact()
    react.tools_subsystem = None

    out = await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_missing")

    assert out["retry_decision"] is True
    assert out["last_tool_result"][0]["error"]["code"] == "exec_missing_code"
    result_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert result_blocks
    assert "exec_missing_code" in (result_blocks[-1].get("text") or "")
