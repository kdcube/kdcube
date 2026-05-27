import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.external import docker as docker_runtime
from kdcube_ai_app.apps.chat.sdk.runtime import iso_runtime
from kdcube_ai_app.apps.chat.sdk.runtime.diagnose import collect_exec_diagnostics, merge_infra_logs
from kdcube_ai_app.infra.rendering import shared_browser


def test_exec_limit_bytes_supports_units_and_disable():
    assert iso_runtime._as_limit_bytes("100m") == 100 * 1024 * 1024
    assert iso_runtime._as_limit_bytes("1.5gb") == int(1.5 * 1024 * 1024 * 1024)
    assert iso_runtime._as_limit_bytes("disabled", default=123) is None
    assert iso_runtime._as_limit_bytes("", default=123) == 123


def test_drop_executor_identity_clears_root_supplementary_group(monkeypatch):
    calls = []

    monkeypatch.setattr(iso_runtime.os, "setgroups", lambda groups: calls.append(("setgroups", groups)))
    monkeypatch.setattr(iso_runtime.os, "umask", lambda mask: calls.append(("umask", mask)))
    monkeypatch.setattr(iso_runtime.os, "setgid", lambda gid: calls.append(("setgid", gid)))
    monkeypatch.setattr(iso_runtime.os, "setuid", lambda uid: calls.append(("setuid", uid)))

    iso_runtime._drop_executor_identity(executor_uid=1001, executor_gid=1000)

    assert calls == [
        ("setgroups", [1000]),
        ("umask", 0o002),
        ("setgid", 1000),
        ("setuid", 1001),
    ]


def test_effective_capabilities_parser_reads_cap_eff():
    assert iso_runtime._effective_capabilities_from_proc_status("Name:\tpython\nCapEff:\t0000000000000000\n") == 0
    assert iso_runtime._effective_capabilities_from_proc_status("CapEff:\t0000000000000020\n") == 0x20
    assert iso_runtime._effective_capabilities_from_proc_status("Name:\tpython\n") is None


