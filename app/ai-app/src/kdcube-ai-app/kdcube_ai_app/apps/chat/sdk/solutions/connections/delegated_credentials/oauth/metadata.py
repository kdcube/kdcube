# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Pure builders for delegated-credential OAuth discovery metadata.

KDCube acts as the OAuth2 authorization server (RFC 8414) for Connection Hub
delegated credentials. Concrete protected resources are bundle/proc MCP URLs
and advertise themselves with RFC 9728 metadata/challenges.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

# Discovery document paths (RFC 8414 / RFC 9728).
WELL_KNOWN_AS_PATH = "/.well-known/oauth-authorization-server"
WELL_KNOWN_OIDC_PATH = "/.well-known/openid-configuration"
WELL_KNOWN_PR_PATH = "/.well-known/oauth-protected-resource"


def authorization_server_metadata(
    issuer: str,
    *,
    authorization_endpoint: str | None = None,
    token_endpoint: str | None = None,
    registration_endpoint: str | None = None,
    scopes_supported: Iterable[str] | None = None,
    service_name: str | None = None,
    logo_uri: str | None = None,
    client_uri: str | None = None,
    icons: Iterable[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """RFC 8414 authorization-server metadata.

    ``issuer`` is the public origin (e.g. ``https://connector.example.test``), no trailing slash.
    """
    issuer = issuer.rstrip("/")
    out: Dict[str, Any] = {
        "issuer": issuer,
        "authorization_endpoint": authorization_endpoint or f"{issuer}/oauth/authorize",
        "token_endpoint": token_endpoint or f"{issuer}/oauth/token",
        # RFC 7591 dynamic client registration — Claude.ai self-registers here
        # when the connector is added without an OAuth Client ID.
        "registration_endpoint": registration_endpoint or f"{issuer}/oauth/register",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        # Public client, no secret -> 'none'.
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": list(scopes_supported or []),
        # jwks_uri intentionally omitted: tokens are opaque (kst1).
    }
    if service_name:
        # Non-standard but harmless for OAuth clients. Some connector UIs use
        # this metadata to present the sign-in service.
        out["service_name"] = service_name
    if logo_uri:
        out["logo_uri"] = logo_uri
    if client_uri:
        out["client_uri"] = client_uri
    icon_rows = [dict(item) for item in (icons or []) if item]
    if icon_rows:
        out["icons"] = icon_rows
    return out


def protected_resource_metadata(
    issuer: str,
    *,
    resource: str | None = None,
    resource_name: str | None = None,
    scopes_supported: Iterable[str] | None = None,
    capabilities: Iterable[Mapping[str, Any]] | None = None,
    tools: Iterable[Mapping[str, Any]] | None = None,
    named_services: Mapping[str, Any] | None = None,
    logo_uri: str | None = None,
    website_url: str | None = None,
    icons: Iterable[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """RFC 9728 protected-resource metadata for a concrete bundle MCP resource."""
    issuer = issuer.rstrip("/")
    resource = (resource or issuer).rstrip("/")
    out = {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": list(scopes_supported or []),
    }
    if resource_name:
        out["resource_name"] = resource_name
    if logo_uri:
        out["logo_uri"] = logo_uri
    if website_url:
        out["client_uri"] = website_url
    icon_rows = [dict(item) for item in (icons or []) if item]
    if icon_rows:
        out["icons"] = icon_rows
    caps = [dict(item) for item in (capabilities or [])]
    if caps:
        # KDCube extension: lets clients/connector UIs discover the concrete
        # grants and tools/actions offered by this resource before authorization.
        out["kdcube_capabilities"] = caps
    tool_rows = [dict(item) for item in (tools or [])]
    if tool_rows:
        # KDCube extension: canonical tool-centric policy for this protected
        # resource. Each tool declares the delegated grants required to call it.
        out["kdcube_tools"] = tool_rows
    if isinstance(named_services, Mapping) and named_services:
        # KDCube extension: namespace/tool boundaries for generic named-service
        # MCP resources. This keeps namespace grants separate from generic MCP
        # entry grants while still making the consent catalog discoverable.
        out["kdcube_named_services"] = dict(named_services)
    return out


def protected_resource_metadata_url(issuer: str, *, resource: str | None = None) -> str:
    """Metadata URL used in WWW-Authenticate challenges."""
    issuer = issuer.rstrip("/")
    url = f"{issuer}{WELL_KNOWN_PR_PATH}"
    if not resource:
        return url
    from urllib.parse import quote

    return f"{url}?resource={quote(resource, safe='')}"
