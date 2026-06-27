"""Generic Settings → Connections operations — provider-parameterized.

Mirrors `integrations/linkedin/settings.py` but dispatches by a resolved
`ConnectionProvider`. `callback` reads the provider FROM the signed state, so a
single public alias (`connection_oauth_callback`) serves every provider.

Bundle-owned policy hooks (storage root, target user, identity resolution) are
bound once via `configure_connections`, exactly like `configure_linkedin_settings`.
"""

from __future__ import annotations

import html
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from kdcube_ai_app.apps.chat.sdk.integrations.bundle_registry import (
    configured_bundle_id,
    register_config,
    resolve_config,
)
from .registry import ConnectionProvider, catalog as _provider_catalog, resolve as _resolve_provider
from .apps import (
    AmbiguousClientApp,
    ClientApp,
    client_app_secret,
    list_client_apps,
    oauth_state_secret,
    resolve_client_app,
)
from .oauth import build_authorize_url, callback_url, exchange_code
from .store import ConnectionStore
from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import integration_definition_value

BUNDLE_ID = ""

_storage_root_or_error: Callable[[Any], Any] | None = None
_target_user_id: Callable[..., str] | None = None
_resolve_identity: Callable[..., Any] | None = None
_CONFIGS: Dict[str, Dict[str, Any]] = {}


