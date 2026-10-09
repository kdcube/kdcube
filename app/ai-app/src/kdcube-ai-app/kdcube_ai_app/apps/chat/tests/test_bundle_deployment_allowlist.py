"""Deployment inventory stays bounded through startup, restart and mutations."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kdcube_ai_app.apps.chat.sdk.config_scopes import PLATFORM_CONFIG
from kdcube_ai_app.apps.chat.tests.test_bundle_store_env_reset import _FakeRedis
from kdcube_ai_app.infra.plugin import bundle_store
from kdcube_ai_app.infra.plugin import bundle_registry


@pytest.fixture
def inventory(monkeypatch, tmp_path):
    allowed = ["selected-app", "selected-service"]
    settings = SimpleNamespace(
        PLATFORM=SimpleNamespace(APPLICATIONS=SimpleNamespace(
            BUNDLES_INCLUDE_EXAMPLES=True, ALLOWED_BUNDLE_IDS=allowed)),
        GATEWAY_COMPONENT="proc")
    monkeypatch.setattr(bundle_store, "get_settings", lambda: settings)
    examples = tmp_path / "examples"
    for bid in ["selected-service", "excluded-example"]:
        path = examples / bid
        path.mkdir(parents=True)
        (path / "entrypoint.py").write_text(f'BUNDLE_ID = "{bid}"\n')
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: examples)
    monkeypatch.setattr(bundle_store, "_ensure_example_bundle_shared", lambda path: path)
    return allowed


def test_examples_filtered_before_materialization(monkeypatch, inventory):
    copied = []
    monkeypatch.setattr(bundle_store, "_ensure_example_bundle_shared", lambda path: copied.append(path.name) or path)
    assert set(bundle_store._load_example_bundles()) == {"selected-service"}
    assert copied == ["selected-service"]


@pytest.mark.asyncio
async def test_file_authority_and_restart_keep_only_selected_apps(monkeypatch, tmp_path, inventory):
    descriptor = tmp_path / "bundles.yaml"
    descriptor.write_text(yaml.safe_dump({"bundles": {
        "default_bundle_id": "selected-app",
        "items": [{"id": bid, "path": "/unused", "module": "entrypoint"}
                  for bid in [*inventory, "excluded-example", "excluded-external"]]}}))
    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor.as_uri())
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda *args: store)
    redis = _FakeRedis()
    # A leftover cache cannot reintroduce an app after a restart.
    await redis.set(bundle_store.redis_key("tenant", "project"), json.dumps({
        "default_bundle_id": "excluded-external", "bundles": {
            "excluded-external": {"id": "excluded-external", "path": "/unused"}}}))
    for _ in range(2):
        bundle_store.clear_descriptor_read_caches()
        loaded = await bundle_store.load_registry(redis, tenant="tenant", project="project")
        assert set(loaded.bundles) == {*inventory, bundle_store.ADMIN_BUNDLE_ID}
        assert loaded.default_bundle_id == "selected-app"
        readonly = await bundle_store.load_registry_from_authority_readonly("tenant", "project")
        assert set(readonly.bundles) == set(loaded.bundles)


@pytest.mark.asyncio
async def test_legacy_redis_registry_evicts_disallowed_apps(monkeypatch, inventory):
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda *args: None)
    redis = _FakeRedis()
    await redis.set(bundle_store.redis_key("tenant", "project"), json.dumps({
        "default_bundle_id": "selected-app", "bundles": {
            bid: {"id": bid, "path": "/unused"}
            for bid in ["selected-app", "excluded-external"]}}))
    loaded = await bundle_store.load_registry(redis, "tenant", "project")
    assert set(loaded.bundles) == {*inventory, bundle_store.ADMIN_BUNDLE_ID}
    persisted = bundle_store.BundlesRegistry.model_validate_json(await redis.get(bundle_store.redis_key("tenant", "project")))
    assert set(persisted.bundles) == set(loaded.bundles)


@pytest.mark.parametrize("op", ["merge", "replace"])
def test_mutation_refuses_disallowed_app(op, inventory):
    with pytest.raises(ValueError, match="excluded-external"):
        bundle_store.apply_update(bundle_store.BundlesRegistry(), op,
                                  {"excluded-external": {"path": "/unused"}})


@pytest.mark.asyncio
async def test_direct_save_refuses_disallowed_app(inventory):
    redis = _FakeRedis()
    registry = bundle_store.BundlesRegistry(bundles={
        "excluded-external": bundle_store.BundleEntry(id="excluded-external", path="/unused")})
    with pytest.raises(ValueError, match="excluded-external"):
        await bundle_store.save_registry(redis, registry, "tenant", "project")
    assert redis.data == {}


def test_empty_allowlist_keeps_only_internal_admin(monkeypatch, inventory):
    inventory.clear()
    assert bundle_store._load_example_bundles() == {}
    registry = bundle_store._ensure_admin_bundle(bundle_store.BundlesRegistry())
    assert set(registry.bundles) == {bundle_store.ADMIN_BUNDLE_ID}


def test_cached_resolution_and_override_cannot_execute_excluded_app(monkeypatch, inventory):
    monkeypatch.setattr(bundle_registry, "_REGISTRY", {
        "excluded-external": {"id": "excluded-external", "path": "/unused"}})
    assert bundle_registry.resolve_bundle("excluded-external") is None
    with pytest.raises(ValueError, match="excluded-external"):
        bundle_registry.resolve_bundle(None, override={"id": "excluded-external", "path": "/unused"})


@pytest.mark.asyncio
async def test_ingress_snapshot_cannot_restore_excluded_app(monkeypatch, inventory):
    monkeypatch.setenv("GATEWAY_COMPONENT", "ingress")
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda *args: None)
    redis = _FakeRedis()
    await redis.set(bundle_store.redis_key("tenant", "project"), json.dumps({
        "default_bundle_id": "selected-app", "bundles": {
            bid: {"id": bid, "path": "/unused"}
            for bid in ["selected-app", "excluded-external"]}}))
    loaded = await bundle_registry.load_persisted_registry_from_runtime_ctx(
        SimpleNamespace(redis_async=redis), "tenant", "project")
    assert set(loaded.bundles) == {"selected-app", bundle_store.ADMIN_BUNDLE_ID}


@pytest.mark.parametrize("value", ["all", [""], [" app "], [1], ["app", "app"]])
def test_invalid_descriptor_allowlist_fails_closed(monkeypatch, value):
    from kdcube_ai_app.apps.chat.sdk import config_scopes
    monkeypatch.setattr(config_scopes, "_load_assembly_plain", lambda _path: value)
    with pytest.raises(ValueError, match="distinct non-empty"):
        PLATFORM_CONFIG()._assembly_bundle_allowlist("platform.services.proc.bundles.allowed_bundle_ids")


def test_unconfigured_allowlist_preserves_existing_inventory(monkeypatch, inventory):
    monkeypatch.setattr(bundle_store, "_allowed_bundle_ids", lambda: None)
    assert set(bundle_store._load_example_bundles()) == {"selected-service", "excluded-example"}


@pytest.mark.asyncio
async def test_explicit_git_app_retains_migrated_example_id_with_examples_off(monkeypatch, tmp_path, inventory):
    settings = bundle_store.get_settings()
    settings.PLATFORM.APPLICATIONS.BUNDLES_INCLUDE_EXAMPLES = False
    descriptor = tmp_path / "bundles.yaml"
    descriptor.write_text(yaml.safe_dump({"bundles": {
        "default_bundle_id": "selected-service",
        "items": [{"id": "selected-service", "repo": "https://example.test/services.git",
                   "ref": "1" * 40, "subdir": "apps/services", "module": "entrypoint",
                   "config": {"feature": "external"}},
                  {"id": "excluded-example"}]}}))
    store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=descriptor.as_uri())
    monkeypatch.setattr(bundle_store, "_get_authoritative_bundle_store", lambda *args: store)
    redis = _FakeRedis()
    for _ in range(2):
        bundle_store.clear_descriptor_read_caches()
        loaded = await bundle_store.load_registry(redis, "tenant", "project")
        assert set(loaded.bundles) == {"selected-service", bundle_store.ADMIN_BUNDLE_ID}
        assert loaded.bundles["selected-service"].repo == "https://example.test/services.git"
        assert loaded.default_bundle_id == "selected-service"
    await bundle_store.save_registry(redis, loaded, "tenant", "project", replace=True,
                                     props_map={"selected-service": {"feature": "external"}})
    saved = yaml.safe_load(descriptor.read_text())
    assert saved["bundles"]["items"][0]["repo"] == "https://example.test/services.git"
    assert saved["bundles"]["items"][0]["config"] == {"feature": "external"}


def test_startup_seed_preserves_explicit_external_source_with_examples_off(monkeypatch, inventory):
    bundle_store.get_settings().PLATFORM.APPLICATIONS.BUNDLES_INCLUDE_EXAMPLES = False
    entries, props = bundle_store._drop_disabled_example_bundle_entries({
        "selected-service": {"repo": "https://example.test/services.git"},
        "excluded-example": {}}, {"selected-service": {"feature": "external"}})
    assert set(entries) == {"selected-service"}
    assert props == {"selected-service": {"feature": "external"}}
