# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import os
import time
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.client_metadata import (
    ClientMetadataError,
    ClientMetadataFetch,
    _cache_ttl,
    fetch_client_metadata_document,
    resolve_client_metadata_document,
    resolve_public_addresses,
    validate_client_metadata_document,
    validate_client_metadata_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.clients import (
    CLIENT_REGISTRATION_METADATA_DOCUMENT,
    redirect_uri_allowed,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientMetadataDocumentsConfig,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import (
    make_s256_challenge,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_card_persistence,
    enable_delegated_client,
    mount_test_oauth_adapter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.test_clients_and_store import (
    FakeRedis,
)

ISSUER = "https://connector.example.test"
CLIENT_ID = "https://client.example.test/oauth/client.json"
CALLBACK = "http://127.0.0.1:43123/callback"
VERIFIER = "v" * 60


def _document(**overrides):
    document = {
        "client_id": CLIENT_ID,
        "client_name": "Example desktop client",
        "client_uri": "https://client.example.test",
        "redirect_uris": [CALLBACK],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "native",
    }
    document.update(overrides)
    return document


def test_metadata_url_requires_https_path_and_rejects_dot_segments():
    config = OAuthDelegatedClientMetadataDocumentsConfig(enabled=True)

    with pytest.raises(ClientMetadataError):
        validate_client_metadata_url("http://client.example.test/client.json", config)
    with pytest.raises(ClientMetadataError):
        validate_client_metadata_url("https://client.example.test", config)
    for url in (
        "https://client.example.test/a/../client.json",
        "https://client.example.test/a/%2e%2e/client.json",
    ):
        with pytest.raises(ClientMetadataError) as exc_info:
            validate_client_metadata_url(url, config)
        assert "dot path segments" in exc_info.value.description

    # The current IETF draft permits `/`, although it does not recommend it.
    assert validate_client_metadata_url("https://client.example.test/", config)[0] == "client.example.test"


def test_metadata_url_enforces_descriptor_domain_policy():
    config = OAuthDelegatedClientMetadataDocumentsConfig(
        enabled=True,
        allowed_domains=("trusted.example",),
        allow_subdomains=True,
    )

    assert validate_client_metadata_url("https://app.trusted.example/client.json", config)[0] == "app.trusted.example"
    with pytest.raises(ClientMetadataError) as exc_info:
        validate_client_metadata_url(CLIENT_ID, config)
    assert exc_info.value.code == "unauthorized_client"


@pytest.mark.asyncio
async def test_metadata_dns_rejects_private_address(monkeypatch):
    loop = __import__("asyncio").get_running_loop()

    async def fake_getaddrinfo(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ClientMetadataError) as exc_info:
        await resolve_public_addresses("client.example.test", 443)
    assert exc_info.value.description.endswith("non-public address")


@pytest.mark.asyncio
async def test_metadata_dns_resolution_obeys_fetch_timeout():
    config = OAuthDelegatedClientMetadataDocumentsConfig(
        enabled=True,
        fetch_timeout_seconds=0.01,
    )

    async def stalled_resolver(_host, _port):
        await asyncio.sleep(1)
        return ("203.0.113.10",)

    with pytest.raises(ClientMetadataError) as exc_info:
        await fetch_client_metadata_document(
            CLIENT_ID,
            config,
            address_resolver=stalled_resolver,
        )

    assert exc_info.value.code == "temporarily_unavailable"
    assert "resolution timed out" in exc_info.value.description


def test_metadata_document_requires_exact_client_id():
    with pytest.raises(ClientMetadataError) as exc_info:
        validate_client_metadata_document(
            CLIENT_ID,
            _document(client_id="https://attacker.example/client.json"),
        )
    assert exc_info.value.code == "invalid_client_metadata"


def test_metadata_document_builds_exact_redirect_client():
    client = validate_client_metadata_document(CLIENT_ID, _document())

    assert client.registration_kind == CLIENT_REGISTRATION_METADATA_DOCUMENT
    assert client.client_name == "Example desktop client"
    assert redirect_uri_allowed(client, CALLBACK)
    assert not redirect_uri_allowed(client, "http://127.0.0.1:43124/callback")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"client_name": 7}, "client_name must be a string"),
        ({"redirect_uris": [CALLBACK, CALLBACK]}, "valid redirect_uris"),
        ({"redirect_uris": ["javascript:alert(1)"]}, "valid redirect_uris"),
        (
            {"application_type": "web", "redirect_uris": [CALLBACK]},
            "valid redirect_uris",
        ),
        ({"client_secret": "must-not-be-here"}, "cannot contain shared client secrets"),
    ],
)
def test_metadata_document_rejects_unsafe_or_ambiguous_client_fields(overrides, message):
    with pytest.raises(ClientMetadataError) as exc_info:
        validate_client_metadata_document(CLIENT_ID, _document(**overrides))

    assert message in exc_info.value.description