def configure_connections(
    *,
    storage_root_or_error: Callable[[Any], Any],
    target_user_id: Callable[..., str],
    resolve_identity: Callable[..., Any] | None = None,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned policy hooks used by the reusable connections settings ops."""
    global BUNDLE_ID, _storage_root_or_error, _target_user_id, _resolve_identity
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_root_or_error = storage_root_or_error
    _target_user_id = target_user_id
    _resolve_identity = resolve_identity
    register_config(
        _CONFIGS,
        bundle_id=BUNDLE_ID,
        config={
            "storage_root_or_error": storage_root_or_error,
            "target_user_id": target_user_id,
            "resolve_identity": resolve_identity,
        },
    )


def _config(entrypoint: Any = None) -> Dict[str, Any]:
    cfg = resolve_config(_CONFIGS, entrypoint=entrypoint, label="Connections settings integration")
    if not cfg.get("storage_root_or_error") or not cfg.get("target_user_id"):
        raise RuntimeError("Connections settings integration is not configured")
    return cfg


def _bundle_id(entrypoint: Any = None) -> str:
    return configured_bundle_id(_config(entrypoint)) or BUNDLE_ID


def _storage_root(entrypoint: Any) -> Any:
    return _config(entrypoint)["storage_root_or_error"](entrypoint)


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
    return _config(entrypoint)["target_user_id"](entrypoint, user_id=user_id, fingerprint=fingerprint)


async def _telegram_identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = "") -> Any:
    resolver = _config(entrypoint).get("resolve_identity")
    if resolver is None:
        raise RuntimeError("Connections settings integration is not configured: resolve_identity is missing")
    identity = resolver(entrypoint, request=request, telegram_init_data=telegram_init_data)
    if hasattr(identity, "__await__"):
        identity = await identity
    return identity


def _configured(value: str) -> bool:
    cleaned = str(value or "").strip()
    return bool(cleaned and not (cleaned.startswith("<") and cleaned.endswith(">")))


def store_for(
    entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None
) -> tuple[ConnectionStore, str]:
    resolved_user = _target_user(entrypoint, user_id=user_id, fingerprint=fingerprint)
    return ConnectionStore(_storage_root(entrypoint), user_id=resolved_user, bundle_id=_bundle_id(entrypoint)), resolved_user


# ── client-app catalog for a provider ───────────────────────────────────────

def _apps_for(entrypoint: Any, provider: ConnectionProvider) -> List[ClientApp]:
    return list_client_apps(entrypoint, provider.provider)


def _app_summaries(apps: List[ClientApp]) -> List[Dict[str, Any]]:
    """UI-facing client-app rows (NO secret, NO client_id) — includes the per-app
    scope ceiling so the connect UI can offer a per-connect scope choice."""
    return [
        {
            "app_id": a.app_id, "provider": a.provider, "label": a.label,
            "enabled": a.enabled, "scopes": list(a.scopes),
        }
        for a in apps
    ]


async def status(
    entrypoint: Any,
    *,
    provider: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    apps = _apps_for(entrypoint, prov)
    enabled_apps = [a for a in apps if a.enabled]
    state_secret_ok = _configured(await oauth_state_secret(entrypoint))
    configured = bool(enabled_apps and state_secret_ok)
    return {
        "ok": True,
        "user_id": resolved_user,
        "provider": prov.provider,
        "label": prov.label,
        "enabled": bool(enabled_apps),
        "configured": configured,
        "apps": _app_summaries(apps),
        "configuration": {
            "apps_configured": len(apps),
            "apps_enabled": len(enabled_apps),
            "oauth_state_secret_configured": state_secret_ok,
        },
        "accounts": await store.list_accounts_async(provider=prov.provider),
    }


async def start_oauth(
    entrypoint: Any,
    *,
    provider: str,
    app_id: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    request: Any = None,
    return_hint: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    source: str = "kdcube_widget",
) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    try:
        client_app = resolve_client_app(entrypoint, prov.provider, app_id)
    except AmbiguousClientApp as exc:
        return {
            "ok": False,
            "error": {
                "code": f"{prov.provider}_app_required",
                "message": str(exc),
                "details": {"provider": exc.provider, "app_ids": exc.app_ids},
            },
        }
    except Exception as exc:
        return {"ok": False, "error": {"code": f"{prov.provider}_integration_disabled", "message": str(exc)}}
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    try:
        payload = await build_authorize_url(
            prov,
            client_app,
            entrypoint=entrypoint,
            store=store,
            request=request,
            source=source,
            return_hint=return_hint,
            scopes=scopes,
        )
    except Exception as exc:
        return {"ok": False, "error": {"code": f"{prov.provider}_oauth_start_failed", "message": str(exc)}}
    return {"ok": True, "user_id": resolved_user, **payload}


async def disconnect(
    entrypoint: Any,
    *,
    provider: str,
    account_id: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    deleted = await store.delete_account_async(account_id)
    return {
        "ok": True,
        "user_id": resolved_user,
        "provider": prov.provider,
        "deleted": deleted,
        "accounts": await store.list_accounts_async(provider=prov.provider),
    }


async def catalog(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """Registry-driven Settings → Connections catalog: one row per provider,
    each listing its configured client apps and the user's connected accounts."""
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    state_secret_ok = _configured(await oauth_state_secret(entrypoint))
    rows: List[Dict[str, Any]] = []
    for prov in _provider_catalog():
        apps = _apps_for(entrypoint, prov)
        enabled_apps = [a for a in apps if a.enabled]
        accounts = await store.list_accounts_async(provider=prov.provider)
        rows.append(
            {
                "provider": prov.provider,
                "label": prov.label,
                "enabled": bool(enabled_apps),
                "configured": bool(enabled_apps and state_secret_ok),
                "connected": any(a.get("has_token") for a in accounts),
                "apps": _app_summaries(apps),
                "accounts": accounts,
            }
        )
    return {"ok": True, "user_id": resolved_user, "providers": rows}


# ── callback (provider read from signed state) ──────────────────────────────

def _html_done(*, title: str, body: str, link: str = "") -> HTMLResponse:
    safe_title = html.escape(str(title or ""))
    safe_body = html.escape(str(body or ""))
    safe_link = html.escape(str(link or ""), quote=True)
    link_html = f'<p><a href="{safe_link}">Return to app</a></p>' if safe_link else ""
    content = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{safe_title}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:32px;line-height:1.45;max-width:680px}"
        ".ok{color:#047857}.err{color:#b91c1c}</style></head><body>"
        f"<h1>{safe_title}</h1><p>{safe_body}</p>{link_html}</body></html>"
    )
    return HTMLResponse(content=content)


