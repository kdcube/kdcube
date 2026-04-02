# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.tools.exec_tools as exec_tools_module
from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    _build_exec_context_from_comm_spec,
    _build_exec_error_payload,
    _build_exec_loader_wrapper,
    _normalize_artifacts_spec,
    build_exec_output_contract,
    run_exec_tool,
)


def test_build_exec_error_payload_prefers_runtime_failure_over_missing_outputs():
    error = _build_exec_error_payload(
        missing=["turn_1/files/out.txt"],
        errors=[],
        run_res={
            "ok": False,
            "error": "fargate_run_task_exception: InvalidParameterException: Container Overrides length must be at most 8192",
            "error_summary": "InvalidParameterException: Container Overrides length must be at most 8192",
        },
        infra_text="trace",
    )

    assert error is not None
    assert error["code"] == "fargate_run_task_exception"
    assert "Container Overrides length must be at most 8192" in error["message"]
    assert "Missing output files" in error["message"]
    assert error["details"]["missing"] == ["turn_1/files/out.txt"]


def test_build_exec_error_payload_uses_stderr_tail_when_summary_missing():
    error = _build_exec_error_payload(
        missing=["turn_1/files/hello.txt"],
        errors=[],
        run_res={
            "ok": False,
            "returncode": 125,
            "stderr_tail": "docker: Error response from daemon: permission denied while trying to connect to the Docker daemon socket\nSee 'docker run --help'.",
        },
        infra_text="",
    )

    assert error is not None
    assert error["code"] == "execution_failed"
    assert "permission denied while trying to connect to the Docker daemon socket" in error["message"]
    assert "Missing output files" in error["message"]


def test_build_exec_error_payload_uses_timeout_summary_from_backend_result():
    error = _build_exec_error_payload(
        missing=["turn_1/files/test_report.txt"],
        errors=[],
        run_res={
            "ok": False,
            "returncode": 124,
            "error": "timeout",
            "error_summary": "Timeout after 30s",
        },
        infra_text="",
    )

    assert error is not None
    assert error["code"] == "timeout"
    assert "Timeout after 30s" in error["message"]
    assert "Missing output files" in error["message"]


def test_build_exec_context_from_comm_spec_preserves_identity_fields():
    ctx = _build_exec_context_from_comm_spec(
        comm_spec={
            "service": {"request_id": "req-1"},
            "conversation": {
                "session_id": "sess-1",
                "conversation_id": "conv-1",
                "turn_id": "turn-1",
            },
            "user_id": "user-1",
            "user_type": "privileged",
            "tenant": "demo",
            "project": "demo-project",
        },
        runtime_globals={
            "BUNDLE_SPEC": {"id": "with-isoruntime@2026-02-16-14-00"},
        },
        exec_id="exec-1",
        exec_runtime={"mode": "fargate"},
    )

    assert ctx == {
        "tenant": "demo",
        "project": "demo-project",
        "user_id": "user-1",
        "user_type": "privileged",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "session_id": "sess-1",
        "request_id": "req-1",
        "bundle_id": "with-isoruntime@2026-02-16-14-00",
        "exec_id": "exec-1",
        "codegen_run_id": "exec-1",
        "exec_runtime": {"mode": "fargate"},
    }


def test_normalize_artifacts_spec_marks_markdown_as_text():
    artifacts, err = _normalize_artifacts_spec([
        {
            "filename": "turn_1/files/preferences_exec_report.md",
            "description": "Markdown report generated from bundle-local preference history.",
        }
    ])

    assert err is None
    assert artifacts is not None
    assert artifacts[0]["mime"] == "text/markdown"


def test_build_exec_output_contract_preserves_visibility_and_defaults_external():
    contract, normalized, err = build_exec_output_contract([
        {
            "filename": "turn_1/files/public.md",
            "description": "User-visible report.",
        },
        {
            "filename": "turn_1/files/internal.json",
            "description": "Agent-only scratch output.",
            "visibility": "internal",
        },
    ])

    assert err is None
    assert normalized is not None
    assert normalized[0]["visibility"] == "external"
    assert normalized[1]["visibility"] == "internal"
    assert contract == {
        "public": {
            "type": "file",
            "filename": "turn_1/files/public.md",
            "mime": "text/markdown",
            "description": "User-visible report.",
            "visibility": "external",
        },
        "internal": {
            "type": "file",
            "filename": "turn_1/files/internal.json",
            "mime": "application/json",
            "description": "Agent-only scratch output.",
            "visibility": "internal",
        },
    }


