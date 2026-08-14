# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Test helpers for the Connection Hub delegated credential OAuth adapter."""
from __future__ import annotations

from fastapi import FastAPI

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import AuthorityRegistry
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.authority import OAuthDelegatedClientAuthorityProvider
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.discovery import router as discovery_router
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.routes import router as oauth_routes_router


def enable_delegated_client(app: FastAPI, *, issuer: str = "https://connector.example.test") -> None:
    app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": issuer,
        "capabilities": [
            {
                "grant": "records:read",
                "label": "Read records",
                "description": "Read example records exposed by the protected resource.",
                "tools": [
                    {
                        "name": "records_export",
                        "label": "Export records",
                        "description": "Read-only example export tool.",
                        "grants": ["records:read"],
                    },
                ],
            },
        ],
        "resources": [
            {
                "resource": "*",
                "grants": ["records:read"],
                "tools": [
                    {
                        "name": "records_export",
                        "label": "Export records",
                        "description": "Read-only example export tool.",
                        "grants": ["records:read"],
                    },
                ],
            },
        ],
    }


class _FixedCatalogResolver:
    """Serves one registered catalog generation.

    Publication, caching, and read-through have their own Redis-backed tests;
    a guard test states the generation it is enforcing against.
    """

    def __init__(self, document, *, unavailable: str = "") -> None:
        self._document = document
        self._unavailable = unavailable

    async def resolve_active(self):
        if self._unavailable:
            from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.resolver import (
                CatalogUnavailable,
            )

            raise CatalogUnavailable(self._unavailable)
        return self._document

    async def resolve_version(self, version: str):
        return self._document if version == self._document.version else None


def bind_delegated_catalog(app, connections, *, unavailable: str = "", cards=None) -> None:
    """Install the serving resolvers over a fixed catalog generation.

    ``cards`` is the durable card store a cache miss reads through; without one
    the guard resolves cards from the projection alone.
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
        CatalogDocument,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.serving import (
        SERVING_RESOLVERS_ATTR,
        DelegatedServingResolvers,
    )

    setattr(
        app.state,
        SERVING_RESOLVERS_ATTR,
        DelegatedServingResolvers(
            catalog=_FixedCatalogResolver(
                CatalogDocument.build(connections or {}), unavailable=unavailable
            ),
            cards=cards,
        ),
    )


def _clear_delegated_keys(*, tenant: str, project: str) -> None:
    """Drop delegated-access keys left by an earlier test in this namespace."""
    import os

    import redis as sync_redis

    url = os.environ.get("REDIS_URL")
    if not url:
        return
    client = sync_redis.Redis.from_url(url)
    try:
        keys = list(client.scan_iter(match=f"{tenant}:{project}:kdcube:delegated-access:*"))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


def bind_delegated_card_persistence(app: FastAPI, *, redis, storage_root) -> None:
    """Give a mounted test app the delegated-access capability.

    Production binds this per request from the Connection Hub bundle; a test
    app that mounts the router binds it once. Without it, token issuance
    withholds the token because the card cannot be committed.
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        AutomationAccessService,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.persistence import (
        DurableCardPersistence,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
        BundleStorageDelegatedCardStore,
    )

    cfg = oauth_delegated_config(app)
    # Test apps share one tenant/project namespace, so a projection from an
    # earlier test would otherwise be read as this card's current revision.
    _clear_delegated_keys(tenant=cfg.tenant, project=cfg.project)
    persistence = DurableCardPersistence(
        redis=redis,
        tenant=cfg.tenant,
        project=cfg.project,
        card_store=BundleStorageDelegatedCardStore(storage_root),
    )

    def _build():
        store = getattr(app.state, "oauth_grant_store", None)
        return AutomationAccessService(
            redis=getattr(store, "redis", None),
            tenant=cfg.tenant,
            project=cfg.project,
            config=cfg,
            grant_store=store,
            card_persistence=persistence,
        )

    app.state.automation_access_factory = _build


def mount_test_oauth_adapter(app: FastAPI) -> FastAPI:
    """Mount OAuth adapter routes for tests only.

    Production exposure is the Connection Hub bundle public ``oauth`` operation,
    not an ingress package alias.
    """
    if not oauth_delegated_config(app).enabled:
        return app
    registry = getattr(app.state, "connection_hub_authority_registry", None)
    if registry is None:
        registry = AuthorityRegistry()
        app.state.connection_hub_authority_registry = registry
    if registry.get("delegated_client") is None:
        registry.register(OAuthDelegatedClientAuthorityProvider())
    app.include_router(discovery_router, tags=["oauth-delegated-credential discovery"])
    app.include_router(oauth_routes_router, tags=["oauth-delegated-credential authorize"])
    return app
