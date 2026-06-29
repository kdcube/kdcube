# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Delegated-client session minting for Connection Hub external credentials."""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)

FEEDBACK_READER_ROLE = "kdcube:role:feedback-reader"

# Read-only permission for conversation export across tenants/projects.
CONVERSATIONS_READ_PERMISSION = "kdcube:*:conversations:*;read"

# Short-lived access token; the refresh token (long-lived, rotating) keeps a
# daily-or-seldom routine working without re-consent.
ACCESS_TOKEN_TTL_SECONDS = 3600

# Which tools each role may invoke over MCP. feedback-reader is read-only.
ROLE_TOOLS = {
    FEEDBACK_READER_ROLE: {"conversations_export"},
}


def oauth_tenant_project(source: Any | None = None) -> tuple[str, str]:
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    cfg = oauth_delegated_config(source)
    return cfg.tenant, cfg.project


def integration_subject(admin_sub: str) -> str:
    """Dedicated identity for the Claude Code integration tied to the consenting admin."""
    return f"integration:claude:{admin_sub}"


def can_call_tool(roles: Iterable[str], tool: str) -> bool:
    roles = set(roles or [])
    # Platform admins retain access to everything.
    if authority_has_platform_privilege(roles):
        return True
    return any(tool in ROLE_TOOLS.get(r, set()) for r in roles)


def _delegated_roles_for_scopes(scopes: Iterable[str]) -> list[str]:
    scopes_set = {str(item).strip() for item in (scopes or []) if str(item).strip()}
    roles = ["kdcube:role:delegated-client"]
    if "conversations:read" in scopes_set:
        roles.append(FEEDBACK_READER_ROLE)
    return roles


def _delegated_permissions_for_scopes(scopes: Iterable[str]) -> list[str]:
    scopes_set = {str(item).strip() for item in (scopes or []) if str(item).strip()}
    permissions = sorted(scopes_set)
    if "conversations:read" in scopes_set and CONVERSATIONS_READ_PERMISSION not in permissions:
        permissions.append(CONVERSATIONS_READ_PERMISSION)
    return permissions


async def mint_feedback_reader_access_token(
    sub: str,
    scopes: List[str],
    *,
    authority=None,
    client_id: str = "",
    tools: List[str] | None = None,
    credential: Mapping[str, Any] | None = None,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> dict:
    """Mint a least-privilege integration session for the consenting user's connection.

    ``sub`` is the consenting user's subject; the token is issued to the derived
    integration identity, never to ``sub`` itself.
    """
    if authority is None:
        from kdcube_ai_app.auth.bundle import get_bundle_session_authority

        tenant, project = oauth_tenant_project()
        authority = get_bundle_session_authority(tenant=tenant, project=project)

    isub = integration_subject(sub)
    grant = await authority.login_or_register(
        sub=isub,
        username="claude-feedback-reader",
        name="Claude Code delegated connection",
        roles=_delegated_roles_for_scopes(scopes),
        permissions=_delegated_permissions_for_scopes(scopes),
        provider="integration",
        provider_subject=sub,
        metadata={
            "credential": dict(credential or {}),
            "delegated_client": {
                "client_id": str(client_id or "").strip(),
                "scopes": list(scopes or []),
                "tools": list(tools or []),
            },
        },
        ttl_seconds=ttl_seconds,
    )
    return {"access_token": grant.token, "expires_in": ttl_seconds}
