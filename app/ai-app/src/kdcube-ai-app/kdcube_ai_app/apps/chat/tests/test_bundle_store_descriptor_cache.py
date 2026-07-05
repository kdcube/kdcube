# SPDX-License-Identifier: MIT
"""Per-process descriptor read-cache behavior of the file bundle store.

Covers:
- parse cache hit while bundles.yaml is unchanged (stat on every read),
- invalidation on file change (admin-style hot reload in the same process),
- gated Redis write-back (skipped while unchanged, re-fired on change),
- writer paths invalidating the cache,
- example-bundle share memoization keyed by a source stat signature.
"""

import asyncio
import fnmatch
import os
import time
from pathlib import Path

import pytest
import yaml

from kdcube_ai_app.infra.plugin import bundle_store


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.set_calls = []

    async def set(self, key, value, **kwargs):
        self.data[key] = value
        self.set_calls.append(key)
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def scan_iter(self, match=None):
        for key in list(self.data.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


def _write_descriptor(path: Path, props_marker: str) -> None:
    payload = {
        "bundles": {
            "version": "1",
            "default_bundle_id": "app-one",
            "items": [
                {
                    "id": "app-one",
                    "name": "App One",
                    "path": "/bundles/app-one",
                    "module": "entrypoint",
                    "config": {"marker": props_marker},
                },
                {
                    "id": "app-two",
                    "name": "App Two",
                    "path": "/bundles/app-two",
                    "module": "entrypoint",
                },
            ],
        }
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    # Force a distinct mtime_ns even on coarse-timestamp filesystems.
    bump = time.time_ns() + int(time.monotonic() * 1e3)
    os.utime(path, ns=(bump, bump))


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch, tmp_path):
    bundle_store.clear_descriptor_read_caches()
    bundle_store.clear_example_share_cache()
    # Deterministic share prefix without touching real settings.
    monkeypatch.setenv("PLATFORM_REF", "test-ref")
    # Keep example merging inert and off the real examples tree.
    monkeypatch.setattr(bundle_store, "_examples_enabled", lambda: True)
    empty_examples = tmp_path / "no-examples"
    empty_examples.mkdir(exist_ok=True)
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: empty_examples)
    yield
    bundle_store.clear_descriptor_read_caches()
    bundle_store.clear_example_share_cache()


def _counting_yaml_loader(monkeypatch):
    calls = {"n": 0}
    real = bundle_store._load_yaml_mapping_from_storage

    def wrapper(uri, **kwargs):
        calls["n"] += 1
        return real(uri, **kwargs)

    monkeypatch.setattr(bundle_store, "_load_yaml_mapping_from_storage", wrapper)
    return calls


def _store_for(path: Path) -> "bundle_store._FileBundleDescriptorStore":
    return bundle_store._FileBundleDescriptorStore(bundles_yaml_uri=str(path))


# ── (a) parse cache hit while the file is unchanged ──────────────────────────

