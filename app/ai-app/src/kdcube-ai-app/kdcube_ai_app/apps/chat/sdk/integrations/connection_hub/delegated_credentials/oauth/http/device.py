# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""HTTP-facing helpers for OAuth device authorization.

The browser handoff carries only the public user code. Consent drafts retain
only digests that bind a decision to the short-lived device request.
"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from connection_hub.delegated_credentials.oauth.device import normalize_user_code
from connection_hub.delegated_credentials.oauth.device_store import DeviceGrantStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.deps import (
    get_grant_store,
    oauth_tenant_project,
)


DEVICE_CONSENT_SCHEMA = "connection_hub.oauth.device_consent.v1"
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


def get_device_grant_store(request: Request) -> DeviceGrantStore:
    for state in (
        getattr(request, "state", None),
        getattr(request.app, "state", None),
    ):
        store = getattr(state, "oauth_device_grant_store", None)
        if store is not None:
            return store
    tenant, project = oauth_tenant_project(request)
    return DeviceGrantStore(get_grant_store(request).redis, tenant, project)


def device_consent_binding(context: Mapping[str, Any]) -> dict[str, Any] | None:
    value = context.get("device_authorization")
    if not isinstance(value, Mapping):
        return None
    if str(value.get("schema") or "") != DEVICE_CONSENT_SCHEMA:
        return None
    device_digest = str(value.get("device_digest") or "").strip()
    user_digest = str(value.get("user_digest") or "").strip()
    if len(device_digest) != 64 or len(user_digest) != 64:
        return None
    try:
        normalized_user_code = normalize_user_code(value.get("user_code") or "")
    except ValueError:
        return None
    expected_revision = value.get("expected_card_revision")
    if expected_revision is not None:
        try:
            expected_revision = int(expected_revision)
        except (TypeError, ValueError):
            return None
        if expected_revision < 1:
            return None
    return {
        "device_digest": device_digest,
        "user_digest": user_digest,
        "user_code": f"{normalized_user_code[:4]}-{normalized_user_code[4:]}",
        "requested_access_id": str(value.get("requested_access_id") or "").strip(),
        "expected_card_revision": expected_revision,
    }


def verification_uri(issuer: str) -> str:
    base = str(issuer or "").rstrip("/")
    suffix = "/device" if base.endswith("/oauth") else "/oauth/device"
    return f"{base}{suffix}"


def verification_uri_complete(issuer: str, user_code: str) -> str:
    return f"{verification_uri(issuer)}?{urlencode({'user_code': user_code})}"


def device_oauth_error(
    error: str,
    description: str,
    *,
    status: int = 400,
    retry_after: int | None = None,
) -> JSONResponse:
    headers = dict(_NO_STORE_HEADERS)
    if retry_after is not None:
        headers["Retry-After"] = str(max(0, int(retry_after)))
    return JSONResponse(
        status_code=status,
        content={"error": str(error), "error_description": str(description)},
        headers=headers,
    )


def device_verification_page(
    issuer: str,
    *,
    user_code: str = "",
    error: str = "",
    status: int = 200,
) -> HTMLResponse:
    action = escape(verification_uri(issuer), quote=True)
    code = escape(str(user_code or ""), quote=True)
    problem = (
        f'<p role="alert" class="error">{escape(error)}</p>' if error else ""
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Connect a device</title>
  <style>
    :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: Canvas; color: CanvasText; }}
    main {{ width: min(420px, calc(100vw - 32px)); }}
    h1 {{ font-size: 24px; letter-spacing: 0; }}
    form {{ display: grid; gap: 12px; }}
    label {{ display: grid; gap: 6px; font-weight: 650; }}
    input {{ min-height: 44px; padding: 0 12px; font: inherit; text-transform: uppercase; letter-spacing: 0; }}
    button {{ min-height: 44px; padding: 0 16px; border: 0; border-radius: 6px; background: #1769aa; color: white; font: inherit; font-weight: 700; }}
    .error {{ padding: 10px 12px; border-left: 3px solid #b42318; background: color-mix(in srgb, #b42318 10%, Canvas); }}
  </style>
</head>
<body>
  <main>
    <h1>Connect a device</h1>
    {problem}
    <form method="get" action="{action}">
      <label>Code<input name="user_code" value="{code}" autocomplete="one-time-code" inputmode="text" required maxlength="12" autofocus></label>
      <button type="submit">Continue</button>
    </form>
  </main>
</body>
</html>"""
    return HTMLResponse(html, status_code=status, headers=_NO_STORE_HEADERS)


def device_completion_page(*, approved: bool) -> HTMLResponse:
    title = "Device connected" if approved else "Device authorization denied"
    body = (
        "Return to the terminal to continue."
        if approved
        else "No access was granted. You can close this window."
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer"><title>{title}</title></head>
<body><main><h1>{title}</h1><p>{body}</p></main></body></html>"""
    return HTMLResponse(html, headers=_NO_STORE_HEADERS)


__all__ = [
    "DEVICE_CONSENT_SCHEMA",
    "device_completion_page",
    "device_consent_binding",
    "device_oauth_error",
    "device_verification_page",
    "get_device_grant_store",
    "verification_uri",
    "verification_uri_complete",
]
