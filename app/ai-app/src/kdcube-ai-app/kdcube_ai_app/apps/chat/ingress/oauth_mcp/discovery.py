# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Discovery + unauthenticated-handshake routes for the OAuth2 AS / MCP resource.

Serves the RFC 8414 authorization-server and RFC 9728 protected-resource
documents, and answers an unauthenticated MCP request with a ``401`` carrying a
``WWW-Authenticate`` challenge that points at the protected-resource metadata.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.config import oauth_mcp_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.metadata import (
    WELL_KNOWN_AS_PATH,
    WELL_KNOWN_PR_PATH,
    authorization_server_metadata,
    protected_resource_metadata,
)

router = APIRouter()


def resolve_issuer(request: Request) -> str:
    """Public origin of this AS.

    Prefers ``auth.oauth_mcp.issuer`` from ``assembly.yaml``
    (the deployment knows its CloudFront-fronted public host); falls back to the
    request's own base URL so local/dev runs work without configuration.
    """
    configured = oauth_mcp_config(request).issuer
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get(WELL_KNOWN_AS_PATH, include_in_schema=False)
async def well_known_authorization_server(request: Request) -> JSONResponse:
    return JSONResponse(authorization_server_metadata(resolve_issuer(request)))


@router.get(WELL_KNOWN_PR_PATH, include_in_schema=False)
async def well_known_protected_resource(request: Request) -> JSONResponse:
    return JSONResponse(protected_resource_metadata(resolve_issuer(request)))


def unauthorized_challenge(issuer: str) -> JSONResponse:
    """RFC 9728 §5.1 challenge advertising where to find the AS."""
    pr_url = f"{issuer}{WELL_KNOWN_PR_PATH}"
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "error_description": "authorization required"},
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{pr_url}"'},
    )
