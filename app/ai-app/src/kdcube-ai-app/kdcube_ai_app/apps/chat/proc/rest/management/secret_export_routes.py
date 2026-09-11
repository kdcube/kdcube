# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""HTTP ceremony for browser-approved, one-use secret descriptor export."""

from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

from connection_hub.connection_edges import request_origin
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from kdcube_ai_app.apps.chat.proc.rest.management.config import (
    HumanSecretExportConfig,
)
from kdcube_ai_app.apps.chat.proc.rest.management.http_input import (
    ManagementRequestBodyError,
    read_bounded_body,
    read_json_object,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    HumanApprovalChallenge,
    HumanApprovalContext,
    HumanApprovalError,
    evaluate_human_approval,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export import (
    SECRET_EXPORT_ERROR_SCHEMA,
    SECRET_EXPORT_RESULT_SCHEMA,
    SECRET_EXPORT_START_SCHEMA,
    RedisSecretExportStore,
    SecretExportError,
    SecretExportRequest,
    SecretExportTransaction,
    secret_export_values_size,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_runtime import (
    KDCubeSecretRuntime,
    ManagementSecretNotFound,
    ManagementSecretsProviderUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.bundle.login_lane import sign_in_bounce_path

router = APIRouter()
LOGGER = logging.getLogger("kdcube.management.secret_export")
SECRET_EXPORT_ACTION = "kdcube.management.secret.export"

SECRET_RESPONSE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}

_HTML_HEADERS = {
    **SECRET_RESPONSE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _origin(request: Request) -> str:
    return request_origin(request).rstrip("/")


def _configuration() -> tuple[Any, HumanSecretExportConfig, str, str]:
    settings = get_settings()
    config = HumanSecretExportConfig.from_settings(settings)
    config.validate()
    tenant = str(getattr(settings, "TENANT", "") or "").strip()
    project = str(getattr(settings, "PROJECT", "") or "").strip()
    if not tenant or not project:
        raise RuntimeError("secret_export_coordinates_unavailable")
    return settings, config, tenant, project


def _store(
    request: Request,
    *,
    config: HumanSecretExportConfig,
    tenant: str,
    project: str,
) -> RedisSecretExportStore:
    override = getattr(request.app.state, "secret_export_store", None)
    if override is not None:
        return override
    redis = getattr(request.app.state, "redis_async", None)
    if redis is None:
        raise RuntimeError("secret_export_store_unavailable")
    return RedisSecretExportStore(
        redis,
        tenant=tenant,
        project=project,
        transaction_ttl_seconds=config.transaction_ttl_seconds,
        consumed_tombstone_seconds=config.consumed_tombstone_seconds,
        max_targets=config.max_targets,
    )


def _runtime(
    request: Request,
    *,
    tenant: str,
    project: str,
) -> KDCubeSecretRuntime:
    override = getattr(request.app.state, "secret_export_runtime", None)
    if override is not None:
        return override
    return KDCubeSecretRuntime(request, tenant=tenant, project=project)


def _error(code: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema": SECRET_EXPORT_ERROR_SCHEMA,
            "ok": False,
            "error": {"code": str(code or "secret_export_unavailable")},
        },
        headers=SECRET_RESPONSE_HEADERS,
    )


def _html(
    *,
    title: str,
    content: str,
    status_code: int = 200,
) -> HTMLResponse:
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f4f7f8; color: #152334; }}
    main {{ width: min(720px, calc(100% - 32px)); margin: 48px auto; }}
    h1 {{ font-size: 24px; margin: 0 0 12px; }}
    p {{ line-height: 1.5; }}
    .panel {{ border: 1px solid #cbd8dc; background: #fff; padding: 20px; }}
    .warning {{ border-left: 4px solid #b45309; padding: 10px 12px; background: #fff7ed; }}
    ul {{ padding-left: 24px; }}
    li {{ margin: 8px 0; overflow-wrap: anywhere; }}
    code {{ font-family: ui-monospace, monospace; font-size: 13px; }}
    .actions {{ display: flex; gap: 10px; margin-top: 20px; }}
    button {{ border: 1px solid #76909a; padding: 9px 14px; font: inherit; cursor: pointer; }}
    .approve {{ color: white; background: #075985; border-color: #075985; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #111827; color: #e5edf2; }}
      .panel {{ background: #17212b; border-color: #41515b; }}
      .warning {{ background: #332418; }}
    }}
  </style>
</head>
<body><main><div class="panel">{content}</div></main></body>
</html>"""
    return HTMLResponse(body, status_code=status_code, headers=_HTML_HEADERS)


def _html_error(code: str, *, status_code: int) -> HTMLResponse:
    return _html(
        title="Secret export unavailable",
        status_code=status_code,
        content=(
            "<h1>Secret export unavailable</h1>"
            "<p>The request cannot continue. Return to the terminal and start "
            f"a new export.</p><p><code>{html.escape(code)}</code></p>"
        ),
    )


def _browser_return_to(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def _approval_context(
    request: Request,
    *,
    tenant: str,
    project: str,
    transaction: SecretExportTransaction,
) -> HumanApprovalContext:
    return HumanApprovalContext(
        action=SECRET_EXPORT_ACTION,
        tenant=tenant,
        project=project,
        transaction_id=transaction.transaction_id,
        request_digest=transaction.request_digest,
        required_assurance=transaction.required_assurance,
        max_evidence_age_seconds=transaction.max_evidence_age_seconds,
        return_url=(
            f"{request.url.path}?"
            + urlencode({"transaction": transaction.transaction_id})
        ),
    )


def _context() -> tuple[HumanSecretExportConfig, str, str]:
    try:
        _settings, config, tenant, project = _configuration()
    except (RuntimeError, TypeError, ValueError) as exc:
        raise SecretExportError(str(exc), status_code=503) from None
    if not config.enabled:
        raise SecretExportError("secret_export_disabled", status_code=404)
    return config, tenant, project


def _transaction_query(request: Request) -> str:
    pairs = list(request.query_params.multi_items())
    if len(pairs) != 1 or pairs[0][0] != "transaction":
        raise SecretExportError(
            "secret_export_transaction_invalid",
            status_code=400,
        )
    return str(pairs[0][1]).strip()


async def _approval_fields(request: Request) -> dict[str, str]:
    try:
        raw = await read_bounded_body(
            request,
            maximum_bytes=8192,
            media_type="application/x-www-form-urlencoded",
        )
    except ManagementRequestBodyError:
        raise SecretExportError(
            "secret_export_approval_invalid",
            status_code=400,
        ) from None
    try:
        pairs = parse_qsl(
            raw.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
    except (UnicodeDecodeError, ValueError):
        raise SecretExportError(
            "secret_export_approval_invalid",
            status_code=400,
        ) from None
    if len(pairs) != 3 or {key for key, _value in pairs} != {
        "transaction",
        "csrf",
        "decision",
    }:
        raise SecretExportError("secret_export_approval_invalid", status_code=400)
    return dict(pairs)


@router.post("/secrets/export/start", include_in_schema=False)
async def start_secret_export(request: Request) -> JSONResponse:
    try:
        config, tenant, project = _context()
        try:
            payload = await read_json_object(request, maximum_bytes=256 * 1024)
        except ManagementRequestBodyError:
            raise SecretExportError(
                "secret_export_request_invalid",
                status_code=400,
            ) from None
        normalized_payload = dict(payload)
        selection = str(normalized_payload.pop("selection", "") or "").strip()
        if selection:
            if selection != "all" or normalized_payload.get("targets") not in (
                None,
                [],
            ):
                raise SecretExportError(
                    "secret_export_selection_invalid",
                    status_code=400,
                )
            targets = await _runtime(
                request,
                tenant=tenant,
                project=project,
            ).inventory()
            if not targets:
                raise SecretExportError(
                    "secret_export_inventory_empty",
                    status_code=404,
                )
            if len(targets) > config.max_targets:
                raise SecretExportError(
                    "secret_export_inventory_too_large",
                    status_code=413,
                )
            normalized_payload["targets"] = [
                target.public_dict() for target in targets
            ]
        export_request = SecretExportRequest.from_mapping(
            normalized_payload,
            tenant=tenant,
            project=project,
            max_targets=config.max_targets,
        )
        transaction = await _store(
            request,
            config=config,
            tenant=tenant,
            project=project,
        ).create(
            export_request,
            required_assurance=config.required_assurance,
            max_evidence_age_seconds=config.max_evidence_age_seconds,
            max_total_value_bytes=config.max_total_value_bytes,
        )
    except SecretExportError as exc:
        return _error(exc.code, status_code=exc.status_code)
    except ManagementSecretsProviderUnavailable:
        return _error("secret_export_provider_unavailable", status_code=503)
    except Exception:  # noqa: BLE001
        return _error("secret_export_store_unavailable", status_code=503)

    authorization_url = (
        f"{_origin(request)}/api/integrations/management/v1/"
        "secrets/export/authorize?"
        + urlencode({"transaction": transaction.transaction_id})
    )
    LOGGER.info(
        "[secret-export] started digest=%s targets=%s",
        transaction.request_digest,
        len(transaction.request.targets),
    )
    return JSONResponse(
        {
            "schema": SECRET_EXPORT_START_SCHEMA,
            "ok": True,
            "transaction_id": transaction.transaction_id,
            "request_digest": transaction.request_digest,
            "authorization_url": authorization_url,
            "required_assurance": transaction.required_assurance,
            "expires_at": transaction.expires_at,
            "target_count": len(transaction.request.targets),
            # A whole-provider start route is intentionally unauthenticated so
            # the CLI can begin the browser ceremony. Do not disclose the
            # provider inventory until the approved one-use exchange.
            "targets": (
                []
                if selection == "all"
                else [
                    target.public_dict() for target in transaction.request.targets
                ]
            ),
        },
        headers=SECRET_RESPONSE_HEADERS,
    )


@router.get("/secrets/export/authorize", include_in_schema=False)
async def authorize_secret_export(
    request: Request,
) -> Response:
    try:
        transaction_id = _transaction_query(request)
        config, tenant, project = _context()
        transaction = await _store(
            request,
            config=config,
            tenant=tenant,
            project=project,
        ).load(transaction_id)
        if transaction.status != "pending":
            raise SecretExportError("secret_export_transaction_moved", status_code=409)
        approval = await evaluate_human_approval(
            request,
            context=_approval_context(
                request,
                tenant=tenant,
                project=project,
                transaction=transaction,
            ),
            phase="present",
        )
        if isinstance(approval, HumanApprovalChallenge):
            return RedirectResponse(
                approval.authorization_url,
                status_code=302,
                headers=SECRET_RESPONSE_HEADERS,
            )
    except HumanApprovalError as exc:
        if exc.status_code == 401:
            next_url = quote(_browser_return_to(request), safe="")
            return RedirectResponse(f"{sign_in_bounce_path()}?next={next_url}", status_code=302)
        return _html_error(exc.code, status_code=exc.status_code)
    except SecretExportError as exc:
        return _html_error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _html_error("secret_export_store_unavailable", status_code=503)

    target_rows = []
    for target in transaction.request.targets:
        if target.user_id and target.bundle_id:
            owner = f"user {target.user_id}, application {target.bundle_id}"
        elif target.user_id:
            owner = f"user {target.user_id}"
        elif target.bundle_id:
            owner = f"application {target.bundle_id}"
        else:
            owner = "deployment platform"
        target_rows.append(
            "<li><strong>"
            f"{html.escape(owner)}</strong><br><code>{html.escape(target.key)}</code>"
            "</li>"
        )
    action = html.escape(request.url.path, quote=True)
    content = f"""
<h1>Export KDCube secrets</h1>
<p class="warning">This action discloses plaintext secret values to the local
Connection Hub CLI. The approval is bound to this exact list and can be used once.</p>
<p><strong>Deployment:</strong> <code>{html.escape(tenant)} / {html.escape(project)}</code></p>
<p><strong>Return address:</strong> <code>{html.escape(transaction.request.callback_uri)}</code></p>
<p><strong>Request digest:</strong> <code>{html.escape(transaction.request_digest)}</code></p>
<p><strong>Required assurance:</strong> <code>{html.escape(transaction.required_assurance)}</code></p>
<p><strong>Secrets ({len(target_rows)}):</strong></p>
<ul>{''.join(target_rows)}</ul>
<form method="post" action="{action}">
  <input type="hidden" name="transaction" value="{html.escape(transaction.transaction_id, quote=True)}">
  <input type="hidden" name="csrf" value="{html.escape(transaction.csrf_token, quote=True)}">
  <div class="actions">
    <button type="submit" name="decision" value="deny">Deny</button>
    <button class="approve" type="submit" name="decision" value="approve">Export once</button>
  </div>
</form>
"""
    return _html(title="Export KDCube secrets", content=content)


@router.post("/secrets/export/authorize", include_in_schema=False)
async def approve_secret_export(request: Request) -> Response:
    try:
        fields = await _approval_fields(request)
        decision = fields.get("decision", "")
        if decision not in {"approve", "deny"}:
            raise SecretExportError(
                "secret_export_approval_invalid",
                status_code=400,
            )
        config, tenant, project = _context()
        store = _store(
            request,
            config=config,
            tenant=tenant,
            project=project,
        )
        evidence = await evaluate_human_approval(
            request,
            context=_approval_context(
                request,
                tenant=tenant,
                project=project,
                transaction=await store.load(fields.get("transaction", "")),
            ),
            phase="commit",
        )
        if isinstance(evidence, HumanApprovalChallenge):
            raise HumanApprovalError(
                "human_approval_restart_required",
                status_code=409,
            )
        if decision == "deny":
            denied = await store.deny(
                fields.get("transaction", ""),
                csrf_token=fields.get("csrf", ""),
                evidence=evidence,
            )
            callback_uri = denied.request.callback_uri
            params = {
                "error": "access_denied",
                "state": denied.request.state,
                "iss": _origin(request),
            }
            LOGGER.info(
                "[secret-export] denied digest=%s subject=%s",
                denied.request_digest,
                evidence.subject,
            )
        else:
            approved = await store.approve(
                fields.get("transaction", ""),
                csrf_token=fields.get("csrf", ""),
                evidence=evidence,
            )
            callback_uri = approved.callback_uri
            params = {
                "code": approved.code,
                "state": approved.state,
                "iss": _origin(request),
            }
            LOGGER.info(
                "[secret-export] approved digest=%s subject=%s assurance=%s",
                approved.request_digest,
                evidence.subject,
                evidence.assurance,
            )
    except HumanApprovalError as exc:
        if exc.status_code == 401:
            return _html_error("human_browser_session_expired", status_code=401)
        return _html_error(exc.code, status_code=exc.status_code)
    except SecretExportError as exc:
        return _html_error(exc.code, status_code=exc.status_code)
    except Exception:  # noqa: BLE001
        return _html_error("secret_export_store_unavailable", status_code=503)
    return RedirectResponse(
        f"{callback_uri}?{urlencode(params)}",
        status_code=302,
        headers=SECRET_RESPONSE_HEADERS,
    )


@router.post("/secrets/export/exchange", include_in_schema=False)
async def exchange_secret_export(request: Request) -> JSONResponse:
    try:
        config, tenant, project = _context()
        try:
            payload = await read_json_object(request, maximum_bytes=8192)
        except ManagementRequestBodyError:
            raise SecretExportError(
                "secret_export_exchange_invalid",
                status_code=400,
            ) from None
        if set(payload) != {
            "transaction_id",
            "code",
            "code_verifier",
        }:
            raise SecretExportError("secret_export_exchange_invalid", status_code=400)
        grant = await _store(
            request,
            config=config,
            tenant=tenant,
            project=project,
        ).consume(
            str(payload.get("transaction_id") or ""),
            code=str(payload.get("code") or ""),
            code_verifier=str(payload.get("code_verifier") or ""),
        )
        runtime = _runtime(request, tenant=tenant, project=project)
        values: list[dict[str, Any]] = []
        for target in grant.request.targets:
            result = dict(await runtime.read(target))
            value = result.get("value")
            if not isinstance(value, str):
                raise ManagementSecretsProviderUnavailable(
                    "secret provider returned an invalid value"
                )
            values.append({**target.public_dict(), "value": value})
        if secret_export_values_size(
            [str(item["value"]) for item in values]
        ) > grant.max_total_value_bytes:
            raise SecretExportError("secret_export_result_too_large", status_code=413)
    except SecretExportError as exc:
        return _error(exc.code, status_code=exc.status_code)
    except ManagementSecretNotFound:
        return _error("secret_export_secret_not_found", status_code=404)
    except ManagementSecretsProviderUnavailable:
        return _error("secret_export_provider_unavailable", status_code=503)
    except Exception:  # noqa: BLE001
        return _error("secret_export_unavailable", status_code=503)

    LOGGER.info(
        "[secret-export] consumed digest=%s subject=%s targets=%s assurance=%s",
        grant.request_digest,
        grant.subject,
        len(values),
        grant.assurance,
    )
    return JSONResponse(
        {
            "schema": SECRET_EXPORT_RESULT_SCHEMA,
            "ok": True,
            "transaction_id": grant.transaction_id,
            "request_digest": grant.request_digest,
            "target": {"tenant": tenant, "project": project},
            "approval": {
                "assurance": grant.assurance,
                "method": grant.approval_method,
                "verified_at": grant.approval_verified_at,
            },
            "values": values,
        },
        headers=SECRET_RESPONSE_HEADERS,
    )


__all__ = ["SECRET_RESPONSE_HEADERS", "router"]
