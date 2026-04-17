import asyncio
from types import SimpleNamespace

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
    clear_agentic_caches,
    get_workflow_instance,
)
from kdcube_ai_app.infra.plugin.bundle_store import _admin_bundle_entry


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
