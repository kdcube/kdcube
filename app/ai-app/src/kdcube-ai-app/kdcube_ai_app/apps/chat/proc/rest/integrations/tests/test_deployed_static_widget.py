# SPDX-License-Identifier: MIT

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
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
from kdcube_ai_app.infra.plugin import bundle_storage


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/widget",
            "raw_path": b"/widget",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


def _async_props(value):
    async def _load(**kwargs):
        del kwargs
        return value

    return _load


@pytest.mark.asyncio
async def test_deployed_widget_serves_without_loading_workflow(monkeypatch, tmp_path: Path) -> None:
    props = {"ui": {"widgets": {"stats": {"enabled": True}}}}
    entry = SimpleNamespace(
        id="app@1-0",
        path="/bundles/app",
        module="entrypoint",
        singleton=True,
        repo=None,
        ref=None,
        subdir=None,
        git_commit="commit-1",
    )
    widget_root = tmp_path / "ui" / "widgets" / "stats"
    widget_root.mkdir(parents=True)
    (widget_root / "index.html").write_text("<html><head></head><body>stats</body></html>", encoding="utf-8")
    manifest = AppStaticSurfaceManifest(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        source_generation=source_generation_for_spec(entry),
        props_fingerprint=props_fingerprint(props),
        deployment_signature="d" * 64,
        generated_at="2026-07-27T00:00:00+00:00",
        widgets={
            "stats": DeployedWidgetSurface(
                alias="stats",
                method_name="stats_widget",
                user_types=["registered"],
                roles=["finance"],
                auth={"authority_id": "kdcube.platform"},
                static=True,
                artifact_relpath="ui/widgets/stats",
            )
        },
    )
    await write_deployment_manifest(tmp_path, manifest)

    async def _load_registry(*args, **kwargs):
        return SimpleNamespace(bundles={"app@1-0": entry})

    visible_calls: list[tuple] = []

    def _visible(user_types, roles, session, auth):
        visible_calls.append((user_types, roles, auth))
        return True

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(
        integrations,
        "store_read_bundle_props_from_authority",
        _async_props(props),
    )
    monkeypatch.setattr(integrations, "_endpoint_visible", _visible)
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: tmp_path)

    response = await integrations._try_serve_deployed_static_widget_app(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        widget_alias="stats",
        widget_path="index.html",
        request=_request(),
        session=SimpleNamespace(roles=[]),
    )

    assert response.status_code == 200
    assert response.headers["X-KDCube-Widget-Delivery"] == "deployed"
    assert response.headers["X-KDCube-App-Deployment"] == "d" * 16
    assert response.headers["Cache-Control"] == "private, no-cache"
    assert visible_calls == [(["registered"], ["finance"], {"authority_id": "kdcube.platform"})]
    assert b"stats" in response.body


