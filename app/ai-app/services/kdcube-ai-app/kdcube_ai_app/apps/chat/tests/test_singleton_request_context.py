from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskActor,
    ChatTaskPayload,
    ChatTaskRequest,
    ChatTaskRouting,
    ChatTaskUser,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import entrypoint as entrypoint_mod
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
