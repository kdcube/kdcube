"""The reload receipt names what loaded, and a fenced activation is refused before eviction (W209)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations, mount_integrations_routers
from kdcube_ai_app.infra.plugin import bundle_snapshot
from kdcube_ai_app.infra.plugin.bundle_store import BundleActivationConfig, BundleEntry, BundlesRegistry

BUNDLE_ID = "demo.bundle@1.0.0"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _mount(monkeypatch, *, registry: BundlesRegistry, previously_loaded: dict | None = None):
    app = FastAPI()
    mount_integrations_routers(app)
    calls: dict[str, object] = {"evictions": []}

    class _Lifecycle:
        async def reconcile(self, reg, *, force=None):
            calls["lifecycle"] = (reg, force)

        async def publish_authority_discovery_change(self, reg):
            calls["authority_discovery"] = reg

    class _Redis:
        async def publish(self, channel, payload):
            calls["publish"] = (channel, payload)
            return 1

    app.state.redis_async = _Redis()
    app.state.application_lifecycle = _Lifecycle()

    async def fake_reload(redis, tenant, project):
        return registry

    async def fake_set_registry_async(reg, default_bundle_id, **kwargs):
        calls["set_registry"] = (reg, default_bundle_id, kwargs)

    def fake_evict(spec, *, drop_sys_modules=True):
        calls["evictions"].append({"path": spec.path, "module": spec.module})
        return {"evicted_modules": 1, "evicted_singletons": 0, "evicted_manifests": 0, "sys_modules_deleted": 3}

    async def fake_invalidate(**kwargs):
        calls["deployed"] = kwargs

    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store

    monkeypatch.setattr(integrations, "get_settings", lambda: SimpleNamespace(TENANT="t", PROJECT="p", INSTANCE_ID="proc-test"))
    monkeypatch.setattr(integrations, "_LOCALHOST", {"testclient", "127.0.0.1", "::1"})
    monkeypatch.setattr(integrations, "_invalidate_deployed_widget_manifests", fake_invalidate)
    monkeypatch.setattr(bundle_store, "reload_registry_from_authority", fake_reload)
    monkeypatch.setattr(bundle_registry, "set_registry_async", fake_set_registry_async)
    monkeypatch.setattr(bundle_registry, "get_all", lambda: {BUNDLE_ID: previously_loaded} if previously_loaded else {})
    monkeypatch.setattr(bundle_loader, "evict_bundle_scope", fake_evict)
    monkeypatch.setattr(bundle_loader, "clear_bundle_loader_caches", lambda: calls.__setitem__("cleared", True))
    return TestClient(app), calls


def _registry(**activation) -> BundlesRegistry:
    entry = BundleEntry(
        id=BUNDLE_ID,
        path="/bundles/demo",
        module="demo.entrypoint",
        singleton=True,
        activation=BundleActivationConfig(**activation) if activation else None,
    )
    return BundlesRegistry(default_bundle_id=BUNDLE_ID, bundles={BUNDLE_ID: entry})


def test_a_plain_reload_receipt_carries_evidence_about_the_mounted_tree(monkeypatch):
    async def fake_describe(path):
        return {"mode": "local-path", "path": str(path), "head": COMMIT_B, "dirty": False, "changed_paths": []}

    monkeypatch.setattr(bundle_snapshot, "describe_mounted_source", fake_describe)
    client, calls = _mount(monkeypatch, registry=_registry())

    response = client.post("/internal/bundles/reload-authority", json={"bundle_id": BUNDLE_ID})

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["activation"] == {
        "mode": "local-path", "path": "/bundles/demo", "head": COMMIT_B, "dirty": False, "changed_paths": [], "origin": "",
    }
    # Nothing about the entry changed for the loader: the descriptor path is what loads.
    reg, force = calls["lifecycle"]
    assert reg.bundles[BUNDLE_ID].activation is None and force == {BUNDLE_ID}
    assert calls["evictions"] == [{"path": "/bundles/demo", "module": "demo.entrypoint"}]


def test_an_activation_at_a_commit_rewrites_the_entry_the_lifecycle_loads_and_evicts_the_previous_path(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_prepare(entry, *, commit, expected_commit, managed_root=None, logger=None):
        seen["entry"] = entry
        seen["commit"] = commit
        seen["expected"] = expected_commit
        return COMMIT_A, {"mode": "snapshot", "commit": COMMIT_A, "path": f"/managed/{BUNDLE_ID}/snapshots/{COMMIT_A}", "origin": "request", "durable": False}

    monkeypatch.setattr(bundle_snapshot, "prepare_activation", fake_prepare)
    client, calls = _mount(
        monkeypatch,
        registry=_registry(require_commit=True),
        previously_loaded={"id": BUNDLE_ID, "path": f"/managed/{BUNDLE_ID}/snapshots/{COMMIT_B}", "module": "demo.entrypoint", "singleton": True},
    )

    response = client.post(
        "/internal/bundles/reload-authority",
        json={"bundle_id": BUNDLE_ID, "commit": "main", "expected_commit": COMMIT_A},
    )

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["activation"]["commit"] == COMMIT_A and receipt["activation"]["mode"] == "snapshot"
    assert seen["commit"] == "main" and seen["expected"] == COMMIT_A
    assert seen["entry"]["activation"] == {"commit": None, "require_commit": True}
    # The entry handed to the lifecycle carries the pinned commit and keeps the flag; the descriptor is untouched.
    reg, force = calls["lifecycle"]
    assert reg.bundles[BUNDLE_ID].activation == BundleActivationConfig(commit=COMMIT_A, require_commit=True)
    registry_dict = calls["set_registry"][0]
    assert registry_dict[BUNDLE_ID]["activation"] == {"commit": COMMIT_A, "require_commit": True}
    # Both the descriptor path and the previously loaded snapshot path were evicted, and the counts add up.
    assert calls["evictions"] == [
        {"path": "/bundles/demo", "module": "demo.entrypoint"},
        {"path": f"/managed/{BUNDLE_ID}/snapshots/{COMMIT_B}", "module": "demo.entrypoint"},
    ]
    assert receipt["eviction"]["sys_modules_deleted"] == 6


def test_a_moved_ref_is_refused_with_409_before_anything_is_evicted(monkeypatch):
    async def fake_prepare(entry, *, commit, expected_commit, managed_root=None, logger=None):
        raise bundle_snapshot.BundleSnapshotError(
            "bundle_activation_commit_mismatch",
            "main resolves to bbbb here, not the expected aaaa: the ref moved after it was read. Nothing was evicted.",
            bundle_id=BUNDLE_ID, ref=commit, resolved=COMMIT_B, expected=expected_commit,
        )

    monkeypatch.setattr(bundle_snapshot, "prepare_activation", fake_prepare)
    client, calls = _mount(monkeypatch, registry=_registry())

    response = client.post(
        "/internal/bundles/reload-authority",
        json={"bundle_id": BUNDLE_ID, "commit": "main", "expected_commit": COMMIT_A},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "bundle_activation_commit_mismatch"
    assert detail["resolved"] == COMMIT_B and detail["expected"] == COMMIT_A and detail["ref"] == "main"
    assert calls["evictions"] == [] and "set_registry" not in calls and "lifecycle" not in calls and "publish" not in calls


def test_require_commit_refuses_a_commitless_reload_with_400_and_names_the_flag(monkeypatch):
    client, calls = _mount(monkeypatch, registry=_registry(require_commit=True))

    response = client.post("/internal/bundles/reload-authority", json={"bundle_id": BUNDLE_ID})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "bundle_activation_commit_required"
    assert "activation.require_commit" in detail["message"] and detail["bundle_id"] == BUNDLE_ID
    assert calls["evictions"] == [] and "lifecycle" not in calls


def test_a_commit_without_a_bundle_id_is_refused(monkeypatch):
    client, calls = _mount(monkeypatch, registry=_registry())
    response = client.post("/internal/bundles/reload-authority", json={"commit": "main"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bundle_activation_requires_bundle_id"
    assert "lifecycle" not in calls