@pytest.mark.asyncio
async def test_deployed_widget_refuses_manifest_from_older_application_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    entry = SimpleNamespace(
        id="app@1-0",
        path="/bundles/app",
        module="entrypoint",
        singleton=True,
        repo=None,
        ref=None,
        subdir=None,
        git_commit="commit-1",
    )
    manifest = AppStaticSurfaceManifest(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        source_generation=source_generation_for_spec(entry),
        application_generation="application-generation-old",
        props_fingerprint=props_fingerprint({}),
        deployment_signature="deploy-old",
        generated_at="2026-07-27T00:00:00+00:00",
        widgets={},
    )
    await write_deployment_manifest(tmp_path, manifest)

    async def _load_registry(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(bundles={"app@1-0": entry})

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await integrations._try_serve_deployed_static_widget_app(
            tenant="tenant-a",
            project="project-a",
            bundle_id="app@1-0",
            widget_alias="stats",
            widget_path="index.html",
            request=_request(),
            session=SimpleNamespace(roles=[]),
            application_generation="application-generation-current",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers == {"Retry-After": "2"}
    assert exc_info.value.detail == {
        "type": "application_not_ready",
        "application_id": "app@1-0",
        "state": "widget_artifact_stale",
        "retryable": True,
        "widget_alias": "stats",
        "reason": "application_generation_mismatch",
    }


@pytest.mark.asyncio
async def test_deployed_widget_policy_denial_does_not_fall_back(monkeypatch, tmp_path: Path) -> None:
    entry = SimpleNamespace(
        id="app@1-0",
        path="/bundles/app",
        module="entrypoint",
        singleton=False,
        repo=None,
        ref=None,
        subdir=None,
        git_commit="commit-1",
    )
    manifest = AppStaticSurfaceManifest(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        source_generation=source_generation_for_spec(entry),
        props_fingerprint=props_fingerprint({}),
        deployment_signature="deploy",
        generated_at="2026-07-27T00:00:00+00:00",
        widgets={
            "stats": DeployedWidgetSurface(
                alias="stats",
                method_name="stats_widget",
                roles=["admin"],
                static=True,
                artifact_relpath="ui/widgets/stats",
            )
        },
    )
    await write_deployment_manifest(tmp_path, manifest)

    async def _load_registry(*args, **kwargs):
        return SimpleNamespace(bundles={"app@1-0": entry})

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(
        integrations,
        "store_read_bundle_props_from_authority",
        _async_props({}),
    )
    monkeypatch.setattr(integrations, "_endpoint_visible", lambda *args, **kwargs: False)
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await integrations._try_serve_deployed_static_widget_app(
            tenant="tenant-a",
            project="project-a",
            bundle_id="app@1-0",
            widget_alias="stats",
            widget_path="index.html",
            request=_request(),
            session=SimpleNamespace(roles=[]),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_deployed_widget_enforces_bundle_level_roles(monkeypatch, tmp_path: Path) -> None:
    entry = SimpleNamespace(
        id="app@1-0",
        path="/bundles/app",
        module="entrypoint",
        singleton=False,
        repo=None,
        ref=None,
        subdir=None,
        git_commit=None,
    )
    manifest = AppStaticSurfaceManifest(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        source_generation=source_generation_for_spec(entry),
        props_fingerprint=props_fingerprint({}),
        deployment_signature="deploy",
        generated_at="2026-07-27T00:00:00+00:00",
        bundle_allowed_roles=["kdcube:role:finance"],
        widgets={
            "stats": DeployedWidgetSurface(
                alias="stats",
                method_name="stats_widget",
                static=True,
                artifact_relpath="ui/widgets/stats",
            )
        },
    )
    await write_deployment_manifest(tmp_path, manifest)

    async def _load_registry(*args, **kwargs):
        return SimpleNamespace(bundles={"app@1-0": entry})

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(
        integrations,
        "store_read_bundle_props_from_authority",
        _async_props({}),
    )
    monkeypatch.setattr(integrations, "_endpoint_visible", lambda *args, **kwargs: True)
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await integrations._try_serve_deployed_static_widget_app(
            tenant="tenant-a",
            project="project-a",
            bundle_id="app@1-0",
            widget_alias="stats",
            widget_path="index.html",
            request=_request(),
            session=SimpleNamespace(roles=["kdcube:role:registered"]),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_stale_props_manifest_falls_back_without_using_stale_policy(monkeypatch, tmp_path: Path) -> None:
    entry = SimpleNamespace(
        id="app@1-0",
        path="/bundles/app",
        module="entrypoint",
        singleton=False,
        repo=None,
        ref=None,
        subdir=None,
        git_commit=None,
    )
    manifest = AppStaticSurfaceManifest(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        source_generation=source_generation_for_spec(entry),
        props_fingerprint=props_fingerprint({"revision": 1}),
        deployment_signature="deploy",
        generated_at="2026-07-27T00:00:00+00:00",
        widgets={},
    )
    await write_deployment_manifest(tmp_path, manifest)

    async def _load_registry(*args, **kwargs):
        return SimpleNamespace(bundles={"app@1-0": entry})

    monkeypatch.setattr(integrations, "_resolve_path_scope", lambda **kwargs: ("tenant-a", "project-a"))
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(
        integrations,
        "store_read_bundle_props_from_authority",
        _async_props({"revision": 2}),
    )
    monkeypatch.setattr(bundle_storage, "bundle_storage_dir", lambda **kwargs: tmp_path)

    result = await integrations._try_serve_deployed_static_widget_app(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        widget_alias="stats",
        widget_path="index.html",
        request=_request(),
        session=SimpleNamespace(roles=[]),
    )
    assert result is integrations._DEPLOYED_STATIC_WIDGET_MISS


@pytest.mark.asyncio
async def test_deployed_mode_labels_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(integrations, "static_widget_delivery_mode", lambda: "deployed")

    async def _miss(**kwargs):
        return integrations._DEPLOYED_STATIC_WIDGET_MISS

    async def _legacy(**kwargs):
        return Response("legacy")

    monkeypatch.setattr(integrations, "_try_serve_deployed_static_widget_app", _miss)
    monkeypatch.setattr(integrations, "_serve_legacy_static_widget_app", _legacy)
    response = await integrations._serve_static_widget_app(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        widget_alias="stats",
        widget_path="index.html",
        request=_request(),
        session=SimpleNamespace(roles=[]),
    )
    assert response.headers["X-KDCube-Widget-Delivery"] == "legacy-fallback"


@pytest.mark.asyncio
async def test_deployed_mode_checks_manifest_against_ready_application_generation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(integrations, "static_widget_delivery_mode", lambda: "deployed")
    monkeypatch.setattr(
        integrations,
        "_require_application_ready",
        lambda **kwargs: SimpleNamespace(desired_generation="application-generation-current"),
    )
    seen: dict[str, object] = {}

    async def _deployed(**kwargs):
        seen.update(kwargs)
        return Response("current")

    monkeypatch.setattr(integrations, "_try_serve_deployed_static_widget_app", _deployed)
    response = await integrations._serve_static_widget_app(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        widget_alias="stats",
        widget_path="index.html",
        request=_request(),
        session=SimpleNamespace(roles=[]),
    )

    assert response.body == b"current"
    assert seen["application_generation"] == "application-generation-current"


@pytest.mark.asyncio
async def test_shadow_mode_keeps_legacy_request_path(monkeypatch) -> None:
    monkeypatch.setattr(integrations, "static_widget_delivery_mode", lambda: "shadow")

    async def _deployed(**kwargs):
        raise AssertionError("shadow mode must not use the deployed request path")

    async def _legacy(**kwargs):
        return Response("legacy")

    monkeypatch.setattr(integrations, "_try_serve_deployed_static_widget_app", _deployed)
    monkeypatch.setattr(integrations, "_serve_legacy_static_widget_app", _legacy)
    response = await integrations._serve_static_widget_app(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        widget_alias="stats",
        widget_path="index.html",
        request=_request(),
        session=SimpleNamespace(roles=[]),
    )
    assert response.headers["X-KDCube-Widget-Delivery"] == "legacy-shadow"
