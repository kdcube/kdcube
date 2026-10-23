# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Transport-neutral delegated management orchestration."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from kdcube_ai_app.apps.chat.proc.rest.management.admission import (
    AdmissionDecision,
    AdmissionUnavailable,
)
from kdcube_ai_app.apps.chat.proc.rest.management.contracts import (
    INSPECT_OPERATION,
    RELOAD_OPERATION,
    SURFACES_OPERATION,
    management_error,
    management_request_digest,
    management_success,
)
from kdcube_ai_app.apps.chat.proc.rest.management.effect_ledger import (
    ACTION_CONFLICT,
    ACTION_PENDING,
    ACTION_REPLAY,
    ACTION_UNKNOWN,
    EffectLedger,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    SECRET_DELETE_OPERATION,
    SECRET_METADATA_OPERATION,
    SECRET_OPERATIONS,
    SECRET_READ_OPERATION,
    SECRET_WRITE_OPERATION,
    SecretTarget,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_runtime import (
    ManagementSecretNotFound,
    ManagementSecretsProviderReadOnly,
    ManagementSecretsProviderUnavailable,
)
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_contracts import (
    USER_PROPS_IMPORT_OPERATION,
    UserPropRow,
)
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_runtime import (
    ManagementUserPropsUnavailable,
)

LOGGER = logging.getLogger("ChatProc.DelegatedManagement")


class ManagementApplicationNotFound(LookupError):
    pass


class ManagementRuntimeUnavailable(RuntimeError):
    pass


class AdmissionEvaluator(Protocol):
    async def evaluate(
        self,
        *,
        delegated_bearer: str,
        resource: str,
        operation: str,
        invocation_id: str,
        request_digest: str,
        approval_context: Mapping[str, str] | None = None,
    ) -> AdmissionDecision: ...


class ManagementRuntime(Protocol):
    async def inspect_deployment(self) -> Mapping[str, Any]: ...

    async def application_surfaces(
        self, application_id: str
    ) -> Mapping[str, Any]: ...

    async def reload_application(
        self, application_id: str, *, caller_profile: str
    ) -> Mapping[str, Any]: ...


class SecretRuntime(Protocol):
    async def metadata(self, target: SecretTarget) -> Mapping[str, Any]: ...

    async def read(self, target: SecretTarget) -> Mapping[str, Any]: ...

    async def write(
        self,
        target: SecretTarget,
        *,
        value: str,
        caller_profile: str,
    ) -> Mapping[str, Any]: ...

    async def delete(
        self,
        target: SecretTarget,
        *,
        caller_profile: str,
    ) -> Mapping[str, Any]: ...


class UserPropsRuntime(Protocol):
    async def import_rows(self, rows: Any) -> Mapping[str, Any]: ...


# Operations whose request digest binds values: keyed, so no reusable plaintext hash exists.
_KEYED_DIGEST_OPERATIONS = frozenset({*SECRET_OPERATIONS, USER_PROPS_IMPORT_OPERATION})


@dataclass(frozen=True)
class ManagementResponse:
    status_code: int
    payload: Mapping[str, Any]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _authority_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    principal = _mapping(payload.get("principal"))
    provenance = _mapping(payload.get("provenance"))
    evidence: dict[str, Any] = {}
    values = {
        "decision_id": payload.get("decision_id"),
        "caller_profile": principal.get("client_id"),
        "access_id": provenance.get("access_id"),
        "card_revision": provenance.get("card_revision"),
        "card_catalog_version": provenance.get("card_catalog_version"),
        "active_catalog_version": provenance.get("active_catalog_version"),
        "invocation_policy_revision": provenance.get("invocation_policy_revision"),
        "request_permit_revision": provenance.get("request_permit_revision"),
    }
    for key, value in values.items():
        if value not in (None, "", 0):
            evidence[key] = value
    return evidence


# W929: how long a Card-grant consent stays open in the CLI (the request-permit TTL).
GRANT_RECOVERY_TTL_SECONDS = 600
_RECOVERY_REASONS = {
    # A request-bound operation's exact permit (application reload).
    "delegated_request_permit": "delegated_request_permit_required",
    # A Card grant (Once/Always) for a resource or operation the Card does not hold yet: secret operations.
    "delegated_agent_grant": "delegated_grant_required",
}


def _admission_recovery(
    payload: Mapping[str, Any],
    *,
    invocation_id: str = "",
    request_digest: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    details = _mapping(_mapping(payload.get("ret")).get("details"))
    consent = _mapping(payload.get("consent"))
    if not consent:
        consent = _mapping(details.get("recovery"))
    reason = _RECOVERY_REASONS.get(str(consent.get("kind") or ""))
    if reason is None:
        return {}
    authorization_url = str(
        consent.get("connection_hub_url") or consent.get("authorization_url") or ""
    ).strip()
    if not authorization_url:
        return {}
    context = _mapping(consent.get("approval_context"))
    recovery: dict[str, Any] = {
        "type": "consent_required",
        "reason": reason,
        "authorization_url": authorization_url,
    }
    values = {
        "access_id": consent.get("access_id"),
        "resource": consent.get("resource"),
        "operation": consent.get("outer_operation") or consent.get("operation"),
        "application_id": context.get("application_id")
        or consent.get("application_id"),
        "invocation_id": consent.get("invocation_id"),
        "request_digest": consent.get("request_digest"),
        "card_revision": consent.get("card_revision"),
        "catalog_version": consent.get("catalog_version"),
        "expires_at": consent.get("expires_at"),
        "choices": consent.get("available_choices") or consent.get("choices"),
    }
    if reason == "delegated_grant_required":
        # A Card grant is not bound to one request by the Hub; this management request binds it: the CLI's
        # retry is the identical request (same invocation and digest), and the consent stays open for a bounded
        # time. The Card revision is the admission's own detail.
        values["invocation_id"] = (
            values["invocation_id"] or consent.get("invocation_change_id") or invocation_id
        )
        values["request_digest"] = values["request_digest"] or request_digest
        values["card_revision"] = values["card_revision"] or details.get("card_revision")
        values["expires_at"] = values["expires_at"] or int(
            (time.time() if now is None else now) + GRANT_RECOVERY_TTL_SECONDS
        )
    for key, value in values.items():
        if value not in (None, "", 0, [], ()):
            recovery[key] = value
    return recovery


class DelegatedManagementService:
    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        resource: str,
        runtime_instance: str,
        admission: AdmissionEvaluator,
        ledger: EffectLedger,
        runtime: ManagementRuntime,
        secret_runtime: SecretRuntime | None = None,
        request_digest_secret: str = "",
        user_props_runtime: UserPropsRuntime | None = None,
    ) -> None:
        self._tenant = tenant
        self._project = project
        self._resource = resource
        self._runtime_instance = str(runtime_instance or "").strip()
        self._admission = admission
        self._ledger = ledger
        self._runtime = runtime
        self._secret_runtime = secret_runtime
        self._user_props_runtime = user_props_runtime
        self._request_digest_secret = str(request_digest_secret or "")

    def _error(
        self,
        *,
        operation: str,
        invocation_id: str,
        code: str,
        message: str,
        status_code: int,
        retryable: bool = False,
        recovery: Mapping[str, Any] | None = None,
        resource: str = "",
    ) -> ManagementResponse:
        return ManagementResponse(
            status_code,
            management_error(
                operation=operation,
                resource=resource or self._resource,
                tenant=self._tenant,
                project=self._project,
                invocation_id=invocation_id,
                code=code,
                message=message,
                retryable=retryable,
                recovery=recovery,
            ),
        )

    async def execute(
        self,
        *,
        operation: str,
        delegated_bearer: str,
        invocation_id: str,
        application_id: str = "",
        body: Mapping[str, Any] | None = None,
        resource: str = "",
        approval_context: Mapping[str, str] | None = None,
        secret_target: SecretTarget | None = None,
        user_props: tuple[UserPropRow, ...] | None = None,
    ) -> ManagementResponse:
        effective_resource = str(resource or self._resource).strip()
        try:
            request_digest = management_request_digest(
                resource=effective_resource,
                operation=operation,
                application_id=application_id,
                body=body,
                secret=(
                    self._request_digest_secret
                    if operation in _KEYED_DIGEST_OPERATIONS
                    else ""
                ),
            )
        except ValueError:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="management_request_digest_unavailable",
                message="Secure management request binding is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
        safe_approval_context = dict(approval_context or {})
        if application_id:
            safe_approval_context.setdefault("application_id", application_id)
        try:
            decision = await self._admission.evaluate(
                delegated_bearer=delegated_bearer,
                resource=effective_resource,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                approval_context=safe_approval_context,
            )
        except AdmissionUnavailable:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="delegated_admission_unavailable",
                message="Connection Hub admission is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
        except Exception:
            LOGGER.exception(
                "delegated management admission failed operation=%s "
                "application=%s invocation=%s",
                operation,
                application_id or "<deployment>",
                invocation_id,
            )
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="delegated_admission_unavailable",
                message="Connection Hub admission is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )

        if not decision.allowed:
            error = _mapping(decision.payload.get("error"))
            code = str(error.get("code") or "delegated_admission_denied")
            message = str(
                error.get("message") or "Connection Hub denied this operation."
            )
            retryable = bool(error.get("retryable", False))
            status_code = (
                decision.status_code
                if decision.status_code in {400, 401, 403, 409, 503}
                else 503
            )
            LOGGER.info(
                "delegated management denied operation=%s application=%s "
                "invocation=%s code=%s",
                operation,
                application_id or "<deployment>",
                invocation_id,
                code,
            )
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code=code,
                message=message,
                status_code=status_code,
                retryable=retryable,
                recovery=_admission_recovery(
                    decision.payload,
                    invocation_id=invocation_id,
                    request_digest=request_digest,
                ),
                resource=effective_resource,
            )

        authority = _authority_evidence(decision.payload)
        access_id = str(authority.get("access_id") or "").strip()
        caller_profile = str(authority.get("caller_profile") or "").strip()
        if not access_id or not caller_profile:
            LOGGER.error(
                "delegated management admission omitted caller identity "
                "operation=%s application=%s invocation=%s",
                operation,
                application_id or "<deployment>",
                invocation_id,
            )
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="delegated_admission_invalid",
                message="Connection Hub returned incomplete caller authority.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
        audit = {
            **authority,
            "tenant": self._tenant,
            "project": self._project,
            "resource": effective_resource,
            "operation": operation,
            "application_id": application_id,
            "invocation_id": invocation_id,
            "request_digest": request_digest,
            "runtime_instance": self._runtime_instance,
        }
        try:
            reservation = await self._ledger.reserve(
                access_id=access_id,
                resource=effective_resource,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                audit=audit,
            )
        except Exception:
            LOGGER.exception(
                "delegated management effect ledger unavailable operation=%s "
                "application=%s invocation=%s",
                operation,
                application_id or "<deployment>",
                invocation_id,
            )
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="effect_ledger_unavailable",
                message="Management effect tracking is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )

        if reservation.action == ACTION_REPLAY:
            record = _mapping(reservation.record)
            stored = _mapping(record.get("response"))
            if operation == SECRET_READ_OPERATION and stored.get("ok") is True:
                return self._error(
                    operation=operation,
                    invocation_id=invocation_id,
                    code="secret_value_result_not_replayable",
                    message=(
                        "The secret value was already disclosed for this "
                        "Idempotency-Key and is not retained for replay."
                    ),
                    status_code=409,
                    resource=effective_resource,
                )
            replayed = copy.deepcopy(dict(stored))
            invocation = replayed.get("invocation")
            if isinstance(invocation, Mapping):
                replayed["invocation"] = {**invocation, "replay": True}
            return ManagementResponse(int(record.get("status_code") or 200), replayed)
        if reservation.action == ACTION_CONFLICT:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="invocation_id_conflict",
                message="This Idempotency-Key belongs to a different request.",
                status_code=409,
                resource=effective_resource,
            )
        if reservation.action == ACTION_PENDING:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="effect_outcome_pending",
                message="The accepted management operation is still in progress.",
                status_code=409,
                retryable=True,
                resource=effective_resource,
            )
        if reservation.action == ACTION_UNKNOWN:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="effect_outcome_unknown",
                message="The accepted management operation has no recorded outcome.",
                status_code=409,
                resource=effective_resource,
            )

        try:
            if operation == INSPECT_OPERATION:
                result = await self._runtime.inspect_deployment()
            elif operation == SURFACES_OPERATION:
                result = await self._runtime.application_surfaces(application_id)
            elif operation == RELOAD_OPERATION:
                result = await self._runtime.reload_application(
                    application_id,
                    caller_profile=caller_profile,
                )
            elif operation == SECRET_METADATA_OPERATION:
                if self._secret_runtime is None or secret_target is None:
                    raise ManagementSecretsProviderUnavailable(
                        "Delegated secret management is unavailable"
                    )
                result = await self._secret_runtime.metadata(secret_target)
            elif operation == SECRET_READ_OPERATION:
                if self._secret_runtime is None or secret_target is None:
                    raise ManagementSecretsProviderUnavailable(
                        "Delegated secret management is unavailable"
                    )
                result = await self._secret_runtime.read(secret_target)
            elif operation == SECRET_WRITE_OPERATION:
                if self._secret_runtime is None or secret_target is None:
                    raise ManagementSecretsProviderUnavailable(
                        "Delegated secret management is unavailable"
                    )
                result = await self._secret_runtime.write(
                    secret_target,
                    value=str(_mapping(body).get("value", "")),
                    caller_profile=caller_profile,
                )
            elif operation == SECRET_DELETE_OPERATION:
                if self._secret_runtime is None or secret_target is None:
                    raise ManagementSecretsProviderUnavailable(
                        "Delegated secret management is unavailable"
                    )
                result = await self._secret_runtime.delete(
                    secret_target,
                    caller_profile=caller_profile,
                )
            elif operation == USER_PROPS_IMPORT_OPERATION:
                if self._user_props_runtime is None or not user_props:
                    raise ManagementUserPropsUnavailable("Delegated user props import is unavailable")
                result = await self._user_props_runtime.import_rows(user_props)
            else:
                raise ValueError("unsupported management operation")
        except ManagementUserPropsUnavailable:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="user_props_unavailable",
                message="User props could not be imported.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except ManagementApplicationNotFound:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="application_not_found",
                message="The exact application is not declared by this deployment.",
                status_code=404,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except ManagementSecretNotFound:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="secret_not_found",
                message="The exact secret does not exist.",
                status_code=404,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except ManagementSecretsProviderReadOnly:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="secrets_provider_read_only",
                message="The configured secrets provider does not accept writes.",
                status_code=503,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except ManagementSecretsProviderUnavailable:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="secrets_provider_unavailable",
                message="The configured secrets provider is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except ManagementRuntimeUnavailable:
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="management_runtime_unavailable",
                message="The KDCube management runtime is unavailable.",
                status_code=503,
                retryable=True,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response
        except Exception:
            LOGGER.exception(
                "delegated management operation failed operation=%s application=%s "
                "invocation=%s",
                operation,
                application_id or "<deployment>",
                invocation_id,
            )
            response = self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="management_operation_failed",
                message="The KDCube management operation failed.",
                status_code=500,
                resource=effective_resource,
            )
            await self._settle(
                reservation=reservation,
                access_id=access_id,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                response=response,
                failed=True,
                resource=effective_resource,
            )
            return response

        response = ManagementResponse(
            200,
            management_success(
                operation=operation,
                resource=effective_resource,
                tenant=self._tenant,
                project=self._project,
                invocation_id=invocation_id,
                authority=authority,
                result=result,
            ),
        )
        ledger_response = response
        if operation == SECRET_READ_OPERATION:
            ledger_response = ManagementResponse(
                200,
                management_success(
                    operation=operation,
                    resource=effective_resource,
                    tenant=self._tenant,
                    project=self._project,
                    invocation_id=invocation_id,
                    authority=authority,
                    result={"disclosed": True, "replayable": False},
                ),
            )
        settled = await self._settle(
            reservation=reservation,
            access_id=access_id,
            operation=operation,
            invocation_id=invocation_id,
            request_digest=request_digest,
            response=ledger_response,
            resource=effective_resource,
        )
        if not settled:
            return self._error(
                operation=operation,
                invocation_id=invocation_id,
                code="effect_outcome_unrecorded",
                message="The management effect completed but its outcome was not recorded.",
                status_code=503,
                resource=effective_resource,
            )
        LOGGER.info(
            "delegated management completed operation=%s application=%s "
            "invocation=%s decision=%s caller=%s access_id=%s",
            operation,
            application_id or "<deployment>",
            invocation_id,
            authority.get("decision_id", ""),
            authority.get("caller_profile", ""),
            authority.get("access_id", ""),
        )
        return response

    async def _settle(
        self,
        *,
        reservation: Any,
        access_id: str,
        operation: str,
        invocation_id: str,
        request_digest: str,
        response: ManagementResponse,
        failed: bool = False,
        resource: str = "",
    ) -> bool:
        try:
            await self._ledger.finish(
                access_id=access_id,
                resource=resource or self._resource,
                operation=operation,
                invocation_id=invocation_id,
                request_digest=request_digest,
                owner=reservation.owner,
                status_code=response.status_code,
                response=response.payload,
                failed=failed,
            )
            return True
        except Exception:
            LOGGER.exception(
                "delegated management effect settlement failed operation=%s "
                "invocation=%s",
                operation,
                invocation_id,
            )
            return False


__all__ = [
    "AdmissionEvaluator",
    "DelegatedManagementService",
    "ManagementApplicationNotFound",
    "ManagementResponse",
    "ManagementRuntime",
    "ManagementRuntimeUnavailable",
    "SecretRuntime",
    "UserPropsRuntime",
]
