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
