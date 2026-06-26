# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the conversations:read -> feedback-reader grant mapping.

Critical safety property: the access token is minted for a SEPARATE integration
identity (never the consenting admin's own subject), so the admin's account roles
are never downgraded by issuing a read-only integration token.
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.grants import (
    FEEDBACK_READER_ROLE,
    can_call_tool,
    integration_subject,
    mint_feedback_reader_access_token,
)

ADMIN_SUB = "google:admin@example.test"


def test_feedback_reader_role_value():
    assert FEEDBACK_READER_ROLE == "kdcube:role:feedback-reader"


def test_integration_subject_is_distinct_and_deterministic():
    isub = integration_subject(ADMIN_SUB)
    assert isub != ADMIN_SUB
    assert ADMIN_SUB in isub
    assert integration_subject(ADMIN_SUB) == isub  # deterministic


def test_feedback_reader_can_call_export():
    assert can_call_tool([FEEDBACK_READER_ROLE], "conversations_export")


def test_chat_user_cannot_call_export():
    assert not can_call_tool(["kdcube:role:chat-user"], "conversations_export")


def test_feedback_reader_cannot_call_unlisted_write_tool():
    assert not can_call_tool([FEEDBACK_READER_ROLE], "conversations_delete")


def test_super_admin_can_call_export():
    # Admins retain access (superset of the read-only grant).
    assert can_call_tool(["kdcube:role:super-admin"], "conversations_export")


class _FakeAuthority:
    def __init__(self):
        self.calls = []

    async def login_or_register(self, *, sub, roles=None, **kw):
        self.calls.append({"sub": sub, "roles": list(roles or []), **kw})

        class _Grant:
            token = f"kst1.mock.{sub}"

        return _Grant()


@pytest.mark.asyncio
async def test_minter_uses_integration_identity_not_admin():
    authority = _FakeAuthority()
    out = await mint_feedback_reader_access_token(
        ADMIN_SUB, ["conversations:read"], authority=authority, ttl_seconds=3600
    )
    assert out["expires_in"] == 3600
    assert out["access_token"].startswith("kst1.mock.integration:claude:")

    call = authority.calls[0]
    # Minted for the integration identity, with ONLY the read-only role.
    assert call["sub"] == integration_subject(ADMIN_SUB)
    assert call["sub"] != ADMIN_SUB
    assert call["roles"] == [FEEDBACK_READER_ROLE]
