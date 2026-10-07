# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Transport refusals of the delegated-client OAuth adapter.

Each test drives the real FastAPI router (or the real surface guard) and
asserts one refusal a delegated caller must meet:

1. a consent POST whose same-origin fallback is spoofed through
   ``X-Forwarded-Host``/``X-Forwarded-Proto`` and a foreign ``Referer``;
2. a resource or a grant outside the current Card;
3. original access past the expiry captured on the database clock;
4. a replayed authorization code or consent nonce (CSRF token);
5. a token presented to another tenant or project;
6. a revoked Card behind an otherwise valid token.

The Card is the authority and the token is a pointer to it: a token whose
recorded grants are broader than the Card it points to gets what the Card
holds now, and a token whose Card is gone gets nothing. No test mints a new
token or widens one to make a refusal pass.

Inputs: ``REDIS_URL`` names a Redis database these tests own (keys are
namespaced per test and removed afterwards); ``KDCUBE_TEST_POSTGRES_DSN``
names a PostgreSQL database these tests own. A test whose input is absent is
skipped with that reason.
"""
from __future__ import annotations

import os
import re
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from connection_hub.delegated_credentials.cards.store import BundleStorageDelegatedCardStore
from connection_hub.delegated_credentials.oauth.consent import CONSENT_CONTRACT_VERSION
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from connection_hub.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_card_persistence,
    enable_delegated_client,
    mount_test_oauth_adapter,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_surface_guard import (
    GUARD_RESOURCE,
    _Redis,
    _authority,
    _client,
    _commit_card,
    _durable_client,
    _projections_swept,
    _live_card,
    _pointer_grant,
    _rpc_tool_call,
    _store_live_card,
)

ISSUER = "https://connector.example.test"
ADMIN = {"Authorization": "Bearer admin-tok"}
VERIFIER = "b6-verifier-" + "v" * 60
CHALLENGE = make_s256_challenge(VERIFIER)
ATTACKER_VERIFIER = "b6-attacker-" + "a" * 60
ATTACKER_CHALLENGE = make_s256_challenge(ATTACKER_VERIFIER)
REDIRECT = "http://127.0.0.1:9876/callback"
ATTACKER_REDIRECT = "http://127.0.0.1:9999/stolen"


# --------------------------------------------------------------------------
# Owned Redis fixture: one namespace per test, removed afterwards.
# --------------------------------------------------------------------------


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL", "")
    if not url:
        pytest.skip("REDIS_URL is not set; these refusals run against a real Redis")
    return url


def _namespace() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    return f"b6t{suffix}", f"b6p{suffix}"


def _drop_namespace(url: str, *tenants: str) -> None:
    import redis as sync_redis

    client = sync_redis.Redis.from_url(url)
    try:
        for tenant in tenants:
            keys = list(client.scan_iter(match=f"*{tenant}*"))
            if keys:
                client.delete(*keys)
    finally:
        client.close()


async def _authenticate(token):
    table = {
        "admin-tok": {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]},
    }
    return table.get(token)


async def _fake_minter(sub, scopes):
    return {"access_token": f"kst1.b6.{uuid.uuid4().hex}", "expires_in": 3600}


def _app(tmp_path, redis, *, tenant: str, project: str) -> FastAPI:
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    app.state.oauth_delegated_config.update({"tenant": tenant, "project": project})
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_mint_access_token = _fake_minter
    app.state.oauth_grant_store = GrantStore(redis, tenant=tenant, project=project)
    bind_delegated_card_persistence(app, redis=redis, storage_root=tmp_path)
    return app


@pytest.fixture
def real_oauth(tmp_path):
    """The router over a GrantStore on the owned Redis, in a fresh namespace."""
    url = _redis_url()
    import redis.asyncio as redis_asyncio

    tenant, project = _namespace()
    redis = redis_asyncio.from_url(url)
    app = _app(tmp_path, redis, tenant=tenant, project=project)
    try:
        with TestClient(app) as client:
            yield client, app.state.oauth_grant_store
    finally:
        _drop_namespace(url, tenant)


def _params(**over):
    params = {
        "client_id": "claude",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "records:read",
        "state": "st-b6",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    params.update(over)
    return params


def _consent_form(csrf, *, drop=(), **over):
    form = dict(_params(**over))
    form["decision"] = "approve"
    form["platform_grants"] = ["records:read"]
    form["tools"] = ["records_export"]
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
    form["csrf_token"] = csrf
    for key in drop:
        form.pop(key, None)
    return form


def _csrf_from_consent_page(client) -> str:
    response = client.get("/oauth/authorize", params=_params(), headers=ADMIN)
    assert response.status_code == 200, response.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match, "the consent page embeds a csrf_token field"
    return match.group(1)


def _issued_code(response) -> dict:
    """The authorization response's query, or {} when no code was issued."""
    if response.status_code not in (302, 303):
        return {}
    query = parse_qs(urlsplit(response.headers.get("location", "")).query)
    return query if "code" in query else {}


