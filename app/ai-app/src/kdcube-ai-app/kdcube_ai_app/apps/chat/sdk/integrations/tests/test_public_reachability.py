"""Deployment origin diagnostics use the public fetcher's request shape."""
import asyncio

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.public_reachability import (
    MAX_MARKER_BYTES, PublicURLExpectation, deployment_origin_error, probe_public_url,
)


def install_origin(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


def probe(url="https://origin.example/file", *, caller="browser", **expect):
    return asyncio.run(probe_public_url(url, caller=caller, expect=PublicURLExpectation(**expect)))


def test_tunnel_warning_depends_on_public_caller(monkeypatch):
    requests = []

    def origin(request):
        requests.append(request)
        browser = request.headers["user-agent"].startswith("Mozilla/")
        return httpx.Response(200 if browser else 403, headers={"content-type": "text/html" if browser else "application/json"})

    install_origin(monkeypatch, origin)
    browser = probe(media_prefix="image/")
    server = probe(caller="server", media_prefix="image/")
    assert (browser.kind, browser.status, browser.media_type) == ("answered_by_other", 200, "text/html")
    assert (server.kind, server.status, server.media_type) == ("answered_by_other", 403, "application/json")
    assert all("authorization" not in req.headers and "cookie" not in req.headers for req in requests)
    error = deployment_origin_error(browser, purpose="docs.embed_image")
    assert error.code == "deployment_origin_not_public"
    assert error.status == 502
    assert error.details == {"where": "deployment_config", "purpose": "docs.embed_image", "origin_status": 200, "origin_media_type": "text/html"}
    assert "runtime administrator" in str(error)


@pytest.mark.parametrize("status,media,kind", [
    (200, "IMAGE/PNG; charset=binary", "reachable"),
    (200, "text/html", "answered_by_other"),
    (403, "image/png", "answered_by_other"),
    (200, "", "answered_by_other"),
])
def test_expected_image_response(monkeypatch, status, media, kind):
    install_origin(monkeypatch, lambda req: httpx.Response(status, headers={"content-type": media}))
    result = probe(media_prefix="image/")
    assert result.kind == kind
    if kind == "reachable":
        assert deployment_origin_error(result, purpose="docs.embed_image") is None


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.TooManyRedirects])
def test_unreachable_keeps_only_exception_class(monkeypatch, failure):
    url = "https://origin.example/file?capability=private-test-capability"

    def origin(request):
        raise failure("failed fetch of " + str(request.url), request=request)

    install_origin(monkeypatch, origin)
    result = probe(url, media_prefix="image/")
    assert result.kind == "unreachable"
    assert result.detail == failure.__name__
    error = deployment_origin_error(result, purpose="docs.embed_image")
    assert error.code == "deployment_origin_unreachable"
    assert error.details["where"] == "deployment_config"
    assert "private-test-capability" not in str(error) + repr(error.details) + repr(result)


def test_invalid_configured_url_is_a_deployment_failure(monkeypatch):
    def origin(request):
        raise httpx.InvalidURL("invalid configured capability URL")

    install_origin(monkeypatch, origin)
    result = probe(media_prefix="image/")
    assert result.kind == "unreachable"
    assert result.detail == "InvalidURL"


@pytest.mark.parametrize("body,kind", [
    (b"prefix public-marker suffix", "reachable"),
    (b"wrong application", "answered_by_other"),
    (b"x" * MAX_MARKER_BYTES + b"public-marker", "answered_by_other"),
])
def test_optional_marker_is_bounded(monkeypatch, body, kind):
    install_origin(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/plain"}, content=body))
    assert probe(media_prefix="text/", marker=b"public-marker").kind == kind


def test_status_and_media_checks_do_not_read_response_body(monkeypatch):
    class UnreadableBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("header-only probe must not read the image body")
            yield b""  # pragma: no cover

    install_origin(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "image/png"}, stream=UnreadableBody()))
    assert probe(media_prefix="image/").kind == "reachable"


def test_redirect_is_followed_as_an_anonymous_browser(monkeypatch):
    requests = []

    def origin(request):
        requests.append(request)
        if request.url.path == "/file":
            return httpx.Response(302, headers={"location": "/image"})
        return httpx.Response(200, headers={"content-type": "image/png"})

    install_origin(monkeypatch, origin)
    assert probe(media_prefix="image/").kind == "reachable"
    assert [req.url.path for req in requests] == ["/file", "/image"]
    assert all(req.headers["user-agent"].startswith("Mozilla/") for req in requests)
