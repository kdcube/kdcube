# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The platform-hosted sign-in, end to end, against a mock OIDC issuer.

Everything real except the identity provider and the proxy: the ingress
router, the package's code flow (discovery over HTTP, PKCE, the confidential
client's Basic authentication, the PyJWT verifier fetching the issuer's JWKS
over HTTP, the nonce check), the platform session backend over the session
authority, the Redis attempt store, the session cookie, the gateway
manager's validation and slide, logout with the upstream sign-out URL.

The issuer is a small FastAPI app served by uvicorn on a loopback port for
the duration of the module. It issues RS256 ID tokens with a key generated
here and enforces PKCE S256 and the client secret, so a regression in either
shows up as a refused sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("cryptography")
jwt = pytest.importorskip("jwt")
uvicorn = pytest.importorskip("uvicorn")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient

from connection_hub.server_side_login.cookies import StandardCookiePolicy
from connection_hub.server_side_login.flow import BrowserSessionFlow
from connection_hub.server_side_login.model import SessionPolicy
from connection_hub.server_side_login.oidc import OidcClientConfig, OidcCodeFlow
from connection_hub.server_side_login.oidc_jwt import PyJwtVerifier

from kdcube_ai_app.apps.chat.ingress.platform_session import create_platform_session_router
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.login_lane import PlatformSessionBackend, RedisLoginAttemptStore
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis

CLIENT_ID = "kdcube-platform"
CLIENT_SECRET = "s3cret-for-the-token-endpoint"
PLATFORM_ORIGIN = "https://kdcube.example"
REDIRECT_URI = f"{PLATFORM_ORIGIN}/api/platform/session/callback"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class MockIssuer:
    """An OIDC issuer: discovery, authorize, token, JWKS, end-session."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.codes: dict[str, dict[str, str]] = {}
        self.authorizations: list[dict[str, str]] = []
        self.token_requests: list[dict[str, str]] = []
        self.subject = "idp-user-1"
        self.email = "Person@Example.com"
        self.groups = ["staff"]
        self.wrong_nonce = False
        self.app = self._build()
        self.issuer = ""

    def _jwk(self) -> dict[str, str]:
        numbers = self.key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "kid": self.kid,
            "use": "sig",
            "alg": "RS256",
            "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        }

    def _id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": self.subject,
            "email": self.email,
            "email_verified": True,
            "name": "Person Example",
            "cognito:groups": list(self.groups),
            "nonce": "not-the-attempt" if self.wrong_nonce else nonce,
            "iat": now,
            "exp": now + 300,
        }
        pem = self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": self.kid})

    def _build(self) -> FastAPI:
        app = FastAPI()
        issuer = self

        @app.get("/.well-known/openid-configuration")
        async def discovery() -> dict[str, object]:
            return {
                "issuer": issuer.issuer,
                "authorization_endpoint": f"{issuer.issuer}/authorize",
                "token_endpoint": f"{issuer.issuer}/token",
                "jwks_uri": f"{issuer.issuer}/jwks",
                "end_session_endpoint": f"{issuer.issuer}/logout",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }

        @app.get("/jwks")
        async def jwks() -> dict[str, object]:
            return {"keys": [issuer._jwk()]}

        @app.get("/authorize")
        async def authorize(request: Request) -> RedirectResponse:
            params = dict(request.query_params)
            issuer.authorizations.append(params)
            assert params.get("response_type") == "code"
            assert params.get("client_id") == CLIENT_ID
            assert params.get("redirect_uri") == REDIRECT_URI
            assert params.get("code_challenge_method") == "S256"
            code = secrets.token_urlsafe(16)
            issuer.codes[code] = {
                "nonce": params.get("nonce", ""),
                "code_challenge": params.get("code_challenge", ""),
                "redirect_uri": params.get("redirect_uri", ""),
            }
            return RedirectResponse(f"{params['redirect_uri']}?code={code}&state={params.get('state', '')}", status_code=302)

        @app.post("/token")
        async def token(
            grant_type: str = Form(...),
            code: str = Form(...),
            redirect_uri: str = Form(...),
            client_id: str = Form(...),
            code_verifier: str = Form(...),
            authorization: str | None = Header(default=None),
        ) -> JSONResponse:
            issuer.token_requests.append({"grant_type": grant_type, "code": code, "client_id": client_id})
            expected_basic = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if authorization != expected_basic:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            issued = issuer.codes.pop(code, None)
            if issued is None or grant_type != "authorization_code" or redirect_uri != issued["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest()) != issued["code_challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, status_code=400)
            return JSONResponse({
                "token_type": "Bearer",
                "id_token": issuer._id_token(issued["nonce"]),
                "access_token": secrets.token_urlsafe(8),
                "expires_in": 300,
            })

        return app


@pytest.fixture(scope="module")
def issuer():
    mock = MockIssuer()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    mock.issuer = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(mock.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the mock issuer did not start"
    yield mock
    server.should_exit = True
    thread.join(timeout=5)


class Harness:
    def __init__(self, mock: MockIssuer) -> None:
        self.redis = FakeRedis()
        self.authority = BundleSessionAuthority(tenant="t", project="p", redis=self.redis, secret="platform-secret")
        self.policy = SessionPolicy(idle_ttl_seconds=600, max_ttl_seconds=3600, touch_interval_seconds=60, attempt_ttl_seconds=120)
        self.seen: list[str] = []

        def grants(identity, platform_user_id):
            self.seen.append(identity.subject)
            assert platform_user_id == "mock-idp:idp-user-1"
            roles = ["kdcube:role:registered", *[g for g in identity.claims.get("cognito:groups", []) if isinstance(g, str)]]
            return roles, [], "test"

        client = OidcClientConfig(
            issuer=mock.issuer,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scopes=("openid", "email", "profile"),
            provider="mock-idp",
        )
        self.upstream = OidcCodeFlow(client, verifier=PyJwtVerifier(f"{mock.issuer}/jwks"))
        self.flow = BrowserSessionFlow(
            backend=PlatformSessionBackend(self.authority, grants=grants, policy=self.policy),
            attempts=RedisLoginAttemptStore(self.authority),
            upstream=self.upstream,
            cookies=StandardCookiePolicy(session_name="__Secure-LATC", secure=True),
            policy=self.policy,
        )

        async def provide(_request):
            return self.flow

        app = FastAPI()
        app.include_router(create_platform_session_router(flow_provider=provide))
        self.client = TestClient(app, base_url=PLATFORM_ORIGIN)


def _cookie_values(response) -> dict[str, str]:
    from http.cookies import SimpleCookie

    jar: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            jar[name] = morsel.value
    return jar


def _walk_to_callback(harness: Harness, mock: MockIssuer, next_path: str = "/chat?tab=2"):
    """The browser's walk: login -> issuer authorize -> callback location."""
    import httpx

    start = harness.client.get("/api/platform/session/login", params={"next": next_path}, follow_redirects=False)
    assert start.status_code == 302
    authorize_url = start.headers["location"]
    assert authorize_url.startswith(f"{mock.issuer}/authorize?")
    query = parse_qs(urlparse(authorize_url).query)
    assert query["code_challenge_method"] == ["S256"] and query["nonce"] and query["state"]
    binding = _cookie_values(start)["__Host-kdcube-login"]

    with httpx.Client() as browser:
        at_issuer = browser.get(authorize_url, follow_redirects=False)
    assert at_issuer.status_code == 302
    callback = at_issuer.headers["location"]
    assert callback.startswith(REDIRECT_URI)
    return callback, binding, query["state"][0]


