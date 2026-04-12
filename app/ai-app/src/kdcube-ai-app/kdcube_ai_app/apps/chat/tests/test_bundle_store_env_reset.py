import fnmatch
import json

import pytest

from kdcube_ai_app.infra.plugin import bundle_store


class _FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, *args, **kwargs):
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        return 1

    async def keys(self, pattern):
        return [key for key in self.data.keys() if fnmatch.fnmatch(key, pattern)]


class _FakeAuthoritativeStore:
    def __init__(self, reg=None, props_map=None):
        self.reg = reg
        self.props_map = props_map or {}
        self.saved = []
        self.props_updates = []

    def load_registry(self):
        if self.reg is None:
            return None
        return self.reg, dict(self.props_map)

    def save_registry(self, reg, props_map, *, replace):
        self.reg = reg
        self.props_map = dict(props_map)
        self.saved.append((reg, dict(props_map), replace))

    def load_bundle_props(self, bundle_id):
        return dict(self.props_map.get(bundle_id) or {})

    def set_bundle_props(self, bundle_id, entry, props):
        self.props_map[bundle_id] = dict(props)
        self.props_updates.append((bundle_id, entry, dict(props)))


@pytest.mark.asyncio
async def test_reset_registry_from_env_removes_stale_bundle_props(monkeypatch):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))

    monkeypatch.setenv(
        "AGENTIC_BUNDLES_JSON",
        json.dumps(
            {
                "default_bundle_id": bundle_id,
                "bundles": {
                    bundle_id: {
                        "id": bundle_id,
                        "path": "/bundles/demo.bundle",
                        "module": "entrypoint",
                        "config": {
                            "role_models": {
                                "solver.react.v2.decision.v2.strong": {
                                    "provider": "anthropic",
                                    "model": "claude-sonnet-4-6",
                                }
                            }
                        },
                    }
                },
            }
        ),
    )

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert json.loads(redis.data[props_key]) == {
        "role_models": {
            "solver.react.v2.decision.v2.strong": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            }
        }
    }

    monkeypatch.setenv(
        "AGENTIC_BUNDLES_JSON",
        json.dumps(
            {
                "default_bundle_id": bundle_id,
                "bundles": {
                    bundle_id: {
                        "id": bundle_id,
                        "path": "/bundles/demo.bundle",
                        "module": "entrypoint",
                    }
                },
            }
        ),
    )

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert await redis.get(props_key) is None


@pytest.mark.asyncio
async def test_load_registry_falls_back_to_authoritative_store(monkeypatch):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)

    reg = bundle_store.BundlesRegistry(
        default_bundle_id=bundle_id,
        bundles={
            bundle_id: bundle_store.BundleEntry(
                id=bundle_id,
                path="/bundles/demo.bundle",
                module="entrypoint",
            )
        },
    )
    store = _FakeAuthoritativeStore(
        reg=reg,
        props_map={bundle_id: {"feature": {"enabled": True}}},
    )

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

    loaded = await bundle_store.load_registry(redis, tenant=tenant, project=project)

    assert bundle_id in loaded.bundles
    assert json.loads(redis.data[props_key]) == {"feature": {"enabled": True}}


@pytest.mark.asyncio
async def test_put_bundle_props_persists_to_authoritative_store(monkeypatch):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"

    reg = bundle_store.BundlesRegistry(
        default_bundle_id=bundle_id,
        bundles={
            bundle_id: bundle_store.BundleEntry(
                id=bundle_id,
                path="/bundles/demo.bundle",
                module="entrypoint",
            )
        },
    )
    store = _FakeAuthoritativeStore(reg=reg)

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

    await bundle_store.save_registry(redis, reg, tenant=tenant, project=project)
    await bundle_store.put_bundle_props(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        props={"feature": {"enabled": True}},
    )

    assert store.saved
    saved_reg, saved_props, replace = store.saved[-1]
    assert replace is False
    assert bundle_id in saved_reg.bundles
    assert saved_props[bundle_id] == {"feature": {"enabled": True}}


@pytest.mark.asyncio
async def test_reset_registry_from_env_replaces_authoritative_store(monkeypatch):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    old_bundle_id = "old.bundle"
    new_bundle_id = "new.bundle"

    existing = bundle_store.BundlesRegistry(
        default_bundle_id=old_bundle_id,
        bundles={
            old_bundle_id: bundle_store.BundleEntry(
                id=old_bundle_id,
                path="/bundles/old.bundle",
                module="entrypoint",
            )
        },
    )
    store = _FakeAuthoritativeStore(reg=existing, props_map={old_bundle_id: {"old": True}})

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)
    monkeypatch.setenv(
        "AGENTIC_BUNDLES_JSON",
        json.dumps(
            {
                "default_bundle_id": new_bundle_id,
                "bundles": {
                    new_bundle_id: {
                        "id": new_bundle_id,
                        "path": "/bundles/new.bundle",
                        "module": "entrypoint",
                        "config": {"feature": {"enabled": True}},
                    }
                },
            }
        ),
    )

    reg = await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert reg.default_bundle_id == new_bundle_id
    assert store.saved
    saved_reg, saved_props, replace = store.saved[-1]
    assert replace is True
    assert new_bundle_id in saved_reg.bundles
    assert saved_props == {new_bundle_id: {"feature": {"enabled": True}}}
