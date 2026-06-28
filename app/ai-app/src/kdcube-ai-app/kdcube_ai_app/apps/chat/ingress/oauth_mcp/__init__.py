# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
KDCube OAuth2 Authorization Server + MCP resource server.

KDCube exposes its conversation-export capability as an MCP server at ``/mcp``
and protects it with a standards OAuth2 flow it hosts itself (authorization_code
+ PKCE S256). Claude Code connects natively, a human consents once, and the
nightly feedback-triage routine then calls the ``conversations_export`` tool
over the authenticated MCP channel.

This package is mounted onto the chat-ingress FastAPI app via
:func:`mount_oauth_mcp`. It is additive: it introduces no dependency on the
existing control-plane routers and leaves write/admin surfaces super-admin-only.
"""
from __future__ import annotations

from fastapi import FastAPI

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.authority import OAuthMcpAuthorityProvider
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.config import oauth_mcp_config
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.metadata import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.discovery import router as discovery_router
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.routes import router as oauth_routes_router
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.mcp_server import router as mcp_router
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import AuthorityRegistry

__all__ = [
    "mount_oauth_mcp",
    "authorization_server_metadata",
    "protected_resource_metadata",
]


def mount_oauth_mcp(app: FastAPI) -> FastAPI:
    """Mount the OAuth2 AS discovery routes and the MCP resource onto ``app``.

    This is an opt-in ingress feature controlled by
    ``auth.oauth_mcp.enabled`` in ``assembly.yaml``.
    """
    if not oauth_mcp_config(app).enabled:
        return app
    registry = getattr(app.state, "connection_hub_authority_registry", None)
    if registry is None:
        registry = AuthorityRegistry()
        app.state.connection_hub_authority_registry = registry
    if registry.get("oauth_mcp") is None:
        registry.register(OAuthMcpAuthorityProvider())
    app.include_router(discovery_router, tags=["oauth-mcp discovery"])
    app.include_router(oauth_routes_router, tags=["oauth-mcp authorize"])
    app.include_router(mcp_router, tags=["oauth-mcp server"])
    return app