# --------------------------------------------------------------------------
# 1. Cross-origin and forwarded-host spoof on consent
# --------------------------------------------------------------------------


def _consent_with_foreign_referer(client, *, spoof_forwarded: bool):
    """POST a valid consent whose form omits code_challenge and state.

    The Referer is the attacker's authorize URL carrying the attacker's PKCE
    challenge and state. With ``spoof_forwarded`` the request also claims,
    through X-Forwarded-Host/Proto, to have reached the attacker's host.
    """
    csrf = _csrf_from_consent_page(client)
    foreign = "https://attacker.example/oauth/authorize?" + urlencode(
        _params(code_challenge=ATTACKER_CHALLENGE, state="attacker-state")
    )
    headers = {**ADMIN, "Referer": foreign}
    if spoof_forwarded:
        headers.update({"X-Forwarded-Host": "attacker.example", "X-Forwarded-Proto": "https"})
    return client.post(
        "/oauth/authorize/consent",
        data=_consent_form(csrf, drop=("code_challenge", "state")),
        headers=headers,
        follow_redirects=False,
    )


def _assert_no_code_for_the_attacker(client, response) -> None:
    issued = _issued_code(response)
    if not issued:
        return
    assert issued.get("state") != ["attacker-state"], (
        "the authorization response carries the state taken from a foreign Referer"
    )
    redeemed = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": "claude",
            "code_verifier": ATTACKER_VERIFIER,
        },
    )
    assert redeemed.status_code == 400, (
        "a code bound to the PKCE challenge from a foreign Referer redeems with "
        f"the attacker's verifier: {redeemed.status_code} {redeemed.text[:120]}"
    )


def test_consent_takes_nothing_from_a_cross_origin_referer(real_oauth):
    """A Referer from another origin supplies no authorize parameter."""
    client, _ = real_oauth

    response = _consent_with_foreign_referer(client, spoof_forwarded=False)

    _assert_no_code_for_the_attacker(client, response)


def test_consent_takes_nothing_from_a_referer_made_same_origin_by_forwarded_headers(real_oauth):
    """Forwarded headers the request carries cannot make a foreign Referer same-origin.

    Same-origin is decided against the deployment's public origin, never
    against X-Forwarded-Host/Proto values the caller chose.
    """
    client, _ = real_oauth

    response = _consent_with_foreign_referer(client, spoof_forwarded=True)

    _assert_no_code_for_the_attacker(client, response)


# --------------------------------------------------------------------------
# 4. Replayed code and replayed consent nonce
# --------------------------------------------------------------------------


def _code_from_consent(client) -> str:
    csrf = _csrf_from_consent_page(client)
    response = client.post(
        "/oauth/authorize/consent",
        data=_consent_form(csrf),
        headers=ADMIN,
        follow_redirects=False,
    )
    issued = _issued_code(response)
    assert issued, f"precondition: a valid consent issues a code ({response.status_code} {response.text})"
    return issued["code"][0]


