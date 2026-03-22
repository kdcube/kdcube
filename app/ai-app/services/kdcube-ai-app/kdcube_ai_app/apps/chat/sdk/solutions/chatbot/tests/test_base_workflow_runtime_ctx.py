from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot import base_workflow as workflow_mod
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx


def _payload(*, tenant: str, project: str, user_id: str = "u1", turn_id: str = "turn-1"):
    return SimpleNamespace(
        actor=SimpleNamespace(tenant_id=tenant, project_id=project),
        user=SimpleNamespace(user_id=user_id, user_type="registered", timezone="UTC"),
        routing=SimpleNamespace(conversation_id="conv-1", turn_id=turn_id),
    )


def test_rebind_request_context_refreshes_runtime_ctx_bundle_storage(monkeypatch, tmp_path):
    resolved_storage = tmp_path / "bundle-storage" / "tenant-b" / "project-b" / "react.doc__main"

    def _fake_storage_for_spec(*, spec, tenant=None, project=None, ensure=True):
        assert getattr(spec, "id", None) == "react.doc@2026-03-02-22-10"
        assert tenant == "tenant-b"
        assert project == "project-b"
        if ensure:
            resolved_storage.mkdir(parents=True, exist_ok=True)
        return resolved_storage

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.bundle_storage.storage_for_spec",
        _fake_storage_for_spec,
    )
    monkeypatch.setattr(workflow_mod, "build_comm_from_comm_context", lambda *args, **kwargs: SimpleNamespace(delta=None))
    monkeypatch.setattr(workflow_mod, "build_relay_from_env", lambda: None)
    monkeypatch.setattr(workflow_mod, "AIBEmitters", lambda comm: SimpleNamespace())

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="react.doc@2026-03-02-22-10"))
    wf.bundle_props = {}
    wf.comm_context = _payload(tenant="tenant-a", project="project-a")
    wf._continuation_source = None
    wf.hosting_service = None
    wf.turn_status = None
    wf.runtime_ctx = RuntimeCtx(bundle_storage=None)

    wf.rebind_request_context(comm_context=_payload(tenant="tenant-b", project="project-b", turn_id="turn-2"))

    assert wf.runtime_ctx.tenant == "tenant-b"
    assert wf.runtime_ctx.project == "project-b"
    assert wf.runtime_ctx.turn_id == "turn-2"
    assert wf.runtime_ctx.bundle_storage == str(resolved_storage)


def test_resolve_mcp_services_config_prefers_bundle_props_over_env(monkeypatch):
    monkeypatch.setenv("MCP_SERVICES", '{"mcpServers":{"env_only":{"transport":"stdio","command":"python"}}}')

    wf = BaseWorkflow.__new__(BaseWorkflow)
    wf.bundle_props = {
        "mcp": {
            "services": {
                "mcpServers": {
                    "docs": {
                        "transport": "http",
                        "url": "https://mcp.example.com",
                        "auth": {"type": "bearer", "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token"},
                    }
                }
            }
        }
    }

    resolved = wf._resolve_mcp_services_config()

    assert resolved == {
        "mcpServers": {
            "docs": {
                "transport": "http",
                "url": "https://mcp.example.com",
                "auth": {"type": "bearer", "secret": "bundles.react.mcp@2026-03-09.secrets.docs.token"},
            }
        }
    }
