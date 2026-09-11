# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The platform side of the server-held browser session: the session
authority's idle bound and sliding touch, the ``SessionBackend`` and
``LoginAttemptStore`` adapters, the gateway manager's slide, and the lane's
configuration from the platform provider config."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from connection_hub.server_side_login.cookies import StandardCookiePolicy
from connection_hub.server_side_login.flow import BrowserSessionFlow, LoginAttemptRejected
from connection_hub.server_side_login.model import LoginAttempt, SessionPolicy, VerifiedIdentity

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.login_lane import (
    LOGIN_ROUTE,
    PlatformSessionBackend,
    RedisLoginAttemptStore,
    bundle_login_config,
    login_authenticator_is_oidc,
)
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis


class Clock:
    def __init__(self, start: int = 1_800_000_000) -> None:
        self.now = start

    def __call__(self) -> float:
        return float(self.now)


class FakeUpstream:
    name = "fake-idp"

    def __init__(self, identity: VerifiedIdentity) -> None:
        self.identity = identity
        self.begun: list[LoginAttempt] = []

    async def begin(self, attempt: LoginAttempt) -> str:
        self.begun.append(attempt)
        return f"https://idp.example/authorize?state={attempt.state}"

    async def complete(self, params, attempt: LoginAttempt) -> VerifiedIdentity:
        assert params.get("state") == attempt.state
        return self.identity

    def logout_url(self, *, post_logout_redirect: str = "") -> str:
        return f"https://idp.example/logout?to={post_logout_redirect}"


def _authority(redis: FakeRedis | None = None) -> BundleSessionAuthority:
    return BundleSessionAuthority(tenant="t", project="p", redis=redis or FakeRedis(), secret="s3cret")


def _identity(**overrides) -> VerifiedIdentity:
    base = dict(provider="cognito", subject="abc-123", email="Person@Example.com", name="Person", email_verified=True, claims={"cognito:groups": ["staff"]})
    base.update(overrides)
    return VerifiedIdentity(**base)


# ---- the authority: idle bound and touch ------------------------------------

