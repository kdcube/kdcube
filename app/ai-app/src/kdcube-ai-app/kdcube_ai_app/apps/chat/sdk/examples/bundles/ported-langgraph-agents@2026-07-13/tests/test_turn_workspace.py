# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The turn's distributed workspace, model-facing (``platform/turn_workspace.py``).

React's paradigm, completed for the ported agent: the turn input carries ONLY
the user message + arriving-file METADATA and conversation LINKS (framed as
``[Turn start ...]`` / ``[User message]`` / ``[Files arriving this turn]``);
nothing is read for the model automatically. The triad over links does the
rest: ``read_file`` to view (react.read semantics — text bounded, images/PDF
as visual payloads), ``pull_files`` to materialize, ``run_python`` to
process. Offline tests: tmp dirs, faked resolver, no store, no redis.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_BODY = b"PK\x03\x04 not really a docx"


def _module(name: str):
    _n, m = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / f"{name}.py")
    return m


def _module_entrypoint():
    _n, m = load_dynamic_module_for_path(BUNDLE_ROOT / "entrypoint.py")
    return m


def _attachment_event(*, filename: str, mime: str, size: int = len(_BODY)) -> dict:
    return {
        "event_id": f"evt-{filename}",
        "type": "event.user.attachment.file",
        "reactive": True,
        "payload": {
            "mime": mime,
            "event": {
                "filename": filename,
                "mime": mime,
                "size": size,
                "hosted_uri": f"cb/tenants/t/projects/p/attachments/u/c/turn_x/20260716000000-{filename}",
            },
        },
    }


def _ctx(tmp_path: Path, *, enabled: bool = True):
    code_exec = _module("code_exec")
    return code_exec.CodeExecContext(
        enabled=enabled,
        outdir=tmp_path / "out",
        workdir=tmp_path / "work",
        turn_id="turn_x",
        conversation_id="conv-1",
        tenant="tenant-a",
        project="project-a",
        user_id="user-1",
    )


def _prepare(tw, ctx, events, *, bound=True):
    return asyncio.run(tw.prepare_turn_workspace(ctx, events, exec_tool_bound=bound))


# ── the turn frame: metadata + links only, nothing auto-read ────────────────

def test_frame_carries_metadata_and_link_only_and_moves_no_bytes(tmp_path):
    tw = _module("turn_workspace")
    code_exec = _module("code_exec")
    ctx = _ctx(tmp_path)
    events = [_attachment_event(filename="report.docx", mime=_DOCX_MIME)]

    ws = _prepare(tw, ctx, events)
    framed = tw.frame_turn_input("whats here", ws)

    assert "[Turn start turn_x]" in framed
    assert "[User message]\nwhats here" in framed
    assert "[Files arriving this turn]" in framed
    assert f"report.docx ({_DOCX_MIME}" in framed
    assert "link: conv:fi:turn_x.user.attachments/report.docx" in framed
    # The empty-fresh rule + both doors are stated in-band.
    assert "EMPTY" in framed and "read_file" in framed and "pull_files" in framed
    # NOTHING was materialized automatically.
    files_dir = code_exec.exec_files_dir(ctx, create=False)
    assert not files_dir.exists() or not any(files_dir.iterdir())


def test_live_turn_without_files_still_frames_the_boundary(tmp_path):
    tw = _module("turn_workspace")
    ws = _prepare(tw, _ctx(tmp_path), [])
    framed = tw.frame_turn_input("continue", ws)

    assert "[Turn start turn_x]" in framed
    assert "EMPTY" in framed and "starts fresh every turn" in framed
    assert "[User message]\ncontinue" in framed
    assert "[Files arriving this turn]" not in framed


def test_no_workspace_no_files_passes_the_question_through():
    tw = _module("turn_workspace")
    ws = asyncio.run(tw.prepare_turn_workspace(None, [], exec_tool_bound=False))

    assert tw.frame_turn_input("plain question", ws) == "plain question"


def test_no_workspace_with_files_is_reported_never_silent(tmp_path):
    tw = _module("turn_workspace")
    ctx = _ctx(tmp_path, enabled=False)
    events = [_attachment_event(filename="report.docx", mime=_DOCX_MIME)]

    ws = _prepare(tw, ctx, events, bound=False)
    framed = tw.frame_turn_input("whats here", ws)

    assert ws.live is False
    assert "no workspace tools are active" in framed
    assert "not available to you" in framed
    assert "Conversation link: conv:fi:turn_x.user.attachments/report.docx" in framed


# ── read_file: react.read semantics over links ──────────────────────────────