@pytest.mark.asyncio
async def test_metadata_resolution_uses_shared_cache():
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    config = OAuthDelegatedClientMetadataDocumentsConfig(enabled=True)
    calls = 0

    async def fetcher(client_id, fetch_config):
        nonlocal calls
        calls += 1
        assert client_id == CLIENT_ID
        assert fetch_config is config
        return ClientMetadataFetch(document=_document(), cache_ttl_seconds=120)

    first = await resolve_client_metadata_document(
        CLIENT_ID,
        config=config,
        store=store,
        fetcher=fetcher,
    )
    second = await resolve_client_metadata_document(
        CLIENT_ID,
        config=config,
        store=store,
        fetcher=fetcher,
    )

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_metadata_resolution_evicts_invalid_cached_snapshot_and_refetches():
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    config = OAuthDelegatedClientMetadataDocumentsConfig(enabled=True)
    poisoned = _document(client_id="https://attacker.example/client.json")
    await store.cache_client_metadata_document(
        CLIENT_ID,
        poisoned,
        ttl_seconds=120,
    )

    calls = 0

    async def fetcher(_client_id, _config):
        nonlocal calls
        calls += 1
        return ClientMetadataFetch(document=_document(), cache_ttl_seconds=120)

    client = await resolve_client_metadata_document(
        CLIENT_ID,
        config=config,
        store=store,
        fetcher=fetcher,
    )

    assert client.client_id == CLIENT_ID
    assert calls == 1


@pytest.mark.asyncio
async def test_invalid_metadata_is_not_cached():
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    config = OAuthDelegatedClientMetadataDocumentsConfig(enabled=True)
    calls = 0

    async def fetcher(_client_id, _config):
        nonlocal calls
        calls += 1
        return ClientMetadataFetch(
            document=_document(client_id="https://attacker.example/client.json"),
            cache_ttl_seconds=120,
        )

    for _attempt in range(2):
        with pytest.raises(ClientMetadataError):
            await resolve_client_metadata_document(
                CLIENT_ID,
                config=config,
                store=store,
                fetcher=fetcher,
            )

    assert calls == 2
    assert await store.get_client_metadata_cache(CLIENT_ID) is None


def test_shared_metadata_cache_does_not_store_no_cache_or_private_responses():
    config = OAuthDelegatedClientMetadataDocumentsConfig(enabled=True)

    assert _cache_ttl({"Cache-Control": "no-cache"}, config) == (None, False)
    assert _cache_ttl({"Cache-Control": "private, max-age=120"}, config) == (None, False)


async def _authenticate(token):
    if token == "admin-tok":
        return {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]}
    return None


async def _mint_access_token(sub, scopes):
    return {
        "access_token": f"kst1.cimd.{sub}",
        "expires_in": 3600,
    }


def _route_client(document, *, card_storage_root=None):
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    app.state.oauth_delegated_config["client_id_metadata_documents"] = {
        "enabled": True,
        "allowed_domains": ["client.example.test"],
    }
    calls = []

    async def fetcher(client_id, config):
        calls.append(client_id)
        return ClientMetadataFetch(document=document, cache_ttl_seconds=120)

    app.state.oauth_client_metadata_fetcher = fetcher
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_mint_access_token = _mint_access_token
    if card_storage_root is not None:
        # Token issuance commits a delegated card, so a test that exchanges a
        # code needs the capability a bundle binds in production.
        if not os.environ.get("REDIS_URL"):
            pytest.skip("REDIS_URL is not set; token issuance commits a delegated card")
        import redis.asyncio as redis_asyncio

        bind_delegated_card_persistence(
            app,
            redis=redis_asyncio.from_url(os.environ["REDIS_URL"]),
            storage_root=card_storage_root,
        )
    return TestClient(app), calls


def _authorize_params(redirect_uri=CALLBACK):
    return {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "records:read",
        "state": "s1",
        "code_challenge": make_s256_challenge(VERIFIER),
        "code_challenge_method": "S256",
    }


