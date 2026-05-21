from __future__ import annotations

import html
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin.accounts import (
    LinkedInAccountStore,
    build_linkedin_authorize_url,
    callback_url,
    exchange_linkedin_code,
    fetch_linkedin_profile,
    linkedin_client_id,
    linkedin_client_secret_async,
    linkedin_scopes,
    oauth_state_secret,
)

BUNDLE_ID = ""

_storage_root_or_error: Callable[[Any], Any] | None = None
_target_user_id: Callable[..., str] | None = None
_resolve_identity: Callable[..., Any] | None = None


def configure_linkedin_settings(
    *,
    storage_root_or_error: Callable[[Any], Any],
    target_user_id: Callable[..., str],
    resolve_identity: Callable[..., Any] | None = None,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned policy hooks used by the reusable LinkedIn settings operations."""
    global BUNDLE_ID, _storage_root_or_error, _target_user_id, _resolve_identity
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_root_or_error = storage_root_or_error
    _target_user_id = target_user_id
    _resolve_identity = resolve_identity


def _storage_root(entrypoint: Any) -> Any:
    if _storage_root_or_error is None:
        raise RuntimeError("LinkedIn settings integration is not configured: storage_root_or_error is missing")
    return _storage_root_or_error(entrypoint)


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
    if _target_user_id is None:
        raise RuntimeError("LinkedIn settings integration is not configured: target_user_id is missing")
    return _target_user_id(entrypoint, user_id=user_id, fingerprint=fingerprint)


def _telegram_identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = "") -> Any:
    if _resolve_identity is None:
        raise RuntimeError("LinkedIn settings integration is not configured: resolve_identity is missing")
    return _resolve_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)


def _configured(value: str) -> bool:
    cleaned = str(value or "").strip()
    return bool(cleaned and not (cleaned.startswith("<") and cleaned.endswith(">")))


def store_for(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> tuple[LinkedInAccountStore, str]:
    resolved_user = _target_user(entrypoint, user_id=user_id, fingerprint=fingerprint)
    return LinkedInAccountStore(_storage_root(entrypoint), user_id=resolved_user, bundle_id=BUNDLE_ID), resolved_user


def status(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    enabled = bool(entrypoint.bundle_prop("integrations.linkedin.enabled", False))
    client_id_configured = _configured(linkedin_client_id(entrypoint))
    state_secret_configured = _configured(oauth_state_secret(entrypoint))
    missing = []
    if not enabled:
        missing.append("integrations.linkedin.enabled")
    if not client_id_configured:
        missing.append("integrations.linkedin.client_id")
    if not state_secret_configured:
        missing.append("integrations.linkedin.oauth_state_secret")
    return {
        "ok": True,
        "user_id": resolved_user,
        "enabled": enabled,
        "linkedin_configured": bool(enabled and client_id_configured and state_secret_configured),
        "configuration": {
            "linkedin_enabled": enabled,
            "linkedin_client_id_configured": client_id_configured,
            "oauth_state_secret_configured": state_secret_configured,
        },
        "configuration_missing": missing,
        "accounts": store.list_accounts(),
        "operations": {
            "start_oauth": "linkedin_oauth_start",
            "status": "linkedin_accounts_status",
            "disconnect": "linkedin_disconnect_account",
        },
    }


def start_oauth(
    entrypoint: Any,
    *,
    request: Any = None,
    return_hint: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    source: str = "kdcube_widget",
) -> Dict[str, Any]:
    if not bool(entrypoint.bundle_prop("integrations.linkedin.enabled", False)):
        return {"ok": False, "error": {"code": "linkedin_integration_disabled", "message": "LinkedIn integration is disabled."}}
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    try:
        payload = build_linkedin_authorize_url(
            entrypoint=entrypoint,
            store=store,
            request=request,
            source=source,
            return_hint=return_hint,
        )
    except Exception as exc:
        return {"ok": False, "error": {"code": "linkedin_oauth_start_failed", "message": str(exc)}}
    return {"ok": True, "user_id": resolved_user, **payload}


def disconnect(
    entrypoint: Any,
    *,
    account_id: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    deleted = store.delete_account(account_id)
    return {"ok": True, "user_id": resolved_user, "deleted": deleted, "accounts": store.list_accounts()}


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
        return _html_done(title="LinkedIn connection failed", body=f"OAuth provider returned: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state are required")

    secret = oauth_state_secret(entrypoint)
    try:
        payload = LinkedInAccountStore(_storage_root(entrypoint), user_id="state-reader").consume_oauth_state(
            state=state,
            secret=secret,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user_id = str(payload.get("user_id") or "").strip()
    account_id = str(payload.get("account_id") or "").strip()
    if not user_id or not account_id:
        raise HTTPException(status_code=400, detail="OAuth state is missing user/account")

    store = LinkedInAccountStore(_storage_root(entrypoint), user_id=user_id, bundle_id=BUNDLE_ID)
    try:
        bundle_id = str(BUNDLE_ID or "").strip()
        token = await exchange_linkedin_code(
            code=code,
            redirect_uri=callback_url(entrypoint, request=request),
            client_id=linkedin_client_id(entrypoint),
            client_secret=await linkedin_client_secret_async(bundle_id),
        )
        profile = await fetch_linkedin_profile(access_token=str(token.get("access_token") or ""))
        person_id = str(profile.get("sub") or "").strip()
        email = str(profile.get("email") or "").strip()
        display_name = str(profile.get("name") or profile.get("given_name") or email or person_id or "LinkedIn account").strip()
        raw_scope = str(token.get("scope") or "")
        scope_str = [s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()]
        account = store.upsert_account(
            {
                "account_id": account_id,
                "provider": "linkedin",
                "person_id": person_id,
                "email": email,
                "display_name": display_name,
                "status": "connected",
                "scope": scope_str,
            }
        )
        await store.set_tokens_async(str(account.get("account_id") or account_id), token)
    except Exception as exc:
        return _html_done(title="LinkedIn connection failed", body=str(exc))

    return_link = str(entrypoint.bundle_prop("integrations.telegram.webapp_deeplink", "") or "").strip()
    if return_link and str(payload.get("source") or "").startswith("telegram"):
        if bool(entrypoint.bundle_prop("integrations.linkedin.oauth.auto_redirect_to_telegram", False)):
            return RedirectResponse(return_link)
        return _html_done(
            title="LinkedIn connected",
            body=f"{account.get('display_name') or account.get('account_id')} is connected. You can return to Telegram.",
            link=return_link,
        )
    return _html_done(
        title="LinkedIn connected",
        body=f"{account.get('display_name') or account.get('account_id')} is connected. You can close this browser tab and refresh Settings.",
    )


def telegram_status(entrypoint: Any, *, request: Any = None, telegram_init_data: str = "") -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = status(entrypoint, user_id=identity.user_id, fingerprint=identity.fingerprint)
    payload["auth_surface"] = "telegram_webapp"
    return payload


def telegram_start_oauth(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    return_hint: str = "",
) -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = start_oauth(
        entrypoint,
        request=request,
        return_hint=return_hint,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        source="telegram_webapp",
    )
    payload["auth_surface"] = "telegram_webapp"
    return payload


def telegram_disconnect(
    entrypoint: Any,
    *,
    account_id: str,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = disconnect(entrypoint, account_id=account_id, user_id=identity.user_id, fingerprint=identity.fingerprint)
    payload["auth_surface"] = "telegram_webapp"
    return payload
