"""Connection Hub backed Google Docs service for the productivity door.

Mirrors ``google_sheets`` but calls the Google Docs proxy **async in-proc**
(no ``@venv``): the proxy speaks raw REST over ``httpx`` and needs no heavy
blocking dependency, exactly like ``gmail_tools``. Credential resolution,
the one refresh-retry, and the consent-error surface are identical to Sheets.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
    connected_account_auth_failure,
    resolve_connected_account_claim,
    run_with_connected_account_retry,
)
from kdcube_ai_app.apps.chat.sdk.integrations.file_delivery import (
    resolve_connected_account_access_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_proxy import (
    execute_google_docs_operation,
)
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_proxy_flex import (
    execute_google_docs_flex_operation,
)

# Operations served by the FLEXIBLE (graph-faithful, tab-aware) proxy, side by
# side with the narrow typed proxy. Routed by name in _execute_with_access_token.
_FLEX_OPERATIONS = frozenset({"get_structure", "list_tabs", "batch_edit"})
from kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service import (
    DOCS_NAMESPACE,
    document_export_filename,
    make_docs_named_service_provider,
    parse_docs_export_ref,
    parse_docs_ref,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connector_app_resolution import (
    resolve_connector_app_id,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
    NamedServiceStreamResult,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    OBJECT_GET,
)


LOGGER = logging.getLogger("kdcube.services.productivity.google_docs")

DOCS_PROVIDER_ID = "google"
DOCS_READ_CLAIM = "docs:read"
DOCS_WRITE_CLAIM = "docs:write"
DOCS_COMMENT_CLAIM = "docs:comment"
# The document as a file: trashing and restoring it, not editing its contents.
DOCS_DELETE_CLAIM = "docs:delete"
# Drive-as-files claims. Separate from the Docs claims because docs:write's
# drive.file scope cannot reach pre-existing folders: raw upload and folder
# listing need the honest Drive scopes, and a claim that says so.
DRIVE_READ_CLAIM = "drive:read"
DRIVE_WRITE_CLAIM = "drive:write"

# Provider-failure categories (from the shared provider_errors normalizer the
# proxy uses) that mean the connected credential must refresh/reconnect.
_CREDENTIAL_FAILURE_CATEGORIES = {"authorization_failed", "scope_insufficient"}

_SERVICE: Any = None


def bind_service(service: Any) -> None:
    global _SERVICE
    _SERVICE = service


def _error_result(
    *, code: str, message: str, where: str, ret: Any = None
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": str(code or "google_docs_error"),
            "message": str(message or "Google Docs operation failed."),
            "where": where,
            "managed": True,
        },
        "ret": ret,
    }


class GoogleDocsService:
    async def _credential(
        self,
        *,
        claim: str | Sequence[str],
        tool_name: str,
        account_id: str,
    ) -> ConnectedAccountCredential:
        claims = (
            [str(item).strip() for item in claim if str(item or "").strip()]
            if not isinstance(claim, str)
            else [claim.strip()]
        )
        if not claims:
            raise ValueError("At least one Google Docs claim is required.")
        selected_account_id = str(account_id or "").strip()
        credential: ConnectedAccountCredential | None = None
        for required_claim in claims:
            credential = await resolve_connected_account_claim(
                globals(),
                provider_id=DOCS_PROVIDER_ID,
                connector_app_id=resolve_connector_app_id(DOCS_PROVIDER_ID),
                claim=required_claim,
                account_id=selected_account_id,
                tool_name=tool_name,
            )
            if not credential.ok:
                return credential
            selected_account_id = credential.account_id or selected_account_id
        assert credential is not None
        return credential

    async def execute(
        self,
        *,
        operation: str,
        claim: str | Sequence[str],
        tool_name: str,
        payload: Mapping[str, Any] | None = None,
        account_id: str = "",
    ) -> dict[str, Any]:
        where = f"google_docs.{operation}"
        return await run_with_connected_account_retry(
            globals(),
            where=where,
            run=lambda: self._execute_once(
                operation=operation,
                claim=claim,
                tool_name=tool_name,
                payload=dict(payload or {}),
                account_id=str(account_id or "").strip(),
                where=where,
            ),
        )

    async def _execute_once(
        self,
        *,
        operation: str,
        claim: str | Sequence[str],
        tool_name: str,
        payload: dict[str, Any],
        account_id: str,
        where: str,
    ) -> dict[str, Any]:
        credential = await self._credential(
            claim=claim, tool_name=tool_name, account_id=account_id
        )
        if not credential.ok:
            return credential.error_envelope(where=where)
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="The connected Google credential has no access token.",
                where=where,
            )
        return await self._execute_with_access_token(
            operation=operation,
            access_token=credential.access_token,
            payload=payload,
            account_id=credential.account_id,
            where=where,
            credential=credential,
        )

    async def _execute_with_access_token(
        self,
        *,
        operation: str,
        access_token: str,
        payload: Mapping[str, Any],
        account_id: str,
        where: str,
        credential: ConnectedAccountCredential | None,
    ) -> dict[str, Any]:
        proxy = (
            execute_google_docs_flex_operation
            if operation in _FLEX_OPERATIONS
            else execute_google_docs_operation
        )
        try:
            result = await proxy(
                operation=operation,
                access_token=access_token,
                payload=dict(payload or {}),
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Google Docs operation crashed operation=%s", operation
            )
            return _error_result(
                code="google_docs_runtime_error",
                message="The Google Docs runtime failed.",
                where=where,
            )
        if not isinstance(result, Mapping):
            return _error_result(
                code="google_docs_invalid_result",
                message="The Google Docs provider returned an invalid result.",
                where=where,
            )
        error = result.get("error")
        error_map = dict(error or {}) if isinstance(error, Mapping) else {}
        ret_map = result.get("ret")
        ret_map = dict(ret_map or {}) if isinstance(ret_map, Mapping) else {}

        if not bool(result.get("ok")):
            error_code = str(error_map.get("code") or "google_docs_provider_error")
            category = str(ret_map.get("category") or "provider_error")
            message = str(error_map.get("message") or "Google Docs operation failed.")
            LOGGER.error(
                "Google Docs provider operation failed operation=%s code=%s "
                "category=%s status=%s provider_code=%s reason=%s retryable=%s "
                "outcome_unknown=%s",
                operation, error_code, category,
                ret_map.get("provider_status"), ret_map.get("provider_code"),
                ret_map.get("provider_reason"), ret_map.get("retryable"),
                ret_map.get("outcome_unknown"),
            )
            if category in _CREDENTIAL_FAILURE_CATEGORIES and not ret_map.get(
                "outcome_unknown"
            ):
                if credential is not None:
                    return connected_account_auth_failure(credential, message)
            return _error_result(
                code=error_code, message=message, where=where, ret=ret_map
            )

        normalized = ret_map if ret_map else {"value": result.get("ret")}
        normalized["account_id"] = str(account_id or "").strip()
        return {"ok": True, "error": None, "ret": normalized}


async def fetch_google_docs_snapshot(
    entrypoint: Any,
    *,
    user_id: str,
    tenant: str,
    project: str,
    object_ref: str,
    bundle_id: str,
) -> dict[str, Any]:
    """Resolve a complete Docs snapshot for one signed object URL."""
    try:
        parsed = parse_docs_ref(object_ref)
    except ValueError as exc:
        return {
            "ok": False,
            "status": 400,
            "error": {"code": "invalid_docs_ref", "message": str(exc)},
        }
    access_token, failure = await resolve_connected_account_access_token(
        entrypoint,
        user_id=str(user_id or "").strip(),
        tenant=str(tenant or "").strip(),
        project=str(project or "").strip(),
        provider_id=DOCS_PROVIDER_ID,
        connector_app_id=resolve_connector_app_id(DOCS_PROVIDER_ID),
        claim=DOCS_READ_CLAIM,
        account_id=str(parsed.get("account_id") or "").strip(),
    )
    if failure is not None:
        return dict(failure)

    service = GoogleDocsService()

    async def _execute(**kwargs: Any) -> dict[str, Any]:
        return await service._execute_with_access_token(
            operation=str(kwargs.get("operation") or ""),
            access_token=access_token,
            payload=dict(kwargs.get("payload") or {}),
            account_id=str(parsed.get("account_id") or "").strip(),
            where=f"google_docs.{kwargs.get('operation') or ''}",
            credential=None,
        )

    provider = make_docs_named_service_provider(
        execute_operation=_execute,
        bundle_id=str(bundle_id or "").strip(),
    )
    result = await provider.dispatch(
        NamedServiceContext(
            tenant=str(tenant or "").strip(),
            project=str(project or "").strip(),
            user_id=str(user_id or "").strip(),
        ),
        NamedServiceRequest(
            operation=OBJECT_GET,
            namespace=DOCS_NAMESPACE,
            object_ref=object_ref,
            response_mode="stream",
            context={"source": "signed_download", "materialize": True},
        ),
    )
    if not isinstance(result, NamedServiceStreamResult):
        response = result
        error = response.error.to_dict() if response.error is not None else {}
        return {
            "ok": False,
            "status": int(response.status or 500),
            "error": error
            or {
                "code": "docs_snapshot_unavailable",
                "message": "The document snapshot could not be produced.",
            },
        }
    return {
        "ok": True,
        "chunks": result.chunks,
        "filename": result.filename or "document.docs.json",
        "mime_type": result.media_type or "application/json",
        "headers": dict(result.headers or {}),
        "status": int(result.status_code or 200),
    }


async def fetch_google_docs_export(
    entrypoint: Any,
    *,
    user_id: str,
    tenant: str,
    project: str,
    object_ref: str,
) -> dict[str, Any]:
    """Resolve one signed Docs export ref into provider bytes.

    The signed download route calls this after token verification. The Google
    credential is resolved again for the token-bound user and never enters the
    URL, chat event, or model-visible tool result.
    """

    try:
        parsed = parse_docs_export_ref(object_ref)
    except ValueError as exc:
        return {
            "ok": False,
            "status": 400,
            "error": {"code": "invalid_docs_export_ref", "message": str(exc)},
        }
    access_token, failure = await resolve_connected_account_access_token(
        entrypoint,
        user_id=str(user_id or "").strip(),
        tenant=str(tenant or "").strip(),
        project=str(project or "").strip(),
        provider_id=DOCS_PROVIDER_ID,
        connector_app_id=resolve_connector_app_id(DOCS_PROVIDER_ID),
        claim=DOCS_READ_CLAIM,
        account_id=str(parsed.get("account_id") or "").strip(),
    )
    if failure is not None:
        return dict(failure)

    service = GoogleDocsService()
    metadata = await service._execute_with_access_token(
        operation="get",
        access_token=access_token,
        payload={
            "document_ref": parsed["document_id"],
            "include_text": False,
        },
        account_id=parsed["account_id"],
        where="google_docs.get",
        credential=None,
    )
    if not metadata.get("ok"):
        return metadata
    exported = await service._execute_with_access_token(
        operation="export",
        access_token=access_token,
        payload={
            "document_ref": parsed["document_id"],
            "format": parsed["format"],
        },
        account_id=parsed["account_id"],
        where="google_docs.export",
        credential=None,
    )
    if not exported.get("ok"):
        return exported
    ret = exported.get("ret") if isinstance(exported.get("ret"), Mapping) else {}
    encoded = str((ret or {}).get("content_base64") or "").strip()
    try:
        data = base64.b64decode(encoded, validate=True) if encoded else b""
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "status": 502,
            "error": {
                "code": "docs_export_payload_invalid",
                "message": "The document provider returned invalid export bytes.",
                "details": {"error": str(exc)},
            },
        }
    if not data:
        return {
            "ok": False,
            "status": 502,
            "error": {
                "code": "docs_export_payload_missing",
                "message": "The document provider returned no export bytes.",
            },
        }
    metadata_ret = (
        metadata.get("ret") if isinstance(metadata.get("ret"), Mapping) else {}
    )
    return {
        "ok": True,
        "data": data,
        "filename": document_export_filename(
            title=(metadata_ret or {}).get("title"),
            document_id=parsed["document_id"],
            extension=parsed["extension"],
        ),
        "mime_type": parsed["mime_type"],
        "status": 200,
    }


__all__ = [
    "GoogleDocsService",
    "DOCS_PROVIDER_ID",
    "DOCS_READ_CLAIM",
    "DOCS_WRITE_CLAIM",
    "DOCS_COMMENT_CLAIM",
    "DOCS_DELETE_CLAIM",
    "DRIVE_READ_CLAIM",
    "DRIVE_WRITE_CLAIM",
    "bind_service",
    "fetch_google_docs_export",
    "fetch_google_docs_snapshot",
]
