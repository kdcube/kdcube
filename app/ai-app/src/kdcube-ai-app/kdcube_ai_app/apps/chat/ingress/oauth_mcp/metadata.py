# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Pure builders for OAuth2 / MCP discovery metadata.

KDCube acts as an OAuth2 authorization server (RFC 8414) protecting an MCP
resource (RFC 9728). Access tokens are the existing opaque ``kst1`` session
bearer, so there is no ``jwks_uri`` (no asymmetric signing). The only scope is
``conversations:read``, which maps a consented session to the read-only
``kdcube:role:feedback-reader`` role.
"""
from __future__ import annotations

from typing import Any, Dict

# The single scope this AS issues. Maps to kdcube:role:feedback-reader (read-only).
CONVERSATIONS_READ_SCOPE = "conversations:read"

# Path of the MCP resource, relative to the issuer.
MCP_RESOURCE_PATH = "/mcp"

# Discovery document paths (RFC 8414 / RFC 9728).
WELL_KNOWN_AS_PATH = "/.well-known/oauth-authorization-server"
WELL_KNOWN_PR_PATH = "/.well-known/oauth-protected-resource"


def authorization_server_metadata(issuer: str) -> Dict[str, Any]:
    """RFC 8414 authorization-server metadata.

    ``issuer`` is the public origin (e.g. ``https://yey.boats``), no trailing slash.
    """
    issuer = issuer.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        # RFC 7591 dynamic client registration — Claude.ai self-registers here
        # when the connector is added without an OAuth Client ID.
        "registration_endpoint": f"{issuer}/oauth/register",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        # Public client, no secret -> 'none'.
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": [CONVERSATIONS_READ_SCOPE],
        # jwks_uri intentionally omitted: tokens are opaque (kst1).
    }


def protected_resource_metadata(issuer: str) -> Dict[str, Any]:
    """RFC 9728 protected-resource metadata for the MCP resource."""
    issuer = issuer.rstrip("/")
    return {
        "resource": f"{issuer}{MCP_RESOURCE_PATH}",
        "authorization_servers": [issuer],
        "scopes_supported": [CONVERSATIONS_READ_SCOPE],
    }
