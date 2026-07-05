# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for delegated-client access-token minting."""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    DELEGATED_CLIENT_ROLE,
    integration_subject,
    mint_delegated_client_access_token,
)

ADMIN_SUB = "google:admin@example.test"


def test_delegated_client_role_value():
    assert DELEGATED_CLIENT_ROLE == "kdcube:role:delegated-client"


def test_integration_subject_is_distinct_and_deterministic():
    isub = integration_subject(ADMIN_SUB, client_id="example-client")
    assert isub != ADMIN_SUB
    assert ADMIN_SUB in isub
    assert "example-client" in isub
    assert integration_subject(ADMIN_SUB, client_id="example-client") == isub


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
    out = await mint_delegated_client_access_token(
        ADMIN_SUB, ["records:read"], authority=authority, client_id="example-client", ttl_seconds=3600
    )
    assert out["expires_in"] == 3600
    assert out["access_token"].startswith("kst1.mock.integration:example-client:")

    call = authority.calls[0]
    assert call["sub"] == integration_subject(ADMIN_SUB, client_id="example-client")
    assert call["sub"] != ADMIN_SUB
    assert call["roles"] == [DELEGATED_CLIENT_ROLE]
    assert call["permissions"] == ["records:read"]


@pytest.mark.asyncio
async def test_minter_passes_credential_metadata_to_session_authority():
    authority = _FakeAuthority()
    credential = {
        "schema": "kdcube.credential.v1",
        "credential_id": "cred_test",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "issuer_authenticator_id": "delegated_client.bearer",
        "subject": integration_subject(ADMIN_SUB, client_id="claude"),
        "audience": "kdcube:delegated_client",
    }
    await mint_delegated_client_access_token(
        ADMIN_SUB,
        ["records:read"],
        authority=authority,
        client_id="claude",
        operations=["records_export"],
        credential=credential,
        ttl_seconds=3600,
    )

    metadata = authority.calls[0]["metadata"]
    assert metadata["credential"] == credential
    assert metadata["delegated_client"]["client_id"] == "claude"
    assert metadata["delegated_client"]["operations"] == ["records_export"]
