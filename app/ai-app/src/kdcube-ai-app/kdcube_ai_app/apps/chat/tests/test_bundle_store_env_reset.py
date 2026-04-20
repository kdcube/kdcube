import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kdcube_ai_app.infra.plugin import bundle_store


class _FakeRedis:
    def __init__(self):
        self.data = {}
        self.published = []

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

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


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
async def test_reset_registry_from_env_removes_stale_bundle_props(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    descriptor_path = tmp_path / "bundles.yaml"

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))

    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": bundle_id,
                    "items": [
                        {
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
                    ],
                }
            },
            sort_keys=False,
        )
    )
    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(descriptor_path.resolve()))

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert json.loads(redis.data[props_key]) == {
        "role_models": {
            "solver.react.v2.decision.v2.strong": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            }
        }
    }

    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": bundle_id,
                    "items": [
                        {
                            "id": bundle_id,
                            "path": "/bundles/demo.bundle",
                            "module": "entrypoint",
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )

    await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert await redis.get(props_key) is None


@pytest.mark.asyncio
async def test_reset_registry_from_env_uses_descriptor_authority_when_env_is_unset(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"

    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": bundle_id,
                    "items": [
                        {
                            "id": bundle_id,
                            "path": "/bundles/demo.bundle",
                            "module": "entrypoint",
                            "config": {
                                "feature": {
                                    "enabled": True,
                                }
                            },
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )

    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(descriptor_path.resolve()))
    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))

    reg = await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert reg.default_bundle_id == bundle_id
    assert bundle_id in reg.bundles
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    assert json.loads(redis.data[props_key]) == {"feature": {"enabled": True}}


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
async def test_load_registry_file_authority_overrides_stale_redis(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    stale_bundle_id = "stale.bundle"
    fresh_bundle_id = "fresh.bundle"

    redis.data[bundle_store.redis_key(tenant, project)] = bundle_store.BundlesRegistry(
        default_bundle_id=stale_bundle_id,
        bundles={
            stale_bundle_id: bundle_store.BundleEntry(
                id=stale_bundle_id,
                path="/bundles/stale.bundle",
                module="entrypoint",
            )
        },
    ).model_dump_json()
    redis.data[bundle_store._props_key(tenant=tenant, project=project, bundle_id=stale_bundle_id)] = json.dumps(
        {"stale": True}
    )

    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": fresh_bundle_id,
                    "items": [
                        {
                            "id": fresh_bundle_id,
                            "path": "/bundles/fresh.bundle",
                            "module": "entrypoint",
                            "config": {
                                "feature": {
                                    "enabled": True,
                                }
                            },
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )

    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor_path.resolve().as_uri())

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

    loaded = await bundle_store.load_registry(redis, tenant=tenant, project=project)

    assert loaded.default_bundle_id == fresh_bundle_id
    assert set(loaded.bundles.keys()) == {fresh_bundle_id, bundle_store.ADMIN_BUNDLE_ID}
    assert await redis.get(bundle_store._props_key(tenant=tenant, project=project, bundle_id=stale_bundle_id)) is None
    assert json.loads(redis.data[bundle_store._props_key(tenant=tenant, project=project, bundle_id=fresh_bundle_id)]) == {
        "feature": {"enabled": True}
    }


@pytest.mark.asyncio
async def test_load_registry_file_authority_preserves_default_example_bundle(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "example.bundle"

    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": bundle_id,
                    "items": [],
                }
            },
            sort_keys=False,
        )
    )

    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor_path.resolve().as_uri())

    monkeypatch.setattr(
        bundle_store,
        "_load_example_bundles",
        lambda: {
            bundle_id: bundle_store.BundleEntry(
                id=bundle_id,
                path="/bundles/example.bundle",
                module="entrypoint",
            )
        },
    )
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

    loaded = await bundle_store.load_registry(redis, tenant=tenant, project=project)

    assert loaded.default_bundle_id == bundle_id
    assert bundle_id in loaded.bundles


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
async def test_put_bundle_props_publishes_props_update(monkeypatch):
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
        actor="tester",
        source="unit-test",
    )

    assert len(redis.published) == 1
    channel, message = redis.published[0]
    assert channel == bundle_store.props_update_channel(tenant, project)
    payload = json.loads(message)
    assert payload["type"] == "bundles.props.update"
    assert payload["bundle_id"] == bundle_id
    assert payload["tenant"] == tenant
    assert payload["project"] == project
    assert payload["updated_by"] == "tester"
    assert payload["source"] == "unit-test"


