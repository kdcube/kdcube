# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn delegated-to-KDCube adapter contract."""

from __future__ import annotations

import base64
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import providers  # noqa: F401
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    resolve_adapter,
)

CLAIM_MAP = {
    "linkedin:profile": {"provider_scopes": ["openid", "profile", "email"]},
    "linkedin:post": {"provider_scopes": ["w_member_social"]},
}


def _id_token(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


@pytest.fixture()
def adapter():
    return resolve_adapter("linkedin.oauth_member")


def test_adapter_is_registered_with_oauth_endpoints(adapter):
    assert adapter.oauth_enabled
    assert adapter.authorize_url == "https://www.linkedin.com/oauth/v2/authorization"
    assert adapter.token_url == "https://www.linkedin.com/oauth/v2/accessToken"


def test_identity_scopes_are_requested_even_for_a_write_only_claim(adapter):
    scopes = adapter.provider_scopes_for_claims(["linkedin:post"], CLAIM_MAP)
    assert "openid" in scopes and "profile" in scopes
    assert "w_member_social" in scopes


def test_identity_scopes_are_not_duplicated(adapter):
    scopes = adapter.provider_scopes_for_claims(
        ["linkedin:profile", "linkedin:post"], CLAIM_MAP
    )
    assert scopes.count("openid") == 1
    assert scopes.count("profile") == 1


@pytest.mark.asyncio
async def test_subject_is_read_from_the_id_token(adapter):
    profile = await adapter.normalize_profile(
        {
            "access_token": "T",
            "id_token": _id_token(
                {
                    "sub": "dE5aOhH-ap",
                    "email": "jane@example.com",
                    "given_name": "Jane",
                    "family_name": "Smith",
                }
            ),
        }
    )
    assert profile["external_subject"] == "dE5aOhH-ap"
    assert profile["email"] == "jane@example.com"
    assert profile["display_name"] == "Jane Smith"


@pytest.mark.asyncio
async def test_missing_id_token_yields_an_empty_subject(adapter):
    profile = await adapter.normalize_profile({"access_token": "T"})
    assert profile["external_subject"] == ""


def test_credential_without_refresh_token_is_not_refreshable(adapter):
    assert adapter.credential_refreshable({"access_token": "T"}) is False
    assert adapter.credential_refreshable({"access_token": "T", "refresh_token": "R"}) is True