def _with_ctx(tw, ctx, coro_factory):
    runtime_code_exec = importlib.import_module(f"{tw.__package__}.code_exec")
    token = runtime_code_exec._CODE_EXEC_HANDLE_CV.set(ctx)
    try:
        return asyncio.run(coro_factory())
    finally:
        runtime_code_exec._CODE_EXEC_HANDLE_CV.reset(token)


def _fake_resolver(monkeypatch, payload: dict):
    import kdcube_ai_app.apps.chat.sdk.runtime.harness.events.resolver as resolver

    async def read_event_ref_bytes(*, ref, tenant, project, user_id, storage_path=None, conversation_id=""):
        if ref not in payload:
            raise FileNotFoundError(f"conv:fi: artifact bytes not found for {ref}")
        body, relpath = payload[ref]
        return body, {"relpath": relpath, "turn_id": "turn_1", "namespace": "files"}

    monkeypatch.setattr(resolver, "read_event_ref_bytes", read_event_ref_bytes)


def test_read_file_returns_bounded_text_for_text_files(tmp_path, monkeypatch):
    tw = _module("turn_workspace")
    _fake_resolver(monkeypatch, {"conv:fi:conv_c.turn_1.files/notes.txt": (b"hello " * 10, "notes.txt")})
    tool = tw.build_read_file_tool()

    out = _with_ctx(tw, _ctx(tmp_path), lambda: tool.ainvoke(
        {"path": "conv:fi:conv_c.turn_1.files/notes.txt", "max_text_symbols": 10}))

    assert isinstance(out, str)
    assert "notes.txt (text/plain" in out
    assert "hello hell" in out and "...[truncated]" in out


def test_read_file_returns_visual_payload_for_images(tmp_path, monkeypatch):
    tw = _module("turn_workspace")
    png = bytes.fromhex("89504e470d0a1a0a") + b"0" * 32  # png magic + stub body
    _fake_resolver(monkeypatch, {"conv:fi:conv_c.turn_1.files/chart.png": (png, "chart.png")})
    tool = tw.build_read_file_tool()

    out = _with_ctx(tw, _ctx(tmp_path), lambda: tool.ainvoke(
        {"path": "conv:fi:conv_c.turn_1.files/chart.png"}))

    assert isinstance(out, list)
    kinds = [b.get("type") for b in out]
    assert kinds == ["text", "image"]
    assert out[1]["media_type"] == "image/png"
    assert out[1]["data"]  # base64 body present


def test_read_file_routes_binaries_to_pull_and_code(tmp_path, monkeypatch):
    tw = _module("turn_workspace")
    _fake_resolver(monkeypatch, {"conv:fi:conv_c.turn_1.files/report.docx": (_BODY, "report.docx")})
    tool = tw.build_read_file_tool()

    out = _with_ctx(tw, _ctx(tmp_path), lambda: tool.ainvoke(
        {"path": "conv:fi:conv_c.turn_1.files/report.docx"}))

    assert isinstance(out, str)
    assert "pull_files" in out and "run_python" in out


def test_read_file_outside_a_scope_fails_open():
    tw = _module("turn_workspace")
    tool = tw.build_read_file_tool()

    out = asyncio.run(tool.ainvoke({"path": "conv:fi:conv_c.turn_1.files/x"}))

    assert "not available" in out


# ── pull_files: materialize through the shared SDK core ─────────────────────

def test_pull_files_materializes_through_the_shared_workspace_core(tmp_path, monkeypatch):
    tw = _module("turn_workspace")
    code_exec = _module("code_exec")
    ctx = _ctx(tmp_path)

    from kdcube_ai_app.apps.chat.sdk.runtime.harness import (
        workspace as sdk_workspace,
    )

    async def _fake_pull(*, refs, dest_dir, tenant, project, user_id, conversation_id="", storage_path=None):
        assert tenant == "tenant-a" and project == "project-a" and user_id == "user-1"
        target = Path(dest_dir) / "old_report.xlsx"
        target.write_bytes(_BODY)
        return [{
            "ref": refs[0], "ok": True, "filename": "old_report.xlsx",
            "path": str(target), "size": len(_BODY), "mime": "application/vnd.ms-excel",
        }]

    monkeypatch.setattr(sdk_workspace, "pull_refs_into_dir", _fake_pull)

    tool = tw.build_pull_files_tool()
    report = _with_ctx(tw, ctx, lambda: tool.ainvoke(
        {"paths": ["conv:fi:conv_c.turn_1.files/old_report.xlsx"]}))

    assert "Pulled 1/1" in report and "old_report.xlsx" in report
    files_dir = code_exec.exec_files_dir(ctx, create=False)
    assert (files_dir / "old_report.xlsx").read_bytes() == _BODY


