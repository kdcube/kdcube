import asyncio
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


@pytest.mark.asyncio
async def test_run_py_in_docker_terminates_child_process_when_cancelled(tmp_path, monkeypatch):
    fake_proc = _FakeProc()

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_proc

    monkeypatch.setattr(docker_runtime.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(docker_runtime, "filter_host_environment", lambda env: dict(env))
    monkeypatch.setattr(docker_runtime, "check_and_apply_cloud_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_runtime, "_resolve_redis_url_for_container", lambda url, logger=None: url)
    monkeypatch.setattr(docker_runtime, "_translate_container_path_to_host", lambda path: path)
    monkeypatch.setattr(docker_runtime, "_is_running_in_docker", lambda: False)
    monkeypatch.setattr(docker_runtime, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://example"))

    task = asyncio.create_task(
        docker_runtime.run_py_in_docker(
            workdir=tmp_path / "work",
            outdir=tmp_path / "out",
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
