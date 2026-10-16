"""W389 / W883: the parsed descriptor registry is kept between reads and dropped on every config change.

Operator, W883, verbatim: "the first cache which you said exist is invalidated when soemthing changes.
when config changes. if we have something additional it also should be invalidated".
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
import yaml

from kdcube_ai_app.infra.plugin import bundle_store
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry


def _write(path, name="one"):
    items = [{"id": f"app-{n}@1-0", "path": f"/tmp/app-{n}", "module": "entrypoint",
              "config": {"feature": {"name": name if n == 1 else f"app-{n}", "list": [1, {"deep": n}]}}} for n in range(4)]
    path.write_text(yaml.safe_dump({"bundles": {"version": "1", "items": items}}), encoding="utf-8")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    path = tmp_path / "bundles.yaml"
    _write(path)
    bundle_store.clear_descriptor_read_caches()
    builds = []
    real = bundle_store._FileBundleDescriptorStore._registry_from_payload

    def counting(self, payload):
        builds.append(1)
        return real(self, payload)

    monkeypatch.setattr(bundle_store._FileBundleDescriptorStore, "_registry_from_payload", counting)
    s = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=path.as_uri())
    yield SimpleNamespace(store=s, path=path, builds=builds)
    bundle_store.clear_descriptor_read_caches()


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_the_registry_is_parsed_once_for_an_unchanged_descriptor(store):
    for _ in range(3):
        store.store.load_registry_readonly()
        store.store.load_bundle_props("app-1@1-0")
    assert len(store.builds) == 1


def test_a_changed_descriptor_file_is_parsed_again_on_the_next_read(store):
    assert store.store.load_bundle_props("app-1@1-0")["feature"]["name"] == "one"
    _write(store.path, name="a different longer name")
    _bump(store.path)
    assert store.store.load_bundle_props("app-1@1-0")["feature"]["name"] == "a different longer name"
    assert len(store.builds) == 2


def test_a_same_size_rewrite_within_the_same_timestamp_is_still_seen(store):
    """The content hash is the signal (operator, W883), so (mtime, size) cannot hide a rewrite."""
    assert store.store.load_bundle_props("app-1@1-0")["feature"]["name"] == "one"
    before = store.path.stat()
    _write(store.path, name="two")  # same length as "one"
    os.utime(store.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = store.path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert store.store.load_bundle_props("app-1@1-0")["feature"]["name"] == "two"


def test_a_descriptor_without_a_local_path_is_not_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path / "no-examples")
    path = tmp_path / "bundles.yaml"
    _write(path)
    bundle_store.clear_descriptor_read_caches()
    s = bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=path.as_uri())
    monkeypatch.setattr(s, "_descriptor_local_path", lambda: None)
    s.load_registry_readonly()
    assert bundle_store._DESCRIPTOR_REGISTRY_CACHE == {}


def test_a_platform_write_drops_the_parse_even_without_a_visible_stat_change(store, monkeypatch):
    store.store.load_bundle_props("app-1@1-0")
    entry = BundleEntry(id="app-1@1-0", path="/tmp/app-1", module="entrypoint")
    store.store.set_bundle_props("app-1@1-0", entry, {"feature": {"name": "two"}})
    assert store.store.load_bundle_props("app-1@1-0")["feature"]["name"] == "two"


@pytest.mark.parametrize("drop", ["clear_descriptor_read_caches", "forget_descriptor_registry"])
def test_an_explicit_drop_parses_again(store, drop):
    store.store.load_registry_readonly()
    getattr(bundle_store, drop)()
    store.store.load_registry_readonly()
    assert len(store.builds) == 2


def test_callers_get_copies_that_never_reach_the_kept_parse(store):
    reg, props = store.store.load_registry_readonly()
    reg.bundles["app-1@1-0"].path = "/changed"
    reg.bundles.pop("app-2@1-0")
    props["app-1@1-0"]["feature"]["list"].append("changed")
    one = store.store.load_bundle_props("app-1@1-0")
    one["feature"]["name"] = "changed"
    reg2, props2 = store.store.load_registry_readonly()
    assert reg2.bundles["app-1@1-0"].path == "/tmp/app-1" and "app-2@1-0" in reg2.bundles
    assert props2["app-1@1-0"]["feature"] == {"name": "one", "list": [1, {"deep": 1}]}
    assert len(store.builds) == 1


def test_a_registry_read_that_needs_no_props_returns_none(store):
    reg, props = store.store.load_registry_readonly(include_props=False)
    assert props == {} and "app-1@1-0" in reg.bundles


@pytest.mark.parametrize("handler", ["_do_reload_bundles_from_authority", "internal_remove_bundle"])
def test_a_bundle_reload_or_removal_drops_the_parse(monkeypatch, handler):
    from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations

    dropped = []
    monkeypatch.setattr(bundle_store, "forget_descriptor_registry", lambda: dropped.append(1))
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), app=SimpleNamespace(state=SimpleNamespace()))
    call = (integrations._do_reload_bundles_from_authority(request, SimpleNamespace(), None)
            if handler == "_do_reload_bundles_from_authority"
            else integrations.internal_remove_bundle(SimpleNamespace(bundle_id="app-1@1-0", tenant=None, project=None), request))
    try:
        asyncio.run(call)
    except Exception:
        pass  # only the drop is under test; the rest of the handler needs a runtime
    assert dropped == [1]


def test_a_reload_pin_set_on_a_returned_registry_never_reaches_later_reads(store):
    """claude-ops, W883 review: integrations' reload does reg.bundles[requested_bundle_id] = target_entry."""
    reg, _ = store.store.load_registry_readonly()
    pinned = reg.bundles["app-1@1-0"].model_copy(update={"path": "/snapshots/pinned"})
    reg.bundles["app-1@1-0"] = pinned  # the reload's in-memory pin
    again, _ = store.store.load_registry_readonly()
    assert again.bundles["app-1@1-0"].path == "/tmp/app-1"
    assert again is not reg and again.bundles is not reg.bundles