def test_sign_in_round_trip_issues_a_platform_session(issuer):
    harness = Harness(issuer)
    callback, binding, state = _walk_to_callback(harness, issuer)

    harness.client.cookies.set("__Host-kdcube-login", binding)
    done = harness.client.get(callback, follow_redirects=False)
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "/chat?tab=2"
    cookies = _cookie_values(done)
    token = cookies["__Secure-LATC"]
    assert token.startswith("kst1.")
    assert cookies.get("__Host-kdcube-login", "") == "", "the attempt cookie is cleared"

    # The token exchange happened once, with the code the issuer minted, PKCE and the secret checked there.
    assert len(issuer.token_requests) == 1 and issuer.token_requests[0]["client_id"] == CLIENT_ID
    assert harness.seen == ["idp-user-1"]

    # The gateway accepts the cookie and knows the user; the group became a role.
    manager = BundleSessionAuthManager(authority=harness.authority, sliding=harness.policy)
    import asyncio

    user = asyncio.run(manager.authenticate(token))
    assert user.email == "person@example.com"
    assert user.sub == "mock-idp:idp-user-1"
    assert "staff" in user.roles and "kdcube:role:registered" in user.roles

    verification = asyncio.run(harness.authority.validate_token(token))
    assert verification.record["max_exp"] - verification.record["exp"] == 3000, "idle 600 inside max 3600"

    # The code cannot be replayed: the attempt is gone.
    replay = harness.client.get(callback, follow_redirects=False)
    assert replay.status_code == 400 and "attempt_missing" in replay.text

    # Logout ends the session and names the issuer's sign-out.
    ended = asyncio.run(harness.flow.logout(token, post_logout_redirect=f"{PLATFORM_ORIGIN}/"))
    assert ended.ended is True
    assert ended.upstream_logout_url.startswith(f"{issuer.issuer}/logout")
    assert asyncio.run(harness.flow.validate_request(token)) is None


def test_callback_from_another_browser_is_refused(issuer):
    harness = Harness(issuer)
    callback, _binding, _state = _walk_to_callback(harness, issuer)
    harness.client.cookies.clear()
    refused = harness.client.get(callback, follow_redirects=False)
    assert refused.status_code == 400 and "binding_mismatch" in refused.text
    assert "__Secure-LATC" not in _cookie_values(refused)
    assert issuer.token_requests == [] or issuer.token_requests[-1]["code"] not in issuer.codes


def test_id_token_for_another_attempt_is_refused(issuer):
    harness = Harness(issuer)
    issuer.wrong_nonce = True
    try:
        callback, binding, _state = _walk_to_callback(harness, issuer)
        harness.client.cookies.set("__Host-kdcube-login", binding)
        refused = harness.client.get(callback, follow_redirects=False)
    finally:
        issuer.wrong_nonce = False
    assert refused.status_code == 400 and "nonce_mismatch" in refused.text
    assert "__Secure-LATC" not in _cookie_values(refused)
    assert harness.seen == [], "no platform user was written"