def test_unchanged_descriptor_is_parsed_once(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    calls = _counting_yaml_loader(monkeypatch)
    store = _store_for(descriptor)

    first = store.load_registry()
    second = store.load_registry()
    third = store.load_registry_readonly()

    assert calls["n"] == 1
    for loaded in (first, second, third):
        assert loaded is not None
        reg, props_map = loaded
        assert set(reg.bundles) == {"app-one", "app-two"}
        assert props_map["app-one"] == {"marker": "v1"}


def test_cache_is_shared_across_store_instances(monkeypatch, tmp_path):
    # Request paths construct a fresh store per call; the cache must not be
    # per-instance or it would never hit.
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    calls = _counting_yaml_loader(monkeypatch)

    _store_for(descriptor).load_registry()
    _store_for(descriptor).load_registry()

    assert calls["n"] == 1


def test_cache_hit_returns_independent_copies(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    store = _store_for(descriptor)

    _, props_first = store.load_registry()
    props_first["app-one"]["marker"] = "mutated"
    _, props_second = store.load_registry()

    assert props_second["app-one"] == {"marker": "v1"}


# ── (b)+(e) invalidation on file change (admin-style hot reload) ─────────────

def test_file_change_triggers_reparse_and_new_props(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    calls = _counting_yaml_loader(monkeypatch)
    store = _store_for(descriptor)

    assert store.load_bundle_props("app-one") == {"marker": "v1"}
    assert calls["n"] == 1

    # Admin-style rewrite of bundles.yaml (different content → different
    # mtime/size): the next read in the SAME process must serve the new props.
    _write_descriptor(descriptor, "v2")
    assert store.load_bundle_props("app-one") == {"marker": "v2"}
    assert calls["n"] == 2


def test_writer_paths_invalidate_cache_in_process(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    store = _store_for(descriptor)

    loaded = store.load_registry()
    assert loaded is not None
    reg, _ = loaded
    entry = reg.bundles["app-one"]

    # Admin props write goes through _write_mapping → file rewrite + cache pop.
    store.set_bundle_props("app-one", entry, {"marker": "admin-updated"})

    assert store.load_bundle_props("app-one") == {"marker": "admin-updated"}


# ── (c) gated Redis write-back ────────────────────────────────────────────────

def test_get_bundle_props_writeback_gated_on_descriptor_state(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    store = _store_for(descriptor)
    monkeypatch.setattr(
        bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store
    )
    redis = FakeRedis()

    async def scenario():
        props1 = await bundle_store.get_bundle_props(
            redis, tenant="t", project="p", bundle_id="app-one"
        )
        sets_after_first = len(redis.set_calls)
        props2 = await bundle_store.get_bundle_props(
            redis, tenant="t", project="p", bundle_id="app-one"
        )
        sets_after_second = len(redis.set_calls)

        _write_descriptor(descriptor, "v2")
        props3 = await bundle_store.get_bundle_props(
            redis, tenant="t", project="p", bundle_id="app-one"
        )
        sets_after_third = len(redis.set_calls)
        return props1, props2, props3, sets_after_first, sets_after_second, sets_after_third

    p1, p2, p3, s1, s2, s3 = asyncio.run(scenario())

    assert p1 == {"marker": "v1"}
    assert p2 == {"marker": "v1"}
    assert p3 == {"marker": "v2"}
    assert s1 == 1          # first read seeds Redis
    assert s2 == s1         # unchanged file → write-back skipped
    assert s3 == s1 + 1     # file change → write-back fires with fresh props


def test_load_registry_redis_sync_gated_on_descriptor_state(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    store = _store_for(descriptor)
    monkeypatch.setattr(
        bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store
    )
    redis = FakeRedis()

    async def scenario():
        reg1 = await bundle_store.load_registry(redis, "t", "p")
        sets_after_first = len(redis.set_calls)
        reg2 = await bundle_store.load_registry(redis, "t", "p")
        sets_after_second = len(redis.set_calls)

        _write_descriptor(descriptor, "v2")
        reg3 = await bundle_store.load_registry(redis, "t", "p")
        sets_after_third = len(redis.set_calls)
        return reg1, reg2, reg3, sets_after_first, sets_after_second, sets_after_third

    reg1, reg2, reg3, s1, s2, s3 = asyncio.run(scenario())

    assert "app-one" in reg1.bundles and "app-one" in reg2.bundles and "app-one" in reg3.bundles
    assert s1 >= 1          # first load seeds registry + props keys
    assert s2 == s1         # unchanged file → Redis resync skipped
    assert s3 > s1          # file change → resync fires again


def test_writeback_reasserts_after_refresh_interval(monkeypatch, tmp_path):
    descriptor = tmp_path / "bundles.yaml"
    _write_descriptor(descriptor, "v1")
    store = _store_for(descriptor)
    monkeypatch.setattr(
        bundle_store, "_get_authoritative_bundle_store", lambda tenant, project: store
    )
    redis = FakeRedis()

    async def scenario():
        await bundle_store.get_bundle_props(redis, tenant="t", project="p", bundle_id="app-one")
        first = len(redis.set_calls)
        # Simulate the refresh interval elapsing (e.g. Redis was flushed):
        # the write-back must re-assert even though the file is unchanged.
        key = bundle_store._props_key(tenant="t", project="p", bundle_id="app-one")
        with bundle_store._REDIS_WRITEBACK_STATE_LOCK:
            state, _ts = bundle_store._REDIS_WRITEBACK_STATE[key]
            bundle_store._REDIS_WRITEBACK_STATE[key] = (
                state,
                time.monotonic() - bundle_store._REDIS_WRITEBACK_REFRESH_SECONDS - 1.0,
            )
        await bundle_store.get_bundle_props(redis, tenant="t", project="p", bundle_id="app-one")
        return first, len(redis.set_calls)

    first, second = asyncio.run(scenario())
    assert second == first + 1


# ── (f) example-bundle share memoization ─────────────────────────────────────

def _write_example_bundle(root: Path, marker: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "entrypoint.py").write_text(f'BUNDLE_ID = "{marker}"\n', encoding="utf-8")


def test_example_share_is_memoized_per_process(monkeypatch, tmp_path):
    src = tmp_path / "example@1-0"
    shared = tmp_path / "shared"
    _write_example_bundle(src, "v1")

    monkeypatch.setattr(bundle_store, "_shared_bundles_root", lambda: shared)
    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)

    calls = {"n": 0}
    real_hash = bundle_store.compute_dir_sha256

    def counting_hash(*args, **kwargs):
        calls["n"] += 1
        return real_hash(*args, **kwargs)

    monkeypatch.setattr(bundle_store, "compute_dir_sha256", counting_hash)

    first = bundle_store._ensure_example_bundle_shared(src)
    second = bundle_store._ensure_example_bundle_shared(src)

    assert first == second
    assert calls["n"] == 1  # hash + share pass ran once; second call was a memo hit


def test_example_share_memo_detects_source_change(monkeypatch, tmp_path):
    src = tmp_path / "example@1-0"
    shared = tmp_path / "shared"
    _write_example_bundle(src, "v1")

    monkeypatch.setattr(bundle_store, "_shared_bundles_root", lambda: shared)
    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)

    first = bundle_store._ensure_example_bundle_shared(src)
    # Mutate the source (different content → different stat signature): the
    # memo must NOT freeze the old share.
    time.sleep(0.01)
    _write_example_bundle(src, "v2-with-longer-marker")
    second = bundle_store._ensure_example_bundle_shared(src)

    assert first != second
    assert (second / "entrypoint.py").read_text(encoding="utf-8").startswith('BUNDLE_ID = "v2')


def test_example_share_memo_self_heals_when_shared_copy_removed(monkeypatch, tmp_path):
    src = tmp_path / "example@1-0"
    shared = tmp_path / "shared"
    _write_example_bundle(src, "v1")

    monkeypatch.setattr(bundle_store, "_shared_bundles_root", lambda: shared)
    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)

    first = bundle_store._ensure_example_bundle_shared(src)
    # Out-of-band cleanup removed the shared copy: the memo must re-share
    # instead of returning a dangling path.
    import shutil

    shutil.rmtree(first)
    second = bundle_store._ensure_example_bundle_shared(src)

    assert (second / "entrypoint.py").exists()


def test_example_share_failure_is_not_memoized(monkeypatch, tmp_path):
    src = tmp_path / "example@1-0"
    _write_example_bundle(src, "v1")
    shared = tmp_path / "shared"

    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)
    monkeypatch.setattr(
        bundle_store,
        "_example_bundle_lock_path",
        lambda _name: (_ for _ in ()).throw(PermissionError("read-only shared root")),
    )

    # Failure falls back to the source path and must not be cached...
    assert bundle_store._ensure_example_bundle_shared(src) == src

    # ...so once sharing works again, the real share pass runs.
    monkeypatch.setattr(bundle_store, "_shared_bundles_root", lambda: shared)
    monkeypatch.setattr(
        bundle_store,
        "_example_bundle_lock_path",
        lambda name: bundle_store._shared_bundles_root() / ".example-bundle-locks" / f"{name}.lock",
    )
    recovered = bundle_store._ensure_example_bundle_shared(src)
    assert recovered != src
    assert recovered.parent == shared
