import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.external import docker as docker_runtime
from kdcube_ai_app.apps.chat.sdk.runtime import iso_runtime


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
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    bundle_root.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (bundle_root / "tools").mkdir(parents=True, exist_ok=True)
    (bundle_root / "tools" / "react_tools.py").write_text("x = 1\n", encoding="utf-8")

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
            return pathlib.Path("/Users/elenaviter/.kdcube/kdcube-runtime/data/bundles/react.doc@2026-03-02-22-10")
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
        },
        tool_module_names=[],
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        bundle_root=bundle_root,
    )

    assert result["ok"] is True
    assert calls, "docker run should proceed when translated host paths are opaque from proc"


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
