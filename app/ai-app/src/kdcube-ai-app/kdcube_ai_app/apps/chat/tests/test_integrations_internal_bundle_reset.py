from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.proc.rest.integrations import mount_integrations_routers
from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


class _Entry:
    def __init__(self, payload):
        self._payload = payload
        for key, value in payload.items():
            setattr(self, key, value)
        self.singleton = bool(payload.get("singleton", False))

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

    async def fake_set_registry_async(registry, default_bundle_id, **kwargs):
        calls["set_registry"] = (registry, default_bundle_id, kwargs)

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
    assert calls["set_registry"][2] == {
        "resolve_git": False,
        "source": "admin.reload-authority",
    }
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

    async def fake_set_registry_async(registry, default_bundle_id, **kwargs):
        calls["set_registry"] = (registry, default_bundle_id, kwargs)

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


def test_internal_remove_retires_only_descriptor_absent_bundle(monkeypatch):
    app = FastAPI()
    mount_integrations_routers(app)
    calls: dict[str, object] = {}
    current = _Registry(
        default_bundle_id="stable@1-0",
        bundles={
            "stable@1-0": _Entry(
                {
                    "id": "stable@1-0",
                    "path": "/bundles/stable",
                    "module": "entrypoint",
                }
            )
        },
    )

    class _Redis:
        async def publish(self, channel, payload):
            calls["publish"] = (channel, payload)
            return 2

    class _Lifecycle:
        async def retire(self, bundle_id, registry):
            calls["lifecycle"] = (bundle_id, registry)

    async def fake_sync_removal(redis, *, tenant, project, bundle_id):
        calls["sync_registry_cache"] = (redis, tenant, project, bundle_id)
        return current

    async def fake_set_registry(registry, default_bundle_id, **kwargs):
        calls["set_registry"] = (registry, default_bundle_id, kwargs)

    def fake_get_all():
        return {
            "removed@1-0": {
                "id": "removed@1-0",
                "path": "/bundles/removed",
                "module": "entrypoint",
                "singleton": False,
            },
            "stable@1-0": {
                "id": "stable@1-0",
                "path": "/bundles/stable",
                "module": "entrypoint",
                "singleton": False,
            },
        }

    def fake_evict(spec, *, drop_sys_modules=True):
        calls["evicted"] = (spec.id, spec.path, drop_sys_modules)
        return {"evicted_modules": 1}

    def fake_invalidate_static(**kwargs):
        calls["invalidated_static"] = kwargs
        return 1

    def fake_stop_sidecars(**kwargs):
        calls["stopped_sidecars"] = kwargs
        return 1

    async def fake_invalidate_deployed(**kwargs):
        calls["invalidated_deployed"] = kwargs

    app.state.redis_async = _Redis()
    app.state.application_lifecycle = _Lifecycle()
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})
    monkeypatch.setattr(
        integrations,
        "describe_authoritative_bundle_store",
        lambda tenant, project: {"kind": "bundles-yaml"},
    )
    monkeypatch.setattr(integrations, "_invalidate_deployed_widget_manifests", fake_invalidate_deployed)

    import kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars as local_sidecars
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store

    monkeypatch.setattr(bundle_store, "sync_registry_after_bundle_removal", fake_sync_removal)
    monkeypatch.setattr(bundle_registry, "get_all", fake_get_all)
    monkeypatch.setattr(bundle_registry, "set_registry_async", fake_set_registry)
    monkeypatch.setattr(bundle_loader, "evict_bundle_scope", fake_evict)
    monkeypatch.setattr(bundle_loader, "invalidate_static_bundle_entrypoint_loads", fake_invalidate_static)
    monkeypatch.setattr(local_sidecars, "stop_local_sidecars_for_bundle_ids", fake_stop_sidecars)

    response = TestClient(app).post(
        "/internal/bundles/remove",
        json={"bundle_id": "removed@1-0"},
    )

    assert response.status_code == 200
    assert response.json()["changed_bundle_ids"] == ["removed@1-0"]
    assert calls["sync_registry_cache"][1:] == (
        "demo-tenant",
        "demo-project",
        "removed@1-0",
    )
    assert calls["set_registry"][0] == {
        "stable@1-0": {
            "id": "stable@1-0",
            "path": "/bundles/stable",
            "module": "entrypoint",
        }
    }
    assert calls["set_registry"][2] == {
        "resolve_git": False,
        "source": "admin.remove-authority",
    }
    assert calls["evicted"] == ("removed@1-0", "/bundles/removed", True)
    assert calls["stopped_sidecars"]["bundle_ids"] == {"removed@1-0"}
    retired_id, lifecycle_registry = calls["lifecycle"]
    assert retired_id == "removed@1-0"
    assert set(lifecycle_registry.bundles) == {"stable@1-0"}
    event = json.loads(calls["publish"][1])
    assert event["op"] == "remove"
    assert event["changed_bundle_ids"] == ["removed@1-0"]
    assert event["bundles"] == {}


