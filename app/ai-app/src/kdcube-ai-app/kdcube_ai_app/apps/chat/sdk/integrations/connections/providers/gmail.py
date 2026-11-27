"""Gmail (Google OAuth) connection provider (Layer 1 — connect only).

Standard authorization-code flow over Google's OAuth 2.0 endpoints. Google access
tokens are short-lived (~1h), so the provider asks for offline access (a
refresh_token) via `authorize_extra_params`; the connection hub refreshes the
access token on demand using that refresh_token (see provider_impl.get_token).

`fetch_profile` identifies the connecting user via the OpenID Connect userinfo
endpoint (the `sub` claim is the stable Google account id).
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.connections.registry import ConnectionProvider, connection_provider

GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@connection_provider("google")
class GmailConnection(ConnectionProvider):
    # `provider = "google"` MUST match the key the hub's email side used so that
    # `connection.get_token("google")` keeps resolving for already-connected users.
    provider = "google"
    label = "Gmail"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    # Google's consent is granular already; tiers keep OUR ask minimal and give
    # the connect card the same shape as Slack's. Identity scopes ride with read.
    claim_tiers = [
        {
            "id": "read",
            "label": "Read & search mail",
            "description": "Search and read your messages and attachments.",
            "scopes": ["openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"],
        },
        {
            "id": "send",
            "label": "Send as you",
            "description": "Send and forward mail on your behalf.",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        },
    ]

    def authorize_extra_params(self) -> Dict[str, Any]:
        """Force Google to mint a refresh_token.

        `access_type=offline` requests a refresh_token; `prompt=consent` forces the
        consent screen so a refresh_token is returned even on re-connect (Google
        only returns one on the FIRST consent otherwise); `include_granted_scopes`
        keeps previously granted scopes when re-consenting.
        """
        return {
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }

    async def fetch_profile(self, *, access_token: str) -> Dict[str, Any]:
        """Identify the connecting Google user via the OIDC userinfo endpoint.

        Returns {external_user_id, email, display_name}. `external_user_id` is the
        OpenID `sub` claim — the stable id for the connected Google account.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Google userinfo request failed: {exc}") from exc

        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, dict) or response.status_code >= 400:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("error_description") or data.get("error") or "")
            raise RuntimeError(f"Google userinfo failed: {detail or 'unknown error'}")

        sub = str(data.get("sub") or "").strip()
        email = str(data.get("email") or "").strip()
        display_name = str(data.get("name") or email or sub or "Gmail account").strip()
        return {
            "external_user_id": sub,   # the connected Google user (OIDC `sub`)
            "email": email,
            "display_name": display_name,
        }
