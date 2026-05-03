import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskActor,
    ChatTaskPayload,
    ChatTaskRequest,
    ChatTaskRouting,
    ChatTaskUser,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import entrypoint as entrypoint_mod
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)
from kdcube_ai_app.infra.plugin.agentic_loader import (
    AgenticBundleSpec,
    _singleton_cache,
    cache_key_for_spec,
    clear_agentic_caches,
    get_workflow_instance,
    notify_cached_bundle_props_changed,
)
from kdcube_ai_app.infra.plugin.bundle_store import _admin_bundle_entry


class _DummyConfig:
    def __init__(self, bundle_id: str):
        self.ai_bundle_spec = SimpleNamespace(id=bundle_id)
        self.log_level = "INFO"
        self.role_models = {}
        self.embedder_config = {}
        self.embedding_model = "text-embedding-3-small"
        #self.selected_embedder = None
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


def _ctx(*, user_type: str, user_id: str = "user-1") -> ChatTaskPayload:
    return ChatTaskPayload(
        request=ChatTaskRequest(request_id=f"req-{user_type}"),
        routing=ChatTaskRouting(
            session_id=f"sid-{user_type}",
            conversation_id=f"conv-{user_type}",
            turn_id=f"turn-{user_type}",
            bundle_id="kdcube.admin",
        ),
        actor=ChatTaskActor(
            tenant_id="demo",
            project_id="demo-project",
        ),
        user=ChatTaskUser(
            user_type=user_type,
            user_id=user_id,
            username="lena@nestlogic.com",
            roles=["kdcube:role:super-admin"] if user_type == "privileged" else [],
            permissions=[],
            timezone="UTC",
        ),
    )


def test_singleton_workflow_rebinds_request_context(monkeypatch):
    clear_agentic_caches()
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    admin = _admin_bundle_entry()
    spec = AgenticBundleSpec(
        path=admin.path,
        module=admin.module,
        singleton=bool(admin.singleton),
    )
    cfg = _DummyConfig(bundle_id="kdcube.admin")

    first, _ = get_workflow_instance(spec, cfg, comm_context=_ctx(user_type="registered"))
    assert first.user_type_from_comm_ctx(first.comm) == "registered"

    second, _ = get_workflow_instance(spec, cfg, comm_context=_ctx(user_type="privileged"))
    assert second is first
    assert second.user_type_from_comm_ctx(second.comm) == "privileged"

    clear_agentic_caches()


@pytest.mark.asyncio
async def test_singleton_entrypoint_keeps_comm_context_task_local(monkeypatch):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)
    monkeypatch.setattr(
        entrypoint_mod,
        "build_comm_from_comm_context",
        lambda payload, **kwargs: SimpleNamespace(
            user_id=payload.user.user_id,
            service={"user_obj": {"user_type": payload.user.user_type}},
        ),
    )

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

        async def probe(self, pause: float) -> tuple[str | None, str | None, str | None, str | None]:
            before_user = getattr(self.comm, "user_id", None)
            before_ctx = getattr(getattr(self.comm_context, "user", None), "user_id", None)
            await asyncio.sleep(pause)
            after_user = getattr(self.comm, "user_id", None)
            after_ctx = getattr(getattr(self.comm_context, "user", None), "user_id", None)
            return before_user, before_ctx, after_user, after_ctx

    ep = _ProbeEntrypoint(config=_DummyConfig(bundle_id="bundle.probe"), comm_context=_ctx(user_type="registered", user_id="seed"))

    async def _call(ctx: ChatTaskPayload, pause: float):
        ep.rebind_request_context(comm_context=ctx)
        return await ep.probe(pause)

    first_ctx = _ctx(user_type="registered", user_id="user-a")
    second_ctx = _ctx(user_type="privileged", user_id="user-b")

    first, second = await asyncio.gather(
        _call(first_ctx, 0.05),
        _call(second_ctx, 0.0),
    )

    assert first == ("user-a", "user-a", "user-a", "user-a")
    assert second == ("user-b", "user-b", "user-b", "user-b")


def test_economics_entrypoint_rebind_refreshes_managers(monkeypatch):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _EconProbe(BaseEntrypointWithEconomics):
        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

    initial_redis = object()
    ep = _EconProbe(
        config=_DummyConfig(bundle_id="bundle.econ"),
        pg_pool=None,
        redis=initial_redis,
        comm_context=_ctx(user_type="registered"),
    )

    assert ep.cp_manager is not None
    assert ep.cp_manager._pg_pool is None
    assert ep.cp_manager._redis is initial_redis

    rebound_pg_pool = object()
    rebound_redis = object()
    ep.rebind_request_context(
        comm_context=_ctx(user_type="privileged"),
        pg_pool=rebound_pg_pool,
        redis=rebound_redis,
    )

    assert ep.pg_pool is rebound_pg_pool
    assert ep.redis is rebound_redis
    assert ep.cp_manager is not None
    assert ep.cp_manager._pg_pool is rebound_pg_pool
    assert ep.cp_manager._redis is rebound_redis
    assert ep.budget_limiter is not None
    assert ep.budget_limiter.pg_pool is rebound_pg_pool
    assert ep.budget_limiter.r is rebound_redis
    assert ep.rl is not None
    assert ep.rl.r is rebound_redis


