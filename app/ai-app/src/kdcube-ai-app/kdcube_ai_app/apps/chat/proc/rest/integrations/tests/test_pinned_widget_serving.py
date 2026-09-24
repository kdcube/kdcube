# SPDX-License-Identifier: MIT
"""A commit-pinned app serves its deployed widget from every proc process (W202).

On 2026-09-24 08:31Z the operator's pinned app answered every widget request
with ``widget_artifact_stale / deployment_manifest_mismatch`` while the reload
receipt was green. The deploy side hashed the snapshot path the activation
loaded, and the serving side the registry entry's declared path. Both now hash
the declared coordinates: the declared location and a declared
``activation.commit``. Any process computes that from the registry entry
alone, so the answer does not depend on which process serves the request.
"""

from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import (
    props_fingerprint,
    source_generation_for_spec,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.models import (
    AppStaticSurfaceManifest,
    DeployedWidgetSurface,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.storage import write_deployment_manifest
from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.infra.plugin import bundle_snapshot as snap
from kdcube_ai_app.infra.plugin import bundle_storage
from kdcube_ai_app.infra.plugin.bundle_store import BundleActivationConfig, BundleEntry, bundle_entry_to_spec

BUNDLE_ID = "pinned.app"
SUBDIR = "apps/pinned@1-0"
PROPS = {"ui": {"widgets": {"board": {"enabled": True}}}}


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path: pathlib.Path, monkeypatch) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "work"
    bundle = repo / SUBDIR
    bundle.mkdir(parents=True)
    _git(repo, "init", "-q")
    (bundle / "entrypoint.py").write_text("VERSION = 'A'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "A")
    monkeypatch.setattr(snap, "resolve_managed_bundles_root", lambda: tmp_path / "managed")
    return bundle, _git(repo, "rev-parse", "HEAD")


def _declared(bundle: pathlib.Path, commit: str = "") -> BundleEntry:
    """The registry entry a serving process reads from the descriptor."""

    return BundleEntry(
        id=BUNDLE_ID, path=str(bundle), module="entrypoint", singleton=True,
        activation=BundleActivationConfig(commit=commit) if commit else None,
    )


async def _prepare(entry: BundleEntry) -> BundleEntry:
    """What the lifecycle prepares: the activation resolved, a snapshot when pinned."""

    resolved = await snap.resolve_activation_entry(entry.model_dump(mode="python", exclude_none=True))
    return BundleEntry.model_validate(resolved)


async def _deploy(storage: pathlib.Path, prepared: BundleEntry) -> str:
    """What the deploy side writes: the manifest keyed on the prepared spec."""

    generation = source_generation_for_spec(bundle_entry_to_spec(prepared))
    widget = storage / "ui" / "widgets" / "board"
    widget.mkdir(parents=True, exist_ok=True)
    (widget / "index.html").write_text("<html><head></head><body>board</body></html>", encoding="utf-8")
    await write_deployment_manifest(storage, AppStaticSurfaceManifest(
        tenant="tenant-a", project="project-a", bundle_id=BUNDLE_ID,
        source_generation=generation, application_generation="generation-1",
        props_fingerprint=props_fingerprint(PROPS), deployment_signature="d" * 64,
        generated_at="2026-09-24T08:31:00+00:00",
        widgets={"board": DeployedWidgetSurface(
            alias="board", method_name="board_widget", user_types=["registered"], roles=[],
            auth={}, static=True, artifact_relpath="ui/widgets/board",
        )},
    ))
    return generation


def _request(prepared_generation: str = "") -> Request:
    """A serving process; with a generation, its lifecycle prepared the activation."""

    state = SimpleNamespace()
    if prepared_generation:
        state.application_lifecycle = SimpleNamespace(
            loaded_source_diagnostic=lambda application_id: {"source_generation": prepared_generation}
        )
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/widget", "raw_path": b"/widget", "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        "app": SimpleNamespace(state=state),
    })


async def _serve(monkeypatch, storage: pathlib.Path, registry_entry: BundleEntry, request: Request):
    async def _load_registry(*args, **kwargs):
        return SimpleNamespace(bundles={BUNDLE_ID: registry_entry})

    async def _props(**kwargs):
        return PROPS

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(integrations, "store_read_bundle_props_from_authority", _props)
    monkeypatch.setattr(integrations, "_endpoint_visible", lambda *args: True)
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: storage)
    return await integrations._try_serve_deployed_static_widget_app(
        tenant="tenant-a", project="project-a", bundle_id=BUNDLE_ID, widget_alias="board",
        widget_path="index.html", request=request, session=SimpleNamespace(roles=[]),
        application_generation="generation-1",
    )


@pytest.mark.asyncio
async def test_a_descriptor_pinned_app_serves_its_widget_from_any_process(repository, tmp_path, monkeypatch):
    bundle, commit = repository
    storage = tmp_path / "storage"
    declared = _declared(bundle, commit)

    # Reload: the activation loads a snapshot, not the declared path.
    prepared = await _prepare(declared)
    assert prepared.path != str(bundle) and prepared.mounted_path == str(bundle)
    await _deploy(storage, prepared)

    # A process with no lifecycle record, reading the descriptor entry.
    response = await _serve(monkeypatch, storage, declared, _request())
    assert response.status_code == 200 and b"board" in response.body

    # Restart: a new process prepares again and derives the same generation.
    restarted = await _prepare(declared)
    assert source_generation_for_spec(bundle_entry_to_spec(restarted)) == source_generation_for_spec(declared)
    response = await _serve(monkeypatch, storage, declared, _request())
    assert response.status_code == 200

    # A registry that holds the resolved entry (the snapshot path) serves the same way.
    response = await _serve(monkeypatch, storage, restarted, _request())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_unpinned_app_serves_its_widget_from_a_process_with_no_lifecycle_record(repository, tmp_path, monkeypatch):
    bundle, _commit = repository
    storage = tmp_path / "storage"
    declared = _declared(bundle)

    prepared = await _prepare(declared)
    assert prepared.path == str(bundle)
    await _deploy(storage, prepared)

    response = await _serve(monkeypatch, storage, declared, _request())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_request_only_commit_is_served_by_the_process_that_prepared_it(repository, tmp_path, monkeypatch):
    bundle, commit = repository
    storage = tmp_path / "storage"
    # The reload request names the commit; the descriptor has no pin.
    prepared = await _prepare(_declared(bundle, commit))
    generation = await _deploy(storage, prepared)

    response = await _serve(monkeypatch, storage, _declared(bundle), _request(prepared_generation=generation))
    assert response.status_code == 200

    # A process that did not prepare it reads the descriptor, which has no pin.
    with pytest.raises(HTTPException) as refused:
        await _serve(monkeypatch, storage, _declared(bundle), _request())
    assert "deployment_manifest_mismatch" in str(refused.value.detail)


@pytest.mark.asyncio
async def test_a_widget_deployed_for_another_commit_is_refused(repository, tmp_path, monkeypatch):
    bundle, commit = repository
    storage = tmp_path / "storage"
    prepared = await _prepare(_declared(bundle, commit))
    await _deploy(storage, prepared.model_copy(update={"activation": BundleActivationConfig(commit="0" * 40)}))

    with pytest.raises(HTTPException) as refused:
        await _serve(monkeypatch, storage, _declared(bundle, commit), _request())
    assert "deployment_manifest_mismatch" in str(refused.value.detail)
