# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube adapter for Connection Hub's per-invocation named-service authority."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from typing import Any, Mapping

from connection_hub.agent_account_scope import bind_agent_account_scope
from connection_hub.named_service_admission import (
    ADMISSION_MODE_APPLICATION,
    DELEGATED_CARD_BINDING_SCHEMA,
    MANAGED_ADMISSION_STATE_ATTR,
    ManagedNamedServiceAdmissionSnapshot,
    NamedServiceAdmissionEvaluation,
    NamedServiceAdmissionResolutionError,
    delegated_card_binding,
    evaluate_managed_named_service,
    evaluate_resolved_hub_state,
    managed_catalog_operations,
    managed_dispatch_config,
    native_agent_selector,
    snapshot_from_grant,
    validate_relay_selector,
)
from connection_hub.delegated_credentials.controls.attribution import (
    ResolvedCardComposition,
)

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
    NamedServiceAdmissionDecision,
    NamedServiceAdmissionSelector,
    effective_named_service_operation,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceError,
    NamedServiceRequest,
    NamedServiceResponse,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_scope import (
    bind_conversation_targets,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _denial_response(
    payload: Mapping[str, Any],
    *,
    default_status: int = 403,
) -> NamedServiceResponse:
    body = dict(payload or {})
    error = body.get("error")
    error = dict(error) if isinstance(error, Mapping) else {}
    details = error.get("details")
    details = dict(details) if isinstance(details, Mapping) else {}
    for key in ("where", "retryable"):
        if key in error:
            details.setdefault(key, error[key])
    if isinstance(body.get("consent"), Mapping):
        details.setdefault("consent", dict(body["consent"]))
    status = int(body.get("status") or details.get("status") or default_status)
    return NamedServiceResponse(
        ok=False,
        status=status,
        ret=(
            dict(body.get("ret") or {})
            if isinstance(body.get("ret"), Mapping)
            else {}
        ),
        error=NamedServiceError(
            code=_clean(error.get("code")) or "named_service_admission_denied",
            message=_clean(error.get("message"))
            or "Named-service admission was denied.",
            details={**details, "status": status},
            fix=(
                dict(error.get("fix") or {})
                if isinstance(error.get("fix"), Mapping)
                else {}
            ),
        ),
    )


@dataclass(frozen=True)
class DelegatedAccountExecutionScope:
    account_scope: Mapping[str, Any]
    client_id: str
    resource: str
    conversation_targets: tuple[str, ...] = ()

    @contextmanager
    def bind(self):
        with bind_agent_account_scope(
            self.account_scope,
            client_id=self.client_id,
            resource=self.resource,
        ), bind_conversation_targets(self.conversation_targets):
            yield


def _selector_mapping(selector: NamedServiceAdmissionSelector) -> dict[str, Any]:
    return selector.to_dict()


def _decision(
    evaluation: NamedServiceAdmissionEvaluation,
) -> NamedServiceAdmissionDecision:
    if not evaluation.allowed:
        return NamedServiceAdmissionDecision.deny(
            _denial_response(evaluation.denial or {}),
            audit=evaluation.audit,
        )
    scope = None
    if evaluation.client_id:
        scope = DelegatedAccountExecutionScope(
            account_scope=evaluation.account_scope,
            client_id=evaluation.client_id,
            resource=evaluation.resource,
            conversation_targets=evaluation.conversation_targets,
        )
    return NamedServiceAdmissionDecision.allow(
        execution_scope=scope,
        audit=evaluation.audit,
    )


class ManagedNamedServiceAdmissionAuthorizer:
    def __init__(self, snapshot: ManagedNamedServiceAdmissionSnapshot) -> None:
        self._snapshot = snapshot

    async def authorize(
        self, request: NamedServiceRequest
    ) -> NamedServiceAdmissionDecision:
        return _decision(
            evaluate_managed_named_service(
                self._snapshot,
                namespace=request.namespace or "",
                operation=effective_named_service_operation(request),
            )
        )


class _ResolvedHubStateAuthorizer:
    def __init__(
        self,
        *,
        selector: NamedServiceAdmissionSelector,
        state: Mapping[str, Any],
    ) -> None:
        self._selector = selector
        self._state = dict(state or {})

    async def authorize(
        self, request: NamedServiceRequest
    ) -> NamedServiceAdmissionDecision:
        del request
        return _decision(
            evaluate_resolved_hub_state(
                selector=_selector_mapping(self._selector),
                state=self._state,
            )
        )


class HubNamedServiceAdmissionAuthorizer:
    """Resolve current card/catalog authority through Connection Hub."""

    def __init__(self, selector: NamedServiceAdmissionSelector) -> None:
        self._selector = selector

    async def authorize(
        self, request: NamedServiceRequest
    ) -> NamedServiceAdmissionDecision:
        from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
            call_bundle_named_service,
        )
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
            DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        )
        from connection_hub.contract import (
            AGENT_GRANT_CHECK,
            NAMESPACE as CONNECTIONS_NAMESPACE,
        )

        selector = self._selector
        payload = {
            "client_id": selector.client_id,
            "namespace": request.namespace or "",
            "operation": effective_named_service_operation(request),
        }
        if selector.access_id:
            payload["access_id"] = selector.access_id
        if selector.delegate_identity:
            payload["delegate_identity"] = selector.delegate_identity
        try:
            result = await call_bundle_named_service(
                bundle_id=DEFAULT_CONNECTION_HUB_BUNDLE_ID,
                request={
                    "namespace": CONNECTIONS_NAMESPACE,
                    "operation": AGENT_GRANT_CHECK,
                    "payload": payload,
                },
            )
            value = getattr(result, "value", None)
            response = (
                NamedServiceResponse.coerce(value) if value is not None else None
            )
        except Exception:
            response = None
        state = (
            dict(response.object or {})
            if response is not None and response.ok
            else {"unavailable": "agent_grant_check_unavailable"}
        )
        return _decision(
            evaluate_resolved_hub_state(
                selector=_selector_mapping(selector),
                state=state,
            )
        )


