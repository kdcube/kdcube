"""Slack connection provider.

Slack OAuth v2, **user-token** flow: each user authorizes our app (which must have
Public Distribution enabled) to act AS them in THEIR workspace. So scopes go under
`user_scope` (not `scope`), and the stored token is the USER token from
`authed_user.access_token` (not the top-level bot token). `fetch_profile`
identifies the connecting user via `auth.test` with that user token.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from ..registry import ConnectionProvider, connection_provider

SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"


@connection_provider("slack")
class SlackConnection(ConnectionProvider):
    provider = "slack"
    label = "Slack"
    authorize_url = "https://slack.com/oauth/v2/authorize"
    # Slack API methods are dotted: oauth.v2.access (a slash here → HTTP 404).
    token_url = "https://slack.com/api/oauth.v2.access"
    # These are USER-token scopes (the user acts on their own workspace), so they
    # are requested under `user_scope` — see authorize_scope_param below.
    scopes = ["search:read", "channels:history", "groups:history"]

    def authorize_scope_param(self) -> str:
        # Slack: request a USER token (act as the user), not a bot token.
        return "user_scope"

    def extract_token(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the USER token out of Slack's `oauth.v2.access` response.

        Initial exchange returns the user token under `authed_user`; token-rotation
        refresh returns it at the top level. Slack signals errors with `ok: false`
        and HTTP 200, so raise here when not ok.
        """
        data = dict(raw or {})
        if "ok" in data and not data.get("ok"):
            raise RuntimeError(f"Slack OAuth error: {data.get('error') or 'unknown'}")
        authed = data.get("authed_user")
        if isinstance(authed, dict) and str(authed.get("access_token") or "").strip():
            out: Dict[str, Any] = {
                "access_token": authed.get("access_token"),
                "token_type": authed.get("token_type") or "user",
                "scope": authed.get("scope") or "",
            }
            if authed.get("refresh_token"):
                out["refresh_token"] = authed["refresh_token"]
            if authed.get("expires_in"):
                out["expires_in"] = authed["expires_in"]
            team = data.get("team")
            if isinstance(team, dict):
                out["team_id"] = team.get("id")
                out["team_name"] = team.get("name")
            return out
        # Rotation refresh (or any top-level token response): use as-is.
        return data

    async def fetch_profile(self, *, access_token: str) -> Dict[str, Any]:
        """Identify the connecting Slack user via auth.test.

        Returns {external_user_id, workspace, display_name}. `external_user_id` is
        the Slack user id (the connected USER); `workspace` is the Slack team id —
        a separate dimension, so the same user in two workspaces is two accounts.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    SLACK_AUTH_TEST_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Slack auth.test request failed: {exc}") from exc

        try:
            data = response.json()
        except Exception:
            data = {}
        if not isinstance(data, dict) or not data.get("ok"):
            err = ""
            if isinstance(data, dict):
                err = str(data.get("error") or "")
            raise RuntimeError(f"Slack auth.test failed: {err or 'unknown error'}")

        team_id = str(data.get("team_id") or "").strip()
        user_id = str(data.get("user_id") or "").strip()
        team = str(data.get("team") or "").strip()
        user = str(data.get("user") or "").strip()
        if team and user:
            display_name = f"{user} @ {team}"
        else:
            display_name = user or team or user_id or "Slack account"
        return {
            "external_user_id": user_id,   # the connected Slack user
            "workspace": team_id,          # the Slack team/workspace (separate)
            "workspace_label": team,
            "display_name": display_name,
        }
