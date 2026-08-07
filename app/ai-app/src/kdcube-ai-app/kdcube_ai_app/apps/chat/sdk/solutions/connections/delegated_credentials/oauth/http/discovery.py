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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    kdcube_icon_descriptor,
    kdcube_icon_url,
    kdcube_website_url,
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


# Served at both paths: MCP clients fetch the OIDC location and treat a 404
# there as fatal, so the document carries the OIDC-required fields.
@router.get(WELL_KNOWN_AS_PATH, include_in_schema=False)
@router.get(WELL_KNOWN_OIDC_PATH, include_in_schema=False)
async def well_known_authorization_server(request: Request) -> JSONResponse:
    cfg = oauth_delegated_config(request)
    issuer = resolve_issuer(request)
    icon = kdcube_icon_descriptor(request=request, public_base_url=issuer)
    return JSONResponse(
        authorization_server_metadata(
            issuer,
            scopes_supported=cfg.supported_scopes(),
            service_name=cfg.brand or "KDCube",
            logo_uri=kdcube_icon_url(request=request, public_base_url=issuer),
            client_uri=kdcube_website_url(request=request, public_base_url=issuer),
            icons=[icon] if icon else None,
            dynamic_client_registration_supported=cfg.dynamic_client_registration.enabled,
            client_id_metadata_document_supported=cfg.client_id_metadata_documents.enabled,
        )
    )


@router.get("/oauth/jwks", include_in_schema=False)
async def oauth_jwks() -> JSONResponse:
    """Empty key set: kst1 tokens are opaque, so no public key is published."""
    return JSONResponse({"keys": []})


@router.get(WELL_KNOWN_PR_PATH, include_in_schema=False)
async def well_known_protected_resource(request: Request) -> JSONResponse:
    resource = request.query_params.get("resource")
    cfg = oauth_delegated_config(request)
    scopes = cfg.supported_scopes(resource)
    resource_cfg = cfg.resource_config(resource)
    issuer = resolve_issuer(request)
    icon = kdcube_icon_descriptor(request=request, public_base_url=issuer)
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
            issuer,
            resource=resource,
            resource_name=(resource_cfg.label if resource_cfg is not None and resource_cfg.label else cfg.brand or "KDCube"),
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
            named_services=resource_cfg.named_services if resource_cfg is not None else {},
            logo_uri=kdcube_icon_url(request=request, public_base_url=issuer),
            website_url=kdcube_website_url(request=request, public_base_url=issuer),
            icons=[icon] if icon else None,
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
