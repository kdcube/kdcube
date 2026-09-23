from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.proc.rest.integrations.application_site_readiness import (
    application_site_wait_response,
)
from kdcube_ai_app.apps.chat.sdk.solutions.sites import (
    ApplicationSite,
    ApplicationSiteTarget,
    SITE_AUTH_PLATFORM_SESSION,
    compile_application_site_catalog,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationNotReadyError,
    ApplicationReadinessMode,
    ApplicationReadinessSnapshot,
)


_SITE_TARGET = ApplicationSiteTarget(
    path="/applications/website@1",
    module=None,
    singleton=False,
)


def _request(
    *,
    host: str = "runtime.example.com",
    path: str = "/",
    query_string: bytes = b"",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query_string,
            "headers": [(b"host", host.encode("utf-8")), *headers],
            "scheme": "https",
            "server": (host, 443),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        }
    )


def _user_session() -> UserSession:
    return UserSession(
        session_id="session-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        roles=[],
        permissions=[],
    )


def _readiness_snapshot(
    state: ApplicationLifecycleState,
) -> ApplicationReadinessSnapshot:
    return ApplicationReadinessSnapshot(
        tenant="tenant-a",
        project="project-a",
        application_id="website@1",
        readiness=ApplicationReadinessMode.INDEPENDENT,
        state=state,
        desired_generation="generation-2",
        ready_generation="generation-1",
        attempt=2,
        error_code=None,
        error_message=None,
        retry_at=None,
        started_at="2026-09-13T03:00:00Z",
        finished_at=None,
        updated_at="2026-09-13T03:00:01Z",
    )


@pytest.mark.asyncio
async def test_site_alias_delegates_to_standard_static_serving(monkeypatch) -> None:
    sites = [ApplicationSite("website@1", "docs", False, ("docs.example.com",), _SITE_TARGET)]
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=sites,
    )
    captured = {}

    async def _catalog(_request):
        return catalog

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
    assert captured["base_href"] == "/sites/docs/"
    assert captured["html_context"]["application_id"] == "website@1"
    assert captured["html_context"]["catalog_revision"] == catalog.revision
    # The page routes on the part after the site root, handed to it here so an
    # application's address (/sites/<alias>/<its-own-path>) opens the page on
    # that path without the page parsing its own location.
    assert captured["html_context"]["public_base"] == "/sites/docs/"
    assert captured["html_context"]["site_path"] == "guide/getting-started"
    assert captured["resolved_spec"].id == "website@1"
    assert captured["resolved_spec"].path == _SITE_TARGET.path


@pytest.mark.asyncio
async def test_site_root_and_index_hand_the_page_an_empty_site_path(monkeypatch) -> None:
    sites = [ApplicationSite("website@1", "docs", False, ("docs.example.com",), _SITE_TARGET)]
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=sites,
    )
    seen: list[str] = []

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        seen.append(kwargs["html_context"]["site_path"])
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    await integrations.application_site_root("docs", _request(path="/sites/docs"))
    await integrations.application_site_path("docs", "index.html", _request(path="/sites/docs/index.html"))
    await integrations.application_site_path("docs", "quickstart-works/", _request(path="/sites/docs/quickstart-works/"))

    assert seen == ["", "", "quickstart-works"]
    assert integrations._application_site_path("/a/b/") == "a/b"


