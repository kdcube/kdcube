import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.external import docker as docker_runtime
from kdcube_ai_app.apps.chat.sdk.runtime import bubblewrap, iso_runtime


def test_exec_limit_bytes_supports_units_and_disable():
    assert iso_runtime._as_limit_bytes("100m") == 100 * 1024 * 1024
    assert iso_runtime._as_limit_bytes("1.5gb") == int(1.5 * 1024 * 1024 * 1024)
    assert iso_runtime._as_limit_bytes("disabled", default=123) is None
    assert iso_runtime._as_limit_bytes("", default=123) == 123


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
        max_workspace_bytes=1024,
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
        max_workspace_bytes=1024,
    ) is None


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


def test_docker_argv_grants_bwrap_namespace_capabilities(tmp_path):
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
    assert "--cap-add=NET_ADMIN" in argv
    assert "--security-opt" in argv
    assert "seccomp=unconfined" in argv


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
async def test_iso_runtime_bubblewrap_mounts_declared_workdir_not_output_dir(tmp_path, monkeypatch):
    workdir = tmp_path / "work"
    outdir = tmp_path / "out"
    workdir.mkdir()
    outdir.mkdir()
    entry_path = workdir / "main.py"
    entry_path.write_text("print('ok')\n", encoding="utf-8")

    captured = {}

    def _fake_mk_bwrap_argv(**kwargs):
        captured.update(kwargs)
        return ["python", "-c", "print('ok')"]

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeCompletedProc(returncode=0, stdout=b"ok\n")

    monkeypatch.setattr(bubblewrap, "bwrap_available", lambda: True)
    monkeypatch.setattr(bubblewrap, "mk_bwrap_argv", _fake_mk_bwrap_argv)
    monkeypatch.setattr(iso_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await iso_runtime._run_subprocess(
        entry_path=entry_path,
        cwd=outdir,
        env={
            "WORKDIR": str(workdir),
            "OUTPUT_DIR": str(outdir),
            "EXECUTION_ID": "exec-bwrap-workdir",
            "EXEC_REQUIRE_FS_SANDBOX": "1",
        },
        timeout_s=60,
        outdir=outdir,
        allow_network=False,
        exec_id="exec-bwrap-workdir",
    )

    assert result["ok"] is True
    assert captured["host_cwd"] == workdir
    assert captured["host_outdir"] == outdir
    assert captured["entry_path"] == entry_path
    assert captured["uid"] == 1001
    assert captured["gid"] == 1000


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