def _redeem(client, code: str):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": "claude",
            "code_verifier": VERIFIER,
        },
    )


def test_a_replayed_authorization_code_refuses_after_one_redemption(real_oauth):
    client, _ = real_oauth
    code = _code_from_consent(client)

    first = _redeem(client, code)
    assert first.status_code == 200, first.text
    assert first.json().get("access_token")

    replay = _redeem(client, code)
    assert replay.status_code == 400, replay.text
    assert replay.json()["error"] == "invalid_grant"
    assert "access_token" not in replay.json()


def test_a_replayed_consent_nonce_issues_no_second_code(real_oauth):
    client, _ = real_oauth
    csrf = _csrf_from_consent_page(client)
    first = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf), headers=ADMIN,
        follow_redirects=False,
    )
    assert _issued_code(first), first.text

    replay = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf), headers=ADMIN,
        follow_redirects=False,
    )
    assert replay.status_code == 403, replay.text
    assert replay.json()["error"] == "invalid_csrf"
    assert not _issued_code(replay)


# --------------------------------------------------------------------------
# 5. A token presented to another tenant or project
# --------------------------------------------------------------------------


def test_a_token_from_one_tenant_resolves_nowhere_in_another(tmp_path):
    """An access token issued in one tenant/project is unknown to every other.

    Both grant stores share the owned Redis; only the namespace differs.
    """
    url = _redis_url()
    import redis.asyncio as redis_asyncio

    tenant_a, project_a = _namespace()
    tenant_b, _ = _namespace()
    redis = redis_asyncio.from_url(url)
    app = _app(tmp_path, redis, tenant=tenant_a, project=project_a)
    try:
        with TestClient(app) as client:
            code = _code_from_consent(client)
            issued = _redeem(client, code)
            assert issued.status_code == 200, issued.text
            token = issued.json()["access_token"]

            home = GrantStore(redis, tenant=tenant_a, project=project_a)
            other_tenant = GrantStore(redis, tenant=tenant_b, project=project_a)
            other_project = GrantStore(redis, tenant=tenant_a, project=f"{project_a}x")

            assert client.portal.call(home.get_access_grant_record, token), (
                "precondition: the issuing namespace resolves its own token"
            )
            assert client.portal.call(other_tenant.get_access_grant_record, token) is None
            assert client.portal.call(other_project.get_access_grant_record, token) is None
    finally:
        _drop_namespace(url, tenant_a, tenant_b)


@pytest.mark.asyncio
async def test_a_card_from_another_tenant_does_not_authorize_this_one(monkeypatch, tmp_path):
    """The guard resolves the Card from its own tenant/project's durable store.

    The same pointer token is admitted where its Card was committed and
    refused by a guard whose tenant/project store holds no such Card.
    """
    home = BundleStorageDelegatedCardStore(tmp_path / "home__demo")
    other = BundleStorageDelegatedCardStore(tmp_path / "other__demo")
    await _commit_card(home, _live_card())

    control = _durable_client(monkeypatch, home)
    assert control.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    ).status_code == 200

    client = _durable_client(monkeypatch, other)
    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )
    assert response.status_code == 403, response.text


def _with_card(card) -> _Redis:
    redis = _Redis()
    _store_live_card(redis, card)
    return redis


# --------------------------------------------------------------------------
# 2. Resource or grant outside the Card, and 6. a revoked Card: the pointer ruling
# --------------------------------------------------------------------------


def _stale_broad_pointer(access_id: str = "oauth-access-1") -> dict:
    """A token record whose own grants are broader than any Card it points to."""
    record = _pointer_grant(access_id)
    broad = _authority(scopes=["records:read", "records:write", "memories:read"])
    broad["attrs"]["resource_grants"] = {
        GUARD_RESOURCE: ["records:read", "records:write", "memories:read"],
        "http://testserver/other": ["records:read", "memories:read"],
    }
    record["credential"] = broad
    record["operations"] = ["records_export", "memory_search"]
    return record


