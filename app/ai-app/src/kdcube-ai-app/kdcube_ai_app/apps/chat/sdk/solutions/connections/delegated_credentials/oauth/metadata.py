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
    revocation_endpoint: str | None = None,
    registration_endpoint: str | None = None,
    jwks_uri: str | None = None,
    scopes_supported: Iterable[str] | None = None,
    service_name: str | None = None,
    logo_uri: str | None = None,
    client_uri: str | None = None,
    icons: Iterable[Mapping[str, Any]] | None = None,
    dynamic_client_registration_supported: bool = True,
    client_id_metadata_document_supported: bool = False,
) -> Dict[str, Any]:
    """RFC 8414 authorization-server metadata.

    ``issuer`` is the public origin (e.g. ``https://connector.example.test``), no trailing slash.
    """
    issuer = issuer.rstrip("/")
    out: Dict[str, Any] = {
        "issuer": issuer,
        "authorization_endpoint": authorization_endpoint or f"{issuer}/oauth/authorize",
        "token_endpoint": token_endpoint or f"{issuer}/oauth/token",
        # RFC 7009 token revocation — a disconnecting client revokes its token
        # here, which also retires its Connection Hub card (no orphan).
        "revocation_endpoint": revocation_endpoint or f"{issuer}/oauth/revoke",
        "revocation_endpoint_auth_methods_supported": ["none"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        # Public client, no secret -> 'none'.
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": list(scopes_supported or []),
        # The three fields below are required by OIDC discovery, which MCP
        # clients apply to this document: it is also served at
        # /.well-known/openid-configuration, and a client that gets a 404 or a
        # document without them stops before registering (Claude Code CLI).
        # jwks: empty and permanent — kst1 tokens are opaque.
        # id_token: none is ever issued; `openid` is absent from
        # scopes_supported, so no client can request one.
        "jwks_uri": jwks_uri or f"{issuer}/oauth/jwks",
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    if dynamic_client_registration_supported:
        # RFC 7591 remains available for clients that do not publish a Client
        # ID Metadata Document.
        out["registration_endpoint"] = registration_endpoint or f"{issuer}/oauth/register"
    if client_id_metadata_document_supported:
        out["client_id_metadata_document_supported"] = True
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
