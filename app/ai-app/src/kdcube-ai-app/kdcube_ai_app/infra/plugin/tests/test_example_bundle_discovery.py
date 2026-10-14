# SPDX-License-Identifier: MIT
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from kdcube_ai_app.infra.plugin import bundle_loader
from kdcube_ai_app.infra.plugin import example_bundle_discovery as discovery


@pytest.fixture(autouse=True)
def clear_discovery():
    discovery.clear_example_discovery_cache()
    yield
    discovery.clear_example_discovery_cache()


@pytest.mark.parametrize("result", ["canonical-example@1-0", None])
def test_immutable_entrypoint_discovery_is_single_flight_and_keeps_negative_results(monkeypatch, tmp_path, result):
    calls = []

    def discover(path, module):
        calls.append((path, module))
        # Release the GIL during cold discovery so other readers race the miss.
        time.sleep(0.01)
        return result

    monkeypatch.setattr(bundle_loader, "get_declared_bundle_id", discover)
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(discovery.declared_example_bundle_id, [tmp_path] * 32))
    assert answers == [result] * 32
    assert calls == [(tmp_path.resolve(), "entrypoint")]


def test_content_addressed_copy_has_its_own_discovery_and_reset_is_explicit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bundle_loader, "get_declared_bundle_id", lambda path, module: calls.append(path) or path.name)
    a, b = tmp_path / "image-a", tmp_path / "image-b"
    assert discovery.declared_example_bundle_id(a) == "image-a"
    assert discovery.declared_example_bundle_id(a / ".") == "image-a"
    assert discovery.declared_example_bundle_id(b) == "image-b"
    assert len(calls) == 2
    discovery.clear_example_discovery_cache()
    assert discovery.declared_example_bundle_id(a) == "image-a"
    assert len(calls) == 3


def test_discovery_metadata_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(bundle_loader, "get_declared_bundle_id", lambda path, module: None)
    for index in range(260):
        discovery.declared_example_bundle_id(tmp_path / str(index))
    assert discovery._discover.cache_info().currsize == 256


def test_example_admission_stays_fresh_after_canonical_id_discovery(monkeypatch, tmp_path):
    from kdcube_ai_app.infra.plugin import bundle_store

    example = tmp_path / "directory-alias"
    example.mkdir()
    entrypoint = example / "entrypoint.py"
    entrypoint.write_text("# immutable example\n")
    calls = []
    monkeypatch.setattr(bundle_loader, "get_declared_bundle_id", lambda path, module: calls.append(path) or "canonical@1-0")
    monkeypatch.setattr(bundle_store, "_examples_enabled", lambda: True)
    monkeypatch.setattr(bundle_store, "_examples_root", lambda: tmp_path)
    monkeypatch.setattr(bundle_store, "_ensure_example_bundle_shared", lambda path: path)
    monkeypatch.setattr(bundle_store, "_declared_example_bundle_id", lambda path: "canonical@1-0")
    allowed = {"canonical@1-0"}
    monkeypatch.setattr(bundle_store, "_bundle_allowed", lambda bid: bid in allowed)

    assert set(bundle_store._load_example_bundles()) == {"canonical@1-0"}
    assert bundle_store._reserved_bundle_entry("canonical@1-0").id == "canonical@1-0"
    assert len(calls) == 1
    allowed.clear()
    assert bundle_store._load_example_bundles() == {}
    assert bundle_store._reserved_bundle_entry("canonical@1-0") is None
    allowed.add("canonical@1-0")
    monkeypatch.setattr(bundle_store, "_examples_enabled", lambda: False)
    assert bundle_store._load_example_bundles() == {}
    assert bundle_store._reserved_bundle_entry("canonical@1-0") is None
    monkeypatch.setattr(bundle_store, "_examples_enabled", lambda: True)
    entrypoint.unlink()
    assert bundle_store._load_example_bundles() == {}
    assert bundle_store._reserved_bundle_entry("canonical@1-0") is None
    assert len(calls) == 1