@pytest.mark.asyncio
async def test_base_entrypoint_on_props_changed_fires_on_refresh(monkeypatch):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def __init__(self, *args, **kwargs):
            self.events = []
            super().__init__(*args, **kwargs)

        def configuration_defaults(self):
            return {"feature": {"enabled": False}}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

        async def on_props_changed(self, **kwargs):
            self.events.append(kwargs)

    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep.kv_cache = MagicMock()
    ep.kv_cache.get_json = AsyncMock(return_value={"feature": {"enabled": True}})

    props = await ep.refresh_bundle_props(state={"tenant": "demo", "project": "demo-project"})

    assert props["feature"]["enabled"] is True
    assert len(ep.events) == 1
    assert ep.events[0]["reason"] == "refresh_bundle_props"
    assert ep.events[0]["current_props"]["feature"]["enabled"] is True


@pytest.mark.asyncio
async def test_base_entrypoint_reconciles_ui_builds_on_ui_props_changed(monkeypatch):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def configuration_defaults(self):
            return {}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep._ensure_ui_build = AsyncMock()

    await ep.on_props_changed(
        previous_props={"ui": {"web_app_widgets": {}}},
        current_props={
            "ui": {
                "web_app_widgets": {
                    "task_webapp": {
                        "enabled": True,
                        "src_folder": "widgets/task_webapp",
                        "build_command": "true",
                    }
                }
            }
        },
        reason="refresh_bundle_props",
        tenant="demo",
        project="demo-project",
    )

    ep._ensure_ui_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_entrypoint_does_not_reconcile_ui_builds_on_non_ui_props_changed(monkeypatch):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def configuration_defaults(self):
            return {}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep._ensure_ui_build = AsyncMock()

    await ep.on_props_changed(
        previous_props={"feature": {"enabled": False}},
        current_props={"feature": {"enabled": True}},
        reason="refresh_bundle_props",
        tenant="demo",
        project="demo-project",
    )

    ep._ensure_ui_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_entrypoint_ui_build_uses_clean_temp_source_and_outdir_env(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def configuration_defaults(self):
            return {}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

    bundle_root = tmp_path / "bundle"
    src = bundle_root / "widgets" / "probe"
    src.mkdir(parents=True)
    (src / "index.html").write_text("<html></html>", encoding="utf-8")
    stale = src / "node_modules"
    stale.mkdir()
    (stale / "stale.txt").write_text("stale", encoding="utf-8")

    storage_root = tmp_path / "storage"
    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep.bundle_storage_root = lambda: storage_root
    ep._bundle_root = lambda: str(bundle_root)

    build_dest = storage_root / "ui" / "widgets" / "probe"
    await ep._ensure_static_ui_app_build(
        kind="widget:probe",
        cfg={
            "src_folder": "widgets/probe",
            "build_command": "test ! -e node_modules/stale.txt && mkdir -p \"$OUTDIR\" && printf '<html></html>' > \"$OUTDIR/index.html\"",
        },
        build_dest=build_dest,
        signature_path=storage_root / ".ui.widgets" / "probe.signature",
        operation="ui-widget-probe",
    )

    assert (build_dest / "index.html").exists()
    assert not any(storage_root.glob(".ui.src.tmp.*"))
    assert (src / "node_modules" / "stale.txt").exists()


@pytest.mark.asyncio
async def test_base_entrypoint_standard_npm_build_runs_package_script_directly(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def configuration_defaults(self):
            return {}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

    bundle_root = tmp_path / "bundle"
    src = bundle_root / "widgets" / "probe"
    src.mkdir(parents=True)
    (src / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "test -n \"$OUTDIR\" && printf '<html></html>' > \"$OUTDIR/index.html\""
                }
            }
        ),
        encoding="utf-8",
    )

    storage_root = tmp_path / "storage"
    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep.bundle_storage_root = lambda: storage_root
    ep._bundle_root = lambda: str(bundle_root)

    build_dest = storage_root / "ui" / "widgets" / "probe"
    await ep._ensure_static_ui_app_build(
        kind="widget:probe",
        cfg={
            "src_folder": "widgets/probe",
            "build_command": "npm install && npm run build --outDir <VI_BUILD_DEST_ABSOLUTE_PATH>",
        },
        build_dest=build_dest,
        signature_path=storage_root / ".ui.widgets" / "probe.signature",
        operation="ui-widget-probe",
    )

    assert (build_dest / "index.html").exists()


