# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

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

    monkeypatch.setattr("kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.external.execute_tool", _fake_execute_tool)

    react = FakeReact()
    react.tools_subsystem = None

    await handle_external_tool(react=react, ctx_browser=ctx, state=state, tool_call_id="e_packet")

    assert captured["params"]["code"] == code_text
