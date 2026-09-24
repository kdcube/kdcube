# SPDX-License-Identifier: MIT
"""A commit-pinned app serves its deployed widget after reload and restart (W202).

On 2026-09-24 08:31Z the operator's pinned app answered every widget request
with ``widget_artifact_stale / deployment_manifest_mismatch`` while the reload
receipt was green. The deploy side hashed the snapshot path the activation
loaded, and the serving side hashed the registry entry's declared path. Both
now key the manifest on the source identity the lifecycle recorded when it
prepared the app, so a pinned app's widget is served.
"""

from __future__ import annotations

import pathlib
import subprocess
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import (
    props_fingerprint,
    source_generation_for_identity,
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
def pinned(tmp_path: pathlib.Path, monkeypatch) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "work"
    bundle = repo / SUBDIR
    bundle.mkdir(parents=True)
    _git(repo, "init", "-q")
    (bundle / "entrypoint.py").write_text("VERSION = 'A'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "A")
    managed = tmp_path / "managed"
    monkeypatch.setattr(snap, "resolve_managed_bundles_root", lambda: managed)
    return bundle, _git(repo, "rev-parse", "HEAD")


async def _prepare(declared: dict) -> tuple[BundleEntry, dict]:
    """What the lifecycle does: resolve the activation and record its identity."""

    resolved = await snap.resolve_activation_entry(declared)
    registry_entry = {**BundleEntry.model_validate(resolved).model_dump(mode="python", exclude_none=True)}
    registry_entry.update({key: resolved[key] for key in ("mounted_path", "source") if resolved.get(key)})
    return BundleEntry.model_validate(resolved), snap.bundle_source_identity(resolved.get("source"), entry=registry_entry)


def _request(identity: dict) -> Request:
    lifecycle = SimpleNamespace(loaded_source_diagnostic=lambda application_id: {"source": identity})
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/widget", "raw_path": b"/widget", "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        "app": SimpleNamespace(state=SimpleNamespace(application_lifecycle=lifecycle)),
    })


async def _serve(monkeypatch, storage: pathlib.Path, registry_entry: BundleEntry, identity: dict):
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
        widget_path="index.html", request=_request(identity), session=SimpleNamespace(roles=[]),
        application_generation="generation-1",
    )


async def _deploy(storage: pathlib.Path, resolved: BundleEntry, identity: dict) -> str:
    """What the deploy side writes: the manifest keyed on the prepared identity."""

    generation = source_generation_for_identity(bundle_entry_to_spec(resolved), identity)
    widget = storage / "ui" / "widgets" / "board"
    widget.mkdir(parents=True, exist_ok=True)
    (widget / "index.html").write_text("<html><head></head><body>board</body></html>", encoding="utf-8")
    await write_deployment_manifest(storage, AppStaticSurfaceManifest(
        tenant="tenant-a", project="project-a", bundle_id=BUNDLE_ID,
        source_generation=generation, source=identity, application_generation="generation-1",
        props_fingerprint=props_fingerprint(PROPS), deployment_signature="d" * 64,
        generated_at="2026-09-24T08:31:00+00:00",
        widgets={"board": DeployedWidgetSurface(
            alias="board", method_name="board_widget", user_types=["registered"], roles=[],
            auth={}, static=True, artifact_relpath="ui/widgets/board",
        )},
    ))
    return generation


@pytest.mark.asyncio
async def test_a_pinned_app_serves_its_widget_after_reload_and_after_restart(pinned, tmp_path, monkeypatch):
    bundle, commit = pinned
    declared = {"id": BUNDLE_ID, "path": str(bundle), "module": "entrypoint", "singleton": True,
                "activation": {"commit": commit}}
    storage = tmp_path / "storage"

    # Reload: the activation loads a snapshot, not the declared path.
    resolved, identity = await _prepare(declared)
    assert resolved.path != str(bundle) and identity["commit"] == commit
    generation = await _deploy(storage, resolved, identity)

    # The serving side reads the registry entry at its declared path. Keyed on
    # the path, the two sides disagreed; that was the outage.
    declared_entry = BundleEntry(id=BUNDLE_ID, path=str(bundle), module="entrypoint", singleton=True,
                                 activation=BundleActivationConfig(commit=commit))
    assert source_generation_for_spec(declared_entry) != source_generation_for_spec(bundle_entry_to_spec(resolved))

    response = await _serve(monkeypatch, storage, declared_entry, identity)
    assert response.status_code == 200 and b"board" in response.body

    # Restart: a new process prepares the app again and records the same identity.
    restarted, identity_after_restart = await _prepare(declared)
    assert identity_after_restart == identity
    assert source_generation_for_identity(bundle_entry_to_spec(restarted), identity_after_restart) == generation
    response = await _serve(monkeypatch, storage, declared_entry, identity_after_restart)
    assert response.status_code == 200

    # A registry that holds the resolved entry (the snapshot path) serves the same way.
    response = await _serve(monkeypatch, storage, resolved, identity_after_restart)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_widget_deployed_for_another_commit_is_still_refused(pinned, tmp_path, monkeypatch):
    bundle, commit = pinned
    declared = {"id": BUNDLE_ID, "path": str(bundle), "module": "entrypoint", "singleton": True,
                "activation": {"commit": commit}}
    storage = tmp_path / "storage"
    resolved, identity = await _prepare(declared)
    await _deploy(storage, resolved, {**identity, "commit": "0" * 40})

    with pytest.raises(Exception) as refused:
        await _serve(monkeypatch, storage, resolved, identity)
    assert "deployment_manifest_mismatch" in str(getattr(refused.value, "detail", refused.value))