def test_assert_no_effective_capabilities_rejects_retained_caps(monkeypatch, tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nCapEff:\t0000000000000020\n", encoding="utf-8")

    monkeypatch.setattr(iso_runtime.sys, "platform", "linux")
    monkeypatch.setattr(iso_runtime.pathlib, "Path", lambda value: status if value == "/proc/self/status" else pathlib.Path(value))

    with pytest.raises(PermissionError, match="retained effective capabilities"):
        iso_runtime._assert_no_effective_capabilities()


def test_workspace_limit_violation_detects_new_large_file(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    existing = root / "existing.bin"
    existing.write_bytes(b"x" * 16)
    baseline = iso_runtime._snapshot_workspace_file_sizes([root])

    generated = root / "generated.bin"
    generated.write_bytes(b"x" * 128)
    current = iso_runtime._snapshot_workspace_file_sizes([root])

    violation = iso_runtime._workspace_limit_violation(
        baseline=baseline,
        current=current,
        max_file_bytes=64,
        max_exec_workspace_delta_bytes=1024,
    )

    assert violation is not None
    assert violation["error"] == "file_size_limit"
    assert violation["offending_path"].endswith("generated.bin")


def test_workspace_limit_violation_ignores_unchanged_baseline_file(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    existing = root / "existing.bin"
    existing.write_bytes(b"x" * 128)
    baseline = iso_runtime._snapshot_workspace_file_sizes([root])
    current = iso_runtime._snapshot_workspace_file_sizes([root])

    assert iso_runtime._workspace_limit_violation(
        baseline=baseline,
        current=current,
        max_file_bytes=64,
        max_exec_workspace_delta_bytes=1024,
    ) is None


def test_workspace_roots_include_executor_log_dirs(tmp_path):
    outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    logdir = tmp_path / "logs" / "executor"
    alt_logdir = tmp_path / "executor-log"
    for path in (outdir, workdir, logdir, alt_logdir):
        path.mkdir(parents=True)

    roots = iso_runtime._workspace_roots(
        env={
            "OUTPUT_DIR": str(outdir),
            "WORKDIR": str(workdir),
            "LOG_DIR": str(logdir),
            "EXECUTOR_LOG_DIR": str(alt_logdir),
        },
        cwd=workdir,
        outdir=outdir,
    )

    root_paths = {path.resolve() for path in roots}
    assert logdir.resolve() in root_paths
    assert alt_logdir.resolve() in root_paths


def test_workspace_limit_violation_detects_turn_total(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    existing = root / "existing.bin"
    existing.write_bytes(b"x" * 40)
    baseline = iso_runtime._snapshot_workspace_file_sizes([root])

    generated = root / "generated.bin"
    generated.write_bytes(b"x" * 20)
    current = iso_runtime._snapshot_workspace_file_sizes([root])

    violation = iso_runtime._workspace_limit_violation(
        baseline=baseline,
        current=current,
        max_file_bytes=64,
        max_exec_workspace_delta_bytes=1024,
        max_workspace_bytes=50,
    )

    assert violation is not None
    assert violation["error"] == "workspace_size_limit"
    assert violation["size_bytes"] == 60
    assert violation["offending_paths"][0].endswith("generated.bin")


def test_workspace_limit_violation_detects_log_dir_growth(tmp_path):
    outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    logdir = tmp_path / "logs" / "executor"
    for path in (outdir, workdir, logdir):
        path.mkdir(parents=True)

    roots = iso_runtime._workspace_roots(
        env={
            "OUTPUT_DIR": str(outdir),
            "WORKDIR": str(workdir),
            "LOG_DIR": str(logdir),
        },
        cwd=workdir,
        outdir=outdir,
    )
    baseline = iso_runtime._snapshot_workspace_file_sizes(roots)
    (logdir / "bulk.bin").write_bytes(b"x" * 128)
    current = iso_runtime._snapshot_workspace_file_sizes(roots)

    violation = iso_runtime._workspace_limit_violation(
        baseline=baseline,
        current=current,
        max_file_bytes=1024,
        max_exec_workspace_delta_bytes=64,
    )

    assert violation is not None
    assert violation["error"] == "exec_workspace_delta_limit"
    assert any(path.endswith("bulk.bin") for path in violation["offending_paths"])


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._shutdown = asyncio.Event()

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15
        self._shutdown.set()

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9
        self._shutdown.set()

    async def communicate(self):
        if self._shutdown.is_set():
            return (b"", b"")
        await asyncio.Event().wait()


class _FakeCompletedProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


class _FakeRunningContainerProc:
    def __init__(self, stdout=b"", stderr=b""):
        self.returncode = None
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        self.returncode = 0
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


def test_docker_argv_grants_network_namespace_capability(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()

    argv = docker_runtime._build_docker_argv(
        image="py-code-exec:latest",
        host_workdir=workdir,
        host_outdir=outdir,
        network_mode="host",
    )

    assert "--cap-add=SYS_ADMIN" in argv
    assert "--network" in argv
    assert "host" in argv
    assert "--cap-add=NET_ADMIN" not in argv
    assert "seccomp=unconfined" not in argv
    assert "apparmor=unconfined" not in argv


def test_split_executor_argv_is_networkless_and_does_not_mount_supervisor_data(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    artifact_outdir = outdir / "workdir"
    executor_logdir = outdir / "logs" / "executor"
    workdir.mkdir()
    outdir.mkdir()

    argv = docker_runtime._build_split_executor_argv(
        image="py-code-exec:latest",
        name="exec-test",
        socket_volume="socket-volume",
        host_workdir=workdir,
        host_outdir=outdir,
        host_artifact_outdir=artifact_outdir,
        host_logdir=executor_logdir,
        extra_env={
            "RUNTIME_GLOBALS_JSON": "{}",
            "SUPERVISOR_AUTH_TOKEN": "secret",
            "EXECUTION_ID": "exec-1",
            "LOG_FILE_PREFIX": "executor-entry",
            "PLAYWRIGHT_BROWSERS_PATH": "/opt/ms-playwright",
        },
        timeout_s=60,
    )

    assert "--network" in argv
    assert "none" in argv
    assert "--cap-drop=ALL" in argv
    assert "--cap-add=CHOWN" in argv
    assert "--cap-add=SETUID" in argv
    assert "--cap-add=SETGID" in argv
    assert "--cap-add=FOWNER" in argv
    assert "--cap-add=KILL" in argv
    assert "--cap-add=SYS_ADMIN" not in argv
    assert "--cap-add=NET_ADMIN" not in argv
    assert "--read-only" in argv
    assert "no-new-privileges" in argv
    assert any(item == f"{artifact_outdir}:/workspace/out:rw" for item in argv)
    assert not any(item == f"{outdir}:/workspace/out:rw" for item in argv)
    assert any(item == f"{executor_logdir}:/workspace/logs/executor:rw" for item in argv)
    assert not any(item.endswith(":/workspace/logs:rw") for item in argv)
    assert all("/workspace/runtime-out" not in item for item in argv)
    assert all("/workspace/logs/supervisor" not in item for item in argv)
    assert any(item == "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" for item in argv)
    assert any(item == "EXEC_BLOCK_AF_ALG=1" for item in argv)
    assert any(item == "EXEC_REQUIRE_AF_ALG_BLOCK=1" for item in argv)
    assert any(item == "KDCUBE_ARTIFACT_OUTPUT_DIR=/workspace/out" for item in argv)
    assert any(item == "LOG_FILE_PREFIX=executor-entry" for item in argv)
    assert all("/tmp/kdcube-supervisor/bundles" not in item for item in argv)
    assert all("KDCUBE_RUNTIME_SECRETS_YAML_B64=" not in item for item in argv)


def test_split_bind_source_prepare_argv_is_networkless_and_scoped(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    artifact_outdir = outdir / "workdir"
    executor_logdir = outdir / "logs" / "executor"
    supervisor_logdir = outdir / "logs" / "supervisor"

    argv = docker_runtime._build_split_bind_source_prepare_argv(
        image="py-code-exec:latest",
        host_paths=[workdir, outdir, artifact_outdir, executor_logdir, supervisor_logdir],
    )

    assert "--network" in argv
    assert "none" in argv
    assert "--cap-drop=ALL" in argv
    assert "--cap-add=CHOWN" in argv
    assert "--cap-add=FOWNER" in argv
    assert "--cap-add=SYS_ADMIN" not in argv
    assert "--cap-add=NET_ADMIN" not in argv
    assert "--read-only" in argv
    assert "no-new-privileges" in argv
    assert "--entrypoint" in argv
    assert "/bin/sh" in argv
    assert any(item == f"{workdir}:/kdcube-bind-src-0:rw" for item in argv)
    assert any(item == f"{outdir}:/kdcube-bind-src-1:rw" for item in argv)
    assert any(item == f"{artifact_outdir}:/kdcube-bind-src-2:rw" for item in argv)
    assert any(item == f"{executor_logdir}:/kdcube-bind-src-3:rw" for item in argv)
    assert any(item == f"{supervisor_logdir}:/kdcube-bind-src-4:rw" for item in argv)
    assert any("chmod -R a+rwX" in item for item in argv)


@pytest.mark.asyncio
async def test_prepare_split_host_bind_sources_uses_docker_control(tmp_path, monkeypatch):
    calls = []

    async def _fake_docker_control(args, *, timeout_s=10):
        calls.append((args, timeout_s))
        return 0, "", ""

    monkeypatch.setattr(docker_runtime, "_docker_control", _fake_docker_control)

    ok, summary = await docker_runtime._prepare_split_host_bind_sources_with_docker(
        image="py-code-exec:latest",
        host_paths=[tmp_path / "work", tmp_path / "out" / "logs" / "executor"],
        log=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )

    assert ok is True
    assert summary == ""
    assert calls
    argv, timeout_s = calls[0]
    assert timeout_s == 30
    assert argv[:3] == ["docker", "run", "--rm"]
    assert any(item.endswith(":/kdcube-bind-src-1:rw") for item in argv)


def test_split_supervisor_argv_uses_writable_home_and_playwright_path(tmp_path):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    artifact_outdir = outdir / "workdir"
    workdir.mkdir()
    outdir.mkdir()

    argv = docker_runtime._build_split_supervisor_argv(
        image="py-code-exec:latest",
        name="supervisor-test",
        socket_volume="socket-volume",
        host_workdir=workdir,
        host_outdir=outdir,
        host_runtime_outdir=outdir,
        host_artifact_outdir=artifact_outdir,
        extra_env={
            "HOME": "/tmp/kdcube-supervisor/home",
            "LOG_DIR": "/workspace/runtime-out/logs/supervisor",
            "PLAYWRIGHT_BROWSERS_PATH": "/opt/ms-playwright",
        },
    )

    assert "--read-only" in argv
    assert any(item == f"{artifact_outdir}:/workspace/out:rw" for item in argv)
    assert any(item == f"{outdir}:/workspace/runtime-out:rw" for item in argv)
    assert any(item == "HOME=/tmp/kdcube-supervisor/home" for item in argv)
    assert any(item == "LOG_DIR=/workspace/runtime-out/logs/supervisor" for item in argv)
    assert any(item == "KDCUBE_RUNTIME_OUTPUT_DIR=/workspace/runtime-out" for item in argv)
    assert any(item == "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" for item in argv)


def test_split_runtime_logs_are_merged_for_requestor_feedback(tmp_path):
    log_dir = tmp_path / "out" / "logs"
    executor_log_dir = log_dir / "executor"
    executor_log_dir.mkdir(parents=True)
    exec_id = "exec-split-logs"
    header = f"===== EXECUTION {exec_id} START 2026-04-30 10:00:00 =====\n"
    (log_dir / "docker.err.log").write_text(
        header
        + "[split supervisor stderr]\n"
        + "supervisor tool failed loudly\n"
        + "[split executor stderr]\n"
        + "executor user code failed loudly\n",
        encoding="utf-8",
    )
    (log_dir / "runtime.err.log").write_text(
        header + "runtime wrapped traceback line\n",
        encoding="utf-8",
    )
    (executor_log_dir / "executor-entry.log").write_text(
        header + "executor entry bootstrap line\n",
        encoding="utf-8",
    )
    (executor_log_dir / "executor.log").write_text(
        header + "dropped executor infra line\n",
        encoding="utf-8",
    )
    (executor_log_dir / "user.log").write_text(
        header + "program print should stay out of infra\n",
        encoding="utf-8",
    )

    merged = merge_infra_logs(log_dir=log_dir, exec_id=exec_id, max_chars=4000)

    assert "supervisor tool failed loudly" in merged
    assert "executor user code failed loudly" in merged
    assert "runtime wrapped traceback line" in merged
    assert "executor entry bootstrap line" in merged
    assert "dropped executor infra line" in merged
    assert "program print should stay out of infra" not in merged
    assert (log_dir / "infra.log").exists()


def test_split_executor_user_log_is_read_separately_from_infra(tmp_path):
    sandbox_root = tmp_path / "sandbox"
    outdir = sandbox_root / "out"
    log_dir = outdir / "logs"
    executor_log_dir = log_dir / "executor"
    workdir = sandbox_root / "work"
    executor_log_dir.mkdir(parents=True)
    workdir.mkdir(parents=True)
    (workdir / "main.py").write_text("# === USER CODE START ===\nprint('hello')\n", encoding="utf-8")

    exec_id = "exec-split-user-log"
    header = f"===== EXECUTION {exec_id} START 2026-04-30 10:00:00 =====\n"
    (executor_log_dir / "executor-entry.log").write_text(
        header + "entry process diagnostic\n",
        encoding="utf-8",
    )
    (executor_log_dir / "executor.log").write_text(
        header + "executor diagnostic\n",
        encoding="utf-8",
    )
    (executor_log_dir / "user.log").write_text(
        header + "program output visible to agent\n",
        encoding="utf-8",
    )

    diagnostics = collect_exec_diagnostics(
        sandbox_root=sandbox_root,
        outdir=outdir,
        exec_id=exec_id,
        tree_max_chars=4000,
        log_max_chars=4000,
    )

    assert "program output visible to agent" in diagnostics["info_log"]
    assert "entry process diagnostic" in diagnostics["runtime_error_log"]
    assert "executor diagnostic" in diagnostics["runtime_error_log"]
    assert "program output visible to agent" not in diagnostics["runtime_error_log"]


def test_executor_payload_strips_privileged_runtime_globals():
    from kdcube_ai_app.apps.chat.sdk.runtime.isolated.executor_payload import build_executor_runtime_globals

    payload = build_executor_runtime_globals(
        {
            "PORTABLE_SPEC_JSON": "secret-ish spec",
            "COMM_SPEC": {"x": 1},
            "TOOL_MODULE_FILES": {"web_tools": "/bundle/tool.py"},
            "BUNDLE_STORAGE_DIR": "/bundle-storage/demo",
            "TOOL_ALIAS_MAP": {"web_tools": "dyn_web_tools"},
            "RESULT_FILENAME": "result.json",
        }
    )

    assert "PORTABLE_SPEC_JSON" not in payload
    assert "COMM_SPEC" not in payload
    assert "TOOL_MODULE_FILES" not in payload
    assert "BUNDLE_STORAGE_DIR" not in payload
    assert payload["TOOL_ALIAS_MAP"] == {"web_tools": "dyn_web_tools"}
    assert payload["RESULT_FILENAME"] == "result.json"


@pytest.mark.asyncio
async def test_run_py_in_docker_terminates_child_process_when_cancelled(tmp_path, monkeypatch):
    fake_proc = _FakeProc()
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_proc

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", lambda path: path)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    task = asyncio.create_task(
        docker_runtime.run_py_in_docker(
            workdir=workdir,
            outdir=outdir,
            runtime_globals={"EXECUTION_ID": "exec-1"},
            tool_module_names=[],
            logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        )
    )

    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_proc.terminate_calls == 1
    assert fake_proc.kill_calls == 0


@pytest.mark.asyncio
async def test_run_py_in_docker_fails_fast_when_translated_mount_is_missing(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    bundle_root = tmp_path / "bundle"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    bundle_root.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (bundle_root / "tools").mkdir(parents=True, exist_ok=True)
    (bundle_root / "tools" / "react_tools.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: True)
    monkeypatch.setattr(docker_runtime, "_can_preflight_translated_host_path", lambda path: True)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    def _fake_translate(path):
        p = path.resolve()
        if p == bundle_root.resolve():
            return tmp_path / "missing-host-bundle"
        return p

    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", _fake_translate)

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={
            "EXECUTION_ID": "exec-2",
            "TOOL_MODULE_FILES": {
                "react_tools": str((bundle_root / "tools" / "react_tools.py").resolve()),
            },
        },
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        bundle_root=bundle_root,
    )

    assert result["ok"] is False
    assert result["returncode"] == 127
    assert result["error"].startswith("host_mount_error:")
    assert "bundle root" in result["stderr_tail"] or "react_tools" in result["stderr_tail"]


@pytest.mark.asyncio
async def test_run_py_in_docker_skips_opaque_host_path_preflight_inside_proc(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    bundle_root = tmp_path / "bundle"
    bundle_storage_dir = tmp_path / "bundle-storage" / "tenant" / "project" / "kdcube.copilot@2026-04-03-19-05"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_storage_dir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (bundle_root / "tools").mkdir(parents=True, exist_ok=True)
    (bundle_root / "tools" / "react_tools.py").write_text("x = 1\n", encoding="utf-8")
    (bundle_storage_dir / "index.json").write_text("{}", encoding="utf-8")

    calls = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeCompletedProc(returncode=0)

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: True)
    monkeypatch.setattr(docker_runtime, "_can_preflight_translated_host_path", lambda path: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    def _fake_translate(path):
        p = path.resolve()
        if p == workdir.resolve():
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/exec-workspace/ctx/work")
        if p == outdir.resolve():
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/exec-workspace/ctx/out")
        if p == bundle_root.resolve():
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/bundles/kdcube.copilot@2026-04-03-19-05")
        if p == bundle_storage_dir.resolve():
            return pathlib.Path(
                "/Users/elenaviter/.kdcube/kdcube-runtime/data/bundle-storage/"
                "tenant/project/kdcube.copilot@2026-04-03-19-05"
            )
        return p

    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", _fake_translate)

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={
            "EXECUTION_ID": "exec-opaque-host",
            "TOOL_MODULE_FILES": {
                "react_tools": str((bundle_root / "tools" / "react_tools.py").resolve()),
            },
            "BUNDLE_STORAGE_DIR": str(bundle_storage_dir.resolve()),
        },
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        bundle_root=bundle_root,
    )

    assert result["ok"] is True
    assert calls, "docker run should proceed when translated host paths are opaque from proc"
    argv = calls[0][0]
    private_bundle_root = (docker_runtime._SUPERVISOR_PRIVATE_BUNDLES_ROOT / bundle_root.name).resolve()
    private_bundle_storage = docker_runtime._private_mount_path(
        bundle_storage_dir.resolve(),
        docker_runtime._SUPERVISOR_PRIVATE_BUNDLE_STORAGE_ROOT,
    )
    assert any(arg.endswith(f":{private_bundle_root}:ro") for arg in argv)
    assert any(
        arg == f"BUNDLE_STORAGE_DIR={private_bundle_storage}"
        for arg in argv
    )
    assert any(
        arg.endswith(f":{private_bundle_storage}:ro")
        for arg in argv
    )


@pytest.mark.asyncio
async def test_run_py_in_docker_mounts_local_kdcube_storage_rw(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    proc_storage_dir = pathlib.Path("/kdcube-storage")
    host_storage_dir = tmp_path / "host-kdcube-storage"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    host_storage_dir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    calls = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeCompletedProc(returncode=0)

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: True)
    monkeypatch.setattr(docker_runtime, "_can_preflight_translated_host_path", lambda path: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    def _fake_translate(path):
        p = pathlib.Path(path)
        if p == workdir.resolve():
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/exec-workspace/ctx/work")
        if p == outdir.resolve():
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/exec-workspace/ctx/out")
        if p == proc_storage_dir:
            return host_storage_dir
        return p

    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", _fake_translate)

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={
            "EXECUTION_ID": "exec-kdcube-storage",
            "PORTABLE_SPEC_JSON": json.dumps(
                {"accounting_storage": {"storage_path": "file:///kdcube-storage"}}
            ),
        },
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )

    assert result["ok"] is True
    assert calls, "docker run should proceed with a writable KDCUBE storage mount"
    argv = calls[0][0]
    private_storage_dir = docker_runtime._private_mount_path(
        proc_storage_dir,
        docker_runtime._SUPERVISOR_PRIVATE_KDCUBE_STORAGE_ROOT,
    )
    assert any(arg.endswith(f":{private_storage_dir}:rw") for arg in argv)
    assert any(arg == f"KDCUBE_STORAGE_PATH=file://{private_storage_dir}" for arg in argv)
    runtime_globals_arg = next(arg for arg in argv if arg.startswith("RUNTIME_GLOBALS_JSON="))
    runtime_globals = json.loads(runtime_globals_arg.split("=", 1)[1])
    portable_spec = json.loads(runtime_globals["PORTABLE_SPEC_JSON"])
    assert portable_spec["accounting_storage"]["storage_path"] == f"file://{private_storage_dir}"


@pytest.mark.asyncio
async def test_split_docker_precreates_supervisor_logdir_before_host_bind_prep(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    prepare_calls = []
    docker_runs = []

    async def _fake_prepare_split_host_bind_sources_with_docker(*, image, host_paths, log):
        del image, log
        prepare_calls.append(tuple(host_paths))
        assert (outdir / "logs" / "executor").is_dir()
        assert (outdir / "logs" / "supervisor").is_dir()
        return True, ""

    async def _fake_docker_control(args, *, timeout_s=10):
        del timeout_s
        if args[:3] == ["docker", "volume", "create"]:
            return 0, "socket-volume\n", ""
        if args[:3] == ["docker", "volume", "rm"]:
            return 0, "", ""
        if args[:2] == ["docker", "stop"]:
            return 0, "", ""
        return 0, "", ""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del kwargs
        docker_runs.append(args)
        if len(docker_runs) == 1:
            return _FakeRunningContainerProc()
        return _FakeCompletedProc(returncode=0)

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "_prepare_split_host_bind_sources_with_docker", _fake_prepare_split_host_bind_sources_with_docker)
    monkeypatch.setattr(docker_runtime, "_docker_control", _fake_docker_control)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: True)
    monkeypatch.setattr(docker_runtime, "_can_preflight_translated_host_path", lambda path: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    def _fake_translate(path):
        p = pathlib.Path(path).resolve()
        if p == workdir.resolve():
            return pathlib.Path("/opt/kdcube/efs/exec-workspace/test/work")
        if p == outdir.resolve():
            return pathlib.Path("/opt/kdcube/efs/exec-workspace/test/out")
        return p

    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", _fake_translate)

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={"EXECUTION_ID": "exec-split-supervisor-logdir"},
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        container_strategy="split",
        image="py-code-exec:latest",
    )

    assert result["ok"] is True
    assert prepare_calls, "opaque translated host paths should still be prepared via Docker"
    assert pathlib.Path("/opt/kdcube/efs/exec-workspace/test/out/logs/supervisor") in prepare_calls[0]
    assert docker_runs, "split supervisor/executor docker runs should be attempted"
    supervisor_run, executor_run = docker_runs
    assert any(item == "/opt/kdcube/efs/exec-workspace/test/out:/workspace/runtime-out:rw" for item in supervisor_run)
    assert all("/workspace/runtime-out" not in item for item in executor_run)
    assert all("/logs/supervisor" not in item for item in executor_run)


@pytest.mark.asyncio
async def test_run_py_in_docker_returns_runtime_failure_on_timeout(tmp_path, monkeypatch):
    fake_proc = _FakeProc()
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_proc

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", lambda path: path)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={"EXECUTION_ID": "exec-timeout"},
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        timeout_s=1,
    )

    assert result["ok"] is False
    assert result["returncode"] == 124
    assert result["error"] == "timeout"
    assert result["error_summary"] == "Timeout after 1s"


@pytest.mark.asyncio
async def test_run_py_in_docker_merges_child_runtime_logs_on_failure(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir(parents=True, exist_ok=True)
    (outdir / "logs").mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (outdir / "logs" / "runtime.err.log").write_text(
        "[stderr]\nTraceback (most recent call last):\nNameError: name 'web_tools' is not defined\n",
        encoding="utf-8",
    )

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeCompletedProc(returncode=1, stderr=b"entrypoint failed\n")

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", lambda path: path)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    result = await docker_runtime.run_py_in_docker(
        workdir=workdir,
        outdir=outdir,
        runtime_globals={"EXECUTION_ID": "exec-runtime-log-failure"},
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )

    assert result["ok"] is False
    assert "entrypoint failed" in result["stderr_tail"]
    assert "NameError: name 'web_tools' is not defined" in result["stderr_tail"]
    assert result["error_summary"] == "NameError: name 'web_tools' is not defined"


@pytest.mark.asyncio
async def test_iso_runtime_terminates_child_process_when_cancelled(tmp_path, monkeypatch):
    fake_proc = _FakeProc()

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_proc

    monkeypatch.setattr(iso_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    task = asyncio.create_task(
        iso_runtime._run_subprocess(
            entry_path=tmp_path / "entry.py",
            cwd=tmp_path,
            env={"EXECUTION_ID": "exec-2"},
            timeout_s=60,
            outdir=tmp_path / "out",
            allow_network=True,
            exec_id="exec-2",
        )
    )

    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake_proc.terminate_calls == 1
    assert fake_proc.kill_calls == 0


@pytest.mark.asyncio
async def test_iso_runtime_preflight_blocks_when_workspace_is_already_over_limit(tmp_path, monkeypatch):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "existing.bin").write_bytes(b"x" * 16)

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        raise AssertionError("subprocess should not start when workspace is already over quota")

    monkeypatch.setattr(iso_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await iso_runtime._run_subprocess(
        entry_path=tmp_path / "entry.py",
        cwd=outdir,
        env={
            "EXECUTION_ID": "exec-preflight-quota",
            "EXEC_MAX_FILE_BYTES": "100",
            "EXEC_MAX_WORKSPACE_DELTA_BYTES": "100",
            "EXEC_MAX_WORKSPACE_BYTES": "3",
            "EXEC_WORKSPACE_MONITOR_INTERVAL_S": "0.01",
        },
        timeout_s=60,
        outdir=outdir,
        allow_network=True,
        exec_id="exec-preflight-quota",
    )

    assert result["ok"] is False
    assert result["returncode"] == 153
    assert result["error"] == "workspace_size_limit"
    assert "workspace total exceeds max size" in result["error_summary"]
    assert "workspace total exceeds max size" in (outdir / "logs" / "runtime.err.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_iso_runtime_failure_tail_includes_stdout_diagnostics(tmp_path, monkeypatch):
    outdir = tmp_path / "out"
    outdir.mkdir()

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeCompletedProc(
            returncode=1,
            stdout=b"diagnostic before crash\nValueError: bad input\n",
            stderr=b"",
        )

    monkeypatch.setattr(iso_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await iso_runtime._run_subprocess(
        entry_path=tmp_path / "entry.py",
        cwd=outdir,
        env={"EXECUTION_ID": "exec-stdout-error"},
        timeout_s=60,
        outdir=outdir,
        allow_network=True,
        exec_id="exec-stdout-error",
    )

    assert result["ok"] is False
    assert "diagnostic before crash" in result["stderr_tail"]
    assert result["error_summary"] == "ValueError: bad input"


@pytest.mark.asyncio
async def test_iso_runtime_subprocess_temp_dirs_use_output_workspace(tmp_path, monkeypatch):
    outdir = tmp_path / "out"
    outdir.mkdir()
    captured_env = {}

    async def _fake_create_subprocess_exec(*_args, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return _FakeCompletedProc(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(iso_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    env = {
        "EXECUTION_ID": "exec-temp",
        "TMPDIR": "/tmp/full-overlay",
        "XDG_CACHE_HOME": "/tmp/cache",
        "MPLCONFIGDIR": "/tmp/mpl",
        "FONTCONFIG_PATH": "/tmp/fontconfig",
    }
    result = await iso_runtime._run_subprocess(
        entry_path=tmp_path / "entry.py",
        cwd=outdir,
        env=env,
        timeout_s=60,
        outdir=outdir,
        allow_network=True,
        exec_id="exec-temp",
    )

    assert result["ok"] is True
    runtime_tmp = outdir / "_runtime_tmp"
    assert pathlib.Path(captured_env["TMPDIR"]) == runtime_tmp / "tmp"
    assert pathlib.Path(captured_env["TMP"]).samefile(runtime_tmp / "tmp")
    assert pathlib.Path(captured_env["TEMP"]).samefile(runtime_tmp / "tmp")
    assert pathlib.Path(captured_env["XDG_CACHE_HOME"]) == runtime_tmp / "cache"
    assert pathlib.Path(captured_env["MPLCONFIGDIR"]) == runtime_tmp / "mplconfig"
    assert pathlib.Path(captured_env["FONTCONFIG_PATH"]) == runtime_tmp / "fontconfig"


def test_shared_browser_auto_install_is_only_for_missing_browser_errors():
    assert shared_browser._looks_like_missing_browser_error(
        RuntimeError("Executable doesn't exist at /opt/ms-playwright/chromium/headless_shell")
    )
    assert not shared_browser._looks_like_missing_browser_error(
        RuntimeError("BrowserType.launch: ENOSPC: no space left on device, mkdtemp '/tmp/playwright-artifacts-abc'")
    )