@pytest.mark.asyncio
async def test_reset_registry_from_env_replaces_redis_from_local_descriptor(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    old_bundle_id = "old.bundle"
    new_bundle_id = "new.bundle"
    descriptor_path = tmp_path / "bundles.yaml"

    redis.data[bundle_store.redis_key(tenant, project)] = bundle_store.BundlesRegistry(
        default_bundle_id=old_bundle_id,
        bundles={
            old_bundle_id: bundle_store.BundleEntry(
                id=old_bundle_id,
                path="/bundles/old.bundle",
                module="entrypoint",
            )
        },
    ).model_dump_json()
    old_props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=old_bundle_id)
    redis.data[old_props_key] = json.dumps({"old": True})

    descriptor_path.write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "default_bundle_id": new_bundle_id,
                    "items": [
                        {
                            "id": new_bundle_id,
                            "path": "/bundles/new.bundle",
                            "module": "entrypoint",
                            "config": {"feature": {"enabled": True}},
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(descriptor_path.resolve()))

    reg = await bundle_store.reset_registry_from_env(redis, tenant=tenant, project=project)

    assert reg.default_bundle_id == new_bundle_id
    assert new_bundle_id in reg.bundles
    assert old_bundle_id not in reg.bundles
    assert await redis.get(old_props_key) is None
    new_props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=new_bundle_id)
    assert json.loads(redis.data[new_props_key]) == {"feature": {"enabled": True}}


@pytest.mark.asyncio
async def test_reload_registry_from_authority_replaces_redis_from_authoritative_store(monkeypatch):
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
    store = _FakeAuthoritativeStore(
        reg=reg,
        props_map={bundle_id: {"feature": {"enabled": True}}},
    )

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

    loaded = await bundle_store.reload_registry_from_authority(redis, tenant=tenant, project=project)

    assert loaded.default_bundle_id == bundle_id
    assert bundle_id in loaded.bundles
    props_key = bundle_store._props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    assert json.loads(redis.data[props_key]) == {"feature": {"enabled": True}}
    assert store.saved == []


@pytest.mark.asyncio
async def test_reload_registry_from_authority_does_not_rewrite_local_descriptor_file(monkeypatch, tmp_path: Path):
    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "example.bundle"

    descriptor_path = tmp_path / "bundles.yaml"
    original_payload = {
        "bundles": {
            "version": "1",
            "default_bundle_id": bundle_id,
            "items": [
                {
                    "id": bundle_id,
                    "path": "/bundles/example.bundle",
                    "module": "entrypoint",
                }
            ],
        }
    }
    descriptor_path.write_text(yaml.safe_dump(original_payload, sort_keys=False))

    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor_path.resolve().as_uri())

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)
    monkeypatch.setattr(bundle_store, "_reserved_bundle_ids", lambda: {"kdcube.admin", bundle_id})
    monkeypatch.setattr(
        bundle_store,
        "_reserved_bundle_entry",
        lambda bid: bundle_store.BundleEntry(id=bid, path="/bundles/example.bundle", module="entrypoint")
        if bid == bundle_id else None,
    )

    loaded = await bundle_store.reload_registry_from_authority(redis, tenant=tenant, project=project)

    assert loaded.default_bundle_id == bundle_id
    assert descriptor_path.read_text() == yaml.safe_dump(original_payload, sort_keys=False)


def test_file_bundle_descriptor_store_reads_config_blocks(tmp_path: Path):
    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text(
        """
bundles:
  version: "1"
  items:
    - id: demo.bundle
      path: /bundles/demo.bundle
      module: entrypoint
      config:
        feature:
          enabled: true
  default_bundle_id: demo.bundle
""".strip()
    )

    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor_path.resolve().as_uri())
    loaded = store.load_registry()

    assert loaded is not None
    reg, props_map = loaded
    assert reg.default_bundle_id == "demo.bundle"
    assert reg.bundles["demo.bundle"].path == "/bundles/demo.bundle"
    assert props_map == {"demo.bundle": {"feature": {"enabled": True}}}


def test_authoritative_bundle_store_prefers_aws_sm_over_mounted_bundles_yaml(monkeypatch, tmp_path: Path):
    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text("bundles:\n  version: '1'\n  items: []\n")

    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(descriptor_path.resolve()))
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.manager.build_secrets_manager_config",
        lambda _settings: SimpleNamespace(
            provider="aws-sm",
            aws_sm_prefix="kdcube/demo/proj",
            aws_region="eu-west-1",
            aws_profile=None,
            redis_url=None,
        ),
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: object())

    store = bundle_store._get_authoritative_bundle_store("demo", "demo-project")

    assert isinstance(store, bundle_store._AwsBundleDescriptorStore)


