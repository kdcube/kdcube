# SPDX-License-Identifier: MIT
"""W333: a commit-pinned app runs, in each process, the snapshot that process prepared, or refuses.

Never the mounted folder. The mounted folder holds an uncommitted edit after the pin. Each
proc process's lifecycle prepares the app and records what it imported on its own readiness
record. The request path (resolve_bundle_spec_from_store, used by REST, MCP and both
peer-call paths) and the Data Bus manager resolve the app from that record, with no shared
store and no git.
"""
from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import worker as data_bus_worker
from kdcube_ai_app.infra.plugin import app_readiness
from kdcube_ai_app.infra.plugin import bundle_snapshot as snap
from kdcube_ai_app.infra.plugin import bundle_store
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationReadinessRegistry,
    DesiredApplicationState,
)
from kdcube_ai_app.infra.plugin.bundle_store import BundleActivationConfig, BundleEntry, BundlesRegistry

BUNDLE_ID = "pinned.app"
SUBDIR = "apps/pinned@1-0"
T, P = "tenant", "project"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch):
    """Commits A and B of the app; the mounted folder holds a third, uncommitted edit."""
    work = tmp_path / "work"
    bundle = work / SUBDIR
    bundle.mkdir(parents=True)
    _git(work, "init", "-q")
    commits = {}
    for version in ("A", "B"):
        (bundle / "entrypoint.py").write_text(f"VERSION = '{version}'\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", version)
        commits[version] = _git(work, "rev-parse", "HEAD")
    (bundle / "entrypoint.py").write_text("VERSION = 'mounted, uncommitted'\n", encoding="utf-8")
    monkeypatch.setattr(snap, "resolve_managed_bundles_root", lambda: tmp_path / "managed")
    return SimpleNamespace(bundle=bundle, **commits)


def _entry(bundle, *, commit=None, require=False):
    activation = BundleActivationConfig(commit=commit, require_commit=require) if (commit or require) else None
    return BundleEntry(id=BUNDLE_ID, path=str(bundle), module="entrypoint", activation=activation)


async def _prepare(readiness: ApplicationReadinessRegistry, bundle, ref, *, generation="g1") -> str | None:
    """What one process's lifecycle does: materialize the pin (or not) and record what it imported."""
    readiness.replace_desired(tenant=T, project=P, applications={BUNDLE_ID: DesiredApplicationState(generation=generation)})
    readiness.transition(tenant=T, project=P, application_id=BUNDLE_ID, generation=generation,
                         state=ApplicationLifecycleState.PREPARING)
    path = commit = None
    if ref:
        resolved = await snap.resolve_activation_entry({"id": BUNDLE_ID, "path": str(bundle), "activation": {"commit": ref}})
        path, commit = resolved["path"], resolved["source"]["commit"]
    readiness.record_prepared_source(tenant=T, project=P, application_id=BUNDLE_ID, generation=generation,
                                     path=path, commit=commit)
    readiness.transition(tenant=T, project=P, application_id=BUNDLE_ID, generation=generation,
                         state=ApplicationLifecycleState.READY)
    return path


def _in_process(monkeypatch, readiness):
    monkeypatch.setattr(app_readiness, "application_readiness_registry", readiness)


async def _data_bus_loads(entry, monkeypatch) -> list[str]:
    loaded: list[str] = []

    def manifest(spec, *, bundle_id):
        loaded.append(str(spec.path))
        return SimpleNamespace(data_bus_handlers=())

    monkeypatch.setattr(data_bus_worker, "load_bundle_manifest", manifest)
    manager = data_bus_worker.DataBusRuntimeManager(redis=object(), redis_url=None, tenant=T, project=P, instance_id="i")
    try:
        await manager.reconcile(SimpleNamespace(bundles={BUNDLE_ID: entry}))
    finally:
        await manager.shutdown()
    return loaded


async def _request_path(entry, monkeypatch):
    async def registry(_redis, tenant=None, project=None):
        return BundlesRegistry(default_bundle_id=BUNDLE_ID, bundles={BUNDLE_ID: entry})

    monkeypatch.setattr(bundle_store, "load_registry", registry)
    return await bundle_store.resolve_bundle_spec_from_store(object(), tenant=T, project=P, bundle_id=BUNDLE_ID)