def test_internal_remove_rejects_bundle_still_in_descriptor(monkeypatch):
    app = FastAPI()
    app.state.redis_async = object()
    mount_integrations_routers(app)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store

    async def fake_sync_removal(redis, *, tenant, project, bundle_id):
        del redis, tenant, project
        raise bundle_store.BundleStillDeclaredError(bundle_id)

    monkeypatch.setattr(bundle_store, "sync_registry_after_bundle_removal", fake_sync_removal)

    response = TestClient(app).post(
        "/internal/bundles/remove",
        json={"bundle_id": "still-here@1-0"},
    )

    assert response.status_code == 409
    assert "still present" in response.json()["detail"]


def test_internal_bundle_update_targets_changed_app_and_schedules_preparation(monkeypatch):
    app = FastAPI()
    mount_integrations_routers(app)
    calls: dict[str, object] = {}

    current = BundlesRegistry(
        default_bundle_id="stable@1-0",
        bundles={
            "changed@1-0": BundleEntry(
                id="changed@1-0",
                path="/bundles/changed-old",
                module="entrypoint",
            ),
            "stable@1-0": BundleEntry(
                id="stable@1-0",
                path="/bundles/stable",
                module="entrypoint",
            ),
        },
    )

    class _Redis:
        async def publish(self, channel, payload):
            calls["publish"] = (channel, payload)
            return 1

    class _Lifecycle:
        async def reconcile(self, registry, *, force=None):
            calls["lifecycle"] = (registry, force)

    app.state.redis_async = _Redis()
    app.state.application_lifecycle = _Lifecycle()

    async def _load_registry(redis, tenant, project):
        del redis
        calls["load"] = (tenant, project)
        return current

    async def _save_registry(redis, registry, tenant, project, **kwargs):
        del redis
        calls["save"] = (registry, tenant, project, kwargs)

    async def _set_registry(registry, default_bundle_id, **kwargs):
        calls["set_registry"] = (registry, default_bundle_id, kwargs)

    def _evict(spec, *, drop_sys_modules=True):
        calls.setdefault("evicted", []).append((spec.id, spec.path, drop_sys_modules))
        return {}

    def _invalidate_static(**kwargs):
        calls.setdefault("invalidated", []).append(kwargs)
        return 1

    async def _invalidate_deployed(**kwargs):
        calls["deployed"] = kwargs

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})
    monkeypatch.setattr(integrations, "_invalidate_deployed_widget_manifests", _invalidate_deployed)

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader
    import kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars as local_sidecars

    monkeypatch.setattr(bundle_store, "load_registry", _load_registry)
    monkeypatch.setattr(bundle_store, "save_registry", _save_registry)
    monkeypatch.setattr(bundle_registry, "set_registry_async", _set_registry)
    monkeypatch.setattr(bundle_loader, "evict_bundle_scope", _evict)
    monkeypatch.setattr(bundle_loader, "invalidate_static_bundle_entrypoint_loads", _invalidate_static)
    monkeypatch.setattr(local_sidecars, "stop_local_sidecars_for_bundle_ids", lambda **kwargs: 0)

    client = TestClient(app)
    response = client.post(
        "/internal/bundles/update",
        json={
            "op": "merge",
            "bundles": {
                "changed@1-0": {
                    "id": "changed@1-0",
                    "path": "/bundles/changed-new",
                    "module": "entrypoint",
                }
            },
        },
    )

    assert response.status_code == 200
    assert calls["set_registry"][2] == {
        "resolve_git": False,
        "source": "admin.save",
    }
    assert calls["evicted"] == [("changed@1-0", "/bundles/changed-new", True)]
    assert calls["invalidated"] == [{
        "bundle_id": "changed@1-0",
        "tenant": "demo-tenant",
        "project": "demo-project",
    }]
    lifecycle_registry, force = calls["lifecycle"]
    assert set(lifecycle_registry.bundles) == {
        "changed@1-0",
        "kdcube.admin",
        "stable@1-0",
    }
    assert force == {"changed@1-0"}
