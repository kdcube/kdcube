from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.proc.rest.integrations import mount_integrations_routers
from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations


class _Entry:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class _Registry:
    def __init__(self, *, default_bundle_id: str, bundles: dict[str, _Entry]):
        self.default_bundle_id = default_bundle_id
        self.bundles = bundles


def test_internal_reload_authority_reapplies_registry(monkeypatch):
    app = FastAPI()
    app.state.redis_async = object()
    mount_integrations_routers(app)

    calls: dict[str, object] = {}

    async def fake_reload_registry_from_authority(redis, tenant, project):
        calls["reload"] = (redis, tenant, project)
        return _Registry(
            default_bundle_id="demo.bundle@1.0.0",
            bundles={
                "demo.bundle@1.0.0": _Entry(
                    {
                        "id": "demo.bundle@1.0.0",
                        "path": "/bundles/demo",
                        "module": "demo.entrypoint",
                    }
                )
            },
        )

    async def fake_set_registry_async(registry, default_bundle_id):
        calls["set_registry"] = (registry, default_bundle_id)

    def fake_clear_bundle_loader_caches():
        calls["cleared"] = True

    class _Redis:
        async def publish(self, channel, payload):
            calls["publish"] = (channel, payload)
            return 1

    app.state.redis_async = _Redis()

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader

    monkeypatch.setattr(bundle_store, "reload_registry_from_authority", fake_reload_registry_from_authority)
    monkeypatch.setattr(bundle_registry, "set_registry_async", fake_set_registry_async)
    monkeypatch.setattr(bundle_loader, "clear_bundle_loader_caches", fake_clear_bundle_loader_caches)

    client = TestClient(app)
    response = client.post("/internal/bundles/reload-authority", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["source"] == "authority"
    assert calls["reload"][1:] == ("demo-tenant", "demo-project")
    assert calls["set_registry"][1] == "demo.bundle@1.0.0"
    assert calls["cleared"] is True
    assert calls["publish"][0] == "kdcube:config:bundles:update:demo-tenant:demo-project"


def test_internal_reload_authority_evicts_requested_bundle_scope(monkeypatch):
    app = FastAPI()
    mount_integrations_routers(app)

    calls: dict[str, object] = {}

    async def fake_reload_registry_from_authority(redis, tenant, project):
        calls["reload"] = (redis, tenant, project)
        return _Registry(
            default_bundle_id="demo.bundle@1.0.0",
            bundles={
                "demo.bundle@1.0.0": _Entry(
                    {
                        "id": "demo.bundle@1.0.0",
                        "path": "/bundles/demo",
                        "module": "demo.entrypoint",
                        "singleton": True,
                    }
                )
            },
        )

    async def fake_set_registry_async(registry, default_bundle_id):
        calls["set_registry"] = (registry, default_bundle_id)

    def fake_clear_bundle_loader_caches():
        calls["cleared"] = True

    def fake_evict_bundle_scope(spec, *, drop_sys_modules=True):
        calls["evicted"] = {
            "path": spec.path,
            "module": spec.module,
            "singleton": spec.singleton,
            "drop_sys_modules": drop_sys_modules,
        }
        return {"evicted_modules": 1, "evicted_singletons": 1, "evicted_manifests": 1, "sys_modules_deleted": 2}

    class _Redis:
        async def publish(self, channel, payload):
            calls["publish"] = (channel, payload)
            return 1

    app.state.redis_async = _Redis()

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader

    monkeypatch.setattr(bundle_store, "reload_registry_from_authority", fake_reload_registry_from_authority)
    monkeypatch.setattr(bundle_registry, "set_registry_async", fake_set_registry_async)
    monkeypatch.setattr(bundle_loader, "clear_bundle_loader_caches", fake_clear_bundle_loader_caches)
    monkeypatch.setattr(bundle_loader, "evict_bundle_scope", fake_evict_bundle_scope)

    client = TestClient(app)
    response = client.post("/internal/bundles/reload-authority", json={"bundle_id": "demo.bundle@1.0.0"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["bundle_id"] == "demo.bundle@1.0.0"
    assert response.json()["eviction"]["sys_modules_deleted"] == 2
    assert calls["evicted"] == {
        "path": "/bundles/demo",
        "module": "demo.entrypoint",
        "singleton": True,
        "drop_sys_modules": True,
    }
    assert "cleared" not in calls
