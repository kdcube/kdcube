# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Pure builders for delegated-credential OAuth discovery metadata.

KDCube acts as the OAuth2 authorization server (RFC 8414) for Connection Hub
delegated credentials. Concrete protected resources are bundle/proc MCP URLs
and advertise themselves with RFC 9728 metadata/challenges.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

# The single scope this AS issues. Maps to kdcube:role:feedback-reader (read-only).
CONVERSATIONS_READ_SCOPE = "conversations:read"

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
) -> Dict[str, Any]:
    """RFC 8414 authorization-server metadata.

    ``issuer`` is the public origin (e.g. ``https://yey.boats``), no trailing slash.
    """
    issuer = issuer.rstrip("/")
    return {
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
        "scopes_supported": list(scopes_supported or [CONVERSATIONS_READ_SCOPE]),
        # jwks_uri intentionally omitted: tokens are opaque (kst1).
    }


def protected_resource_metadata(
    issuer: str,
    *,
    resource: str | None = None,
    scopes_supported: Iterable[str] | None = None,
    capabilities: Iterable[Mapping[str, Any]] | None = None,
    tools: Iterable[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """RFC 9728 protected-resource metadata for a concrete bundle MCP resource."""
    issuer = issuer.rstrip("/")
    resource = (resource or issuer).rstrip("/")
    out = {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": list(scopes_supported or [CONVERSATIONS_READ_SCOPE]),
    }
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
    return out


def protected_resource_metadata_url(issuer: str, *, resource: str | None = None) -> str:
    """Metadata URL used in WWW-Authenticate challenges."""
    issuer = issuer.rstrip("/")
    url = f"{issuer}{WELL_KNOWN_PR_PATH}"
    if not resource:
        return url
    from urllib.parse import quote

    return f"{url}?resource={quote(resource, safe='')}"
