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
    # the file was passed to hosting as a file artifact, and delivered to chat
    assert hosting.hosted_files and hosting.hosted_files[0]["output"]["path"] == "turn_test/files/hello.txt"
    assert hosting.emitted_files is not None


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
    assert "rn=rn:hello.txt" in text


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


def test_read_code_exec_config_defaults_off() -> None:
    mod = _code_exec_module()
    # No runtime_ctx -> no on-board runtime -> exec_runtime resolves to empty (the
    # runtime is NOT hardcoded to docker; it comes from the deployment).
    ep = SimpleNamespace(bundle_prop=lambda path, default=None: default, runtime_ctx=None)
    cfg = mod.read_code_exec_config(ep)
    assert cfg["enabled"] is False
    assert not cfg["exec_runtime"]
    assert cfg["timeout_s"] == mod.CODE_EXEC_TIMEOUT_DEFAULT


def test_read_code_exec_config_uses_onboard_runtime() -> None:
    # By default (no `tools.code_exec.runtime` override) the exec runtime IS the
    # deployment's on-board runtime (runtime_ctx.exec_runtime) — same as the React
    # harness, no hardcoded docker.
    mod = _code_exec_module()
    props = {"tools": {"code_exec": {"enabled": True, "timeout_s": 45}}}
    ep = SimpleNamespace(
        bundle_prop=_bundle_prop_factory(props),
        runtime_ctx=SimpleNamespace(exec_runtime={"mode": "subprocess"}),
    )
    cfg = mod.read_code_exec_config(ep)
    assert cfg["enabled"] is True
    assert cfg["exec_runtime"].get("mode") == "subprocess"
    assert cfg["timeout_s"] == 45
    assert mod.code_exec_enabled(ep) is True


def test_read_code_exec_config_runtime_string_resolves_via_profile_resolver() -> None:
    # A string `runtime` is passed as the PROFILE selector to the same resolver the
    # React harness uses; the resolved spec still derives from the on-board runtime
    # (exact profile semantics are deployment-defined).
    mod = _code_exec_module()
    props = {"tools": {"code_exec": {"enabled": True, "runtime": "some_profile"}}}
    onboard = {"mode": "subprocess"}
    ep = SimpleNamespace(
        bundle_prop=_bundle_prop_factory(props),
        runtime_ctx=SimpleNamespace(exec_runtime=onboard),
    )
    cfg = mod.read_code_exec_config(ep)
    # Resolves without error and stays anchored to the on-board runtime.
    assert isinstance(cfg["exec_runtime"], dict)
    assert cfg["exec_runtime"].get("mode") == "subprocess"
