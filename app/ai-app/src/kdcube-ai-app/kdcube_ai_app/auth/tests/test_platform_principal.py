from __future__ import annotations

import asyncio
import json

import pytest

from connection_hub.hub.edges import ConnectionEdgeStore
from connection_hub.server_side_login.model import VerifiedIdentity

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle.platform_principal import PlatformPrincipalResolver
from kdcube_ai_app.auth.bundle.sessions import BundleSessionAuthority
from kdcube_ai_app.auth.implementations.cognito import CognitoUser
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis


ISSUER = "https://issuer.example"


def _identity(**overrides) -> VerifiedIdentity:
    values = {
        "provider": "cognito",
        "subject": "subject-1",
        "email": "person@example.test",
        "email_verified": True,
        "name": "Person",
        "claims": {"iss": ISSUER},
    }
    values.update(overrides)
    return VerifiedIdentity(**values)


def _resolver(tmp_path, *, redis=None):
    shared = redis or FakeRedis()
    authority = BundleSessionAuthority(
        tenant="tenant",
        project="project",
        redis=shared,
        secret="secret",
    )
    store = ConnectionEdgeStore(tmp_path)
    resolver = PlatformPrincipalResolver(
        authority=authority,
        edge_store=store,
        tenant="tenant",
        project="project",
        configured_authority_id=ISSUER,
    )
    return resolver, authority, store, shared


@pytest.mark.asyncio
async def test_first_verified_identity_gets_prefixed_principal_and_edge(tmp_path):
    resolver, _authority, store, _redis = _resolver(tmp_path)

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == "cognito:subject-1"
    assert resolved.source == "first_verified_sign_in"
    edge = store.resolve_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
    )
    assert edge["to"]["user_id"] == "cognito:subject-1"
    assert edge["proof"]["type"] == "verified_first_sign_in"


@pytest.mark.asyncio
async def test_explicit_edge_maps_another_sign_in_to_the_original_principal(tmp_path):
    resolver, _authority, store, _redis = _resolver(tmp_path)
    store.upsert_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
        to_user_id="google:first-subject",
        proof={"type": "verified_link"},
    )

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == "google:first-subject"
    assert resolved.source == "connection_edge"


@pytest.mark.asyncio
async def test_provider_scoped_legacy_edge_moves_to_the_verified_issuer(tmp_path):
    resolver, _authority, store, redis = _resolver(tmp_path)
    legacy = store.upsert_edge(
        from_authority_id="cognito",
        from_provider="cognito",
        from_subject="subject-1",
        to_user_id="cognito:original",
    )
    from connection_hub.hub.edge_cache import ConnectionEdgeRuntimeCache

    cache = ConnectionEdgeRuntimeCache(redis, tenant="tenant", project="project")
    await cache.publish_edge(legacy)

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == "cognito:original"
    assert store.resolve_edge(
        from_authority_id="cognito",
        from_provider="cognito",
        from_subject="subject-1",
    ) is None
    assert store.resolve_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
    )["to"]["user_id"] == "cognito:original"
    assert await cache.read(authority_id="cognito", subject="subject-1") is None


@pytest.mark.asyncio
async def test_proven_raw_legacy_user_is_preserved_and_linked(tmp_path):
    resolver, authority, store, _redis = _resolver(tmp_path)
    await authority.register_user(
        sub="subject-1",
        username="legacy",
        email="person@example.test",
        provider="cognito",
        provider_subject="subject-1",
    )
    await authority.register_user(
        sub="cognito:subject-1",
        username="duplicate",
        email="person@example.test",
        provider="cognito",
        provider_subject="subject-1",
    )

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == "subject-1"
    assert resolved.source == "legacy_platform_user"
    edge = store.resolve_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
    )
    assert edge["to"]["user_id"] == "subject-1"
    assert edge["proof"]["type"] == "verified_sign_in_legacy_transition"


@pytest.mark.asyncio
async def test_email_match_does_not_link_an_identity(tmp_path):
    resolver, authority, _store, _redis = _resolver(tmp_path)
    await authority.register_user(
        sub="someone-else",
        username="legacy",
        email="person@example.test",
        provider="cognito",
        provider_subject="someone-else",
    )

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == "cognito:subject-1"


@pytest.mark.asyncio
async def test_second_worker_reads_the_shared_redis_projection(tmp_path, monkeypatch):
    first, _authority, _store, redis = _resolver(tmp_path)
    assert (await first.resolve(_identity())).user_id == "cognito:subject-1"
    second, _authority2, second_store, _redis2 = _resolver(tmp_path, redis=redis)

    def unexpected_read(**_kwargs):
        raise AssertionError("durable storage should not be read on a Redis hit")

    monkeypatch.setattr(second_store, "list_edges", unexpected_read)
    resolved = await second.resolve(_identity())

    assert resolved.user_id == "cognito:subject-1"
    assert resolved.source == "connection_edge_cache"


@pytest.mark.asyncio
async def test_different_workers_serialize_durable_edge_creation(tmp_path):
    first, _authority, _store, redis = _resolver(tmp_path)
    second, _authority2, second_store, _redis2 = _resolver(tmp_path, redis=redis)

    resolved = await asyncio.gather(
        first.resolve(_identity(subject="subject-1")),
        second.resolve(_identity(subject="subject-2")),
    )

    assert {item.user_id for item in resolved} == {
        "cognito:subject-1",
        "cognito:subject-2",
    }
    assert len(second_store.list_edges(source_provider="cognito")) == 2


@pytest.mark.asyncio
async def test_corrupt_durable_edges_fail_closed(tmp_path):
    path = tmp_path / "connections" / "connection-edges.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    resolver, _authority, _store, _redis = _resolver(tmp_path)

    with pytest.raises(AuthenticationError, match="mapping is unavailable"):
        await resolver.resolve(_identity())


@pytest.mark.asyncio
async def test_invalid_cached_projection_reads_through_to_durable_edge(tmp_path):
    resolver, _authority, store, redis = _resolver(tmp_path)
    edge = store.upsert_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
        to_user_id="cognito:subject-1",
    )
    from connection_hub.hub.edge_cache import ConnectionEdgeRuntimeCache

    cache = ConnectionEdgeRuntimeCache(redis, tenant="tenant", project="project")
    redis.values[cache.key(authority_id=ISSUER, subject="subject-1")] = json.dumps(
        {"schema": "invalid"}
    )

    resolved = await resolver.resolve(_identity())

    assert resolved.user_id == edge["to"]["user_id"]


@pytest.mark.asyncio
async def test_direct_token_user_is_copied_and_mapped_through_the_same_edge(tmp_path):
    resolver, _authority, store, _redis = _resolver(tmp_path)
    store.upsert_edge(
        from_authority_id=ISSUER,
        from_provider="cognito",
        from_subject="subject-1",
        to_user_id="google:original",
    )
    upstream = CognitoUser(
        sub="subject-1",
        username="person",
        email="person@example.test",
        roles=["member"],
        permissions=[],
        issuer=ISSUER,
    )

    mapped = await resolver.map_token_user(upstream, provider="cognito")

    assert mapped.sub == "google:original"
    assert upstream.sub == "subject-1"
    assert mapped.roles == ["member"]
