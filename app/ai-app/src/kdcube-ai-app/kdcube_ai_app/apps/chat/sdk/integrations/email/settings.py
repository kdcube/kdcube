from __future__ import annotations

import html
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from kdcube_ai_app.apps.chat.sdk.integrations.email import (
    EmailAccountStore,
    build_google_authorize_url,
    callback_url,
    default_icloud_account_settings,
    exchange_google_code,
    fetch_google_profile,
    google_client_id,
    google_client_secret,
    oauth_state_secret,
)

BUNDLE_ID = ""

_storage_root_or_error: Callable[[Any], Any] | None = None
_target_user_id: Callable[..., str] | None = None
_resolve_identity: Callable[..., Any] | None = None


def configure_email_settings(
    *,
    storage_root_or_error: Callable[[Any], Any],
    target_user_id: Callable[..., str],
    resolve_identity: Callable[..., Any] | None = None,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned policy hooks used by the reusable email settings operations."""
    global BUNDLE_ID, _storage_root_or_error, _target_user_id, _resolve_identity
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_root_or_error = storage_root_or_error
    _target_user_id = target_user_id
    _resolve_identity = resolve_identity


def _storage_root(entrypoint: Any) -> Any:
    if _storage_root_or_error is None:
        raise RuntimeError("email settings integration is not configured: storage_root_or_error is missing")
    return _storage_root_or_error(entrypoint)


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
    if _target_user_id is None:
        raise RuntimeError("email settings integration is not configured: target_user_id is missing")
    return _target_user_id(entrypoint, user_id=user_id, fingerprint=fingerprint)


def _telegram_identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = "") -> Any:
    if _resolve_identity is None:
        raise RuntimeError("email settings integration is not configured: resolve_identity is missing")
    return _resolve_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)


def _configured(value: str) -> bool:
    cleaned = str(value or "").strip()
    return bool(cleaned and not (cleaned.startswith("<") and cleaned.endswith(">")))


def store_for(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> tuple[EmailAccountStore, str]:
    resolved_user = _target_user(entrypoint, user_id=user_id, fingerprint=fingerprint)
    return EmailAccountStore(_storage_root(entrypoint), user_id=resolved_user, bundle_id=BUNDLE_ID), resolved_user


async def status(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    enabled = bool(entrypoint.bundle_prop("integrations.email.enabled", False))
    client_id_configured = _configured(google_client_id(entrypoint))
    client_secret_configured = _configured(await google_client_secret())
    state_secret_configured = _configured(await oauth_state_secret(entrypoint))
    missing = []
    if not enabled:
        missing.append("integrations.email.enabled")
    if not client_id_configured:
        missing.append("integrations.email.google.client_id")
    if not client_secret_configured:
        missing.append("integrations.email.google.client_secret")
    if not state_secret_configured:
        missing.append("integrations.email.oauth_state_secret")
    return {
        "ok": True,
        "user_id": resolved_user,
        "enabled": enabled,
        "google_configured": bool(enabled and client_id_configured and client_secret_configured and state_secret_configured),
        "icloud_supported": bool(enabled),
        "configuration": {
            "email_enabled": enabled,
            "google_client_id_configured": client_id_configured,
            "google_client_secret_configured": client_secret_configured,
            "oauth_state_secret_configured": state_secret_configured,
            "icloud_app_password_supported": True,
        },
        "configuration_missing": missing,
        "accounts": await store.list_accounts_async(),
        "operations": {
            "start_google_oauth": "email_oauth_start",
            "connect_icloud_app_password": "email_connect_app_password",
            "status": "email_accounts_status",
            "disconnect": "email_disconnect_account",
        },
    }


async def start_oauth(
    entrypoint: Any,
    *,
    request: Any = None,
    provider: str = "google",
    return_hint: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    source: str = "kdcube_widget",
) -> Dict[str, Any]:
    if not bool(entrypoint.bundle_prop("integrations.email.enabled", False)):
        return {"ok": False, "error": {"code": "email_integration_disabled", "message": "Email integration is disabled."}}
    normalized = str(provider or "google").strip().lower()
    if normalized != "google":
        return {"ok": False, "error": {"code": "email_provider_not_supported", "message": f"Provider {provider!r} is not supported yet."}}
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    try:
        payload = await build_google_authorize_url(
            entrypoint=entrypoint,
            store=store,
            request=request,
            source=source,
            return_hint=return_hint,
        )
    except Exception as exc:
        return {"ok": False, "error": {"code": "email_oauth_start_failed", "message": str(exc)}}
    return {"ok": True, "user_id": resolved_user, **payload}


async def disconnect(
    entrypoint: Any,
    *,
    account_id: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    deleted = await store.delete_account_async(account_id)
    return {"ok": True, "user_id": resolved_user, "deleted": deleted, "accounts": await store.list_accounts_async()}


async def connect_app_password(
    entrypoint: Any,
    *,
    provider: str = "icloud",
    email: str = "",
    app_password: str = "",
    display_name: str = "",
    username: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    if not bool(entrypoint.bundle_prop("integrations.email.enabled", False)):
        return {"ok": False, "error": {"code": "email_integration_disabled", "message": "Email integration is disabled."}}
    normalized = str(provider or "icloud").strip().lower()
    if normalized != "icloud":
        return {"ok": False, "error": {"code": "email_provider_not_supported", "message": f"Provider {provider!r} is not supported for app-password connection."}}
    email_address = str(email or username or "").strip()
    login_name = str(username or email_address).strip()
    password = str(app_password or "").strip()
    if not email_address or "@" not in email_address:
        return {
            "ok": False,
            "error": {
                "code": "email_address_required",
                "message": "iCloud email address is required.",
                "category": "user_action_required",
                "user_action_required": True,
            },
        }
    if not password:
        return {
            "ok": False,
            "error": {
                "code": "email_app_password_required",
                "message": "An Apple app-specific password is required for iCloud Mail.",
                "category": "user_action_required",
                "user_action_required": True,
            },
        }
    store, resolved_user = store_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    try:
        account = store.upsert_account(
            {
                "provider": "icloud",
                "email": email_address,
                "display_name": str(display_name or email_address).strip(),
                "status": "connected",
                "scope": ["imap.read", "smtp.send"],
                "settings": default_icloud_account_settings(),
            }
        )
        await store.set_tokens_async(
            str(account.get("account_id") or ""),
            {
                "auth_type": "app_password",
                "username": login_name,
                "password": password,
            },
        )
    except Exception as exc:
        return {"ok": False, "error": {"code": "email_connect_failed", "message": str(exc)}}
    return {
        "ok": True,
        "user_id": resolved_user,
        "account": account,
        "accounts": await store.list_accounts_async(),
        "provider": "icloud",
        "note": "iCloud Mail is connected with an Apple app-specific password stored in user-scoped secret storage.",
    }


def _html_done(*, title: str, body: str, link: str = "") -> HTMLResponse:
    safe_title = html.escape(str(title or ""))
    safe_body = html.escape(str(body or ""))
    safe_link = html.escape(str(link or ""), quote=True)
    link_html = f'<p><a href="{safe_link}">Return to Telegram</a></p>' if safe_link else ""
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
        return _html_done(title="Email connection failed", body=f"OAuth provider returned: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state are required")

    secret = await oauth_state_secret(entrypoint)
    try:
        payload = EmailAccountStore(_storage_root(entrypoint), user_id="state-reader").consume_oauth_state(
            state=state,
            secret=secret,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user_id = str(payload.get("user_id") or "").strip()
    account_id = str(payload.get("account_id") or "").strip()
    if not user_id or not account_id:
        raise HTTPException(status_code=400, detail="OAuth state is missing user/account")

    store = EmailAccountStore(_storage_root(entrypoint), user_id=user_id, bundle_id=BUNDLE_ID)
    try:
        token = await exchange_google_code(
            code=code,
            redirect_uri=callback_url(entrypoint, request=request),
            client_id=google_client_id(entrypoint),
            client_secret=await google_client_secret(),
        )
        profile = await fetch_google_profile(access_token=str(token.get("access_token") or ""))
        id_claim_email = str(profile.get("email") or "").strip()
        scope = str(token.get("scope") or "").split()
        account = store.upsert_account(
            {
                "account_id": account_id,
                "provider": "google",
                "email": id_claim_email,
                "display_name": str(profile.get("name") or id_claim_email or "Google account").strip(),
                "status": "connected",
                "scope": scope,
            }
        )
        await store.set_tokens_async(str(account.get("account_id") or account_id), token)
    except Exception as exc:
        return _html_done(title="Email connection failed", body=str(exc))

    return_link = str(entrypoint.bundle_prop("integrations.telegram.webapp_deeplink", "") or "").strip()
    if return_link and str(payload.get("source") or "").startswith("telegram"):
        if bool(entrypoint.bundle_prop("integrations.email.oauth.auto_redirect_to_telegram", False)):
            return RedirectResponse(return_link)
        return _html_done(
            title="Email connected",
            body=f"{account.get('display_name') or account.get('account_id')} is connected. You can return to Telegram.",
            link=return_link,
        )
    return _html_done(
        title="Email connected",
        body=f"{account.get('display_name') or account.get('account_id')} is connected. You can close this browser tab and refresh Settings.",
    )


async def telegram_status(entrypoint: Any, *, request: Any = None, telegram_init_data: str = "") -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = await status(entrypoint, user_id=identity.user_id, fingerprint=identity.fingerprint)
    payload["auth_surface"] = "telegram_webapp"
    return payload


def telegram_start_oauth(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    provider: str = "google",
    return_hint: str = "",
) -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = start_oauth(
        entrypoint,
        request=request,
        provider=provider,
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


def telegram_connect_app_password(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    provider: str = "icloud",
    email: str = "",
    app_password: str = "",
    display_name: str = "",
    username: str = "",
) -> Dict[str, Any]:
    identity = _telegram_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    payload = connect_app_password(
        entrypoint,
        provider=provider,
        email=email,
        app_password=app_password,
        display_name=display_name,
        username=username,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
    )
    payload["auth_surface"] = "telegram_webapp"
    return payload