@pytest.mark.asyncio
async def test_login_with_idle_bound_records_both_bounds_and_touch_slides_within_max(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.sessions.time.time", clock)
    redis = FakeRedis()
    authority = _authority(redis)
    await authority.register_user(sub="u1", username="u1")
    grant = await authority.login(sub="u1", ttl_seconds=3600 * 24, idle_ttl_seconds=600)

    record = json.loads(redis.values[authority._session_key(grant.session_id)])
    assert record["exp"] == clock.now + 600
    assert record["max_exp"] == clock.now + 3600 * 24
    assert record["last_seen"] == clock.now
    assert grant.claims["exp"] == clock.now + 3600 * 24, "the token carries the hard bound"
    assert grant.expires_at == clock.now + 600, "the grant reports the idle bound"

    clock.now += 300
    slid = await authority.touch(grant.session_id, expires_at=clock.now + 600, now=clock.now)
    assert slid is not None and slid["exp"] == clock.now + 600 and slid["last_seen"] == clock.now

    clock.now += 3600 * 23
    capped = await authority.touch(grant.session_id, expires_at=clock.now + 600, now=clock.now)
    assert capped is None, "the idle bound already passed: the session is gone"


@pytest.mark.asyncio
async def test_touch_never_moves_past_the_hard_bound(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.sessions.time.time", clock)
    authority = _authority()
    await authority.register_user(sub="u1", username="u1")
    grant = await authority.login(sub="u1", ttl_seconds=1000, idle_ttl_seconds=800)
    record = await authority.touch(grant.session_id, expires_at=clock.now + 5000, now=clock.now + 100)
    assert record["exp"] == clock.now + 1000


@pytest.mark.asyncio
async def test_validate_token_exposes_the_live_record():
    authority = _authority()
    await authority.register_user(sub="u1", username="u1")
    grant = await authority.login(sub="u1", ttl_seconds=1000, idle_ttl_seconds=800)
    verification = await authority.validate_token(grant.token)
    assert verification.record["session_id"] == grant.session_id
    assert verification.record["max_exp"] - verification.record["exp"] == 200


# ---- the gateway manager slides ----------------------------------------------

@pytest.mark.asyncio
async def test_auth_manager_slides_after_the_touch_interval(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.sessions.time.time", clock)
    redis = FakeRedis()
    authority = _authority(redis)
    await authority.register_user(sub="u1", username="u1", roles=["r"])
    policy = SessionPolicy(idle_ttl_seconds=600, max_ttl_seconds=3600, touch_interval_seconds=60)
    grant = await authority.login(sub="u1", ttl_seconds=policy.max_ttl_seconds, idle_ttl_seconds=policy.idle_ttl_seconds)
    manager = BundleSessionAuthManager(authority=authority, sliding=policy)
    key = authority._session_key(grant.session_id)

    clock.now += 30
    await manager.authenticate(grant.token)
    assert json.loads(redis.values[key])["exp"] == grant.expires_at, "inside the interval: no touch"

    clock.now += 60
    user = await manager.authenticate(grant.token)
    assert user.roles == ["r"]
    assert json.loads(redis.values[key])["exp"] == clock.now + 600, "past the interval: slid to idle from now"

    fixed = BundleSessionAuthManager(authority=authority)
    clock.now += 120
    await fixed.authenticate(grant.token)
    assert json.loads(redis.values[key])["exp"] == clock.now - 120 + 600, "no policy: no slide"


# ---- the adapters under the package flow --------------------------------------

@pytest.mark.asyncio
async def test_flow_over_the_platform_backend_issues_a_platform_session(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.sessions.time.time", clock)
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.login_lane.time.time", clock)
    redis = FakeRedis()
    authority = _authority(redis)
    policy = SessionPolicy(idle_ttl_seconds=600, max_ttl_seconds=3600, touch_interval_seconds=60, attempt_ttl_seconds=120)
    seen: list[VerifiedIdentity] = []

    def grants(identity: VerifiedIdentity):
        seen.append(identity)
        return ["member"], ["chat:use"], "test"

    backend = PlatformSessionBackend(authority, grants=grants, policy=policy)
    upstream = FakeUpstream(_identity())
    flow = BrowserSessionFlow(
        backend=backend,
        attempts=RedisLoginAttemptStore(authority),
        upstream=upstream,
        cookies=StandardCookiePolicy(session_name="__Secure-LATC", secure=True),
        policy=policy,
        clock=clock,
    )

    start = await flow.begin_login("/app?tab=2")
    assert start.redirect_url.endswith(start.attempt.state)
    attempt_key = authority._ns(f"kdcube:auth:browser-login:{start.attempt.state}")
    assert attempt_key in redis.values

    done = await flow.complete_login({"state": start.attempt.state, "code": "c"}, attempt_binding=start.attempt.binding)
    assert done.redirect_to == "/app?tab=2"
    assert done.session.subject == "cognito:abc-123"
    assert done.session.user["email"] == "person@example.com"
    assert done.session.user["roles"] == ["member"] and done.session.user["permissions"] == ["chat:use"]
    assert done.session_cookie.name == "__Secure-LATC" and done.session_cookie.http_only
    assert done.session_cookie.max_age == policy.max_ttl_seconds
    assert attempt_key not in redis.values, "the attempt is one-time"
    assert seen and seen[0].canonical_subject == "cognito:abc-123"

    verification = await authority.validate_token(done.session.token)
    assert verification.user.sub == "cognito:abc-123"
    assert verification.record["exp"] == clock.now + 600 and verification.record["max_exp"] == clock.now + 3600
    assert verification.record["metadata"]["upstream"] == "fake-idp"

    with pytest.raises(LoginAttemptRejected):
        await flow.complete_login({"state": start.attempt.state, "code": "c"}, attempt_binding=start.attempt.binding)

    clock.now += 61
    state = await flow.validate_request(done.session.token)
    assert state is not None and state.expires_at == clock.now + 600
    assert state.user["username"] == "person@example.com"

    ended = await flow.logout(done.session.token, post_logout_redirect="https://site.example/")
    assert ended.ended and ended.upstream_logout_url == "https://idp.example/logout?to=https://site.example/"
    assert await flow.validate_request(done.session.token) is None


@pytest.mark.asyncio
async def test_attempt_store_take_is_get_and_delete_and_respects_expiry():
    redis = FakeRedis()
    authority = _authority(redis)
    store = RedisLoginAttemptStore(authority)
    live = LoginAttempt(state="s1", binding="b", nonce="n", code_verifier="v", next_path="/x", created_at=1, expires_at=4_000_000_000)
    await store.put(live)
    assert (await store.take("s1")).next_path == "/x"
    assert await store.take("s1") is None
    stale = LoginAttempt(state="s2", binding="b", nonce="n", code_verifier="v", next_path="/x", created_at=1, expires_at=2)
    await store.put(stale)
    assert await store.take("s2") is None


# ---- the lane's configuration ------------------------------------------------

def _platform_auth(authenticator_type: str = "multi_cognito", **provider_extra):
    return {
        "auth_provider": "bundle",
        "authority_id": "kdcube.platform",
        "provider_id": "server_login",
        "authority": {"platform": True, "grants": {}},
        "provider": {
            "type": "bundle",
            "input": {"authenticator_ref": {"authority_id": "kdcube.platform", "provider_id": "cognito"}, "groups_claim": "cognito:groups", **provider_extra.pop("input", {})},
            "issuer": {"ttl_seconds": 7200, "max_ttl_seconds": 86400, "cookie": {"same_site": "lax"}},
            **provider_extra,
        },
        "login_authenticator": {
            "ok": True,
            "provider": {
                "type": authenticator_type,
                "authenticator": {
                    "type": "cognito_id_token",
                    "region": "eu-west-1",
                    "user_pool_id": "eu-west-1_POOL",
                    "app_client_id": "client-id",
                    "hosted_ui_domain": "https://auth.example.com",
                },
            },
        },
    }


def _settings(platform_auth):
    return SimpleNamespace(
        connection_hub_platform_auth_config=lambda: platform_auth,
        AUTH=SimpleNamespace(AUTH_TOKEN_COOKIE_NAME="__Secure-LATC"),
    )


def test_config_resolves_cognito_authenticator_from_the_registry():
    config = bundle_login_config(_settings(_platform_auth()))
    assert config is not None
    assert config.issuer_url == "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_POOL"
    assert config.client_id == "client-id"
    assert config.hosted_ui_domain == "https://auth.example.com"
    assert config.authenticator_kind == "cognito"
    assert config.policy.idle_ttl_seconds == 7200 and config.policy.max_ttl_seconds == 86400
    assert config.session_cookie_name == "__Secure-LATC"
    assert config.groups_claim == "cognito:groups"
    assert LOGIN_ROUTE == "/api/platform/session/login"


def test_config_is_absent_without_a_bundle_provider_or_oidc_authenticator():
    assert login_authenticator_is_oidc({"auth_provider": "cognito"}) is False
    assert bundle_login_config(_settings({"auth_provider": "cognito"})) is None
    no_authenticator = _platform_auth()
    no_authenticator["login_authenticator"] = {}
    assert login_authenticator_is_oidc(no_authenticator) is False
    assert bundle_login_config(_settings(no_authenticator)) is None
    assert login_authenticator_is_oidc(_platform_auth()) is True


def test_config_max_ttl_is_never_below_idle_ttl():
    platform_auth = _platform_auth()
    platform_auth["provider"]["issuer"] = {"ttl_seconds": 7200, "max_ttl_seconds": 60}
    config = bundle_login_config(_settings(platform_auth))
    assert config.policy.max_ttl_seconds == 7200


# ---- a host that keeps its own login: tokens beside the session ---------------

class _Rejecting:
    async def authenticate(self, token):
        raise AssertionError("must not be called")

    async def authenticate_with_both(self, access_token, id_token):
        raise AssertionError("must not be called")


class _TokenManager:
    def __init__(self):
        self.calls = []

    async def authenticate(self, token):
        self.calls.append(("one", token, None)); return SimpleNamespace(sub="jwt-user")

    async def authenticate_with_both(self, access_token, id_token):
        self.calls.append(("both", access_token, id_token)); return SimpleNamespace(sub="jwt-user")


@pytest.mark.asyncio
async def test_session_or_token_manager_dispatches_by_credential_shape():
    from kdcube_ai_app.auth.bundle import SessionOrTokenAuthManager

    authority = _authority()
    await authority.register_user(sub="u1", username="u1")
    grant = await authority.login(sub="u1", ttl_seconds=600)
    tokens = _TokenManager()
    manager = SessionOrTokenAuthManager(BundleSessionAuthManager(authority=authority), tokens)

    assert (await manager.authenticate(grant.token)).sub == "u1"
    assert (await manager.authenticate_with_both(grant.token, "ignored-id")).sub == "u1"
    assert tokens.calls == []

    assert (await manager.authenticate_with_both("eyJhbGciOi.jwt.sig", "eyJ.id.sig")).sub == "jwt-user"
    assert tokens.calls[-1] == ("both", "eyJhbGciOi.jwt.sig", "eyJ.id.sig")
    assert (await manager.authenticate("eyJhbGciOi.jwt.sig")).sub == "jwt-user"

    only_session = SessionOrTokenAuthManager(BundleSessionAuthManager(authority=authority), None)
    with pytest.raises(AuthenticationError):
        await only_session.authenticate("eyJhbGciOi.jwt.sig")
    with pytest.raises(AuthenticationError):
        await manager.authenticate("")


def test_accepted_cognito_providers_come_from_the_lane_authenticator():
    from kdcube_ai_app.auth.bundle.login_lane import accepted_cognito_providers

    platform_auth = _platform_auth()
    platform_auth["login_authenticator"]["provider"]["authenticator"]["trusted_providers"] = [
        {"alias": "staging", "kind": "cognito", "region": "eu-west-1", "user_pool_id": "eu-west-1_OTHER", "app_client_id": "other-client"},
    ]
    seen = {}

    def resolve(**kwargs):
        seen.update(kwargs); return ["primary", "staging"]

    settings = _settings(platform_auth)
    settings._resolve_cognito_trusted_providers = resolve
    config = bundle_login_config(settings)
    assert accepted_cognito_providers(config, settings) == ["primary", "staging"]
    assert seen["primary_pool_id"] == "eu-west-1_POOL" and seen["primary_client_id"] == "client-id"
    assert seen["registry_providers"][0]["alias"] == "staging"

    platform_auth["provider"]["input"]["accept_authenticator_tokens"] = False
    assert accepted_cognito_providers(bundle_login_config(settings), settings) == []
