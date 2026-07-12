from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.sdk.solutions.sites import ApplicationSite


def _request(*, host: str = "runtime.example.com") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode("utf-8"))],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        }
    )


@pytest.mark.asyncio
async def test_site_alias_delegates_to_standard_static_serving(monkeypatch) -> None:
    sites = [ApplicationSite("website@1", "docs", False, ("docs.example.com",))]
    captured = {}

    async def _catalog(_request):
        return "tenant-a", "project-a", sites

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations._serve_application_site(
        request=_request(),
        site_alias="docs",
        path="guide/getting-started",
    )

    assert response.status_code == 200
    assert captured["tenant"] == "tenant-a"
    assert captured["project"] == "project-a"
    assert captured["bundle_id"] == "website@1"
    assert captured["path"] == "guide/getting-started"
    assert captured["base_href"].endswith("/website@1/public/static/")


@pytest.mark.asyncio
async def test_root_selects_site_by_forwarded_host(monkeypatch) -> None:
    sites = [ApplicationSite("website@1", "docs", False, ("docs.example.com",))]
    captured = {}

    async def _catalog(_request):
        return "tenant-a", "project-a", sites

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    request = _request(host="proxy.internal")
    request.scope["headers"].append((b"x-forwarded-host", b"docs.example.com"))
    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    await integrations._serve_application_site(request=request, site_alias="")

    assert captured["bundle_id"] == "website@1"


@pytest.mark.asyncio
async def test_root_without_site_redirects_to_configured_platform(monkeypatch) -> None:
    async def _catalog(_request):
        return "tenant-a", "project-a", []

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(plain=lambda key: "/platform" if key == "proxy.route_prefix" else None),
    )

    response = await integrations._serve_application_site(
        request=_request(),
        site_alias="",
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"] == "/platform/chat"
