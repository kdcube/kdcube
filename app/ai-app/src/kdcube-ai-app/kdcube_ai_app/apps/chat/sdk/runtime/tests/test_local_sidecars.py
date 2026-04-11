import asyncio
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskActor,
    ChatTaskPayload,
    ChatTaskRequest,
    ChatTaskRouting,
    ChatTaskUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime import local_sidecars
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import entrypoint as entrypoint_mod


def _ctx() -> ChatTaskPayload:
    return ChatTaskPayload(
        request=ChatTaskRequest(request_id="req-sidecar"),
        routing=ChatTaskRouting(
            session_id="sid-sidecar",
            conversation_id="conv-sidecar",
            turn_id="turn-sidecar",
            bundle_id="bundle.sidecar",
        ),
        actor=ChatTaskActor(
            tenant_id="demo-tenant",
            project_id="demo-project",
        ),
        user=ChatTaskUser(
            user_type="registered",
            user_id="user-1",
            username="user@example.com",
            roles=[],
            permissions=[],
            timezone="UTC",
        ),
    )


class _DummyConfig:
    def __init__(self, bundle_id: str):
        self.ai_bundle_spec = SimpleNamespace(id=bundle_id)
        self.log_level = "INFO"
        self.role_models = {}
        self.embedder_config = {}
        self.embedding_model = "text-embedding-3-small"
        self.selected_embedder = None
        self.custom_embedding_endpoint = None
        self.custom_embedding_size = None
        self.openai_api_key = "test-openai-key"
        self.claude_api_key = None
        self.google_api_key = None
        self.custom_model_endpoint = None
        self.custom_model_api_key = None
        self.gemini_cache_enabled = False
        self.gemini_cache_ttl_seconds = None

    def set_role_models(self, models):
        self.role_models = dict(models or {})

    def set_embedding(self, _embedding):
        return None

    def ensure_role(self, _role):
        return {"provider": "openai", "model": "gpt-4o-mini"}

    def __getattr__(self, _name):
        return None