def test_describe_authoritative_bundle_store_reports_bundles_yaml(monkeypatch, tmp_path: Path):
    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text("bundles:\n  version: '1'\n  items: []\n")

    monkeypatch.setenv("BUNDLES_YAML_DESCRIPTOR_PATH", str(descriptor_path.resolve()))
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.manager.build_secrets_manager_config",
        lambda _settings: SimpleNamespace(provider="file"),
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: object())

    descriptor = bundle_store.describe_authoritative_bundle_store("demo", "demo-project")

    assert descriptor == {
        "kind": "bundles-yaml",
        "label": "bundles.yaml",
        "description": "Reload from the mounted bundle descriptor file.",
        "detail": str(descriptor_path.resolve()),
    }


def test_describe_authoritative_bundle_store_reports_aws_sm(monkeypatch):
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.manager.build_secrets_manager_config",
        lambda _settings: SimpleNamespace(
            provider="aws-sm",
            aws_sm_prefix="kdcube/demo/proj",
            aws_region="eu-west-1",
            aws_profile=None,
            redis_url=None,
        ),
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: object())

    descriptor = bundle_store.describe_authoritative_bundle_store("demo", "demo-project")

    assert descriptor == {
        "kind": "aws-sm",
        "label": "AWS Secrets Manager",
        "description": "Reload from the live AWS bundle descriptor store.",
        "detail": "kdcube/demo/proj",
    }


def test_authoritative_bundle_store_prefers_aws_sm_when_no_local_descriptor(monkeypatch):
    monkeypatch.delenv("BUNDLES_YAML_DESCRIPTOR_PATH", raising=False)
    monkeypatch.delenv("PLATFORM_DESCRIPTORS_DIR", raising=False)
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.manager.build_secrets_manager_config",
        lambda _settings: SimpleNamespace(
            provider="aws-sm",
            aws_sm_prefix="kdcube/demo/proj",
            aws_region="eu-west-1",
            aws_profile=None,
            redis_url=None,
        ),
    )
    monkeypatch.setattr(bundle_store, "get_settings", lambda: object())

    store = bundle_store._get_authoritative_bundle_store("demo", "demo-project")

    assert isinstance(store, bundle_store._AwsBundleDescriptorStore)


@pytest.mark.asyncio
async def test_put_bundle_props_rewrites_local_bundles_yaml(monkeypatch, tmp_path: Path):
    descriptor_path = tmp_path / "bundles.yaml"
    descriptor_path.write_text(
        """
bundles:
  version: "1"
  items:
    - id: demo.bundle
      path: /bundles/demo.bundle
      module: entrypoint
      config:
        feature:
          enabled: false
  default_bundle_id: demo.bundle
""".strip()
    )

    redis = _FakeRedis()
    tenant = "demo"
    project = "demo-project"
    bundle_id = "demo.bundle"
    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor_path.resolve().as_uri())

    monkeypatch.setattr(bundle_store, "_merge_example_bundles", lambda reg: (reg, False))
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store)

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

    await bundle_store.save_registry(redis, reg, tenant=tenant, project=project)
    await bundle_store.put_bundle_props(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        props={"feature": {"enabled": True}, "limits": {"n": 3}},
    )

    written = yaml.safe_load(descriptor_path.read_text())
    items = written["bundles"]["items"]
    bundle_item = next(item for item in items if item["id"] == bundle_id)
    assert bundle_item["config"] == {
        "feature": {"enabled": True},
        "limits": {"n": 3},
    }


@pytest.mark.asyncio
async def test_force_env_reset_is_disabled_for_aws_sm_authority(monkeypatch):
    redis = _FakeRedis()

    monkeypatch.setattr(
        bundle_store,
        "get_settings",
        lambda: SimpleNamespace(
            BUNDLES_FORCE_ENV_ON_STARTUP=True,
            BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS=60,
            TENANT="demo",
            PROJECT="demo-project",
        ),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.manager.build_secrets_manager_config",
        lambda _settings: SimpleNamespace(
            provider="aws-sm",
            aws_sm_prefix="kdcube/demo/demo-project",
            aws_region="eu-west-1",
            aws_profile=None,
            redis_url=None,
        ),
    )

    called = {"reset": 0}

    async def _fake_reset(*args, **kwargs):
        called["reset"] += 1
        raise AssertionError("reset_registry_from_env should not be called for aws-sm")

    monkeypatch.setattr(bundle_store, "reset_registry_from_env", _fake_reset)

    result = await bundle_store.force_env_reset_if_requested(
        redis,
        tenant="demo",
        project="demo-project",
        actor="startup-env",
    )

    assert result is None
    assert called["reset"] == 0
