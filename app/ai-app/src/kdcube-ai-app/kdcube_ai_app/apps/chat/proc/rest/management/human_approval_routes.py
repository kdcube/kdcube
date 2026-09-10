# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Browser callback routes for stronger human-approval adapters."""

from __future__ import annotations

import html
import json
import secrets
from urllib.parse import parse_qsl, quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from kdcube_ai_app.apps.chat.proc.rest.management.http_input import (
    ManagementRequestBodyError,
    read_bounded_body,
    read_json_object,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    HumanApprovalChallenge,
    HumanApprovalError,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval_oidc import (
    complete_oidc_callback,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval_webauthn import (
    authentication_options,
    complete_authentication,
    complete_registration,
    registration_options,
    start_enrollment,
)
from kdcube_ai_app.auth.bundle.browser_session import sign_in_bounce_path

router = APIRouter(prefix="/human-approval")

_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}


def _error(code: str, *, status_code: int) -> HTMLResponse:
    safe = html.escape(str(code or "human_approval_unavailable"))
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Approval unavailable</title></head><body><main>"
        "<h1>Approval unavailable</h1>"
        f"<p><code>{safe}</code></p>"
        "<p>Return to the original action and start again.</p>"
        "</main></body></html>",
        status_code=status_code,
        headers=_HEADERS,
    )


def _json_error(code: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": str(code or "human_approval_unavailable")}},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _single_query(request: Request, name: str) -> str:
    pairs = list(request.query_params.multi_items())
    if len(pairs) != 1 or pairs[0][0] != name:
        raise HumanApprovalError(
            "human_approval_request_invalid",
            status_code=400,
        )
    value = str(pairs[0][1] or "").strip()
    if not value or len(value) > 2048:
        raise HumanApprovalError(
            "human_approval_request_invalid",
            status_code=400,
        )
    return value


def _sign_in_redirect(request: Request) -> RedirectResponse:
    return_to = request.url.path
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    return RedirectResponse(
        f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}",
        status_code=302,
        headers=_HEADERS,
    )


