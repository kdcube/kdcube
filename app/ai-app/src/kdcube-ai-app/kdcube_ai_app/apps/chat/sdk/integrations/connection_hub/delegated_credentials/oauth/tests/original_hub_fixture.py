"""Real Hub/SDK stores and private file custody over an in-process ASGI API.

Card serving projection, declared catalog resolver, signing key and physical
volume qualification are explicit fixtures. No encryption or installed runtime
is qualified; RuntimeFileStore enforces private modes, locks and fsync.
"""
from __future__ import annotations

import hashlib
import os
import traceback
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from starlette.datastructures import FormData

from connection_hub.delegated_credentials.automation_access import AutomationAccessService
from connection_hub.delegated_credentials.cards import transaction_store
from connection_hub.delegated_credentials.cards.card_participant import (
    PARTICIPANT, DecisionStorePort, HubCardParticipant, HubLocalReceiptVerifier, LocalCardIntentSource,
)
from connection_hub.delegated_credentials.cards.credential_handles import RedisCardCredentialHandleStore
from connection_hub.delegated_credentials.cards.effect_targets import compose_card_effects
from connection_hub.delegated_credentials.cards.persistence import DurableCardPersistence
from connection_hub.delegated_credentials.cards.service import DelegatedCardService
from connection_hub.delegated_credentials.cards.store import BundleStorageDelegatedCardStore, subject_hash_for
from connection_hub.delegated_credentials.catalog.models import CatalogDocument
from connection_hub.delegated_credentials.oauth.authority_store import PostgresOAuthAuthorityStore
from connection_hub.delegated_credentials.oauth.config import oauth_delegated_config_from_connections
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from connection_hub.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import service_adapter
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import original_code
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_code_flow import OriginalCodeExchangeFlow
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import CodeExchangeProof
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import PostgresOriginalExchangeStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import OriginalCredentialPairProvider
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore

RESOURCE = "https://unit.test/api/mcp/records"


