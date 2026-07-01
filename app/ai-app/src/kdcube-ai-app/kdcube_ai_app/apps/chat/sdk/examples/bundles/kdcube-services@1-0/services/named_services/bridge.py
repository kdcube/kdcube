from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import CredentialEnvelope
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
    get_current_user_identity,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceBoundaryCatalog,
    NamedServiceEndpoint,
    NamedServiceRequest,
    NamedServiceResponse,
    NamespaceBoundaryPolicy,
    as_list,
    call_named_service_endpoint,
    clean_namespace,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_HOST_FILE,
    OBJECT_LIST,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
    PROVIDER_OPERATION,
)


EXPOSED_OPERATIONS = (
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_GET,
    OBJECT_HOST_FILE,
    OBJECT_SCHEMA,
    OBJECT_UPSERT,
    OBJECT_DELETE,
    OBJECT_ACTION,
)
LOGGER = logging.getLogger("kdcube.kdcube_services.named_services_mcp")


def _parse_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"{field_name} must be a JSON object")


def _parse_json_list(value: Any, *, field_name: str) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return list(parsed)
    raise ValueError(f"{field_name} must be a JSON list")


def _response_payload(response: NamedServiceResponse) -> dict[str, Any]:
    payload = response.to_dict()
    payload["status"] = int(response.status or (200 if response.ok else 400))
    return payload


