"""Secret-safe projections of delegated management protocol results."""

from __future__ import annotations

from typing import Any

from kdcube_cli.management.models import (
    SECRET_VALUE_READ,
    ManagementDenial,
    ManagementRequest,
    ManagementResult,
)


def management_view(
    request: ManagementRequest,
    result: ManagementResult | ManagementDenial,
    *,
    result_schema: str = "kdcube_cli.management_result.v1",
    error_schema: str = "kdcube_cli.management_error.v1",
) -> dict[str, Any]:
    """Project a management response without disclosing a returned secret."""

    if isinstance(result, ManagementResult):
        projected_result = dict(result.result)
        if request.operation == SECRET_VALUE_READ:
            projected_result.pop("value", None)
            projected_result["disclosed"] = True
        return {
            "schema": result_schema,
            "ok": True,
            "operation": result.operation,
            "resource": result.resource,
            "invocation": {
                "id": result.invocation_id,
                "replay": result.replay,
            },
            "authority": dict(result.authority),
            "result": projected_result,
        }

    value: dict[str, Any] = {
        "schema": error_schema,
        "ok": False,
        "status": result.status,
        "operation": request.operation,
        "resource": request.resource,
        "invocation_id": request.invocation_id,
        "error": {"code": result.code, "retryable": result.retryable},
    }
    if request.request_digest:
        value["request_digest"] = request.request_digest
    if result.recovery is not None:
        recovery = result.recovery
        value["recovery"] = {
            "type": "consent_required",
            "reason": recovery.reason,
            "authorization_url": recovery.authorization_url,
            "access_id": recovery.access_id,
            "resource": recovery.resource,
            "operation": recovery.operation,
            "application_id": recovery.application_id,
            "invocation_id": recovery.invocation_id,
            "request_digest": recovery.request_digest,
            "card_revision": recovery.card_revision,
            "catalog_version": recovery.catalog_version,
            "expires_at": recovery.expires_at,
            "choices": list(recovery.choices),
        }
    return value


__all__ = ["management_view"]