def _content(path) -> str:
    return (pathlib.Path(path) / "entrypoint.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_kind", ["sha", "branch"])
async def test_a_descriptor_pin_runs_the_prepared_snapshot_on_every_consumer(repo, monkeypatch, ref_kind):
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    ref = repo.B if ref_kind == "sha" else "HEAD"
    snapshot = await _prepare(readiness, repo.bundle, ref)
    entry = _entry(repo.bundle, commit=ref, require=True)

    assert await _data_bus_loads(entry, monkeypatch) == [snapshot]
    spec = await _request_path(entry, monkeypatch)
    assert spec.path == snapshot and _content(spec.path) == "VERSION = 'B'\n"


@pytest.mark.asyncio
async def test_f1_a_reload_to_b_over_a_descriptor_pinned_to_a_runs_b_on_every_consumer(repo, monkeypatch):
    """claude-ops F1: the lifecycle runs the reload's B, so every consumer in that process runs B."""
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    await _prepare(readiness, repo.bundle, repo.A, generation="g1")
    snapshot_b = await _prepare(readiness, repo.bundle, repo.B, generation="g2")
    entry = _entry(repo.bundle, commit=repo.A, require=True)  # bundles.yaml still says A

    assert await _data_bus_loads(entry, monkeypatch) == [snapshot_b]
    assert _content((await _request_path(entry, monkeypatch)).path) == "VERSION = 'B'\n"


@pytest.mark.asyncio
async def test_two_processes_a_restart_in_y_does_not_change_what_x_runs(repo, monkeypatch):
    """claude-ops F2: each process answers from its own preparation; nothing is shared."""
    x, y = ApplicationReadinessRegistry(), ApplicationReadinessRegistry()
    entry = _entry(repo.bundle, commit=repo.A, require=True)
    await _prepare(x, repo.bundle, repo.A)
    snapshot_b = await _prepare(x, repo.bundle, repo.B, generation="g2")   # X reloaded to B
    await _prepare(y, repo.bundle, repo.A, generation="g9")                # Y restarted from the descriptor

    assert snap.pinned_snapshot_path(entry, tenant=T, project=P, readiness=x) == snapshot_b
    assert _content(snap.pinned_snapshot_path(entry, tenant=T, project=P, readiness=y)) == "VERSION = 'A'\n"
    _in_process(monkeypatch, x)
    assert await _data_bus_loads(entry, monkeypatch) == [snapshot_b]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no-lifecycle-record", "preparing", "prepared-unpinned", "require-without-commit"])
async def test_a_pinned_app_this_process_has_not_prepared_refuses_and_never_runs_the_mounted_folder(
        repo, monkeypatch, case):
    """A new or restarted process, a startup race, or a process without a lifecycle: refuse."""
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    entry = _entry(repo.bundle, require=True) if case == "require-without-commit" else _entry(repo.bundle, commit=repo.A)
    if case == "preparing":
        await _prepare(readiness, repo.bundle, repo.A)
        readiness.replace_desired(tenant=T, project=P, applications={BUNDLE_ID: DesiredApplicationState(generation="g2")})
        readiness.transition(tenant=T, project=P, application_id=BUNDLE_ID, generation="g2",
                             state=ApplicationLifecycleState.PREPARING)
    if case == "prepared-unpinned":
        await _prepare(readiness, repo.bundle, None)  # prepared before the descriptor was pinned

    assert await _data_bus_loads(entry, monkeypatch) == []
    with pytest.raises(snap.BundleSnapshotError) as refused:
        await _request_path(entry, monkeypatch)
    assert refused.value.code == "bundle_snapshot_not_prepared"


@pytest.mark.asyncio
async def test_an_unpinned_app_keeps_its_declared_path(repo, monkeypatch):
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    await _prepare(readiness, repo.bundle, None)
    entry = _entry(repo.bundle)
    assert await _data_bus_loads(entry, monkeypatch) == [str(repo.bundle)]
    assert (await _request_path(entry, monkeypatch)).path == str(repo.bundle)
