# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn member adapter registration for delegated to KDCube."""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

import httpx

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    DelegatedToKdcubeAdapter,
    adapter,
)

LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


def _decode_id_token_claims(id_token: str) -> dict[str, Any]:
    parts = str(id_token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        parsed = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _identity_from_claims(claims: Mapping[str, Any]) -> dict[str, Any]:
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip()
    given = str(claims.get("given_name") or "").strip()
    family = str(claims.get("family_name") or "").strip()
    name = str(claims.get("name") or "").strip() or " ".join(part for part in (given, family) if part)
    return {
        "external_subject": subject,
        "email": email,
        "display_name": name or email or subject,
    }


@adapter("linkedin.oauth_member")
class LinkedInMemberAdapter(DelegatedToKdcubeAdapter):
    """LinkedIn member OAuth.

    ``external_subject`` is the OIDC ``sub``; authorship is
    ``urn:li:person:{external_subject}``.

    LinkedIn issues refresh tokens only to approved applications. Without one
    the base class reports the credential as non-refreshable.
    """

    label = "LinkedIn"
    kind = "oauth2"
    authorize_url = "https://www.linkedin.com/oauth/v2/authorization"
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    oauth_default_scopes = ("openid", "profile", "email")

    # Identity scopes every LinkedIn connection needs, whatever was ticked.
    REQUIRED_IDENTITY_SCOPES = ("openid", "profile")

    def provider_scopes_for_claims(self, claims: list, claim_map: dict) -> list:
        # `sub` is delivered only at connect time, via id_token or userinfo.
        # Identity scopes are added to every request regardless of claims.
        scopes = super().provider_scopes_for_claims(claims, claim_map)
        missing = [scope for scope in self.REQUIRED_IDENTITY_SCOPES if scope not in scopes]
        return [*missing, *scopes]

    async def fetch_profile(self, *, access_token: str, token: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    LINKEDIN_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LinkedIn userinfo request failed: {exc}") from exc
        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, Mapping) or response.status_code >= 400:
            detail = ""
            if isinstance(data, Mapping):
                detail = str(data.get("error_description") or data.get("message") or data.get("error") or "")
            # A w_member_social-only token cannot read userinfo.
            fallback = _identity_from_claims(_decode_id_token_claims(str((token or {}).get("id_token") or "")))
            if fallback.get("external_subject"):
                return fallback
            raise RuntimeError(f"LinkedIn userinfo failed: {detail or 'unknown error'}")
        return _identity_from_claims(data)

    async def normalize_profile(self, credential: dict) -> dict:
        # The token response carries no subject; it is in the id_token when
        # openid was granted.
        data = dict(credential or {})
        claims = {**_decode_id_token_claims(str(data.get("id_token") or "")), **data}
        return _identity_from_claims(claims)


__all__ = ["LinkedInMemberAdapter"]