def test_metadata_client_can_reach_consent_and_is_cached():
    client, calls = _route_client(_document())

    first = client.get(
        "/oauth/authorize",
        params=_authorize_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )
    second = client.get(
        "/oauth/authorize",
        params=_authorize_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert first.status_code == 200
    assert "Example desktop client" in first.text
    assert "metadata published by client.example.test" in first.text
    assert "Local callback:" in first.text
    assert second.status_code == 200
    assert calls == [CLIENT_ID]


def test_metadata_client_rejects_redirect_not_in_document():
    client, _calls = _route_client(_document())

    response = client.get(
        "/oauth/authorize",
        params=_authorize_params("http://127.0.0.1:43124/callback"),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_metadata_client_rejects_mismatched_document_identity():
    client, _calls = _route_client(
        _document(client_id="https://attacker.example/client.json")
    )

    response = client.get(
        "/oauth/authorize",
        params=_authorize_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"



def _seed_live_card(store, refresh_record) -> None:
    """The live card projection the refresh path reads."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_io import (
        encode_cache_value,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
        DelegatedCardRuntimeCache,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
        CardAuthority,
        NamedServiceSelection,
    )

    credential = refresh_record.get("credential") or {}
    access_id = str(refresh_record["registry_access_id"])
    resource = str(refresh_record.get("resource") or "") or "*"
    authority = CardAuthority(
        access_id=access_id,
        client_id=str(refresh_record.get("client_id") or ""),
        grantor_subject=str(refresh_record.get("sub") or ""),
        delegate_subject=str(credential.get("subject") or ""),
        source="oauth",
        card_revision=1,
        operations=tuple(refresh_record.get("operations") or ()),
        resource_grants={resource: tuple(refresh_record.get("scopes") or ())},
        named_service_operations=NamedServiceSelection.unknown(),
        expires_at=int(time.time()) + 3600,
    )
    cache = DelegatedCardRuntimeCache(None, tenant="home", project="demo")
    store.redis.values[cache.card_key(access_id)] = encode_cache_value(
        {"kind": "card", "card_revision": 1, "authority": authority.to_dict()}
    )


def test_metadata_client_survives_code_refresh_and_revocation_lifecycle(tmp_path):
    client, _calls = _route_client(_document(), card_storage_root=tmp_path)
    store = client.app.state.oauth_grant_store
    # Entered as a context manager so every request runs on the same event
    # loop: the Redis client the card persistence holds cannot survive a
    # per-request loop.
    with client:
        _run_metadata_client_lifecycle(client, store)


def _run_metadata_client_lifecycle(client, store):
    shown = client.get(
        "/oauth/authorize",
        params=_authorize_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', shown.text)
    assert shown.status_code == 200 and match is not None

    consent = client.post(
        "/oauth/authorize/consent",
        data={
            **_authorize_params(),
            "csrf_token": match.group(1),
            "decision": "approve",
            "platform_grants": "records:read",
            "tools": "records_export",
        },
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert consent.status_code == 302
    from urllib.parse import parse_qs, urlsplit

    code = parse_qs(urlsplit(consent.headers["location"]).query)["code"][0]
    exchanged = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "client_id": CLIENT_ID,
            "code_verifier": VERIFIER,
        },
    )
    assert exchanged.status_code == 200
    first = exchanged.json()
    first_refresh = first["refresh_token"]
    refresh_record = asyncio.run(store.validate_refresh_token(first_refresh))
    assert refresh_record["client_metadata"]["registration_kind"] == (
        CLIENT_REGISTRATION_METADATA_DOCUMENT
    )
    assert refresh_record["client_metadata"]["client_name"] == (
        "Example desktop client"
    )
    _seed_live_card(store, refresh_record)

    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first_refresh,
            "client_id": CLIENT_ID,
        },
    )
    assert refreshed.status_code == 200
    second_refresh = refreshed.json()["refresh_token"]
    assert second_refresh != first_refresh

    revoked = client.post(
        "/oauth/revoke",
        data={
            "token": second_refresh,
            "token_type_hint": "refresh_token",
            "client_id": CLIENT_ID,
        },
    )
    assert revoked.status_code == 200
    assert asyncio.run(store.validate_refresh_token(second_refresh)) is None


def test_metadata_change_after_display_requires_fresh_consent():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    app.state.oauth_delegated_config["client_id_metadata_documents"] = {
        "enabled": True,
        "allowed_domains": ["client.example.test"],
    }
    documents = iter([
        _document(client_name="Client shown to the user"),
        _document(client_name="Changed client identity"),
    ])

    async def fetcher(_client_id, _config):
        return ClientMetadataFetch(
            document=next(documents),
            cache_ttl_seconds=None,
            cacheable=False,
        )

    app.state.oauth_client_metadata_fetcher = fetcher
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    client = TestClient(app)

    shown = client.get(
        "/oauth/authorize",
        params=_authorize_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', shown.text)
    assert shown.status_code == 200 and match is not None

    consent = client.post(
        "/oauth/authorize/consent",
        data={
            **_authorize_params(),
            "csrf_token": match.group(1),
            "decision": "approve",
            "platform_grants": "records:read",
        },
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )

    assert consent.status_code == 400
    assert consent.json()["error"] == "invalid_client_metadata"
    assert "restart authorization" in consent.json()["error_description"]


def test_absent_application_type_infers_native_from_loopback_redirects():
    """Claude Code's published document omits application_type and lists only
    http loopback redirects; read as "web" those are refused outright."""
    document = _document(redirect_uris=["http://localhost/callback", "http://127.0.0.1/callback"])
    document.pop("application_type")

    client = validate_client_metadata_document(CLIENT_ID, document)

    assert client.application_type == "native"
    assert client.redirect_uris == ("http://localhost/callback", "http://127.0.0.1/callback")


def test_absent_application_type_does_not_admit_a_non_loopback_http_redirect():
    document = _document(redirect_uris=["http://client.example.test/callback"])
    document.pop("application_type")

    with pytest.raises(ClientMetadataError):
        validate_client_metadata_document(CLIENT_ID, document)


def test_absent_application_type_with_https_redirects_stays_web():
    document = _document(redirect_uris=["https://client.example.test/callback"])
    document.pop("application_type")

    assert validate_client_metadata_document(CLIENT_ID, document).application_type == "web"
