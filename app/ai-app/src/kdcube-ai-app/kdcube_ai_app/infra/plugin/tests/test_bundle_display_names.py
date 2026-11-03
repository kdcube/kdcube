"""Descriptor, cache, runtime and management views agree on app display names."""
import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.plugin import bundle_registry, bundle_store, git_bundle
from kdcube_ai_app.apps.chat.sdk.infra.control_plane.storage import summarize_registry_bundles


@pytest.mark.parametrize("fields,expected", [
    ({}, "custom-app@1-0"),
    ({"name": None}, "custom-app@1-0"),
    ({"name": ""}, "custom-app@1-0"),
    ({"name": " \t\n"}, "custom-app@1-0"),
    ({"name": "Custom App"}, "Custom App"),
])
def test_descriptor_and_cached_metadata_keep_a_visible_name(fields, expected):
    entry = bundle_store.BundleEntry(id="custom-app@1-0", path="/bundles/custom", **fields)
    assert entry.name == expected
    assert entry.model_dump()["name"] == expected
    legacy = json.dumps({"bundles": {entry.id: {"id": entry.id, "path": entry.path, **fields}}})
    registry = bundle_store.BundlesRegistry.model_validate_json(legacy)
    assert registry.bundles[entry.id].name == expected
    assert bundle_store.bundle_entry_to_spec(registry.bundles[entry.id]).name == expected
    assert summarize_registry_bundles(registry.bundles)["bundles"][0]["name"] == expected


@pytest.mark.parametrize("name,expected", [(None, "custom-app@1-0"), ("", "custom-app@1-0"), (" \t", "custom-app@1-0"), ("Custom App", "Custom App")])
def test_runtime_specs_and_registry_updates_use_the_same_default(monkeypatch, name, expected):
    monkeypatch.setattr(bundle_store, "_require_allowed_bundles", lambda ids: None)
    normalized = bundle_registry._normalize({"id": "custom-app@1-0", "path": "/bundles/custom", "name": name})
    assert normalized["name"] == expected
    assert bundle_registry.BundleSpec(id="custom-app@1-0", name=name).name == expected


def test_git_migrated_examples_have_labels_with_examples_disabled(monkeypatch, tmp_path):
    ids = ["workspace@2026-03-31-13-36", "user-memories@2026-06-26", "website@2026-07-12", "ported-langgraph-agents@2026-07-13", "kdcube-services@1-0"]
    monkeypatch.setattr(bundle_store, "_examples_enabled", lambda: False)
    monkeypatch.setattr(bundle_store, "_is_example_bundle_id", lambda bid: bid in ids)
    monkeypatch.setattr(bundle_store, "_bundle_allowed", lambda bid: True)
    monkeypatch.setattr(git_bundle, "compute_git_bundle_paths", lambda **kw: SimpleNamespace(bundle_root=tmp_path / kw["bundle_id"]))
    descriptor = {"bundles": {"default_bundle_id": ids[0], "items": [
        {"id": bid, "repo": "https://example.test/apps.git", "ref": "v1", "subdir": bid, "module": "entrypoint"} for bid in ids
    ]}}
    path = tmp_path / "bundles.yaml"
    # JSON is valid YAML and keeps this fixture independent of serializer style.
    original = json.dumps(descriptor)
    path.write_text(original)
    bundle_store.clear_descriptor_read_caches()
    try:
        store = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=str(path))
        for _ in range(2):  # initial parse and cached read
            registry, _props = store.load_registry()
            assert {bid: entry.name for bid, entry in registry.bundles.items()} == {bid: bid for bid in ids}
            assert all(entry.repo == "https://example.test/apps.git" for entry in registry.bundles.values())
        assert path.read_text() == original  # reading labels does not rewrite descriptors
    finally:
        bundle_store.clear_descriptor_read_caches()
