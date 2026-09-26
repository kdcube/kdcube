# SPDX-License-Identifier: MIT

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.proc.rest.integrations.widget_navigation_auth import (
    enforce_protected_widget_user,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType


_WIDGET_PATH = (
    "/api/integrations/bundles/demo-tenant/demo-project/"
    "connection-hub@1-0/widgets/connections_settings"
)


def _request(
    *,
    accept: str = "text/html,application/xhtml+xml",
    fetch_destination: str | None = "document",
    fetch_mode: str | None = "navigate",
    query: str = "tab=delegated_to_kdcube&provider_id=google%20drive",
    path: str = _WIDGET_PATH,
) -> Request:
    headers = [(b"accept", accept.encode("latin-1"))]
    if fetch_destination is not None:
        headers.append((b"sec-fetch-dest", fetch_destination.encode("ascii")))
    if fetch_mode is not None:
        headers.append((b"sec-fetch-mode", fetch_mode.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.replace("@", "%40").encode("ascii"),
            "query_string": query.encode("ascii"),
            "headers": headers,
            "server": ("example.test", 443),
            "client": ("127.0.0.1", 1234),
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


def _session(user_type: UserType, *, roles: list[str] | None = None) -> UserSession:
    return UserSession(
        session_id="session-1",
        user_type=user_type,
        user_id="user-1" if user_type != UserType.ANONYMOUS else None,
        username="user-1" if user_type != UserType.ANONYMOUS else None,
        roles=roles or [],
    )


def test_anonymous_top_level_widget_navigation_redirects_to_same_origin_sign_in() -> None:
    request = _request()

    response = enforce_protected_widget_user(
        request,
        _session(UserType.ANONYMOUS),
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = urlsplit(response.headers["location"])
    assert location.path == "/signin/"
    assert parse_qs(location.query) == {
        "next": [f"{_WIDGET_PATH}?{request.url.query}"],
    }


def test_html_navigation_without_fetch_metadata_still_redirects() -> None:
    response = enforce_protected_widget_user(
        _request(fetch_destination=None, fetch_mode=None),
        _session(UserType.ANONYMOUS),
    )

    assert isinstance(response, RedirectResponse)


@pytest.mark.parametrize(
    ("accept", "fetch_destination", "fetch_mode"),
    [
        ("application/json", "document", "navigate"),
        ("text/html", "iframe", "navigate"),
        ("text/html", "script", "no-cors"),
        ("text/html", "document", "cors"),
    ],
)
def test_non_navigation_requests_keep_the_existing_json_denial(
    accept: str,
    fetch_destination: str,
    fetch_mode: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        enforce_protected_widget_user(
            _request(
                accept=accept,
                fetch_destination=fetch_destination,
                fetch_mode=fetch_mode,
            ),
            _session(UserType.ANONYMOUS),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "User is required."


def test_authenticated_user_continues_to_widget_policy_checks() -> None:
    response = enforce_protected_widget_user(
        _request(),
        _session(UserType.REGISTERED, roles=["kdcube:role:registered"]),
    )

    assert response is None


def test_authenticated_session_without_roles_is_not_redirected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        enforce_protected_widget_user(
            _request(),
            _session(UserType.REGISTERED),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "User has no roles assigned."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "extra_kwargs", "request_path"),
    [
        (integrations.fetch_bundle_widget, {}, _WIDGET_PATH),
        (
            integrations.serve_bundle_widget_path,
            {"widget_path": "settings/profile"},
            f"{_WIDGET_PATH}/settings/profile",
        ),
    ],
)
async def test_protected_widget_handlers_redirect_before_serving_app_content(
    handler,
    extra_kwargs,
    request_path,
) -> None:
    response = await handler(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="connection-hub@1-0",
        widget_alias="connections_settings",
        request=_request(path=request_path),
        session=_session(UserType.ANONYMOUS),
        **extra_kwargs,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"][0].startswith(
        request_path
    )


def test_a_navigation_just_after_sign_out_shows_signed_out_instead_of_signing_in_again() -> None:
    """W260: the signed-out route returns with signed_out=1. Bouncing that load to
    sign-in would let a provider still signed in upstream sign the person back in."""

    request = _request(query="tab=people&signed_out=1")
    response = enforce_protected_widget_user(request, _session(UserType.ANONYMOUS))

    assert not isinstance(response, RedirectResponse)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.body.decode("utf-8")
    assert "You are signed out." in body
    # The Sign in link returns to the page without the marker.
    link = body.split('href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    target = urlsplit(link)
    assert target.path == "/signin/"
    assert parse_qs(target.query) == {"next": [f"{_WIDGET_PATH}?tab=people"]}
