"""W389: a descriptor read scans the examples folder once, and a props read parses only its bundle."""
from __future__ import annotations

import json
import os

import yaml

from kdcube_ai_app.infra.plugin import bundle_store


def _descriptor(tmp_path, *, props_for_b="one"):
    items = [
        {"id": f"app-{n}@1-0", "path": str(tmp_path / f"app-{n}"), "module": "entrypoint",
         "config": {"feature": {"name": f"app-{n}", "list": [1, 2, {"deep": n}]}, "flag": {"type": "value", "value": {"on": True}}}}
        for n in range(6)
    ]
    items[1]["config"]["feature"]["name"] = props_for_b
    path = tmp_path / "bundles.yaml"
    path.write_text(yaml.safe_dump({"bundles": {"version": "1", "items": items}}), encoding="utf-8")
    return path


def _store(path):
    bundle_store.clear_descriptor_read_caches()
    return bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=path.as_uri())


def test_props_read_equals_the_full_build_for_every_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    store = _store(_descriptor(tmp_path))
    _reg, props_map = store.load_registry_readonly()
    for n in range(6):
        bid = f"app-{n}@1-0"
        assert json.dumps(store.load_bundle_props(bid), sort_keys=True) == json.dumps(props_map[bid], sort_keys=True)
    assert store.load_bundle_props("absent@1-0") == {}


def test_one_registry_read_scans_the_examples_folder_once(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    scans = []
    real = bundle_store._scan_example_bundle_ids
    monkeypatch.setattr(bundle_store, "_scan_example_bundle_ids", lambda **kw: scans.append(kw) or real(**kw))
    store = _store(_descriptor(tmp_path))
    store.load_registry_readonly()
    assert len(scans) <= 2  # once per mode (respecting the enabled flag, and not), not once per bundle
    scans.clear()
    store.load_registry_readonly()
    assert scans == []  # W883: an unchanged descriptor is not parsed again, so nothing is rescanned
    bundle_store.forget_descriptor_registry()
    store.load_registry_readonly()
    assert 1 <= len(scans) <= 2  # the memo lives for one build only: the next build scans again


def test_a_caller_changing_returned_props_does_not_change_the_next_read(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    store = _store(_descriptor(tmp_path))
    first = store.load_bundle_props("app-1@1-0")
    first["feature"]["list"].append("mutated")
    first["flag"]["on"] = False
    again = store.load_bundle_props("app-1@1-0")
    assert again["feature"]["list"] == [1, 2, {"deep": 1}] and again["flag"] == {"on": True}


def test_a_descriptor_edit_is_seen_on_the_next_props_read(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    path = _descriptor(tmp_path, props_for_b="one")
    store = _store(path)
    assert store.load_bundle_props("app-1@1-0")["feature"]["name"] == "one"
    stat = path.stat()
    _descriptor(tmp_path, props_for_b="a longer name")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert store.load_bundle_props("app-1@1-0")["feature"]["name"] == "a longer name"