def test_base_entrypoint_ui_build_command_uses_placeholder_as_env_only(tmp_path):
    out_dir = tmp_path / "out"

    command = entrypoint_mod.BaseEntrypoint._prepare_ui_build_command(
        "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
        out_dir,
    )
    assert command == "npm install --no-package-lock && npm run build"

    command = entrypoint_mod.BaseEntrypoint._prepare_ui_build_command(
        "npm install --no-package-lock && npm run build <VI_BUILD_DEST_ABSOLUTE_PATH>",
        out_dir,
    )
    assert command == "npm install --no-package-lock && npm run build"

    command = entrypoint_mod.BaseEntrypoint._prepare_ui_build_command(
        f"npm install --no-package-lock && npm run build {out_dir}",
        out_dir,
    )
    assert command == "npm install --no-package-lock && npm run build"

    command = entrypoint_mod.BaseEntrypoint._prepare_ui_build_command(
        "npm install --no-package-lock && npm run build ../.ui.build.tmp.8241.4ceeb93/index.html",
        out_dir,
    )
    assert command == "npm install --no-package-lock && npm run build"

    command = entrypoint_mod.BaseEntrypoint._prepare_ui_build_command(
        "vite build --outDir <VI_BUILD_DEST_ABSOLUTE_PATH>",
        out_dir,
    )
    assert command == f"vite build --outDir {out_dir}"


def test_base_entrypoint_standard_npm_ui_build_detection():
    assert entrypoint_mod.BaseEntrypoint._is_standard_npm_ui_build(
        "npm install --no-package-lock && npm run build"
    ) is True
    assert entrypoint_mod.BaseEntrypoint._is_standard_npm_ui_build("npm run build") is False


def test_base_entrypoint_npm_install_args_for_ui_build():
    assert entrypoint_mod.BaseEntrypoint._npm_install_args_for_ui_build(
        "npm install --no-package-lock && npm run build"
    ) == ["npm", "install", "--no-package-lock"]
    assert entrypoint_mod.BaseEntrypoint._npm_install_args_for_ui_build(
        "npm install && npm run build --outDir /tmp/.ui.build.tmp.123"
    ) == ["npm", "install"]
    assert entrypoint_mod.BaseEntrypoint._npm_install_args_for_ui_build("npm run build") is None


@pytest.mark.asyncio
async def test_notify_cached_bundle_props_changed_calls_singleton_hook(monkeypatch):
    clear_agentic_caches()
    monkeypatch.setattr(entrypoint_mod, "get_settings", lambda: SimpleNamespace(TENANT="demo", PROJECT="demo-project"))
    monkeypatch.setattr(entrypoint_mod, "create_kv_cache_from_env", lambda: None)

    class _ProbeEntrypoint(entrypoint_mod.BaseEntrypoint):
        def __init__(self, *args, **kwargs):
            self.events = []
            super().__init__(*args, **kwargs)

        def configuration_defaults(self):
            return {"feature": {"enabled": False}}

        async def execute_core(self, *, state, thread_id, params):
            del state, thread_id, params
            return {}

        async def on_props_changed(self, **kwargs):
            self.events.append(kwargs)

    ep = _ProbeEntrypoint(
        config=_DummyConfig(bundle_id="bundle.props"),
        comm_context=_ctx(user_type="registered"),
    )
    ep.kv_cache = MagicMock()
    ep.kv_cache.get_json = AsyncMock(return_value={"feature": {"enabled": True}})

    spec = AgenticBundleSpec(path="/tmp/bundle.props", module="entrypoint", singleton=True)
    _singleton_cache[cache_key_for_spec(spec)] = (ep, SimpleNamespace(__name__="bundle.props.entrypoint"))

    try:
        changed = await notify_cached_bundle_props_changed(
            spec,
            bundle_id="bundle.props",
            tenant="demo",
            project="demo-project",
            updated_by="tester",
            source="unit-test",
            redis=object(),
        )
        assert changed is True
        assert len(ep.events) == 1
        assert ep.events[0]["reason"] == "bundles.props.update"
        assert ep.events[0]["updated_by"] == "tester"
        assert ep.events[0]["source"] == "unit-test"
        assert ep.events[0]["current_props"]["feature"]["enabled"] is True
    finally:
        clear_agentic_caches()