@pytest.mark.asyncio
async def test_platform_session_site_redirects_signed_out_browser_before_serving(
    monkeypatch,
) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[
            ApplicationSite(
                "board@1",
                "problem-board",
                False,
                (),
                _SITE_TARGET,
                SITE_AUTH_PLATFORM_SESSION,
            )
        ],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(
        integrations,
        "serve_static_asset",
        lambda **_kwargs: pytest.fail("signed-out site shell was served"),
    )
    response = await integrations.application_site_path(
        site_alias="problem-board",
        path="inbox",
        request=_request(
            path="/sites/problem-board/inbox",
            query_string=b"worker=codex-ui&message=mail-9",
            headers=(
                (b"accept", b"text/html,application/xhtml+xml"),
                (b"sec-fetch-dest", b"document"),
                (b"sec-fetch-mode", b"navigate"),
            ),
        ),
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        "?next=%2Fsites%2Fproblem-board%2Finbox%3Fworker%3Dcodex-ui%26message%3Dmail-9"
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_platform_session_site_rejects_signed_out_non_document_request(
    monkeypatch,
) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[
            ApplicationSite(
                "board@1",
                "problem-board",
                False,
                (),
                _SITE_TARGET,
                SITE_AUTH_PLATFORM_SESSION,
            )
        ],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(
        integrations,
        "serve_static_asset",
        lambda **_kwargs: pytest.fail("signed-out site asset was served"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await integrations.application_site_path(
            site_alias="problem-board",
            path="assets/app.js",
            request=_request(
                path="/sites/problem-board/assets/app.js",
                headers=(
                    (b"accept", b"*/*"),
                    (b"sec-fetch-dest", b"script"),
                    (b"sec-fetch-mode", b"no-cors"),
                ),
            ),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User is required."


@pytest.mark.asyncio
async def test_platform_session_site_serves_for_authenticated_user(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[
            ApplicationSite(
                "board@1",
                "problem-board",
                False,
                (),
                _SITE_TARGET,
                SITE_AUTH_PLATFORM_SESSION,
            )
        ],
    )
    captured = {}

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    request = _request(
        path="/sites/problem-board/",
        headers=((b"accept", b"text/html"),),
    )
    request.state.user_session = _user_session()
    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations.application_site_root(
        site_alias="problem-board",
        request=request,
    )

    assert response.status_code == 200
    assert captured["session"] is request.state.user_session


@pytest.mark.asyncio
async def test_site_route_translates_readiness_json_into_browser_wait_page(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("website@1", "docs", False, (), _SITE_TARGET)],
    )

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**_kwargs):
        raise ApplicationNotReadyError(
            _readiness_snapshot(ApplicationLifecycleState.PREPARING)
        )

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations.application_site_path(
        site_alias="docs",
        path="projects/alpha",
        request=_request(
            path="/sites/docs/projects/alpha",
            query_string=b"conversation=conv-7&message=mail-9",
        ),
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 503
    assert response.media_type == "text/html"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "2"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert "This site is getting ready" in body
    assert '"autoRetry":true' in body
    assert '"maxAttempts":8' in body
    assert "window.location.reload()" in body
    assert "window.fetch(window.location.href" in body
    assert "window.location.replace(window.location.href)" in body
    assert "window.location.href" in body
    assert "application_not_ready" not in body


@pytest.mark.parametrize(
    ("state", "expected_title", "auto_retry"),
    [
        (ApplicationLifecycleState.RETRYING, "This site is recovering", True),
        (ApplicationLifecycleState.FAILED, "This site needs attention", False),
        (ApplicationLifecycleState.DEPROVISIONING, "This site needs attention", False),
    ],
)
def test_site_wait_page_only_auto_retries_transient_states(
    state: ApplicationLifecycleState,
    expected_title: str,
    auto_retry: bool,
) -> None:
    response = application_site_wait_response(
        snapshot=_readiness_snapshot(state)
    )
    body = response.body.decode("utf-8")

    assert expected_title in body
    assert f'"autoRetry":{str(auto_retry).lower()}' in body
    assert response.headers["x-kdcube-site-retryable"] == str(auto_retry).lower()
    assert response.headers["x-kdcube-application-state"] == state.value
    assert "Automatic checks are paused because preparation is taking longer" in body


@pytest.mark.asyncio
async def test_root_selects_site_by_forwarded_host(monkeypatch) -> None:
    sites = [ApplicationSite("website@1", "docs", False, ("docs.example.com",), _SITE_TARGET)]
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=sites,
    )
    captured = {}

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    request = _request(host="proxy.internal")
    request.scope["headers"].append((b"x-forwarded-host", b"docs.example.com"))
    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    await integrations._serve_application_site(request=request, site_alias="")

    assert captured["bundle_id"] == "website@1"
    assert captured["base_href"] == "/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_prefix", "expected_location"),
    [
        ("/platform", "/platform/chat"),
        ("/control/ui", "/control/ui/chat"),
    ],
)
async def test_root_without_site_redirects_to_configured_platform(
    monkeypatch,
    route_prefix: str,
    expected_location: str,
) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(plain=lambda key: route_prefix if key == "proxy.route_prefix" else None),
    )

    response = await integrations.application_site_landing(request=_request())

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"] == expected_location