async def callback(entrypoint: Any, *, request: Any = None, code: str = "", state: str = "", error: str = ""):
    if error:
        return _html_done(title="Connection failed", body=f"OAuth provider returned: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state are required")

    # Peek the provider from the (unverified) state to resolve its secret, then
    # verify the signature with that provider's state secret on consume.
    from .store import _unb64url_json  # local: avoid widening module surface

    try:
        peek = _unb64url_json(str(state).rsplit(".", 1)[0])
    except Exception:
        raise HTTPException(status_code=400, detail="OAuth state is invalid")
    provider_name = str(peek.get("provider") or "").strip()
    if not provider_name:
        raise HTTPException(status_code=400, detail="OAuth state is missing provider")
    app_id = str(peek.get("app_id") or "").strip()
    try:
        prov = _resolve_provider(provider_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    secret = await oauth_state_secret(entrypoint)
    try:
        payload = await ConnectionStore(_storage_root(entrypoint), user_id="state-reader").consume_oauth_state_async(
            state=state,
            secret=secret,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user_id = str(payload.get("user_id") or "").strip()
    account_id = str(payload.get("account_id") or "").strip()
    app_id = str(payload.get("app_id") or app_id).strip()
    if not user_id or not account_id:
        raise HTTPException(status_code=400, detail="OAuth state is missing user/account")

    try:
        client_app = resolve_client_app(entrypoint, prov.provider, app_id or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    bundle_id = _bundle_id(entrypoint)
    store = ConnectionStore(_storage_root(entrypoint), user_id=user_id, bundle_id=bundle_id)
    try:
        token = await exchange_code(
            prov,
            code=code,
            redirect_uri=callback_url(entrypoint, request=request, override=client_app.redirect_uri),
            client_id=client_app.client_id,
            client_secret=await client_app_secret(bundle_id, prov.provider, client_app.app_id),
        )
        profile = await prov.fetch_profile(access_token=str(token.get("access_token") or ""))
        external_user_id = str(profile.get("external_user_id") or profile.get("external_id") or "").strip()
        workspace = str(profile.get("workspace") or "").strip()
        email = str(profile.get("email") or "").strip()
        display_name = str(
            profile.get("display_name") or email or external_user_id or f"{prov.label} account"
        ).strip()
        raw_scope = profile.get("scope")
        if isinstance(raw_scope, str):
            scope_str = [s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()]
        elif isinstance(raw_scope, list):
            scope_str = [str(s).strip() for s in raw_scope if str(s).strip()]
        else:
            token_scope = str(token.get("scope") or "")
            scope_str = [s.strip() for s in token_scope.replace(",", " ").split() if s.strip()]
        account = await store.upsert_account_async(
            {
                "account_id": account_id,
                "provider": prov.provider,
                "app_id": client_app.app_id,
                "external_user_id": external_user_id,
                "workspace": workspace,
                "email": email,
                "display_name": display_name,
                "status": "connected",
                "scope": scope_str,
            }
        )
        await store.set_tokens_async(str(account.get("account_id") or account_id), token)
    except Exception as exc:
        return _html_done(title="Connection failed", body=str(exc))

    return_link = str(
        integration_definition_value(entrypoint, provider="telegram", key="webapp_deeplink", default="")
        or ""
    ).strip()
    if return_link and str(payload.get("source") or "").startswith("telegram"):
        if bool(entrypoint.bundle_prop("connections.oauth.auto_redirect_to_telegram", False)):
            return RedirectResponse(return_link)
        return _html_done(
            title=f"{prov.label} connected",
            body=f"{account.get('display_name') or account.get('account_id')} is connected. You can return to Telegram.",
            link=return_link,
        )
    return _html_done(
        title=f"{prov.label} connected",
        body=f"{account.get('display_name') or account.get('account_id')} is connected. You can close this browser tab and refresh Settings.",
    )


# ── Telegram-Mini-App variants ──────────────────────────────────────────────

async def telegram_status(
    entrypoint: Any,
    *,
    provider: str,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = await status(entrypoint, provider=provider, user_id=identity.user_id, fingerprint=identity.fingerprint)
    payload["auth_surface"] = "telegram_webapp"
    return payload


async def telegram_start_oauth(
    entrypoint: Any,
    *,
    provider: str,
    app_id: Optional[str] = None,
    request: Any = None,
    telegram_init_data: str = "",
    return_hint: str = "",
) -> Dict[str, Any]:
    identity = await _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = await start_oauth(
        entrypoint,
        provider=provider,
        app_id=app_id,
        request=request,
        return_hint=return_hint,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        source="telegram_webapp",
    )
    payload["auth_surface"] = "telegram_webapp"
    return payload


async def telegram_disconnect(
    entrypoint: Any,
    *,
    provider: str,
    account_id: str,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = await disconnect(
        entrypoint, provider=provider, account_id=account_id, user_id=identity.user_id, fingerprint=identity.fingerprint
    )
    payload["auth_surface"] = "telegram_webapp"
    return payload
