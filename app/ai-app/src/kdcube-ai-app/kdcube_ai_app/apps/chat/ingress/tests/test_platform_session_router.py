# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The platform sign-in routes over an in-memory flow: the login redirect
with its attempt cookie, the callback that sets the session cookie and
clears the attempt, the refusals, and the status document."""

from __future__ import annotations

from http.cookies import SimpleCookie

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from connection_hub.browser_session.cookies import StandardCookiePolicy
from connection_hub.browser_session.flow import BrowserSessionFlow
from connection_hub.browser_session.memory import MemoryLoginAttemptStore, MemorySessionBackend
from connection_hub.browser_session.model import LoginAttempt, SessionPolicy, VerifiedIdentity
from connection_hub.browser_session.protocols import UpstreamRejected

from kdcube_ai_app.apps.chat.ingress.platform_session import create_platform_session_router


class FakeUpstream:
    name = "fake-idp"

    def __init__(self) -> None:
        self.reject = False

    async def begin(self, attempt: LoginAttempt) -> str:
        return f"https://idp.example/authorize?state={attempt.state}"

    async def complete(self, params, attempt: LoginAttempt) -> VerifiedIdentity:
        if self.reject:
            raise UpstreamRejected("id_token_invalid", "bad token")
        return VerifiedIdentity(provider="fake", subject="u1", email="u1@example.com", name="U One")

    def logout_url(self, *, post_logout_redirect: str = "") -> str:
        return ""


def _cookies(response) -> dict[str, SimpleCookie]:
    jar: dict[str, SimpleCookie] = {}
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            jar[name] = morsel
    return jar


@pytest.fixture
def harness():
    upstream = FakeUpstream()
    policy = SessionPolicy(idle_ttl_seconds=600, max_ttl_seconds=3600, touch_interval_seconds=60, attempt_ttl_seconds=120)
    flow = BrowserSessionFlow(
        backend=MemorySessionBackend(secret="s"),
        attempts=MemoryLoginAttemptStore(),
        upstream=upstream,
        cookies=StandardCookiePolicy(session_name="__Secure-LATC", secure=True),
        policy=policy,
    )
    state = {"flow": flow}

    async def provide(_request):
        return state["flow"]

    app = FastAPI()
    app.include_router(create_platform_session_router(flow_provider=provide))
    client = TestClient(app, base_url="https://kdcube.example")
    return client, upstream, state


def test_login_redirects_to_the_upstream_with_a_bound_attempt_cookie(harness):
    client, _, _ = harness
    response = client.get("/api/platform/session/login", params={"next": "/chat?x=1"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.example/authorize?state=")
    assert response.headers["cache-control"] == "no-store"
    jar = _cookies(response)
    attempt = jar["__Host-kdcube-login"]
    assert attempt["httponly"] and attempt["secure"] and attempt["path"] == "/"
    assert int(attempt["max-age"]) == 120


def test_callback_sets_the_session_cookie_and_clears_the_attempt(harness):
    client, _, _ = harness
    start = client.get("/api/platform/session/login", params={"next": "/chat?x=1"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1]
    binding = _cookies(start)["__Host-kdcube-login"].value

    client.cookies.set("__Host-kdcube-login", binding)
    done = client.get("/api/platform/session/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert done.status_code == 302
    assert done.headers["location"] == "/chat?x=1"
    jar = _cookies(done)
    session = jar["__Secure-LATC"]
    assert session.value.startswith("kst1.")
    assert session["httponly"] and session["secure"] and session["samesite"].lower() == "lax"
    assert int(session["max-age"]) == 3600
    assert jar["__Host-kdcube-login"]["max-age"] in ("0", "") or jar["__Host-kdcube-login"].value == ""


def test_callback_without_the_binding_cookie_is_refused_and_the_attempt_is_spent(harness):
    client, _, _ = harness
    start = client.get("/api/platform/session/login", follow_redirects=False)
    state = start.headers["location"].split("state=")[1]
    binding = _cookies(start)["__Host-kdcube-login"].value
    client.cookies.clear()  # another browser: it never received the attempt cookie

    refused = client.get("/api/platform/session/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert refused.status_code == 400
    assert "binding_mismatch" in refused.text
    assert "__Secure-LATC" not in _cookies(refused)

    client.cookies.set("__Host-kdcube-login", binding)
    replay = client.get("/api/platform/session/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert replay.status_code == 400 and "attempt_missing" in replay.text, "the attempt was spent by the refused call"


def test_upstream_rejection_is_a_plain_page_not_a_stack(harness):
    client, upstream, _ = harness
    upstream.reject = True
    start = client.get("/api/platform/session/login", follow_redirects=False)
    state = start.headers["location"].split("state=")[1]
    binding = _cookies(start)["__Host-kdcube-login"].value
    client.cookies.set("__Host-kdcube-login", binding)
    response = client.get("/api/platform/session/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert response.status_code == 400
    assert "id_token_invalid" in response.text and "Try again" in response.text
    assert "Traceback" not in response.text


def test_status_and_the_unconfigured_lane(harness):
    client, _, state = harness
    status = client.get("/api/platform/session/status").json()
    assert status["configured"] is True and status["upstream"] == "fake-idp"
    assert status["loginUrl"] == "/api/platform/session/login" and status["idleTtlSeconds"] == 600

    state["flow"] = None
    assert client.get("/api/platform/session/status").json()["configured"] is False
    assert client.get("/api/platform/session/login", follow_redirects=False).status_code == 404
    assert client.get("/api/platform/session/callback", params={"state": "x"}, follow_redirects=False).status_code == 404