def store_managed_named_service_admission_snapshot(
    request: Any,
    *,
    catalog: Any,
    grant_record: Mapping[str, Any],
    credential: Any,
    resource: str,
    request_resource: str,
    outer_operation: str = "",
    card_composition: ResolvedCardComposition | None = None,
) -> ManagedNamedServiceAdmissionSnapshot:
    snapshot = snapshot_from_grant(
        catalog=catalog,
        grant_record=grant_record,
        credential=credential,
        resource=resource,
        request_resource=request_resource,
        outer_operation=outer_operation,
        card_composition=card_composition,
    )
    setattr(request.state, MANAGED_ADMISSION_STATE_ATTR, snapshot)
    return snapshot


def _request_snapshot(request: Any) -> ManagedNamedServiceAdmissionSnapshot:
    snapshot = getattr(
        getattr(request, "state", None), MANAGED_ADMISSION_STATE_ATTR, None
    )
    if not isinstance(snapshot, ManagedNamedServiceAdmissionSnapshot):
        raise ValueError("Managed named-service admission snapshot is unavailable")
    return snapshot


def managed_named_service_admission(request: Any) -> NamedServiceAdmission:
    snapshot = _request_snapshot(request)
    return NamedServiceAdmission.delegated(
        selector=NamedServiceAdmissionSelector.from_mapping(snapshot.selector()),
        authorizer=ManagedNamedServiceAdmissionAuthorizer(snapshot),
    )


def managed_named_service_catalog_operations(
    request: Any,
) -> dict[str, set[str]]:
    return managed_catalog_operations(_request_snapshot(request))


def managed_named_service_dispatch_config(request: Any) -> dict[str, Any]:
    return managed_dispatch_config(_request_snapshot(request))


def delegated_card_binding_from_request(request: Any) -> dict[str, Any]:
    snapshot = getattr(
        getattr(request, "state", None), MANAGED_ADMISSION_STATE_ATTR, None
    )
    return delegated_card_binding(
        snapshot if isinstance(snapshot, ManagedNamedServiceAdmissionSnapshot) else None
    )


def delegated_resource_from_request(request: Any) -> str:
    """Return the canonical catalog resource admitted for this request."""
    snapshot = getattr(
        getattr(request, "state", None), MANAGED_ADMISSION_STATE_ATTR, None
    )
    if not isinstance(snapshot, ManagedNamedServiceAdmissionSnapshot):
        return ""
    return _clean(snapshot.resource)


def native_agent_admission_selector(
    *,
    source_bundle_id: str,
    source_agent_id: str,
    client_id: str,
    grantor_user_id: str,
) -> NamedServiceAdmissionSelector:
    return NamedServiceAdmissionSelector.from_mapping(
        native_agent_selector(
            source_bundle_id=source_bundle_id,
            source_agent_id=source_agent_id,
            client_id=client_id,
            grantor_user_id=grantor_user_id,
        )
    )


def native_agent_admission_from_state(
    *,
    selector: NamedServiceAdmissionSelector,
    state: Mapping[str, Any],
) -> NamedServiceAdmission:
    return NamedServiceAdmission.delegated(
        selector=selector,
        authorizer=_ResolvedHubStateAuthorizer(selector=selector, state=state),
    )


def admission_from_relay_selector(
    value: Mapping[str, Any],
    *,
    actor: Mapping[str, Any],
) -> NamedServiceAdmission:
    selector = NamedServiceAdmissionSelector.from_mapping(value)
    validate_relay_selector(_selector_mapping(selector), actor=actor)
    if selector.mode == ADMISSION_MODE_APPLICATION:
        return NamedServiceAdmission.application(source=selector.source)
    return NamedServiceAdmission.delegated(
        selector=selector,
        authorizer=HubNamedServiceAdmissionAuthorizer(selector),
    )


__all__ = [
    "DELEGATED_CARD_BINDING_SCHEMA",
    "DelegatedAccountExecutionScope",
    "HubNamedServiceAdmissionAuthorizer",
    "MANAGED_ADMISSION_STATE_ATTR",
    "ManagedNamedServiceAdmissionSnapshot",
    "NamedServiceAdmissionResolutionError",
    "admission_from_relay_selector",
    "delegated_card_binding_from_request",
    "delegated_resource_from_request",
    "managed_named_service_admission",
    "managed_named_service_catalog_operations",
    "managed_named_service_dispatch_config",
    "native_agent_admission_from_state",
    "native_agent_admission_selector",
    "store_managed_named_service_admission_snapshot",
]
