"""The code-execution seam (platform/code_exec.py + platform/code_exec_tool.py).

Exercises the WIRING offline, with an INJECTED exec runner (the real sandbox is
docker-isolated and LIVE-ONLY) and a MOCK hosting service:

  - `run_code_and_host` runs (injected) code, converts the side-effects diff into
    host artifacts, hosts them via the hosting service, and returns compact file
    refs (rn + filename + mime) — never the bytes;
  - the `run_python` tool renders a sensible text result;
  - `code_exec_scope` BINDS the tool subsystem, OUTDIR_CV, and the per-turn handle,
    and UNBINDS them all on exit;
  - the disabled / offline path is a clean no-op error result — the turn still runs;
  - config defaults + parsing.

Fully offline — no DB, no docker, no API key.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for
from kdcube_ai_app.apps.chat.sdk.tools import bundle_tool_context

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _code_exec_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "code_exec.py")
    return module


def _code_exec_tool_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "code_exec_tool.py")
    return module


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeHosting:
    """Records what it was asked to host, returns canned hosted rows."""

    def __init__(self) -> None:
        self.hosted_files: list = []
        self.emitted_files: list | None = None

    async def host_files_to_conversation(self, *, rid, files, outdir, tenant, project,
                                         user, conversation_id, user_type, turn_id) -> list:
        self.hosted_files = list(files or [])
        rows = []
        for a in self.hosted_files:
            out = a.get("output") or {}
            name = out.get("filename") or "file"
            rows.append({
                "rn": f"rn:{name}",
                "hosted_uri": f"s3://bucket/{conversation_id}/{turn_id}/{name}",
                # The React-style downloadable link host_files_to_conversation returns.
                "logical_path": f"fi:conv_{conversation_id}.{turn_id}.files/{name}",
                "mime": out.get("mime") or "application/octet-stream",
                "filename": name,
                "physical_path": out.get("path"),
            })
        return rows

    async def emit_solver_artifacts(self, *, files, citations) -> None:
        self.emitted_files = list(files or [])


def _make_ctx(mod, *, tmp_path: Path, exec_runner, hosting=None, enabled=True):
    hosting = hosting or _FakeHosting()
    comm = SimpleNamespace(
        tenant="t", project="p", user_id="u", user_type="registered",
        conversation={"conversation_id": "conv-1", "turn_id": "turn_test"},
        service={"request_id": "req-1"},
    )
    return mod.CodeExecContext(
        enabled=enabled,
        comm=comm,
        hosting_service=hosting,
        tool_subsystem=SimpleNamespace(name="fake-subsystem"),
        outdir=tmp_path / "out",
        workdir=tmp_path / "work",
        sandbox_root=None,  # skip rmtree cleanup in tests
        turn_id="turn_test",
        conversation_id="conv-1",
        tenant="t", project="p", user_id="u", user_type="registered", request_id="req-1",
        exec_runtime={"mode": "docker"},
        timeout_s=30,
        exec_runner=exec_runner,
    )


def _make_side_effects_runner(*, ok=True):
    """An injected exec runner that simulates a side-effects run: writes a file into
    the artifact outdir and returns a side-effects-shaped envelope with `items`."""
    async def _runner(*, tool_manager, code, timeout_s, workdir, outdir, exec_id, exec_runtime, logger):
        art = artifact_outdir_for(outdir)
        rel = "turn_test/files/hello.txt"
        p = art / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hi", encoding="utf-8")
        return {
            "ok": ok,
            "user_out_tail": "ran ok",
            "user_error_lines": "" if ok else "boom",
            "user_tracebacks": "",
            "items": [{
                "artifact_id": "hello",
                "artifact_kind": "file",
                "summary": "created via side-effects",
                "output": {
                    "type": "file",
                    "path": rel,
                    "filename": "hello.txt",
                    "mime": "text/plain",
                    "text": "hi",
                    "size_bytes": 2,
                },
            }],
            "workspace_diff": {"created": [{"path": rel, "size": 2.0, "mtime": 1.0}],
                               "modified": [], "deleted": []},
        }
    return _runner


# ── run_code_and_host ────────────────────────────────────────────────────────

def test_run_code_and_host_hosts_created_file_and_returns_refs(tmp_path: Path) -> None:
    mod = _code_exec_module()
    hosting = _FakeHosting()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(), hosting=hosting)

    result = asyncio.run(mod.run_code_and_host("open('hello.txt','w').write('hi')", ctx=ctx))

    assert result["ok"] is True
    assert result["stdout"] == "ran ok"
    assert result["files"], "expected the created file to be hosted"
    ref = result["files"][0]
    assert ref["filename"] == "hello.txt"
    assert ref["mime"] == "text/plain"
    assert ref["rn"] == "rn:hello.txt"
    assert ref["hosted_uri"].endswith("hello.txt")
    # the React-style downloadable link is surfaced so the model can hand it to the user
    assert ref["logical_path"] == "fi:conv_conv-1.turn_test.files/hello.txt"
    # the file was passed to hosting as a file artifact, and delivered to chat
    assert hosting.hosted_files and hosting.hosted_files[0]["output"]["path"] == "turn_test/files/hello.txt"
    assert hosting.emitted_files is not None
    # the tool report cites the downloadable link (not just the rn)
    from types import SimpleNamespace as _NS  # noqa: F401 (keep offline-light)
    report = _code_exec_tool_module()._format_result(result)
    assert "link=fi:conv_conv-1.turn_test.files/hello.txt" in report


def test_run_code_and_host_maps_stderr_on_failure(tmp_path: Path) -> None:
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(ok=False))
    result = asyncio.run(mod.run_code_and_host("x = 1", ctx=ctx))
    assert result["ok"] is False
    assert "boom" in result["stderr"]


def test_run_code_and_host_disabled_is_clean_error(tmp_path: Path) -> None:
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(), enabled=False)
    result = asyncio.run(mod.run_code_and_host("open('x','w')", ctx=ctx))
    assert result["ok"] is False
    assert result["files"] == []
    assert result["error"] == "code_exec_disabled"


def test_run_code_and_host_no_scope_fails_open(tmp_path: Path) -> None:
    mod = _code_exec_module()
    # No ctx passed and no active scope -> clean error, never raises.
    result = asyncio.run(mod.run_code_and_host("open('x','w')"))
    assert result["ok"] is False
    assert result["error"] == "code_exec_disabled"


def test_run_code_and_host_survives_exec_runner_exception(tmp_path: Path) -> None:
    mod = _code_exec_module()

    async def _boom(**_kwargs):
        raise RuntimeError("sandbox down")

    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_boom)
    result = asyncio.run(mod.run_code_and_host("x = 1", ctx=ctx))
    assert result["ok"] is False
    assert "sandbox down" in result["stderr"]


# ── live code-exec widget stream ─────────────────────────────────────────────

def test_run_code_and_host_streams_the_exec_widget(tmp_path: Path) -> None:
    """The live code-exec widget: run_code_and_host drives the SAME `code_exec.*`
    subsystem stream React emits — program name + the ORIGINAL code + status
    transitions ending in `done` — keyed by ONE execution_id, so the reusable chat
    renders the exec panel."""
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner())
    captured: list = []

    async def _delta(**kwargs):
        captured.append(kwargs)

    ctx.comm.delta = _delta  # the widget's emit path (== React's comm.delta)
    result = asyncio.run(
        mod.run_code_and_host("# my program\nopen('hello.txt','w').write('hi')", ctx=ctx)
    )
    assert result["ok"] is True

    subtypes = [d.get("sub_type") for d in captured]
    assert "code_exec.program.name" in subtypes
    assert "code_exec.code" in subtypes
    assert "code_exec.status" in subtypes

    exec_ids = {d.get("execution_id") for d in captured if d.get("execution_id")}
    assert len(exec_ids) == 1 and next(iter(exec_ids))

    statuses = [json.loads(d["text"]).get("status") for d in captured if d.get("sub_type") == "code_exec.status"]
    assert "done" in statuses
    code_deltas = [str(d.get("text") or "") for d in captured if d.get("sub_type") == "code_exec.code"]
    assert any("open('hello.txt'" in t for t in code_deltas)


def test_run_code_and_host_widget_terminal_error_on_failure(tmp_path: Path) -> None:
    """A failed exec closes the widget with status=error, not done."""
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(ok=False))
    captured: list = []

    async def _delta(**kwargs):
        captured.append(kwargs)

    ctx.comm.delta = _delta
    asyncio.run(mod.run_code_and_host("x = 1", ctx=ctx))
    statuses = [json.loads(d["text"]).get("status") for d in captured if d.get("sub_type") == "code_exec.status"]
    assert "error" in statuses and "done" not in statuses


# ── error propagation (runtime vs program) ───────────────────────────────────

def test_run_code_and_host_classifies_program_vs_runtime_error(tmp_path: Path) -> None:
    """A non-zero exit is a PROGRAM error (the model's code); a sandbox/runner failure
    is a RUNTIME error (platform, retryable). The classification rides the result so
    the model can react correctly."""
    mod = _code_exec_module()

    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(ok=False))
    prog = asyncio.run(mod.run_code_and_host("boom", ctx=ctx))
    assert prog["ok"] is False
    assert prog["error_kind"] == "program"
    assert prog["error"] == "program_error"

    async def _boom(**_kwargs):
        raise RuntimeError("sandbox down")

    ctx2 = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_boom)
    rt = asyncio.run(mod.run_code_and_host("x = 1", ctx=ctx2))
    assert rt["ok"] is False
    assert rt["error_kind"] == "runtime"
    assert rt["error"] == "sandbox_execution_failed"


def test_run_python_report_distinguishes_error_class() -> None:
    """The model-facing report tells a retryable platform failure apart from a program
    error the model must fix."""
    tool_mod = _code_exec_tool_module()
    prog = tool_mod._format_result(
        {"ok": False, "error": "program_error", "error_kind": "program",
         "error_message": "NameError: x is not defined", "stderr": "NameError: x", "files": []}
    )
    assert "Program error" in prog and "fix the code" in prog
    rt = tool_mod._format_result(
        {"ok": False, "error": "sandbox_execution_failed", "error_kind": "runtime",
         "error_message": "docker: permission denied", "files": []}
    )
    assert "Runtime/sandbox error" in rt and "RETRY" in rt


def test_run_python_tool_description_lists_available_packages() -> None:
    """The tool description carries the sandbox's `AVAILABLE PACKAGES` block (the same
    one the React exec tool surfaces), so the model writes imports that resolve."""
    tool_mod = _code_exec_tool_module()
    tool = tool_mod.build_run_python_tool()
    assert "AVAILABLE PACKAGES" in (tool.description or "")


# ── contract-mode exec (declared output files) ───────────────────────────────

def test_run_python_tool_exposes_contract_signature() -> None:
    """The tool takes the same params as the real exec tool (contract/prog_name/
    timeout_s) plus `code` (which the create_agent loop passes as an argument)."""
    tool_mod = _code_exec_tool_module()
    tool = tool_mod.build_run_python_tool()
    args = set((tool.args or {}).keys())
    assert {"code", "contract", "prog_name", "timeout_s"} <= args


def test_begin_exec_widget_emits_program_name_and_contract(tmp_path: Path) -> None:
    """When the model declares a contract, the widget shows the program name AND the
    contract of files it will produce — the three inputs the widget is built for."""
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner())
    captured: list = []

    async def _delta(**kwargs):
        captured.append(kwargs)

    ctx.comm.delta = _delta
    output_contract = {
        "report": {"type": "file", "filepath": "turn_test/files/report.csv",
                   "mime": "text/csv", "description": "the report", "visibility": "external"},
    }
    asyncio.run(mod._begin_exec_widget(ctx, "exec-1", "print(1)", prog_name="My Prog",
                                       output_contract=output_contract))
    subtypes = [d.get("sub_type") for d in captured]
    assert "code_exec.program.name" in subtypes
    assert "code_exec.code" in subtypes
    assert "code_exec.contract" in subtypes


def test_run_code_and_host_invalid_contract_is_program_error(tmp_path: Path) -> None:
    """A malformed contract is the MODEL's error to fix (program), returned before any
    sandbox runs — never a platform failure."""
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner())
    result = asyncio.run(mod.run_code_and_host("open('x','w')", contract="not-a-list", ctx=ctx))
    assert result["ok"] is False
    assert result["error_kind"] == "program"


# ── code wrapping + artifact conversion ──────────────────────────────────────

def test_wrap_code_targets_hosted_files_namespace() -> None:
    mod = _code_exec_module()
    wrapped = mod._wrap_code("print('x')", turn_id="turn_abc")
    assert "'turn_abc'" in wrapped
    assert "'files'" in wrapped
    assert "chdir" in wrapped
    assert "print('x')" in wrapped


def test_artifacts_from_side_effects_recovers_from_diff(tmp_path: Path) -> None:
    mod = _code_exec_module()
    art = artifact_outdir_for(tmp_path / "out")
    rel = "turn_test/files/data.json"
    p = art / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"a":1}', encoding="utf-8")
    envelope = {"workspace_diff": {"created": [{"path": rel, "size": 7.0, "mtime": 1.0}],
                                   "modified": [], "deleted": []}}
    arts = mod._artifacts_from_side_effects(envelope, outdir=tmp_path / "out")
    assert len(arts) == 1
    assert arts[0]["type"] == "file"
    assert arts[0]["output"]["path"] == rel
    assert arts[0]["output"]["filename"] == "data.json"


# ── code_exec_scope binds/unbinds ────────────────────────────────────────────

def test_code_exec_scope_binds_and_unbinds(tmp_path: Path) -> None:
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner())

    prev = getattr(bundle_tool_context, "_TOOL_SUBSYSTEM", None)

    async def _run():
        assert mod.current_code_exec_context() is None
        async with mod.code_exec_scope(ctx):
            assert mod.current_code_exec_context() is ctx
            assert bundle_tool_context._TOOL_SUBSYSTEM is ctx.tool_subsystem
            assert OUTDIR_CV.get() == str(ctx.outdir)
            assert WORKDIR_CV.get() == str(ctx.workdir)
        # unbound on exit
        assert mod.current_code_exec_context() is None
        assert bundle_tool_context._TOOL_SUBSYSTEM is prev

    asyncio.run(_run())


def test_code_exec_scope_disabled_is_noop(tmp_path: Path) -> None:
    mod = _code_exec_module()
    ctx = _make_ctx(mod, tmp_path=tmp_path, exec_runner=_make_side_effects_runner(), enabled=False)

    async def _run():
        async with mod.code_exec_scope(ctx) as bound:
            # no-op: the handle is never published for a disabled ctx
            assert mod.current_code_exec_context() is None
            assert bound is ctx
        async with mod.code_exec_scope(None):
            assert mod.current_code_exec_context() is None

    asyncio.run(_run())


# ── the run_python tool renders a sensible string ────────────────────────────

def test_run_python_tool_returns_report(tmp_path: Path) -> None:
    ce = _code_exec_module()
    tool_mod = _code_exec_tool_module()
    tool = tool_mod.build_run_python_tool()
    ctx = _make_ctx(ce, tmp_path=tmp_path, exec_runner=_make_side_effects_runner())

    async def _run():
        async with ce.code_exec_scope(ctx):
            return await tool.ainvoke({"code": "open('hello.txt','w').write('hi')"})

    text = asyncio.run(_run())
    assert "Status: success" in text
    assert "hello.txt" in text
    # Cites the downloadable React-style link so the user can fetch the file.
    assert "link=fi:conv_conv-1.turn_test.files/hello.txt" in text


def test_run_python_tool_reports_disabled(tmp_path: Path) -> None:
    tool_mod = _code_exec_tool_module()
    tool = tool_mod.build_run_python_tool()
    # No active scope -> the tool reports a clean error, never raises.
    text = asyncio.run(tool.ainvoke({"code": "print('x')"}))
    assert "Status: error" in text


# ── config ───────────────────────────────────────────────────────────────────

def _bundle_prop_factory(props):
    def _bp(path, default=None):
        cur = props
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
    return _bp


_AGENT_ID = "lg-react"


def _agent_tools_props(code_exec: dict, *, agent_id: str = _AGENT_ID) -> dict:
    """Build the per-agent tools CONNECTION LIST the code now reads at
    `surfaces.as_consumer.agents.<agent_id>.tools`.

    The exec connection is included iff `code_exec.get("enabled", True)` — its
    PRESENCE is the admin ceiling. Its `timeout_s`/`runtime` ride the connection's
    own `code_exec` sub-block. A plain `calc` connection is always present so the
    list is a realistic inventory."""
    tools: list = [{"name": "calc", "kind": "python", "alias": "calc", "allowed": ["calc"]}]
    if code_exec.get("enabled", True):
        conn: dict = {"name": "code_exec", "kind": "python", "alias": "code_exec", "allowed": ["run_python"]}
        sub = {k: v for k, v in code_exec.items() if k in ("timeout_s", "runtime")}
        if sub:
            conn["code_exec"] = sub
        tools.append(conn)
    return {"surfaces": {"as_consumer": {"agents": {agent_id: {"tools": tools}}}}}


def test_read_code_exec_config_defaults_off() -> None:
    mod = _code_exec_module()
    # No runtime_ctx -> no on-board runtime -> exec_runtime resolves to empty (the
    # runtime is NOT hardcoded to docker; it comes from the deployment).
    ep = SimpleNamespace(bundle_prop=lambda path, default=None: default, runtime_ctx=None)
    cfg = mod.read_code_exec_config(ep, _AGENT_ID)
    assert cfg["enabled"] is False
    assert not cfg["exec_runtime"]
    assert cfg["timeout_s"] == mod.CODE_EXEC_TIMEOUT_DEFAULT


def test_read_code_exec_config_is_per_agent() -> None:
    # The block lives under the ACTIVE agent; a different agent id sees no config.
    mod = _code_exec_module()
    props = _agent_tools_props({"enabled": True, "timeout_s": 45})
    ep = SimpleNamespace(
        bundle_prop=_bundle_prop_factory(props),
        runtime_ctx=SimpleNamespace(exec_runtime={"mode": "subprocess"}),
    )
    assert mod.code_exec_enabled(ep, _AGENT_ID) is True
    assert mod.code_exec_enabled(ep, "lg-solution") is False


def test_read_code_exec_config_uses_onboard_runtime() -> None:
    # By default (no `runtime` override) the exec runtime IS the deployment's
    # on-board runtime (runtime_ctx.exec_runtime) — same as the React harness, no
    # hardcoded docker.
    mod = _code_exec_module()
    props = _agent_tools_props({"enabled": True, "timeout_s": 45})
    ep = SimpleNamespace(
        bundle_prop=_bundle_prop_factory(props),
        runtime_ctx=SimpleNamespace(exec_runtime={"mode": "subprocess"}),
    )
    cfg = mod.read_code_exec_config(ep, _AGENT_ID)
    assert cfg["enabled"] is True
    assert cfg["exec_runtime"].get("mode") == "subprocess"
    assert cfg["timeout_s"] == 45
    assert mod.code_exec_enabled(ep, _AGENT_ID) is True


def test_read_code_exec_config_runtime_string_resolves_via_profile_resolver() -> None:
    # A string `runtime` is passed as the PROFILE selector to the same resolver the
    # React harness uses; the resolved spec still derives from the on-board runtime
    # (exact profile semantics are deployment-defined).
    mod = _code_exec_module()
    props = _agent_tools_props({"enabled": True, "runtime": "some_profile"})
    onboard = {"mode": "subprocess"}
    ep = SimpleNamespace(
        bundle_prop=_bundle_prop_factory(props),
        runtime_ctx=SimpleNamespace(exec_runtime=onboard),
    )
    cfg = mod.read_code_exec_config(ep, _AGENT_ID)
    # Resolves without error and stays anchored to the on-board runtime.
    assert isinstance(cfg["exec_runtime"], dict)
    assert cfg["exec_runtime"].get("mode") == "subprocess"
