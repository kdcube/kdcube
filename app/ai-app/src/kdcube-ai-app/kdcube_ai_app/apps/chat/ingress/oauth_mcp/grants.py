# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
conversations:read -> feedback-reader grant.

The issued access token is a real ``kst1`` bundle session, but minted for a
dedicated *integration* identity (``integration:claude:<admin-sub>``) carrying
only the read-only ``kdcube:role:feedback-reader`` role. This keeps the
consenting admin's own account untouched while giving Claude Code a least-
privilege, refreshable, revocable token. Write/delete tools remain admin-only.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.deps import ADMIN_ROLES, oauth_tenant_project

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


def integration_subject(admin_sub: str) -> str:
    """Dedicated identity for the Claude Code integration tied to the consenting admin."""
    return f"integration:claude:{admin_sub}"


def can_call_tool(roles: Iterable[str], tool: str) -> bool:
    roles = set(roles or [])
    # Platform admins retain access to everything.
    if roles & ADMIN_ROLES:
        return True
    return any(tool in ROLE_TOOLS.get(r, set()) for r in roles)


async def mint_feedback_reader_access_token(
    sub: str,
    scopes: List[str],
    *,
    authority=None,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> dict:
    """Mint a read-only integration session for the consenting admin's connection.

    ``sub`` is the consenting admin's subject; the token is issued to the derived
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
        name="Claude Code (feedback-reader)",
        roles=[FEEDBACK_READER_ROLE],
        permissions=[CONVERSATIONS_READ_PERMISSION],
        provider="integration",
        provider_subject=sub,
        ttl_seconds=ttl_seconds,
    )
    return {"access_token": grant.token, "expires_in": ttl_seconds}
