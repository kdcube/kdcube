# SPDX-License-Identifier: MIT
"""W333: a commit-pinned app runs its pinned snapshot on every path, or refuses; never the mounted folder.

The mounted folder holds an uncommitted edit after the pin. The lifecycle prepares
the snapshot and leaves a durable activation record; the request path
(resolve_bundle_spec_from_store) and the Data Bus manager resolve the app from the
descriptor entry and the record, without git.
"""
from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import worker as data_bus_worker
from kdcube_ai_app.infra.plugin import bundle_snapshot as snap
from kdcube_ai_app.infra.plugin import bundle_store
from kdcube_ai_app.infra.plugin.bundle_store import BundleActivationConfig, BundleEntry, BundlesRegistry

BUNDLE_ID = "pinned.app"
SUBDIR = "apps/pinned@1-0"
T, P = "tenant", "project"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value
        return True

    async def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pinned(tmp_path: pathlib.Path, monkeypatch):
    repo = tmp_path / "work"
    bundle = repo / SUBDIR
    bundle.mkdir(parents=True)
    _git(repo, "init", "-q")
    (bundle / "entrypoint.py").write_text("VERSION = 'A'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "A")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "release")
    (bundle / "entrypoint.py").write_text("VERSION = 'B uncommitted'\n", encoding="utf-8")
    monkeypatch.setattr(snap, "resolve_managed_bundles_root", lambda: tmp_path / "managed")
    return bundle, commit


def _entry(bundle, *, commit=None, require=False):
    activation = BundleActivationConfig(commit=commit, require_commit=require) if (commit or require) else None
    return BundleEntry(id=BUNDLE_ID, path=str(bundle), module="entrypoint", activation=activation)


async def _prepare(redis, bundle, ref):
    """What the lifecycle does at startup and reload: materialize, then record."""
    resolved = await snap.resolve_activation_entry({"id": BUNDLE_ID, "path": str(bundle), "activation": {"commit": ref}})
    await snap.write_activation_record(redis, tenant=T, project=P, bundle_id=BUNDLE_ID, ref=ref,
                                       commit=resolved["source"]["commit"], path=resolved["path"])
    return resolved["path"]


async def _data_bus_loads(entry, redis, monkeypatch) -> list[str]:
    loaded: list[str] = []

    def manifest(spec, *, bundle_id):
        loaded.append(str(spec.path))
        return SimpleNamespace(data_bus_handlers=())

    from kdcube_ai_app.infra.plugin import app_readiness

    monkeypatch.setattr(data_bus_worker, "load_bundle_manifest", manifest)
    monkeypatch.setattr(app_readiness.application_readiness_registry, "require_ready", lambda **kwargs: None)
    manager = data_bus_worker.DataBusRuntimeManager(redis=redis, redis_url=None, tenant=T, project=P, instance_id="i")
    try:
        await manager.reconcile(SimpleNamespace(bundles={BUNDLE_ID: entry}))
    finally:
        await manager.shutdown()
    return loaded


async def _request_path_spec(entry, redis, monkeypatch):
    async def registry(_redis, tenant=None, project=None):
        return BundlesRegistry(default_bundle_id=BUNDLE_ID, bundles={BUNDLE_ID: entry})

    monkeypatch.setattr(bundle_store, "load_registry", registry)
    return await bundle_store.resolve_bundle_spec_from_store(redis, tenant=T, project=P, bundle_id=BUNDLE_ID)


def _content(path) -> str:
    return (pathlib.Path(path) / "entrypoint.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_kind", ["sha", "branch"])
async def test_a_descriptor_pin_loads_the_prepared_snapshot_on_both_paths(pinned, monkeypatch, ref_kind):
    bundle, commit = pinned
    ref = commit if ref_kind == "sha" else "release"  # the descriptor documents "any ref"
    redis = _Redis()
    snapshot = await _prepare(redis, bundle, ref)
    entry = _entry(bundle, commit=ref, require=True)

    assert await _data_bus_loads(entry, redis, monkeypatch) == [snapshot]
    spec = await _request_path_spec(entry, redis, monkeypatch)
    assert spec.path == snapshot and _content(spec.path) == "VERSION = 'A'\n"


@pytest.mark.asyncio
async def test_a_reload_pin_reaches_both_paths_through_the_record(pinned, monkeypatch):
    """The reload's pin never reaches bundles.yaml: the descriptor entry names no commit."""
    bundle, commit = pinned
    redis = _Redis()
    snapshot = await _prepare(redis, bundle, commit)
    entry = _entry(bundle)

    assert await _data_bus_loads(entry, redis, monkeypatch) == [snapshot]
    assert (await _request_path_spec(entry, redis, monkeypatch)).path == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["not-prepared", "require-without-commit", "record-for-another-commit",
                                  "snapshot-removed"])
async def test_a_pin_without_a_matching_prepared_snapshot_refuses_and_never_loads_the_mounted_folder(
        pinned, monkeypatch, case):
    bundle, commit = pinned
    redis = _Redis()
    entry = _entry(bundle, commit=commit, require=True)
    if case == "require-without-commit":
        entry = _entry(bundle, require=True)
    if case == "record-for-another-commit":
        await snap.write_activation_record(redis, tenant=T, project=P, bundle_id=BUNDLE_ID, ref="0" * 40,
                                           commit="0" * 40, path=str(bundle))
    if case == "snapshot-removed":
        snapshot = await _prepare(redis, bundle, commit)
        (pathlib.Path(snapshot) / snap.SNAPSHOT_MARKER).unlink()

    assert await _data_bus_loads(entry, redis, monkeypatch) == []
    with pytest.raises(snap.BundleSnapshotError) as refused:
        await _request_path_spec(entry, redis, monkeypatch)
    assert refused.value.code == "bundle_snapshot_not_prepared"


@pytest.mark.asyncio
async def test_an_unpinned_app_keeps_its_declared_path(pinned, monkeypatch):
    bundle, _ = pinned
    redis = _Redis()
    entry = _entry(bundle)
    assert await _data_bus_loads(entry, redis, monkeypatch) == [str(bundle)]
    assert (await _request_path_spec(entry, redis, monkeypatch)).path == str(bundle)


@pytest.mark.asyncio
async def test_the_lifecycle_records_a_pinned_preparation_and_clears_an_unpinned_one(pinned):
    from kdcube_ai_app.apps.chat.proc.app_lifecycle.runtime import ProcApplicationLifecycle

    bundle, commit = pinned
    redis = _Redis()
    lifecycle = object.__new__(ProcApplicationLifecycle)
    lifecycle.redis, lifecycle.tenant, lifecycle.project = redis, T, P
    resolved = await snap.resolve_activation_entry({"id": BUNDLE_ID, "path": str(bundle), "activation": {"commit": "release"}})

    await lifecycle._record_activation(_entry(bundle, commit="release"), resolved)
    record = await snap.read_activation_record(redis, tenant=T, project=P, bundle_id=BUNDLE_ID)
    assert record["commit"] == commit and record["ref"] == "release" and record["path"] == resolved["path"]

    await lifecycle._record_activation(_entry(bundle), {"id": BUNDLE_ID, "path": str(bundle), "source": {}})
    assert await snap.read_activation_record(redis, tenant=T, project=P, bundle_id=BUNDLE_ID) is None