def _result_count(payload: Mapping[str, Any]) -> int | None:
    for key in ("items", "objects", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    attrs = payload.get("attrs")
    if isinstance(attrs, Mapping):
        for key in ("items", "objects", "results"):
            value = attrs.get(key)
            if isinstance(value, list):
                return len(value)
        count = attrs.get("count")
        if isinstance(count, int):
            return count
    count = payload.get("count")
    return count if isinstance(count, int) else None


def _credential_grants_from_request(request: Any) -> set[str]:
    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if not isinstance(delegated, Mapping):
        return set()

    grants: set[str] = set()
    credential = delegated.get("credential")
    if isinstance(credential, Mapping):
        envelope = CredentialEnvelope.coerce(credential)
        attrs = envelope.attrs or {}
        grants.update(as_list(attrs.get("scopes")))
        grants.update(as_list(attrs.get("scope")))
        grants.update(as_list(attrs.get("grants")))

    grant_record = delegated.get("grant_record")
    if isinstance(grant_record, Mapping):
        grants.update(as_list(grant_record.get("scopes")))
        grants.update(as_list(grant_record.get("scope")))
        grants.update(as_list(grant_record.get("grants")))
        record_credential = grant_record.get("credential")
        if isinstance(record_credential, Mapping):
            envelope = CredentialEnvelope.coerce(record_credential)
            attrs = envelope.attrs or {}
            grants.update(as_list(attrs.get("scopes")))
            grants.update(as_list(attrs.get("scope")))
            grants.update(as_list(attrs.get("grants")))

    return {item for item in grants if item}


def _delegated_grant_record(request: Any) -> dict[str, Any]:
    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if not isinstance(delegated, Mapping):
        return {}
    grant_record = delegated.get("grant_record")
    return dict(grant_record or {}) if isinstance(grant_record, Mapping) else {}


def _named_service_catalog_config_from_request(request: Any) -> dict[str, Any]:
    grant_record = _delegated_grant_record(request)
    raw = grant_record.get("named_services")
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _credential_authority_id_from_request(request: Any) -> str:
    grant_record = _delegated_grant_record(request)
    for raw in (grant_record.get("credential"),):
        if isinstance(raw, Mapping):
            authority_id = CredentialEnvelope.coerce(raw).issuer_authority_id
            if authority_id:
                return authority_id
    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if isinstance(delegated, Mapping):
        raw = delegated.get("credential")
        if isinstance(raw, Mapping):
            authority_id = CredentialEnvelope.coerce(raw).issuer_authority_id
            if authority_id:
                return authority_id
    return ""


def _credential_trace_context(request: Any) -> dict[str, Any]:
    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if not isinstance(delegated, Mapping):
        return {}
    grant_record = delegated.get("grant_record")
    grant_record = grant_record if isinstance(grant_record, Mapping) else {}
    raw_credential = delegated.get("credential")
    envelope = CredentialEnvelope.coerce(raw_credential) if isinstance(raw_credential, Mapping) else CredentialEnvelope()
    attrs = envelope.attrs or {}
    grantor_authority = grant_record.get("grantor_authority")
    grantor_authority = grantor_authority if isinstance(grantor_authority, Mapping) else {}
    return {
        "authority_id": envelope.issuer_authority_id,
        "delegate_identity": envelope.subject,
        "grantor_user_id": attrs.get("grantor_subject") or attrs.get("grantor_user_id") or "",
        "identity_scope": attrs.get("identity_scope") or grant_record.get("identity_scope") or "",
        "resource": attrs.get("resource") or "",
        "grants": sorted(_credential_grants_from_request(request)),
        "tools": list(grant_record.get("tools") or []),
        "grantor_roles": list(grantor_authority.get("grantor_roles") or []),
    }


def _runtime_trace_context() -> dict[str, Any]:
    identity = get_current_user_identity()
    ctx = get_current_request_context()
    user = getattr(ctx, "user", None) if ctx is not None else None
    authority = getattr(user, "identity_authority", None)
    authority = authority if isinstance(authority, Mapping) else {}
    return {
        "runtime_user_id": str(identity.get("user_id") or ""),
        "runtime_user_type": str(identity.get("user_type") or ""),
        "runtime_roles": list(identity.get("roles") or []),
        "runtime_permissions": list(identity.get("permissions") or []),
        "runtime_authority_id": str(authority.get("authority_id") or authority.get("issuer_authority_id") or ""),
        "runtime_authority_present": bool(authority),
    }


class NamedServicesMcpBridge:
    """MCP-facing adapter for configured KDCube named-service namespaces."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        tenant: str,
        project: str,
        request: Any,
    ):
        self._config = dict(config or {})
        self._tenant = str(tenant or "")
        self._project = str(project or "")
        self._request = request
        self._catalog = NamedServiceBoundaryCatalog(
            _named_service_catalog_config_from_request(request) or self._config
        )

    def list_services(self) -> dict[str, Any]:
        return {
            "ok": True,
            "services": self._catalog.list_public(),
            "note": (
                "This MCP surface exposes configured named-service namespaces. "
                "Each namespace operation may require additional delegated grants."
            ),
        }

    def _endpoint_for(self, policy: NamespaceBoundaryPolicy, provider: str = "") -> NamedServiceEndpoint:
        endpoint = (
            NamedServiceEndpoint.from_provider_configs(
                list(policy.provider_configs),
                namespace=policy.namespace,
                tenant=self._tenant,
                project=self._project,
            )
            if policy.provider_configs
            else NamedServiceEndpoint(
                namespace=policy.namespace,
                provider=str(provider or "").strip() or None,
                tenant=self._tenant,
                project=self._project,
            )
        )
        if provider and not endpoint.provider_configs:
            endpoint = NamedServiceEndpoint(
                namespace=policy.namespace,
                provider=str(provider or "").strip(),
                tenant=self._tenant,
                project=self._project,
            )
        return endpoint

    def _authorize(self, policy: NamespaceBoundaryPolicy, operation: str, tool_name: str) -> dict[str, Any] | None:
        if not policy.tool_configured(tool_name):
            return {
                "ok": False,
                "error": "named_service_tool_not_configured",
                "message": (
                    f"Named service '{policy.namespace}' does not configure boundary "
                    f"policy for tool '{tool_name}'."
                ),
                "namespace": policy.namespace,
                "tool": tool_name,
                "operation": operation,
            }
        if not policy.operation_configured(tool_name=tool_name, operation=operation):
            return {
                "ok": False,
                "error": "named_service_operation_not_configured",
                "message": (
                    f"Named service '{policy.namespace}' tool '{tool_name}' does not "
                    f"allow operation '{operation}'."
                ),
                "namespace": policy.namespace,
                "tool": tool_name,
                "operation": operation,
            }
        required = set(policy.grants_for(tool_name=tool_name, operation=operation))
        required_authority = policy.authority_for(tool_name=tool_name, operation=operation)
        credential_authority = _credential_authority_id_from_request(self._request)
        if required_authority and credential_authority != required_authority:
            return {
                "ok": False,
                "error": "delegated_authority_required",
                "message": (
                    f"Named service '{policy.namespace}' tool '{tool_name}' "
                    f"requires authority '{required_authority}'."
                ),
                "namespace": policy.namespace,
                "tool": tool_name,
                "operation": operation,
                "required_authority_id": required_authority,
                "credential_authority_id": credential_authority,
            }
        if not required:
            return None
        available = _credential_grants_from_request(self._request)
        missing = sorted(required - available)
        if not missing:
            return None
        return {
            "ok": False,
            "error": "delegated_consent_required",
            "message": (
                f"Named service '{policy.namespace}' tool '{tool_name}' "
                "requires additional delegated consent."
            ),
            "namespace": policy.namespace,
            "tool": tool_name,
            "operation": operation,
            "required_grants": sorted(required),
            "missing_grants": missing,
            "available_grants": sorted(available),
            "next_step": (
                "Reconnect this MCP resource and approve the missing grant if the "
                "client supports incremental consent. Otherwise connect a resource "
                "whose initial consent includes this grant."
            ),
        }

    async def call(
        self,
        *,
        tool_name: str,
        operation: str,
        namespace: str,
        provider: str = "",
        object_ref: str = "",
        object_id: str = "",
        query: str = "",
        limit: int | None = None,
        filters: Mapping[str, Any] | None = None,
        include: Sequence[Any] | None = None,
        object_payload: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        action: str = "",
        base_revision: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        op = str(operation or "").strip()
        ns = clean_namespace(namespace)
        if not op:
            return {"ok": False, "error": "operation_required", "message": "operation is required"}
        if not ns:
            return {"ok": False, "error": "namespace_required", "message": "namespace is required"}
        if op not in EXPOSED_OPERATIONS and op != PROVIDER_OPERATION:
            return {
                "ok": False,
                "error": "operation_not_exposed",
                "message": f"operation '{op}' is not exposed by this MCP bridge",
                "allowed_operations": list(EXPOSED_OPERATIONS),
            }

        # Log EVERY inbound call attempt (including unknown/unconsented namespaces),
        # before any authorization decision, so denials are always traceable.
        trace = _credential_trace_context(self._request)
        runtime_trace = _runtime_trace_context()
        LOGGER.info(
            "[kdcube-services.named_services_mcp] start tool=%s operation=%s namespace=%s provider=%s query=%r object_ref=%s delegate=%s grantor=%s authority=%s identity_scope=%s grants=%s runtime_user=%s runtime_type=%s runtime_authority=%s runtime_roles=%s",
            tool_name,
            op,
            ns,
            provider,
            str(query or "").strip(),
            str(object_ref or "").strip(),
            trace.get("delegate_identity") or "",
            trace.get("grantor_user_id") or "",
            trace.get("authority_id") or "",
            trace.get("identity_scope") or "",
            trace.get("grants") or [],
            runtime_trace.get("runtime_user_id") or "",
            runtime_trace.get("runtime_user_type") or "",
            runtime_trace.get("runtime_authority_id") or "",
            runtime_trace.get("runtime_roles") or [],
        )

        policy = self._catalog.policy_for(ns)
        if policy is None:
            configured = self._catalog.namespace_names()
            LOGGER.warning(
                "[kdcube-services.named_services_mcp] denied tool=%s operation=%s namespace=%s error=namespace_not_configured configured_namespaces=%s delegate=%s grantor=%s",
                tool_name,
                op,
                ns,
                configured,
                trace.get("delegate_identity") or "",
                trace.get("grantor_user_id") or "",
            )
            return {
                "ok": False,
                "error": "namespace_not_configured",
                "message": f"namespace '{ns}' is not configured on this MCP surface",
                "configured_namespaces": configured,
                "next_step": (
                    "This namespace is not part of the current delegated consent. If it "
                    "exists, reconnect this MCP resource and approve it during consent, "
                    "then retry."
                ),
            }

        denial = self._authorize(policy, op, tool_name=tool_name)
        if denial is not None:
            LOGGER.warning(
                "[kdcube-services.named_services_mcp] denied tool=%s operation=%s namespace=%s error=%s missing_grants=%s available_grants=%s delegate=%s grantor=%s",
                tool_name,
                op,
                ns,
                denial.get("error") or "",
                denial.get("missing_grants") or [],
                denial.get("available_grants") or [],
                trace.get("delegate_identity") or "",
                trace.get("grantor_user_id") or "",
            )
            return denial

        request = NamedServiceRequest(
            operation=op,
            provider=str(provider or "").strip() or None,
            namespace=ns,
            object_ref=str(object_ref or "").strip() or None,
            object_id=str(object_id or "").strip() or None,
            query=str(query or "").strip() or None,
            limit=int(limit) if limit not in (None, "") else None,
            filters=dict(filters or {}),
            include=list(include or []),
            action=str(action or "").strip() or None,
            object=dict(object_payload or {}),
            payload=dict(payload or {}),
            base_revision=str(base_revision or "").strip() or None,
            idempotency_key=str(idempotency_key or "").strip() or None,
        )
        try:
            response = await call_named_service_endpoint(
                self._endpoint_for(policy, provider=provider),
                request,
            )
        except Exception:
            LOGGER.exception(
                "[kdcube-services.named_services_mcp] failed tool=%s operation=%s namespace=%s provider=%s",
                tool_name,
                op,
                ns,
                provider,
            )
            raise
        payload = _response_payload(response)
        LOGGER.info(
            "[kdcube-services.named_services_mcp] complete tool=%s operation=%s namespace=%s ok=%s status=%s error=%s count=%s",
            tool_name,
            op,
            ns,
            payload.get("ok"),
            payload.get("status"),
            payload.get("error") or payload.get("code") or "",
            _result_count(payload),
        )
        return payload

    async def about(self, *, namespace: str, provider: str = "") -> dict[str, Any]:
        return await self.call(tool_name="about", operation=PROVIDER_ABOUT, namespace=namespace, provider=provider)

    async def capabilities(self, *, namespace: str, provider: str = "") -> dict[str, Any]:
        return await self.call(
            tool_name="capabilities",
            operation=PROVIDER_CAPABILITIES,
            namespace=namespace,
            provider=provider,
        )

    async def schema(
        self,
        *,
        namespace: str,
        provider: str = "",
        object_kind: str = "",
    ) -> dict[str, Any]:
        payload = {"object_kind": str(object_kind or "").strip()} if object_kind else {}
        return await self.call(
            tool_name="schema",
            operation=OBJECT_SCHEMA,
            namespace=namespace,
            provider=provider,
            payload=payload,
        )

    async def search(
        self,
        *,
        namespace: str,
        query: str = "",
        limit: int = 10,
        filters_json: Any = None,
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="search",
            operation=OBJECT_SEARCH,
            namespace=namespace,
            provider=provider,
            query=query,
            limit=limit,
            filters=_parse_json_object(filters_json, field_name="filters_json"),
        )

    async def get(
        self,
        *,
        namespace: str,
        object_ref: str,
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="get",
            operation=OBJECT_GET,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
        )

    async def upsert(
        self,
        *,
        namespace: str,
        object_json: Any,
        object_ref: str = "",
        object_id: str = "",
        base_revision: str = "",
        idempotency_key: str = "",
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="upsert",
            operation=OBJECT_UPSERT,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            object_id=object_id,
            object_payload=_parse_json_object(object_json, field_name="object_json"),
            base_revision=base_revision,
            idempotency_key=idempotency_key,
        )

    async def host_file(
        self,
        *,
        namespace: str,
        file_ref: str,
        object_ref: str = "",
        object_id: str = "",
        filename: str = "",
        mime: str = "",
        description: str = "",
        payload_json: Any = None,
        provider: str = "",
    ) -> dict[str, Any]:
        payload = _parse_json_object(payload_json, field_name="payload_json")
        payload["file"] = {
            "ref": str(file_ref or "").strip(),
            "filename": str(filename or "").strip(),
            "mime": str(mime or "").strip(),
            "description": str(description or "").strip(),
        }
        return await self.call(
            tool_name="host_file",
            operation=OBJECT_HOST_FILE,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            object_id=object_id,
            payload=payload,
        )

    async def object_action(
        self,
        *,
        namespace: str,
        object_ref: str,
        action: str = "preview",
        payload_json: Any = None,
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="action",
            operation=OBJECT_ACTION,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            action=action or "preview",
            payload=_parse_json_object(payload_json, field_name="payload_json"),
        )

    async def delete(
        self,
        *,
        namespace: str,
        object_ref: str,
        base_revision: str = "",
        payload_json: Any = None,
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="delete",
            operation=OBJECT_DELETE,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            base_revision=base_revision,
            payload=_parse_json_object(payload_json, field_name="payload_json"),
        )

    async def generic_call(
        self,
        *,
        operation: str,
        namespace: str,
        provider: str = "",
        object_ref: str = "",
        object_id: str = "",
        query: str = "",
        action: str = "",
        limit: int = 0,
        filters_json: Any = None,
        include_json: Any = None,
        object_json: Any = None,
        payload_json: Any = None,
        base_revision: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="call",
            operation=operation,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            object_id=object_id,
            query=query,
            action=action,
            limit=limit or None,
            filters=_parse_json_object(filters_json, field_name="filters_json"),
            include=_parse_json_list(include_json, field_name="include_json"),
            object_payload=_parse_json_object(object_json, field_name="object_json"),
            payload=_parse_json_object(payload_json, field_name="payload_json"),
            base_revision=base_revision,
            idempotency_key=idempotency_key,
        )


__all__ = ["NamedServicesMcpBridge"]