def _passkey_page(
    *,
    title: str,
    operation: str,
    payload: dict[str, object],
) -> HTMLResponse:
    nonce = secrets.token_urlsafe(24)
    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).replace("<", "\\u003c")
    mode = "create" if operation == "register" else "get"
    response_fields = (
        "attestationObject: enc(credential.response.attestationObject),"
        "transports: credential.response.getTransports ? credential.response.getTransports() : []"
        if operation == "register"
        else "authenticatorData: enc(credential.response.authenticatorData),"
        "signature: enc(credential.response.signature),"
        "userHandle: credential.response.userHandle ? enc(credential.response.userHandle) : null"
    )
    endpoint = (
        "/api/integrations/management/v1/human-approval/passkeys/register/complete"
        if operation == "register"
        else "/api/integrations/management/v1/human-approval/webauthn/complete"
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ font-family: ui-sans-serif,system-ui,sans-serif; color-scheme:light dark; }}
body {{ margin:0; background:#f4f7f8; color:#152334; }}
main {{ width:min(560px,calc(100% - 32px)); margin:64px auto; }}
.panel {{ border:1px solid #cbd8dc; background:#fff; padding:24px; }}
button {{ padding:10px 14px; border:1px solid #075985; background:#075985; color:#fff; cursor:pointer; }}
code {{ overflow-wrap:anywhere; }} .error {{ color:#b91c1c; }}
@media(prefers-color-scheme:dark) {{ body {{ background:#111827;color:#e5edf2; }} .panel {{ background:#17212b;border-color:#41515b; }} }}
</style></head><body><main><section class="panel">
<h1>{html.escape(title)}</h1>
<p>This approval is bound to the exact pending KDCube operation.</p>
<button id="approve" type="button">{html.escape(title)}</button>
<p id="status" aria-live="polite"></p>
</section></main>
<script nonce="{nonce}">
const envelope={payload_json};
const dec=(v)=>{{const s=v.replace(/-/g,'+').replace(/_/g,'/');const b=atob(s+'='.repeat((4-s.length%4)%4));return Uint8Array.from(b,c=>c.charCodeAt(0));}};
const enc=(v)=>{{const b=new Uint8Array(v);let s='';for(const c of b)s+=String.fromCharCode(c);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');}};
function decodeOptions(o){{o.challenge=dec(o.challenge);if(o.user)o.user.id=dec(o.user.id);for(const k of ['allowCredentials','excludeCredentials'])if(o[k])for(const c of o[k])c.id=dec(c.id);return o;}}
document.getElementById('approve').addEventListener('click',async()=>{{
 const status=document.getElementById('status');status.textContent='Waiting for your authenticator...';
 try {{
  const credential=await navigator.credentials.{mode}({{publicKey:decodeOptions(envelope.options)}});
  const body={{state:envelope.state,credential:{{id:credential.id,rawId:enc(credential.rawId),type:credential.type,authenticatorAttachment:credential.authenticatorAttachment,response:{{clientDataJSON:enc(credential.response.clientDataJSON),{response_fields}}},clientExtensionResults:credential.getClientExtensionResults()}}}};
  const response=await fetch('{endpoint}',{{method:'POST',headers:{{'Content-Type':'application/json'}},credentials:'same-origin',body:JSON.stringify(body)}});
  const result=await response.json();
  if(!response.ok||!result.ok)throw new Error((result.error&&result.error.code)||'human_approval_failed');
  location.assign(result.return_url);
 }} catch(error) {{ status.className='error';status.textContent=error.name==='NotAllowedError'?'Approval was cancelled.':String(error.message||'Approval failed.'); }}
}});
</script></body></html>"""
    headers = {
        **_HEADERS,
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; connect-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        ),
    }
    return HTMLResponse(document, headers=headers)


def _query_fields(request: Request) -> dict[str, str]:
    pairs = list(request.query_params.multi_items())
    allowed = {"state", "code", "iss", "error", "error_description"}
    if len(pairs) > 8 or any(key not in allowed for key, _value in pairs):
        raise HumanApprovalError(
            "human_approval_oidc_response_invalid",
            status_code=400,
        )
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            raise HumanApprovalError(
                "human_approval_oidc_response_invalid",
                status_code=400,
            )
        result[key] = str(value)
    return result


async def _form_fields(request: Request) -> dict[str, str]:
    try:
        raw = await read_bounded_body(
            request,
            maximum_bytes=72 * 1024,
            media_type="application/x-www-form-urlencoded",
        )
        pairs = parse_qsl(
            raw.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (ManagementRequestBodyError, UnicodeDecodeError, ValueError):
        raise HumanApprovalError(
            "human_approval_oidc_response_invalid",
            status_code=400,
        ) from None
    allowed = {
        "state",
        "id_token",
        "iss",
        "error",
        "error_description",
    }
    if any(key not in allowed for key, _value in pairs):
        raise HumanApprovalError(
            "human_approval_oidc_response_invalid",
            status_code=400,
        )
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            raise HumanApprovalError(
                "human_approval_oidc_response_invalid",
                status_code=400,
            )
        result[key] = value
    return result


async def _complete(request: Request, fields: dict[str, str]) -> Response:
    if fields.get("error"):
        raise HumanApprovalError(
            "human_approval_identity_provider_denied",
            status_code=403,
        )
    return_url = await complete_oidc_callback(
        request,
        state=fields.get("state", ""),
        code=fields.get("code", ""),
        id_token=fields.get("id_token", ""),
        response_issuer=fields.get("iss", ""),
    )
    return RedirectResponse(return_url, status_code=303, headers=_HEADERS)


@router.get("/oidc/callback", include_in_schema=False)
async def oidc_callback_get(request: Request) -> Response:
    try:
        return await _complete(request, _query_fields(request))
    except HumanApprovalError as exc:
        return _error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _error("human_approval_unavailable", status_code=503)


@router.post("/oidc/callback", include_in_schema=False)
async def oidc_callback_post(request: Request) -> Response:
    try:
        return await _complete(request, await _form_fields(request))
    except HumanApprovalError as exc:
        return _error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _error("human_approval_unavailable", status_code=503)


@router.get("/webauthn", include_in_schema=False)
async def webauthn_approval_page(request: Request) -> Response:
    try:
        state = _single_query(request, "state")
        payload = await authentication_options(request, state=state)
        return _passkey_page(
            title="Verify with passkey",
            operation="authenticate",
            payload=payload,
        )
    except HumanApprovalError as exc:
        if exc.status_code == 401:
            return _sign_in_redirect(request)
        return _error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _error("human_approval_unavailable", status_code=503)


@router.post("/webauthn/complete", include_in_schema=False)
async def webauthn_approval_complete(request: Request) -> JSONResponse:
    try:
        payload = await read_json_object(request, maximum_bytes=128 * 1024)
        if set(payload) != {"state", "credential"} or not isinstance(
            payload.get("credential"), dict
        ):
            raise HumanApprovalError(
                "human_approval_passkey_response_invalid",
                status_code=400,
            )
        return_url = await complete_authentication(
            request,
            state=str(payload.get("state") or ""),
            credential_payload=payload["credential"],
        )
        return JSONResponse(
            {"ok": True, "return_url": return_url},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except ManagementRequestBodyError:
        return _json_error(
            "human_approval_passkey_response_invalid",
            status_code=400,
        )
    except HumanApprovalError as exc:
        return _json_error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _json_error("human_approval_unavailable", status_code=503)


@router.get("/passkeys/register", include_in_schema=False)
async def passkey_registration_page(request: Request) -> Response:
    try:
        pairs = list(request.query_params.multi_items())
        if len(pairs) != 1 or pairs[0][0] not in {"return_to", "enrollment"}:
            raise HumanApprovalError(
                "human_approval_request_invalid",
                status_code=400,
            )
        name, value = pairs[0]
        if name == "return_to":
            challenge = await start_enrollment(
                request,
                final_return_url=str(value),
            )
            return RedirectResponse(
                challenge.authorization_url,
                status_code=302,
                headers=_HEADERS,
            )
        result = await registration_options(
            request,
            enrollment_id=str(value),
        )
        if isinstance(result, HumanApprovalChallenge):
            return RedirectResponse(
                result.authorization_url,
                status_code=302,
                headers=_HEADERS,
            )
        return _passkey_page(
            title="Register passkey",
            operation="register",
            payload=result,
        )
    except HumanApprovalError as exc:
        if exc.status_code == 401:
            return _sign_in_redirect(request)
        return _error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _error("human_approval_unavailable", status_code=503)


@router.post("/passkeys/register/complete", include_in_schema=False)
async def passkey_registration_complete(request: Request) -> JSONResponse:
    try:
        payload = await read_json_object(request, maximum_bytes=256 * 1024)
        if set(payload) != {"state", "credential"} or not isinstance(
            payload.get("credential"), dict
        ):
            raise HumanApprovalError(
                "human_approval_passkey_response_invalid",
                status_code=400,
            )
        return_url = await complete_registration(
            request,
            state=str(payload.get("state") or ""),
            credential_payload=payload["credential"],
        )
        return JSONResponse(
            {"ok": True, "return_url": return_url},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except ManagementRequestBodyError:
        return _json_error(
            "human_approval_passkey_response_invalid",
            status_code=400,
        )
    except HumanApprovalError as exc:
        return _json_error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _json_error("human_approval_unavailable", status_code=503)


__all__ = ["router"]