def test_build_exec_output_contract_rejects_invalid_visibility():
    contract, normalized, err = build_exec_output_contract([
        {
            "filename": "turn_1/files/public.md",
            "description": "User-visible report.",
            "visibility": "public",
        },
    ])

    assert contract is None
    assert normalized is None
    assert err == {
        "code": "invalid_artifact_spec",
        "message": "visibility must be either 'external' or 'internal'",
    }


@pytest.mark.asyncio
async def test_run_exec_tool_forwards_bundle_storage_dir_to_runtime(tmp_path, monkeypatch):
    captured = {}

    class _FakeRuntime:
        def __init__(self, logger):
            self.logger = logger

        async def execute_py_code(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "returncode": 0}

    monkeypatch.setattr(exec_tools_module, "_InProcessRuntime", _FakeRuntime)
    monkeypatch.setattr(
        exec_tools_module,
        "build_portable_spec",
        lambda **_kwargs: SimpleNamespace(to_json=lambda: "{}"),
    )

    tool_manager = SimpleNamespace(
        svc=object(),
        comm=SimpleNamespace(_export_comm_spec_for_runtime=lambda: {}),
        export_runtime_globals=lambda: {},
        tool_modules_tuple_list=lambda: [],
        bundle_root=None,
    )

    result = await run_exec_tool(
        tool_manager=tool_manager,
        output_contract={},
        code="print('ok')",
        contract=[],
        timeout_s=30,
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
        bundle_storage_dir="/bundle-storage/demo-tenant/demo-project/react.doc__test",
    )

    assert result["ok"] is True
    assert captured["globals"]["BUNDLE_STORAGE_DIR"] == "/bundle-storage/demo-tenant/demo-project/react.doc__test"


@pytest.mark.asyncio
async def test_run_exec_tool_preserves_user_code_verbatim_and_uses_loader_wrapper(tmp_path, monkeypatch):
    captured = {}
    original_code = (
        "entrypoint_content = '''\"\"\"Minimal bundle entrypoint.\"\"\"\n"
        "\n"
        "from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint\n"
        "\n"
        "BUNDLE_ID = \"minimal_test_bundle\"\n"
        "\n"
        "class MinimalWorkflow(BaseEntrypoint):\n"
        "    \"\"\"Minimal workflow implementation.\"\"\"\n"
        "    pass\n"
        "'''\n"
        "await agent_io_tools.tool_call(fn=None, params={}, call_reason='x', tool_id='y')\n"
    )

    class _FakeRuntime:
        def __init__(self, logger):
            self.logger = logger

        async def execute_py_code(self, **kwargs):
            workdir = kwargs["workdir"]
            captured["main.py"] = (workdir / "main.py").read_text(encoding="utf-8")
            captured["user_code.py"] = (workdir / "user_code.py").read_text(encoding="utf-8")
            return {"ok": True, "returncode": 0}

    monkeypatch.setattr(exec_tools_module, "_InProcessRuntime", _FakeRuntime)
    monkeypatch.setattr(
        exec_tools_module,
        "build_portable_spec",
        lambda **_kwargs: SimpleNamespace(to_json=lambda: "{}"),
    )

    tool_manager = SimpleNamespace(
        svc=object(),
        comm=SimpleNamespace(_export_comm_spec_for_runtime=lambda: {}),
        export_runtime_globals=lambda: {},
        tool_modules_tuple_list=lambda: [],
        bundle_root=None,
    )

    result = await run_exec_tool(
        tool_manager=tool_manager,
        output_contract={},
        code=original_code,
        contract=[],
        timeout_s=30,
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
    )

    assert result["ok"] is True
    assert captured["user_code.py"] == original_code
    assert captured["main.py"] == _build_exec_loader_wrapper()
    assert "ast.PyCF_ALLOW_TOP_LEVEL_AWAIT" in captured["main.py"]
    assert "scope['__name__'] = '__kdcube_exec_user_code__'" in captured["main.py"]