def _tool_result_refused(response) -> bool:
    if response.status_code == 403:
        return True
    body = response.json()
    result = body.get("result") if isinstance(body, dict) else None
    return isinstance(result, dict) and result.get("isError") is True


def test_a_resource_outside_the_card_refuses_despite_a_broad_token(monkeypatch):
    card = _live_card(resource_grants={"http://testserver/other": ("records:read",)})
    client = _client(monkeypatch, grant_record=_stale_broad_pointer(), redis=_with_card(card))

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert _tool_result_refused(response), response.text


def test_a_grant_outside_the_card_refuses_despite_a_broad_token(monkeypatch):
    """The catalog's memory_search needs memories:read; the Card holds records:read only.

    The token record claims memories:read; the Card decides.
    """
    client = _client(
        monkeypatch,
        grant_record=_stale_broad_pointer(),
        redis=_with_card(_live_card(operations=("records_export", "memory_search"))),
    )

    response = client.post(
        "/guard",
        json=_rpc_tool_call("memory_search"),
        headers={"Authorization": "Bearer reader"},
    )

    assert _tool_result_refused(response), response.text


def test_an_operation_outside_the_card_refuses_despite_a_broad_token(monkeypatch):
    client = _client(
        monkeypatch,
        grant_record=_stale_broad_pointer(),
        redis=_with_card(_live_card(operations=())),
    )

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert _tool_result_refused(response), response.text


def test_the_card_grants_what_it_holds_to_the_same_broad_token(monkeypatch):
    """Positive control: the same token passes where the current Card allows it."""
    client = _client(monkeypatch, grant_record=_stale_broad_pointer(), redis=_with_card(_live_card()))

    response = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_a_revoked_card_refuses_its_still_valid_token_without_reissue(monkeypatch, tmp_path):
    """The bearer stays valid; the Card's revoked revision ends its authority."""
    store = BundleStorageDelegatedCardStore(tmp_path)
    card = _live_card()
    await _commit_card(store, card)
    redis = _Redis()
    client = _durable_client(monkeypatch, store, redis=redis)
    allowed = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )
    assert allowed.status_code == 200, allowed.text

    await _commit_card(store, card, revoked=True)
    redis.values.clear()  # the projection follows the revocation
    writes_before = redis.write_count

    refused = client.post(
        "/guard", json=_rpc_tool_call(), headers={"Authorization": "Bearer reader"},
    )

    assert refused.status_code == 403, refused.text
    assert redis.write_count == writes_before, "a refusal writes nothing: no re-mint, no re-cache"


# --------------------------------------------------------------------------
# 3. Original access past its expiry captured on the database clock
# --------------------------------------------------------------------------


@pytest.fixture
def _postgres_input():
    if not os.environ.get("KDCUBE_TEST_POSTGRES_DSN"):
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set; expiry is captured on PostgreSQL's clock")


@pytest.mark.asyncio
async def test_original_access_refuses_once_the_database_clock_passes_its_capture(
    _postgres_input, store, ledger
):
    """The deadline is the database clock's, captured once; nothing re-derives it."""
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
        OriginalExchangeRefused,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import (
        plan,
        proof,
        validated,
    )

    binding = validated(store)
    await ledger.begin(binding)
    await ledger.pin_plan(binding, plan(binding))
    captured = await ledger.capture_access_expiry(proof(store), ttl_seconds=1)
    assert (await ledger.read(proof(store))).access_expires_at == captured.access_expires_at

    async with store._pool.acquire() as connection:
        await connection.execute("SELECT pg_sleep(2.2)")
        now = await connection.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint")
    assert now > captured.access_expires_at

    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expired$"):
        await ledger.read(proof(store))
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expired$"):
        await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)


# The PostgreSQL fixtures the expiry test uses, from the suites that own them.
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import store  # noqa: E402,F401
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import (  # noqa: E402,F401
    ledger,
    store_namespace,
)

# The guard suite's projection gate: this Redis run's Card projections are
# already swept, as after startup. Imported so it applies here too.
assert _projections_swept
