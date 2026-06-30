# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Discovery routes for the Connection Hub delegated-credential OAuth adapter.

Serves the RFC 8414 authorization-server and RFC 9728 protected-resource
documents. Concrete bundle MCP resources may point clients here from their own
``WWW-Authenticate`` challenges.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.metadata import (
    WELL_KNOWN_AS_PATH,
    WELL_KNOWN_OIDC_PATH,
    WELL_KNOWN_PR_PATH,
    authorization_server_metadata,
    protected_resource_metadata,
    protected_resource_metadata_url,
)

router = APIRouter()


def resolve_issuer(request: Request) -> str:
    """Public origin of this AS.

    Prefers the request-local issuer set by the Connection Hub bundle mount.
    The compatibility router can still use app-level config in tests. If no
    issuer is configured, local/dev runs derive it from the request origin.
    """
    request_state = getattr(request, "state", None)
    request_issuer = getattr(request_state, "oauth_delegated_issuer", None) if request_state is not None else None
    if request_issuer:
        return str(request_issuer).rstrip("/")
    configured = oauth_delegated_config(request).issuer
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get(WELL_KNOWN_AS_PATH, include_in_schema=False)
@router.get(WELL_KNOWN_OIDC_PATH, include_in_schema=False)
async def well_known_authorization_server(request: Request) -> JSONResponse:
    cfg = oauth_delegated_config(request)
    return JSONResponse(
        authorization_server_metadata(
            resolve_issuer(request),
            scopes_supported=cfg.supported_scopes(),
        )
    )


@router.get(WELL_KNOWN_PR_PATH, include_in_schema=False)
async def well_known_protected_resource(request: Request) -> JSONResponse:
    resource = request.query_params.get("resource")
    cfg = oauth_delegated_config(request)
    scopes = cfg.supported_scopes(resource)
    capabilities = []
    caps = cfg.capability_map()
    tool_catalog = cfg.resource_tool_catalog(resource)
    for scope in scopes:
        cap = caps.get(scope)
        if cap is None:
            capabilities.append(
                {
                    "grant": scope,
                    "label": scope,
                    "tools": [
                        {
                            "name": tool.name,
                            "label": tool.label,
                            "description": tool.description,
                            "grants": list(tool.grants),
                        }
                        for tool in tool_catalog
                        if scope in tool.grants
                    ],
                }
            )
            continue
        capabilities.append(
            {
                "grant": cap.grant,
                "label": cap.label,
                "description": cap.description,
                "tools": [
                    {
                        "name": tool.name,
                        "label": tool.label,
                        "description": tool.description,
                        "grants": list(tool.grants),
                    }
                    for tool in tool_catalog
                    if cap.grant in tool.grants
                ],
            }
        )
    return JSONResponse(
        protected_resource_metadata(
            resolve_issuer(request),
            resource=resource,
            scopes_supported=scopes,
            capabilities=capabilities,
            tools=[
                {
                    "name": tool.name,
                    "label": tool.label,
                    "description": tool.description,
                    "grants": list(tool.grants),
                }
                for tool in tool_catalog
            ],
        )
    )


def unauthorized_challenge(issuer: str, *, resource: str | None = None) -> JSONResponse:
    """RFC 9728 §5.1 challenge advertising where to find the AS."""
    pr_url = protected_resource_metadata_url(issuer, resource=resource)
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "error_description": "authorization required"},
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{pr_url}"'},
    )