def test_pull_files_outside_a_scope_fails_open():
    tw = _module("turn_workspace")
    tool = tw.build_pull_files_tool()

    report = asyncio.run(tool.ainvoke({"paths": ["conv:fi:conv_c.turn_1.files/x"]}))

    assert "not available" in report


# ── binding: the workspace triad stands or falls together ───────────────────

def test_workspace_triad_binds_beside_run_python_and_shares_its_opt_out():
    tool_pick = _module("tool_pick")
    connections = [{"alias": "exec", "kind": "python", "allowed": ["run_python"]}]

    bound = tool_pick.select_bound_tools(
        connections, {},
        plain_registry={},
        run_python_factory=lambda: "RUN_PYTHON",
        pull_files_factory=lambda: "PULL_FILES",
        read_file_factory=lambda: "READ_FILE",
    )
    assert bound == ["RUN_PYTHON", "PULL_FILES", "READ_FILE"]

    none_bound = tool_pick.select_bound_tools(
        connections, {"exec": True},
        plain_registry={},
        run_python_factory=lambda: "RUN_PYTHON",
        pull_files_factory=lambda: "PULL_FILES",
        read_file_factory=lambda: "READ_FILE",
    )
    assert none_bound == []

    assert tool_pick.run_python_bound(connections, {}) is True
    assert tool_pick.run_python_bound(connections, {"exec": True}) is False


# ── prompt + inputs: the frame and the guide reach the model ────────────────

def test_workspace_guide_block_joins_the_prompt_when_tools_are_bound():
    entrypoint = _module_entrypoint()

    bound = [SimpleNamespace(name="run_python"), SimpleNamespace(name="pull_files"), SimpleNamespace(name="read_file")]
    prompt = entrypoint._prebuilt_system_prompt(bound)
    assert "[DISTRIBUTED TURN WORKSPACE" in prompt
    assert "read_file" in prompt and "pull_files" in prompt and "run_python" in prompt
    assert "TURN LIFECYCLE" in prompt and "EMPTY every turn" in prompt
    assert prompt.startswith("You are a concise")  # the agent's own prose leads
    # capability level: exec-as-hands joins with run_python; guards always join
    assert "[CODE IS YOUR HANDS — run_python]" in prompt
    assert "[UNTRUSTED CONTENT]" in prompt
    assert "[CONFIDENTIALITY & PROMPT-STEALING DEFENSE (HARD)]" in prompt
    assert "[YOUR OUTPUT MEDIUM]" not in prompt


def test_prose_only_prompt_states_the_output_medium_without_exec():
    entrypoint = _module_entrypoint()

    prompt = entrypoint._prebuilt_system_prompt([SimpleNamespace(name="calc")])
    assert prompt.startswith("You are a concise")
    assert "[YOUR OUTPUT MEDIUM]" in prompt
    assert "no file-producing tool" in prompt
    assert "[CODE IS YOUR HANDS" not in prompt
    assert "[DISTRIBUTED TURN WORKSPACE" not in prompt
    assert "[UNTRUSTED CONTENT]" in prompt


def test_conversation_recovery_block_joins_when_conv_namespace_connected():
    entrypoint = _module_entrypoint()

    bound = [SimpleNamespace(name="run_python"), SimpleNamespace(name="pull_files")]
    prompt = entrypoint._prebuilt_system_prompt(
        bound, connected_namespaces=["conv", "mem"]
    )
    assert "[CONVERSATION RECOVERY — `conv` namespace]" in prompt
    assert "`pull_files`" in prompt

    without = entrypoint._prebuilt_system_prompt(bound, connected_namespaces=["mem"])
    assert "[CONVERSATION RECOVERY" not in without


def test_admin_customization_envelope_is_last():
    entrypoint = _module_entrypoint()

    bound = [SimpleNamespace(name="run_python")]
    prompt = entrypoint._prebuilt_system_prompt(
        bound, additional_instructions="Answer in Estonian."
    )
    assert prompt.rstrip().endswith("[END AGENT ADMIN CUSTOMIZATION]")
    assert "Answer in Estonian." in prompt
    assert prompt.index("[CODE IS YOUR HANDS") < prompt.index("[START AGENT ADMIN CUSTOMIZATION")


def test_framed_text_rides_both_agents_inputs_verbatim():
    entrypoint = _module_entrypoint()
    ident = SimpleNamespace(user_id="u", thread_id="t")
    framed = "[Turn start turn_x]\n...\n\n[User message]\nwhats here"

    inputs, _ = entrypoint._prebuilt_inputs(framed, ident, [])
    assert inputs["messages"][0] == ("user", framed)

    sol_inputs, _ = entrypoint._solution_inputs(framed, ident, [])
    assert sol_inputs["question"] == framed
    assert sol_inputs["messages"][0] == ("user", framed)