def _write_sidecar_process(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script_path = tmp_path / "sidecar_service.py"
    flag_path = tmp_path / "terminated.txt"
    script_path.write_text(
        textwrap.dedent(
            """
            import os
            import pathlib
            import signal
            import time

            FLAG = pathlib.Path(os.environ["FLAG_PATH"])
            running = True

            def _term(*_args):
                global running
                FLAG.write_text("terminated", encoding="utf-8")
                running = False

            signal.signal(signal.SIGTERM, _term)

            try:
                while running:
                    time.sleep(0.1)
            finally:
                if not FLAG.exists():
                    FLAG.write_text("exited", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    return script_path, flag_path


@pytest.mark.asyncio
async def test_local_sidecar_reuse_and_shutdown(tmp_path: Path):
    local_sidecars.clear_local_sidecars_for_tests()
    script_path, flag_path = _write_sidecar_process(tmp_path)

    handle_one = local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.sidecar",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_path)},
        port=None,
        ready_timeout_sec=5.0,
    )
    handle_two = local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.sidecar",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_path)},
        port=None,
        ready_timeout_sec=5.0,
    )

    assert handle_two.pid == handle_one.pid
    assert handle_one.base_url is None

    fetched = local_sidecars.get_local_sidecar(
        bundle_id="bundle.sidecar",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
    )
    assert fetched is not None
    assert fetched.pid == handle_one.pid

    await local_sidecars.shutdown_all_local_sidecars(terminate_timeout_sec=3.0, kill_timeout_sec=1.0)

    process_gone = False
    for _ in range(30):
        if local_sidecars.get_local_sidecar(
            bundle_id="bundle.sidecar",
            tenant="demo-tenant",
            project="demo-project",
            name="svc",
        ) is None:
            try:
                os.kill(handle_one.pid, 0)
            except ProcessLookupError:
                process_gone = True
                break
            except PermissionError:
                process_gone = True
                break
        if flag_path.exists():
            process_gone = True
            break
        await asyncio.sleep(0.1)

    assert process_gone
    local_sidecars.clear_local_sidecars_for_tests()


@pytest.mark.asyncio
async def test_stop_local_sidecars_for_scope_stops_only_target_bundle(tmp_path: Path):
    local_sidecars.clear_local_sidecars_for_tests()
    script_path, flag_one = _write_sidecar_process(tmp_path / "one")
    _, flag_two = _write_sidecar_process(tmp_path / "two")

    handle_one = local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.one",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_one)},
        port=None,
        ready_timeout_sec=5.0,
    )
    handle_two = local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.two",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_two)},
        port=None,
        ready_timeout_sec=5.0,
    )

    stopped = local_sidecars.stop_local_sidecars_for_scope(
        bundle_id="bundle.one",
        tenant="demo-tenant",
        project="demo-project",
        terminate_timeout_sec=1.0,
        kill_timeout_sec=0.5,
    )

    assert stopped == 1
    assert local_sidecars.get_local_sidecar(
        bundle_id="bundle.one",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
    ) is None
    assert local_sidecars.get_local_sidecar(
        bundle_id="bundle.two",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
    ) is not None

    process_gone = False
    for _ in range(20):
        try:
            os.kill(handle_one.pid, 0)
        except ProcessLookupError:
            process_gone = True
            break
        except PermissionError:
            process_gone = True
            break
        await asyncio.sleep(0.05)
    assert process_gone
    try:
        os.kill(handle_two.pid, 0)
    except ProcessLookupError:
        pytest.fail("non-target sidecar should still be alive")

    local_sidecars.clear_local_sidecars_for_tests()


@pytest.mark.asyncio
async def test_stop_inactive_local_sidecars_stops_removed_bundle_ids(tmp_path: Path):
    local_sidecars.clear_local_sidecars_for_tests()
    script_path, flag_one = _write_sidecar_process(tmp_path / "active")
    _, flag_two = _write_sidecar_process(tmp_path / "inactive")

    local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.active",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_one)},
        port=None,
        ready_timeout_sec=5.0,
    )
    inactive_handle = local_sidecars.ensure_local_sidecar(
        bundle_id="bundle.inactive",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
        command=[sys.executable, str(script_path)],
        cwd=tmp_path,
        env={"FLAG_PATH": str(flag_two)},
        port=None,
        ready_timeout_sec=5.0,
    )

    stopped = local_sidecars.stop_inactive_local_sidecars(
        active_bundle_ids={"bundle.active"},
        tenant="demo-tenant",
        project="demo-project",
        terminate_timeout_sec=1.0,
        kill_timeout_sec=0.5,
    )

    assert stopped == 1
    assert local_sidecars.get_local_sidecar(
        bundle_id="bundle.active",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
    ) is not None
    assert local_sidecars.get_local_sidecar(
        bundle_id="bundle.inactive",
        tenant="demo-tenant",
        project="demo-project",
        name="svc",
    ) is None
    process_gone = False
    for _ in range(20):
        try:
            os.kill(inactive_handle.pid, 0)
        except ProcessLookupError:
            process_gone = True
            break
        except PermissionError:
            process_gone = True
            break
        await asyncio.sleep(0.05)
    assert process_gone

    local_sidecars.clear_local_sidecars_for_tests()


def test_base_entrypoint_ensure_local_sidecar_passes_bundle_scope(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    captured = {}

    def _fake_ensure(**kwargs):
        captured.update(kwargs)
        return local_sidecars.LocalSidecarHandle(
            bundle_id=kwargs["bundle_id"],
            tenant=kwargs["tenant"],
            project=kwargs["project"],
            name=kwargs["name"],
            host=kwargs["host"],
            port=kwargs["port"],
            pid=12345,
            base_url="http://127.0.0.1:9000",
            cwd=str(kwargs["cwd"]),
            started_at=0.0,
        )

    monkeypatch.setattr(entrypoint_mod, "ensure_runtime_local_sidecar", _fake_ensure)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def _bundle_root(self):
            return str(tmp_path / "bundle-root")

        def bundle_storage_root(self):
            return tmp_path / "bundle-storage"

        async def execute_core(self, *, state, thread_id, params):
            return {}

    ep = _ProbeEntrypoint(config=_DummyConfig(bundle_id="bundle.sidecar"), comm_context=_ctx())
    handle = ep.ensure_local_sidecar(
        name="svc",
        command=["node", "server.js"],
        ready_path="/health",
    )

    assert handle.base_url == "http://127.0.0.1:9000"
    assert captured["bundle_id"] == "bundle.sidecar"
    assert captured["tenant"] == "demo-tenant"
    assert captured["project"] == "demo-project"
    assert captured["name"] == "svc"
    assert captured["command"] == ["node", "server.js"]
    assert captured["cwd"] == str(tmp_path / "bundle-root")
    assert captured["ready_path"] == "/health"
    assert captured["env"]["KDCUBE_BUNDLE_ROOT"] == str(tmp_path / "bundle-root")
    assert captured["env"]["KDCUBE_BUNDLE_STORAGE_ROOT"] == str(tmp_path / "bundle-storage")