@pytest.mark.asyncio
async def test_clean_path_without_site_returns_controlled_404(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)

    with pytest.raises(HTTPException) as exc_info:
        await integrations.application_site_landing_path(
            path="docs/page",
            request=_request(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Application site path not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("route_prefix", [None, ""])
async def test_root_without_site_uses_platform_fallback_when_prefix_is_missing(
    monkeypatch,
    route_prefix,
) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(plain=lambda key: route_prefix if key == "proxy.route_prefix" else None),
    )

    response = await integrations.application_site_landing(request=_request())

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"] == "/platform/chat"


@pytest.mark.asyncio
async def test_default_site_serves_root_when_no_host_match(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("site@1", "default", True, (), _SITE_TARGET)],
    )
    captured = {}

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations.application_site_landing(request=_request(host="unknown.example.test"))

    assert response.status_code == 200
    assert captured["bundle_id"] == "site@1"
    assert captured["path"] == "index.html"
    assert captured["base_href"] == "/"


@pytest.mark.asyncio
async def test_wildcard_host_selects_site_for_nested_asset(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[
            ApplicationSite(
                "site@1",
                "site",
                False,
                ("*.example.test",),
                _SITE_TARGET,
            )
        ],
    )
    captured = {}

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations.application_site_landing_path(
        path="assets/site.css",
        request=_request(host="docs.example.test"),
    )

    assert response.status_code == 200
    assert captured["bundle_id"] == "site@1"
    assert captured["path"] == "assets/site.css"
    assert captured["base_href"] == "/"


@pytest.mark.asyncio
async def test_unknown_alias_returns_controlled_404(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("site@1", "known", False, (), _SITE_TARGET)],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)

    with pytest.raises(HTTPException) as exc_info:
        await integrations.application_site_root(site_alias="missing", request=_request())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Application site 'missing' not found"


@pytest.mark.asyncio
async def test_missing_site_owner_returns_structured_503(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("site@1", "site", True, ())],
    )

    async def _catalog(_request):
        return catalog

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)

    with pytest.raises(HTTPException) as exc_info:
        await integrations.application_site_landing(request=_request())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Application site owner 'site@1' is unavailable"


@pytest.mark.asyncio
async def test_hot_catalog_lookup_does_not_access_request_redis(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("website@1", "docs", True, ())],
    )
    monkeypatch.setattr(
        integrations.application_site_catalog_runtime,
        "snapshot",
        lambda: catalog,
    )
    monkeypatch.setattr(
        integrations,
        "_get_app_redis",
        lambda _request: pytest.fail("site request attempted to read Redis"),
    )

    restored = await integrations._application_site_catalog(_request())

    assert restored is catalog


@pytest.mark.asyncio
async def test_host_root_path_forwards_requested_file(monkeypatch) -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[ApplicationSite("website@1", "docs", False, ("docs.example.com",), _SITE_TARGET)],
    )
    captured = {}

    async def _catalog(_request):
        return catalog

    async def _serve_static_asset(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(integrations, "_application_site_catalog", _catalog)
    monkeypatch.setattr(integrations, "serve_static_asset", _serve_static_asset)

    response = await integrations.application_site_landing_path(
        path="guide/index.html",
        request=_request(host="docs.example.com"),
    )

    assert response.status_code == 200
    assert captured["path"] == "guide/index.html"
    assert captured["base_href"] == "/"
