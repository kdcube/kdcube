# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Safe, structured provider failures shared by SDK integrations."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Mapping


_AUTH_REASONS = {
    "ACCOUNTINACTIVE",
    "AUTHERROR",
    "INVALIDAUTH",
    "INVALIDCREDENTIALS",
    "INVALIDGRANT",
    "NOTAUTHED",
    "TOKENEXPIRED",
    "TOKENREVOKED",
    "UNAUTHENTICATED",
}
_SCOPE_REASONS = {
    "ACCESSTOKENSCOPEINSUFFICIENT",
    "INSUFFICIENTPERMISSIONS",
    "INSUFFICIENTSCOPE",
    "MISSINGSCOPE",
    # LinkedIn's wording for a token that lacks the required OAuth scope.
    "NOTENOUGHPERMISSIONS",
}
_CONFIG_REASONS = {
    "ACCESSNOTCONFIGURED",
    "APIDISABLED",
    "SERVICEDISABLED",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _reason_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def _contains_reason(values: set[str], reasons: set[str]) -> bool:
    """Whether a normalized provider value names one known reason.

    Some providers return a reason as a field, while others embed it in a
    sentence (for example LinkedIn's ``Not enough permissions to access``).
    Exact-only matching turns the latter into a generic 403 and skips the
    connected-account refresh/reconnect path.
    """
    return any(reason and reason in value for value in values for reason in reasons)


def _safe_message(value: Any, *, fallback: str) -> str:
    message = _text(value) or fallback
    message = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", message)
    return message[:1_000]


def _json_object(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception:
        return {}
    return dict(body) if isinstance(body, Mapping) else {}


def _provider_reason(error: Mapping[str, Any]) -> str:
    for key in ("details", "errors"):
        rows = error.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping) and _text(row.get("reason")):
                return _text(row.get("reason"))
    return ""


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    service: str
    operation: str
    stage: str
    message: str
    provider_status: int
    provider_code: str
    provider_reason: str
    category: str
    retryable: bool
    outcome_unknown: bool
    retry_after: str = ""

    @property
    def credential_failure(self) -> bool:
        return self.category in {"authorization_failed", "scope_insufficient"}

    @property
    def normalized_code(self) -> str:
        prefix = re.sub(r"[^a-z0-9]+", "_", self.service.lower()).strip("_")
        return f"{prefix}_{self.category}" if prefix else self.category

    def client_ret(self) -> dict[str, Any]:
        ret: dict[str, Any] = {
            "provider": self.provider,
            "operation": self.operation,
            "stage": self.stage,
            "provider_status": self.provider_status,
            "provider_code": self.provider_code,
            "provider_reason": self.provider_reason,
            "category": self.category,
            "retryable": self.retryable,
            "outcome_unknown": self.outcome_unknown,
        }
        if self.retry_after:
            ret["retry_after"] = self.retry_after
        return ret

    def error_result(self, *, where: str, code: str | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": code or self.normalized_code,
                "message": self.message,
                "where": where,
                "managed": True,
            },
            "ret": self.client_ret(),
        }


def provider_failure_from_response(
    response: Any,
    *,
    provider: str,
    service: str,
    operation: str,
    fallback: str,
    stage: str = "",
    mutating: bool = False,
) -> ProviderFailure:
    body = _json_object(response)
    status = int(getattr(response, "status_code", 0) or 0)
    headers = getattr(response, "headers", {}) or {}
    return provider_failure_from_payload(
        body,
        provider_status=status,
        provider=provider,
        service=service,
        operation=operation,
        fallback=fallback,
        stage=stage,
        mutating=mutating,
        retry_after=_text(
            headers.get("Retry-After") or headers.get("retry-after")
        ),
    )


def provider_failure_from_payload(
    body: Mapping[str, Any] | None,
    *,
    provider_status: int,
    provider: str,
    service: str,
    operation: str,
    fallback: str,
    stage: str = "",
    mutating: bool = False,
    retry_after: str = "",
) -> ProviderFailure:
    body = dict(body or {})
    status = int(provider_status or 0)
    provider_code = ""
    provider_reason = ""
    message = ""

    error = body.get("error")
    if isinstance(error, Mapping):
        provider_code = _text(error.get("status") or error.get("code"))
        provider_reason = _provider_reason(error)
        message = _text(error.get("message"))
    elif error:
        provider_code = _text(error)
        provider_reason = provider_code
        message = provider_code
    elif body.get("warning"):
        provider_code = _text(body.get("warning"))
        provider_reason = provider_code
        message = provider_code

    combined_keys = {
        _reason_key(provider_code),
        _reason_key(provider_reason),
        _reason_key(message),
    }
    if status == 401 or _contains_reason(combined_keys, _AUTH_REASONS):
        category = "authorization_failed"
    elif _contains_reason(combined_keys, _SCOPE_REASONS):
        category = "scope_insufficient"
    elif _contains_reason(combined_keys, _CONFIG_REASONS):
        category = "provider_configuration_error"
    elif status == 403:
        category = "access_denied"
    elif status == 404:
        category = "not_found"
    elif status == 409:
        category = "conflict"
    elif status == 429:
        category = "rate_limited"
    elif status >= 500:
        category = "provider_unavailable"
    else:
        category = "provider_error"

    retryable = status in {408, 425, 429} or status >= 500
    return ProviderFailure(
        provider=_text(provider),
        service=_text(service),
        operation=_text(operation),
        stage=_text(stage) or _text(operation),
        message=_safe_message(message, fallback=fallback),
        provider_status=status,
        provider_code=provider_code,
        provider_reason=provider_reason,
        category=category,
        retryable=retryable,
        outcome_unknown=bool(mutating and status >= 500),
        retry_after=_text(retry_after),
    )


def provider_failure_from_exception(
    exc: Exception,
    *,
    provider: str,
    service: str,
    operation: str,
    fallback: str,
    stage: str = "",
    mutating: bool = False,
) -> ProviderFailure:
    return ProviderFailure(
        provider=_text(provider),
        service=_text(service),
        operation=_text(operation),
        stage=_text(stage) or _text(operation),
        message=_safe_message("", fallback=fallback),
        provider_status=0,
        provider_code=exc.__class__.__name__,
        provider_reason=exc.__class__.__name__,
        category="transport_error",
        retryable=True,
        outcome_unknown=bool(mutating),
    )


def log_provider_failure(
    logger: logging.Logger,
    failure: ProviderFailure,
    *,
    where: str,
    exc: Exception | None = None,
) -> None:
    args = (
        where,
        failure.provider,
        failure.service,
        failure.operation,
        failure.stage,
        failure.normalized_code,
        failure.provider_status,
        failure.provider_code,
        failure.provider_reason,
        failure.retryable,
        failure.outcome_unknown,
        failure.message,
    )
    message = (
        "Provider operation failed where=%s provider=%s service=%s "
        "operation=%s stage=%s code=%s status=%s provider_code=%s "
        "provider_reason=%s retryable=%s outcome_unknown=%s message=%s"
    )
    if exc is None:
        logger.error(message, *args)
        return
    logger.error(
        message,
        *args,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


__all__ = [
    "ProviderFailure",
    "log_provider_failure",
    "provider_failure_from_exception",
    "provider_failure_from_payload",
    "provider_failure_from_response",
]
