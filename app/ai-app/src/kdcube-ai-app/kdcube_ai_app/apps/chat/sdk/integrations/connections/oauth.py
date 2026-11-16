"""Generic OAuth helpers — authorize-url build + code exchange, state-signed.

Parameterized by a resolved `ConnectionProvider` (the OAuth mechanics) plus a
`ClientApp` (the credentials). One public callback alias
(`connection_oauth_callback`) serves every provider; the provider + app_id are
read from the signed `state`.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from typing import Any, Dict, List, Mapping

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.connections.apps import ClientApp
from kdcube_ai_app.apps.chat.sdk.integrations.connections.registry import ConnectionProvider, _entrypoint_bundle_id
from kdcube_ai_app.apps.chat.sdk.integrations.connections.store import ConnectionStore

# Hub-level config prefix for the shared OAuth callback (one redirect for all
# providers/apps). NOT per-provider.
OAUTH_CONFIG_PREFIX = "connections.oauth"


class ProviderHttpError(RuntimeError):
    def __init__(
        self,
        *,
        status: int,
        reason: str,
        body: str,
        parsed: Mapping[str, Any] | None = None,
        url: str = "",
    ):
        self.status = int(status or 0)
        self.reason = str(reason or "").strip()
        self.body = str(body or "")
        self.parsed = dict(parsed or {})
        self.url = str(url or "")
        super().__init__(self.message)

    @property
    def message(self) -> str:
        msg = str(self.parsed.get("message") or self.parsed.get("error_description") or "").strip()
        if msg:
            return msg
        if self.reason:
            return f"HTTP {self.status}: {self.reason}"
        return f"HTTP {self.status}"


def _parse_json_object(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _request_public_base_url(request: Any) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", {}) or {}
    proto = str(headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    host = str(headers.get("x-forwarded-host") or headers.get("host") or "").split(",", 1)[0].strip()
    if proto and host:
        return f"{proto}://{host}".rstrip("/")
    try:
        url = request.url
        return f"{url.scheme}://{url.netloc}".rstrip("/")
    except Exception:
        return ""


def callback_url(
    entrypoint: Any,
    *,
    request: Any = None,
    alias: str = "connection_oauth_callback",
    override: str = "",
) -> str:
    """Build the public OAuth callback URL for the shared connections route.

    The redirect is hub-level (one for all providers/apps). Honors an explicit
    `override` (e.g. a client app's `redirect_uri`), else the hub-level
    `connections.oauth.public_base_url`, else the request host, then derives
    `<base>/api/integrations/bundles/<tenant>/<project>/<bundle>/public/<alias>`.
    """
    configured = str(override or "").strip()
    if configured:
        return configured
    base = str(entrypoint.bundle_prop(f"{OAUTH_CONFIG_PREFIX}.public_base_url", "") or "").strip().rstrip("/")
    if not base:
        base = _request_public_base_url(request)
    if not base:
        raise ValueError("Connection OAuth public base URL is unavailable")
    comm_context = getattr(entrypoint, "comm_context", None)
    actor = getattr(comm_context, "actor", None)
    tenant = str(getattr(actor, "tenant_id", "") or getattr(getattr(entrypoint, "settings", None), "TENANT", "") or "").strip()
    project = str(getattr(actor, "project_id", "") or getattr(getattr(entrypoint, "settings", None), "PROJECT", "") or "").strip()
    if not tenant or not project:
        raise ValueError("tenant/project are unavailable for connection OAuth callback URL")
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return f"{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}"


def _dedup(items) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _scopes_for(
    provider_obj: ConnectionProvider,
    client_app: ClientApp,
    requested=None,
) -> List[str]:
    """Effective OAuth scopes for one connect.

    The client app's scopes (falling back to the provider's defaults) are the
    admin-configured CEILING for that app. A per-connect `requested` set lets a
    scenario ask for a SUBSET — it is clamped to the ceiling, so a consumer can
    ask for *less* consent per scenario but never for more than the admin allowed
    (needing more requires widening the client app). An empty intersection falls
    back to the full ceiling so a connect never accidentally requests zero scopes."""
    ceiling = _dedup(list(client_app.scopes or ()) or list(provider_obj.scopes or []))
    if not requested:
        return ceiling
    allowed = set(ceiling)
    clamped = [s for s in _dedup(list(requested)) if s in allowed]
    return clamped or ceiling


async def build_authorize_url(
    provider_obj: ConnectionProvider,
    client_app: ClientApp,
    *,
    entrypoint: Any,
    store: ConnectionStore,
    request: Any = None,
    source: str = "settings",
    return_hint: str = "",
    alias: str = "connection_oauth_callback",
    scopes=None,
) -> Dict[str, Any]:
    client_id = str(client_app.client_id or "").strip()
    if not client_id:
        raise ValueError(
            f"client app '{client_app.app_id}' for provider '{provider_obj.provider}' has no client_id"
        )
    # state carries provider + app_id so the callback knows which app's
    # credentials to use for the code exchange.
    from kdcube_ai_app.apps.chat.sdk.integrations.connections.apps import oauth_state_secret as _state_secret
    state = await store.create_oauth_state_async(
        provider=provider_obj.provider,
        app_id=client_app.app_id,
        secret=await _state_secret(entrypoint),
        source=source,
        return_hint=return_hint,
    )
    redirect_uri = callback_url(entrypoint, request=request, alias=alias, override=client_app.redirect_uri)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state["state"],
        # Scopes go under the provider's scope param — standard `scope`, or Slack's
        # `user_scope` so the install grants a USER token (acts as the user).
        provider_obj.authorize_scope_param(): " ".join(_scopes_for(provider_obj, client_app, requested=scopes)),
    }
    # Provider-specific authorize tuning (e.g. Google offline access for a
    # refresh_token). The standard params above win on any key collision.
    for key, value in (provider_obj.authorize_extra_params() or {}).items():
        params.setdefault(str(key), value)
    return {
        "provider": provider_obj.provider,
        "app_id": client_app.app_id,
        "authorize_url": f"{provider_obj.authorize_url}?{urllib.parse.urlencode(params)}",
        "state_id": hashlib.sha256(state["state"].encode("utf-8")).hexdigest(),
        "account_id": state["payload"]["account_id"],
        "redirect_uri": redirect_uri,
    }


async def exchange_code(
    provider_obj: ConnectionProvider,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, Any]:
    if not client_id or not client_secret:
        raise ValueError(f"{provider_obj.provider} OAuth client id/secret are not configured")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                provider_obj.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{provider_obj.provider} token exchange failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=provider_obj.token_url,
        )
    # Normalize per provider (e.g. Slack returns the user token under authed_user).
    token = provider_obj.extract_token(_parse_json_object(raw))
    if "expires_in" in token and "expires_at" not in token:
        try:
            token["expires_at"] = int(time.time()) + int(token["expires_in"])
        except Exception:
            pass
    return token


async def refresh_access_token(
    provider_obj: ConnectionProvider,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, Any]:
    """Exchange a refresh_token for a fresh access token.

    POSTs `grant_type=refresh_token` to the provider's token endpoint. Returns the
    new token dict (with `expires_at` derived from `expires_in` when present).

    NOTE: Google omits a new `refresh_token` on refresh — the CALLER must preserve
    the old one (merge it back in). This helper returns only what the provider sent.
    """
    rt = str(refresh_token or "").strip()
    if not rt:
        raise ValueError(f"{provider_obj.provider} refresh requires a refresh_token")
    if not client_id or not client_secret:
        raise ValueError(f"{provider_obj.provider} OAuth client id/secret are not configured")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                provider_obj.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{provider_obj.provider} token refresh failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=provider_obj.token_url,
        )
    # Slack token rotation returns the rotated token at top level; extract_token's
    # default/fallback handles that, and surfaces logical errors.
    token = provider_obj.extract_token(_parse_json_object(raw))
    if "expires_in" in token and "expires_at" not in token:
        try:
            token["expires_at"] = int(time.time()) + int(token["expires_in"])
        except Exception:
            pass
    return token
