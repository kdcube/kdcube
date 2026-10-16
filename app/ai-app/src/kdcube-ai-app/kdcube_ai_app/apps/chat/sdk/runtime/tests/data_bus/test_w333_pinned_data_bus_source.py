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


# ---- composition: the real lifecycle preparation, and a peer call (claude-ops round 2) ----

async def _real_prepare(readiness, entry, monkeypatch, *, generation="g1"):
    """ProcApplicationLifecycle._prepare with its real resolution; only loading and deploy are faked."""
    from kdcube_ai_app.apps.chat.proc.app_lifecycle import runtime
    from kdcube_ai_app.apps.chat.proc.app_lifecycle.supervisor import ApplicationPreparation
    from kdcube_ai_app.infra.plugin.app_readiness import ApplicationReadinessMode

    async def _props(**kwargs):
        return {}

    async def _quiet(*args, **kwargs):
        return None

    async def _preload(spec, bundle_spec, **kwargs):
        return object(), object()

    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    monkeypatch.setattr(runtime, "get_bundle_props_from_authority", _props)
    monkeypatch.setattr(runtime, "upsert_bundles_async", _quiet)
    monkeypatch.setattr(runtime, "preload_bundle_async", _preload)
    monkeypatch.setattr(runtime, "validate_prepared_application_manifest", _quiet)
    monkeypatch.setattr(runtime, "deploy_loaded_bundle_app_resources", _quiet)
    lifecycle = runtime.ProcApplicationLifecycle(
        tenant=T, project=P, redis=object(), pg_pool=object(), concurrency=1,
        retry_initial_seconds=0, retry_max_seconds=0, registry=readiness,
    )
    readiness.replace_desired(tenant=T, project=P, applications={BUNDLE_ID: DesiredApplicationState(generation=generation)})
    readiness.transition(tenant=T, project=P, application_id=BUNDLE_ID, generation=generation,
                         state=ApplicationLifecycleState.PREPARING)
    await lifecycle._prepare(ApplicationPreparation(application_id=BUNDLE_ID, generation=generation,
                                                    readiness=ApplicationReadinessMode.INDEPENDENT, payload=entry))
    readiness.transition(tenant=T, project=P, application_id=BUNDLE_ID, generation=generation,
                         state=ApplicationLifecycleState.READY)  # what the supervisor does next
    return lifecycle  # shut down by the caller: shutdown retires this scope's readiness records


@pytest.mark.asyncio
async def test_the_real_lifecycle_preparation_records_the_pin_every_consumer_runs(repo, monkeypatch):
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    entry = _entry(repo.bundle, commit=repo.A, require=True)
    lifecycle = await _real_prepare(readiness, entry, monkeypatch)
    try:
        prepared = readiness.snapshot(tenant=T, project=P, application_id=BUNDLE_ID)
        assert prepared.prepared_source_commit == repo.A
        assert _content(prepared.prepared_source_path) == "VERSION = 'A'\n"
        assert await _data_bus_loads(entry, monkeypatch) == [prepared.prepared_source_path]
        assert (await _request_path(entry, monkeypatch)).path == prepared.prepared_source_path
    finally:
        await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_the_real_lifecycle_preparation_of_an_unpinned_app_records_no_pin(repo, monkeypatch):
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    entry = _entry(repo.bundle)
    lifecycle = await _real_prepare(readiness, entry, monkeypatch)
    try:
        prepared = readiness.snapshot(tenant=T, project=P, application_id=BUNDLE_ID)
        assert prepared.prepared_source_commit is None and prepared.prepared_source_path is None
        assert (await _request_path(entry, monkeypatch)).path == str(repo.bundle)
    finally:
        await lifecycle.shutdown()


def _patch_peer_call(monkeypatch, entry, loaded: list[str]):
    from kdcube_ai_app.infra.plugin import bundle_loader
    from kdcube_ai_app.infra.service_hub import inventory

    class _Target:
        from kdcube_ai_app.infra.plugin.bundle_loader import api

        @api(alias="ping")
        async def ping(self, **kwargs):
            return {"ok": True}

    async def registry(_redis, tenant=None, project=None):
        return BundlesRegistry(default_bundle_id=BUNDLE_ID, bundles={BUNDLE_ID: entry})

    async def props(redis, *, tenant, project, bundle_id):
        return {}

    async def secrets(cfg_req, *, bundle_id):
        return cfg_req

    async def workflow(spec, wf_config, *, comm_context, redis, pg_pool):
        loaded.append(str(spec.path))
        return _Target(), None

    async def session(redis, comm_context, *, tenant, project):
        return SimpleNamespace(user_type=SimpleNamespace(value="registered"), user_id="u", username="u", email=None,
                               fingerprint="f", roles=["kdcube:role:member"], permissions=[], session_id="s",
                               request_context=SimpleNamespace(user_timezone="UTC", user_utc_offset_min=0))

    from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations as operations

    monkeypatch.setattr(bundle_store, "load_registry", registry)
    monkeypatch.setattr(bundle_store, "get_bundle_props", props)
    monkeypatch.setattr(inventory, "resolve_config_request_secrets", secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda cfg: SimpleNamespace(ai_bundle_spec=None))
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", workflow)
    monkeypatch.setattr(operations, "_session_for_comm_context", session)
    return operations


async def _peer_call(operations):
    from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import BundleOperationCall
    from kdcube_ai_app.apps.chat.sdk.protocol import (
        ExternalEventActor, ExternalEventPayload, ExternalEventRequest, ExternalEventRouting, ExternalEventUser,
    )

    context = ExternalEventPayload(
        request=ExternalEventRequest(request_id="r"), routing=ExternalEventRouting(session_id="s", bundle_id="caller@1"),
        actor=ExternalEventActor(tenant_id=T, project_id=P),
        user=ExternalEventUser(user_type="registered", user_id="u", roles=["kdcube:role:member"]),
    )
    return await operations._invoke_local_bundle_operation_raw(
        BundleOperationCall(bundle_id=BUNDLE_ID, operation="ping", data={}), comm_context=context,
        redis=object(), pg_pool=None,
    )


@pytest.mark.asyncio
async def test_a_peer_call_runs_the_prepared_snapshot_of_a_pinned_app(repo, monkeypatch):
    """Condition 2: bundle_operations' local peer call resolves through the same resolver."""
    readiness = ApplicationReadinessRegistry()
    _in_process(monkeypatch, readiness)
    entry = _entry(repo.bundle, commit=repo.A, require=True)
    lifecycle = await _real_prepare(readiness, entry, monkeypatch)
    try:
        loaded: list[str] = []
        operations = _patch_peer_call(monkeypatch, entry, loaded)
        await _peer_call(operations)
        assert loaded == [readiness.snapshot(tenant=T, project=P, application_id=BUNDLE_ID).prepared_source_path]
        assert _content(loaded[0]) == "VERSION = 'A'\n"
    finally:
        await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_a_peer_call_to_a_pinned_app_this_process_has_not_prepared_refuses(repo, monkeypatch):
    _in_process(monkeypatch, ApplicationReadinessRegistry())
    entry = _entry(repo.bundle, commit=repo.A, require=True)
    loaded: list[str] = []
    operations = _patch_peer_call(monkeypatch, entry, loaded)

    with pytest.raises(snap.BundleSnapshotError) as refused:
        await _peer_call(operations)
    assert refused.value.code == "bundle_snapshot_not_prepared"
    assert loaded == []