@pytest_asyncio.fixture
async def hub_world(tmp_path, monkeypatch, secrets_rig):
    dsn, redis_url = os.environ.get("KDCUBE_TEST_POSTGRES_DSN"), os.environ.get("KDCUBE_TEST_REDIS_URL")
    if not dsn or not redis_url:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN and KDCUBE_TEST_REDIS_URL are required")
    import asyncpg
    import redis.asyncio as redis_asyncio
    from service_foundation.coordination.durable_decision_log import Coordinator, PostgresDecisionStore
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    redis = redis_asyncio.from_url(redis_url)
    suffix = uuid.uuid4().hex
    r = SimpleNamespace(tenant="original-hub-" + suffix, project="original-code", signs=0, creates=0,
                        subject="unit-human", client="https://unit.test/oauth/client", pool=pool, errors=[])
    r.schema = "original_hub_" + suffix
    r.oauth = PostgresOAuthAuthorityStore(pg_pool=pool, tenant=r.tenant, project=r.project)
    r.sessions = PostgresBundleSessionStore(pg_pool=pool, tenant=r.tenant, project=r.project)
    try:
        async with pool.acquire() as connection:
            await connection.execute(f"CREATE SCHEMA {r.schema}")
        decisions = PostgresDecisionStore(pool, schema=r.schema, namespace="original-hub")
        await decisions.ensure_schema()
        await r.oauth.ensure_schema()
        await r.sessions.ensure_schema()
        r.grants = GrantStore(redis, tenant=r.tenant, project=r.project, authority_store=r.oauth)
        @asynccontextmanager
        async def mutation_lock(**kwargs):
            yield
        cache = SimpleNamespace(**{name: AsyncMock(return_value=value) for name, value in {
            "claim_transition": True, "reconcile_projection": False, "commit_projection": True,
            "commit_tombstone": True, "index_add": None, "index_remove": None,
            "finalize_removal": None, "read": None}.items()})
        r.cards = BundleStorageDelegatedCardStore(tmp_path / "hub-cards")
        transaction_store.bind_transaction_decisions(r.cards, DecisionStorePort(decisions))
        cards = DelegatedCardService(store=r.cards, cache=cache, mutation_lock=mutation_lock)
        handles = RedisCardCredentialHandleStore(redis, tenant=r.tenant, project=r.project)
        persistence = DurableCardPersistence(redis=redis, tenant=r.tenant, project=r.project,
            card_store=r.cards, mutation_lock=mutation_lock, credential_handles=handles)
        persistence._cards = cards
        compose_card_effects(card_service=cards, card_store=r.cards, grant_store=r.grants,
                             policies=None, issuance_store=r.oauth, credential_handles=handles)
        intents = LocalCardIntentSource(r.cards)
        participant = HubCardParticipant(service=cards, store=r.cards, intents=intents, decisions=decisions)
        connections = {"delegated_credentials": {"oauth": {"enabled": True,
            "capabilities": [{"grant": "records:read", "label": "Read records",
                              "delegable_roles": ["kdcube:role:registered"]}],
            "resources": [{"resource": RESOURCE + "*", "label": "Records", "grants": ["records:read"],
                           "tools": {"search": {"grants": ["records:read"], "description": "Search records"}}}]}}}
        document = CatalogDocument.build(connections)
        catalog = SimpleNamespace(resolve_active=AsyncMock(return_value=document),
                                  resolve_version=AsyncMock(return_value=document))
        r.hub = AutomationAccessService(redis=redis, tenant=r.tenant, project=r.project,
            config=oauth_delegated_config_from_connections(connections), catalog_resolver=catalog,
            grant_store=r.grants, card_persistence=persistence)
        r.hub.notify_change = AsyncMock()
        r.hub.bind_card_coordinator(Coordinator(decisions, {PARTICIPANT: participant}, HubLocalReceiptVerifier(r.cards)),
            intents=intents, decisions=decisions, intent_ttl_seconds=60)
        r.hub.bind_oauth_issuance_store(r.oauth)
        r.hub.bind_card_credential_handles(handles)
        _, app, _, r.custody_root, _ = secrets_rig
        manager = service_adapter(app, monkeypatch)
        create = manager.create_ephemeral_secret
        async def counted_create(**kwargs):
            made = await create(**kwargs)
            r.creates += bool(made)
            return made
        manager.create_ephemeral_secret = counted_create
        r.custody = issuance_secret_custody(namespace="custody", manager=manager)
        async def key():
            r.signs += 1
            return b"unit-original-refresh-key-32-bytes!"
        refresh = PostgresOriginalRefreshStore(pg_pool=pool, tenant=r.tenant, project=r.project)
        r.ledger = PostgresOriginalExchangeStore(pg_pool=pool, tenant=r.tenant, project=r.project)
        await refresh.ensure_schema()
        await r.ledger.ensure_schema()
        r.provider = OriginalCredentialPairProvider(refresh_store=refresh, custody=r.custody,
            custody_namespace="custody", refresh_signer=HmacOriginalRefreshSigner(r.tenant, r.project, key),
            card_kind="connector", refresh_ttl_seconds=180 * 86400,
            authority_factory=lambda **kwargs: authority(r.sessions))
        async def candidates(*, payload):
            return {"grantor_subject": payload["sub"], "client_id": payload["client_id"],
                    "scopes": payload["scopes"], "resource": payload["resource"]}
        async def fence(*, plan, result):
            current = await r.cards.read_current_authority(subject_hash=subject_hash_for(r.subject), access_id=plan.access_id)
            if result.state == "pending":
                return current is None if plan.base_revision == 0 else current[1].card_revision == plan.base_revision
            return (current is not None and current[1].card_revision == plan.candidate_revision
                    and current[1].content_hash() == plan.card_content_hash and current[1].state == "active")
        r.flow = OriginalCodeExchangeFlow(ledger=r.ledger, grant_store=r.grants, hub=r.hub, provider=r.provider,
            custody=r.custody, candidate_inputs=candidates, fence_target=fence)
        async def exchange(**kwargs):
            try:
                return await r.flow.exchange(**kwargs)
            except Exception as exc:
                # Preserve source locations, never exception text, locals,
                # request data, credential values or provider response bodies.
                r.errors.append((type(exc).__name__, [
                    (frame.filename, frame.lineno, frame.name)
                    for frame in traceback.extract_tb(exc.__traceback__)[-5:]]))
                raise
        def handler():
            return original_code.OriginalCodeExchangeHandler(r.tenant, r.project, exchange)
        verifier = "x" * 48
        code = await r.grants.create_auth_code(client_id=r.client, redirect_uri="https://unit.test/cb",
            code_challenge=make_s256_challenge(verifier), sub=r.subject, scopes=["records:read"], resource=RESOURCE)
        r.proof = CodeExchangeProof.from_request(tenant=r.tenant, project=r.project, code=code,
            client_id=r.client, redirect_uri="https://unit.test/cb", verifier=verifier)
        r.request = SimpleNamespace(state=SimpleNamespace(oauth_original_exchange_factory=handler),
                                    app=SimpleNamespace(state=SimpleNamespace()))
        async def form():
            return FormData({"grant_type": "authorization_code", "code": code, "client_id": r.client,
                             "redirect_uri": "https://unit.test/cb", "code_verifier": verifier})
        r.request.form = form
        monkeypatch.setattr(original_code, "oauth_tenant_project", lambda request: (r.tenant, r.project))
        yield r
    finally:
        async with pool.acquire() as connection:
            for schema in {r.schema, r.oauth.schema, r.sessions.schema}:
                await connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        keys = [key async for key in redis.scan_iter(match=f"{r.tenant}:{r.project}:*")]
        if keys:
            await redis.delete(*keys)
        await pool.close()
        await redis.aclose()


def token_digests(response):
    import json
    body = json.loads(response.body)
    return {slot: hashlib.sha256(body[slot + "_token"].encode()).hexdigest() for slot in ("access", "refresh")}
