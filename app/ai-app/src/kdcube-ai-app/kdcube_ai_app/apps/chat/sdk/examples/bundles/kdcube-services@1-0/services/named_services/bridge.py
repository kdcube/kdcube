from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping, Sequence

from connection_hub.delegated_credentials.credential_view import (
    delegated_credential_view,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
    managed_named_service_admission,
    managed_named_service_catalog_operations,
    managed_named_service_dispatch_config,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
    get_current_user_identity,
)
from .request_scope import set_public_base_url_from_request
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceBoundaryCatalog,
    NamedServiceEndpoint,
    NamedServiceRequest,
    NamedServiceResponse,
    NamespaceBoundaryPolicy,
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


def _credential_grants_from_request(request: Any, *, required: Iterable[str] = ()) -> set[str]:
    view = delegated_credential_view(request)
    available = {str(grant).strip() for grant in view.grants if str(grant).strip()}
    required_grants = {str(grant).strip() for grant in required if str(grant).strip()}
    for provider, accounts in dict(view.account_scope or {}).items():
        provider_key = str(provider or "").strip()
        if not provider_key or not isinstance(accounts, Mapping):
            continue
        provider_prefix = f"{provider_key}:"
        for claims in dict(accounts).values():
            if not isinstance(claims, (list, tuple, set)):
                continue
            normalized_claims = {str(claim).strip() for claim in claims if str(claim).strip()}
            if "*" in normalized_claims:
                available.update(grant for grant in required_grants if grant.startswith(provider_prefix))
            available.update(claim for claim in normalized_claims if claim != "*")
    return available


def _denial_code(denial: Mapping[str, Any]) -> str:
    """Denials carry either a flat error string or a structured error object."""
    error = denial.get("error")
    if isinstance(error, Mapping):
        return str(error.get("code") or "")
    return str(error or "")


def _delegated_grant_record(request: Any) -> dict[str, Any]:
    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if not isinstance(delegated, Mapping):
        return {}
    grant_record = delegated.get("grant_record")
    return dict(grant_record or {}) if isinstance(grant_record, Mapping) else {}


def _credential_authority_id_from_request(request: Any) -> str:
    return delegated_credential_view(request).authority_id


def _credential_trace_context(request: Any) -> dict[str, Any]:
    view = delegated_credential_view(request)
    if not view.present:
        return {}
    return {
        "client_id": view.client_id,
        "authority_id": view.authority_id,
        "delegate_identity": view.subject,
        "grantor_user_id": view.grantor_user_id,
        "identity_scope": view.identity_scope,
        "resource": view.resource,
        "resources": list(view.resources),
        "grants": sorted(view.grants),
        "tools": list(view.tools),
        "grantor_roles": list(view.grantor_roles),
        "account_scope": _account_scope_summary(view.account_scope),
    }


def _account_scope_summary(scope: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for provider, accounts in dict(scope or {}).items():
        if not isinstance(accounts, Mapping):
            continue
        provider_key = str(provider or "").strip()
        if not provider_key:
            continue
        summary[provider_key] = {
            str(account_id): sorted(str(claim) for claim in (claims or ()) if str(claim or "").strip())
            for account_id, claims in dict(accounts).items()
            if str(account_id or "").strip()
        }
    return summary


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


def _narrow_public_services(
    services: list[dict[str, Any]], offered: Mapping[str, set[str]]
) -> list[dict[str, Any]]:
    """Drop operations the active catalog no longer offers, then the tools and
    namespaces left with nothing."""
    kept: list[dict[str, Any]] = []
    for namespace in services:
        name = str(namespace.get("namespace") or "")
        allowed = set(offered.get(name) or ())
        if not allowed:
            continue
        tools: dict[str, Any] = {}
        for tool_name, raw in dict(namespace.get("tools") or {}).items():
            tool = dict(raw) if isinstance(raw, Mapping) else {}
            nested = tool.get("operations")
            if isinstance(nested, Mapping) and nested:
                surviving = {
                    op: policy for op, policy in nested.items() if str(op) in allowed
                }
                if not surviving:
                    continue
                tool["operations"] = surviving
            elif str(tool.get("operation") or tool_name) not in allowed:
                continue
            tools[str(tool_name)] = tool
        if not tools:
            continue
        narrowed = dict(namespace)
        narrowed["tools"] = tools
        kept.append(narrowed)
    return kept


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
        # Capture the public origin the client connected to so downstream providers
        # can mint absolute out-of-band URLs (e.g. binary file downloads).
        set_public_base_url_from_request(request)
        view = delegated_credential_view(request)
        LOGGER.info(
            "[kdcube-services.named_services_mcp] credential projection present=%s client_id=%s resources=%s "
            "grants=%s account_scope=%s",
            view.present,
            view.client_id,
            list(view.resources),
            sorted(view.grants),
            _account_scope_summary(view.account_scope),
        )
        # Connection Hub materializes the selected namespace boundary on the
        # card. The active catalog independently narrows that boundary at each
        # invocation, preserving precise catalog-drift denials.
        try:
            catalog_config = managed_named_service_dispatch_config(request)
        except ValueError:
            # Keep the bridge usable in direct/non-managed SDK composition;
            # managed MCP requests always carry the guard snapshot.
            catalog_config = self._config
        # The guarded service decides which connector app serves each provider
        # in auth scenarios (never a user pick): bind its declaration so realm
        # integrations and consent composers resolve it for this request.
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connector_app_resolution import (
            set_service_connector_apps,
        )

        set_service_connector_apps(
            (catalog_config or {}).get("connector_apps")
            if isinstance(catalog_config, Mapping) else None
        )
        self._catalog = NamedServiceBoundaryCatalog(catalog_config)

    async def list_services(self) -> dict[str, Any]:
        # The listing is bounded by the card's materialized tree, which is what
        # the catalog offered when the card was saved. Execution intersects that
        # with the catalog as it is now, so without the same intersection here a
        # withdrawn operation stays advertised - and the removal denial's own
        # recovery, "refresh discovery", confirms the wrong answer.
        try:
            offered = managed_named_service_catalog_operations(self._request)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 503,
                "error": "named_service_admission_unavailable",
                "message": str(exc),
            }
        services = self._catalog.list_public()
        services = _narrow_public_services(services, offered)
        return {
            "ok": True,
            "services": services,
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

    async def _authorize(self, policy: NamespaceBoundaryPolicy, operation: str, tool_name: str) -> dict[str, Any] | None:
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
        elif not policy.operation_configured(tool_name=tool_name, operation=operation):
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
        available = _credential_grants_from_request(self._request, required=required)
        missing = sorted(required - available)
        if not missing:
            return None
        # Demand ordering: when this operation is account-backed and the
        # grantor has ZERO connected accounts on the backing provider, the
        # CONNECT demand leads (the guided plan ends in the agent-grant
        # hand-off) — granting an agent access to a provider with no accounts
        # binds nothing. Falls through to the gate-1 denial otherwise.
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.consent_denial import (
            agent_grant_consent_denial,
            connect_first_denial,
        )

        connect_first = await connect_first_denial(
            self._request,
            namespace=policy.namespace,
            tool=tool_name,
            operation=operation,
            required=sorted(required),
            missing=missing,
            tenant=self._tenant,
            project=self._project,
        )
        if connect_first is not None:
            return connect_first

        # The uniform per-agent grant denial every KDCube-served MCP surface
        # returns: exact missing grants; for a hosted-agent caller the full
        # consent block (agent identity, granted resource, one-click grant);
        # for external clients the reconnect guidance. One shared helper — the
        # bridge holds no consent-shape knowledge of its own.
        return agent_grant_consent_denial(
            self._request,
            namespace=policy.namespace,
            tool=tool_name,
            operation=operation,
            required=sorted(required),
            missing=missing,
            available=sorted(available),
            tenant=self._tenant,
            project=self._project,
            message=(
                f"Named service '{policy.namespace}' tool '{tool_name}' "
                "requires additional delegated consent."
            ),
        )

    async def call(
        self,
        *,
        tool_name: str,
        operation: str,
        namespace: str,
        provider: str = "",
        object_ref: str = "",
        object_kind: str = "",
        schema_path: str = "",
        schema_view: str = "",
        schema_operation: str = "",
        object_id: str = "",
        query: str = "",
        search_mode: str = "",
        limit: int | None = None,
        filters: Mapping[str, Any] | None = None,
        include: Sequence[Any] | None = None,
        object_payload: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        action: str = "",
        cursor: str = "",
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
            "[kdcube-services.named_services_mcp] start tool=%s operation=%s namespace=%s provider=%s query=%r object_ref=%s delegate=%s grantor=%s authority=%s identity_scope=%s grants=%s account_scope=%s runtime_user=%s runtime_type=%s runtime_authority=%s runtime_roles=%s",
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
            trace.get("account_scope") or {},
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

        action_name = str(action or "").strip()
        authorization_operation = (
            f"{OBJECT_ACTION}.{action_name}"
            if op == OBJECT_ACTION and action_name
            else op
        )
        denial = await self._authorize(
            policy, authorization_operation, tool_name=tool_name
        )
        if denial is not None:
            LOGGER.warning(
                "[kdcube-services.named_services_mcp] denied tool=%s operation=%s namespace=%s error=%s missing_grants=%s available_grants=%s delegate=%s grantor=%s",
                tool_name,
                op,
                ns,
                _denial_code(denial),
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
            object_kind=str(object_kind or "").strip() or None,
            schema_path=str(schema_path or "").strip() or None,
            schema_view=str(schema_view or "").strip() or None,
            schema_operation=str(schema_operation or "").strip() or None,
            object_id=str(object_id or "").strip() or None,
            query=str(query or "").strip() or None,
            search_mode=str(search_mode or "").strip().lower() or None,
            cursor=str(cursor or "").strip() or None,
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
            admission = managed_named_service_admission(self._request)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 503,
                "error": "named_service_admission_unavailable",
                "message": str(exc),
            }
        try:
            response = await call_named_service_endpoint(
                self._endpoint_for(policy, provider=provider),
                request,
                admission=admission,
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
        # The card may hold every claim yet not cover this operation; that denial
        # carries the same consent block as a missing claim so a chat can raise it.
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.consent_denial import (
            card_capability_consent_denial,
        )

        payload = dict(
            card_capability_consent_denial(
                self._request,
                payload,
                namespace=ns,
                tool=tool_name,
                operation=authorization_operation,
                tenant=self._tenant,
                project=self._project,
            )
        )
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
        object_ref: str = "",
        schema_path: str = "",
        schema_view: str = "",
        schema_operation: str = "",
        query: str = "",
        search_mode: str = "hybrid",
        limit: int = 10,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "object_kind": str(object_kind or "").strip(),
                "schema_path": str(schema_path or "").strip(),
                "schema_view": str(schema_view or "").strip(),
                "schema_operation": str(schema_operation or "").strip(),
                "query": str(query or "").strip(),
                "search_mode": (
                    str(search_mode or "hybrid").strip().lower() if query else ""
                ),
            }.items()
            if value
        }
        return await self.call(
            tool_name="schema",
            operation=OBJECT_SCHEMA,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            object_kind=object_kind,
            schema_path=schema_path,
            schema_view=schema_view,
            schema_operation=schema_operation,
            query=query,
            search_mode=(search_mode if query else ""),
            limit=max(1, min(int(limit or 10), 50)),
            payload=payload,
        )

    async def search(
        self,
        *,
        namespace: str,
        query: str = "",
        limit: int = 10,
        cursor: str = "",
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
            cursor=cursor,
            filters=_parse_json_object(filters_json, field_name="filters_json"),
        )

    async def get(
        self,
        *,
        namespace: str,
        object_ref: str,
        filters_json: Any = None,
        provider: str = "",
    ) -> dict[str, Any]:
        return await self.call(
            tool_name="get",
            operation=OBJECT_GET,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            filters=_parse_json_object(filters_json, field_name="filters_json"),
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
        cursor: str = "",
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
            cursor=cursor,
            filters=_parse_json_object(filters_json, field_name="filters_json"),
            include=_parse_json_list(include_json, field_name="include_json"),
            object_payload=_parse_json_object(object_json, field_name="object_json"),
            payload=_parse_json_object(payload_json, field_name="payload_json"),
            base_revision=base_revision,
            idempotency_key=idempotency_key,
        )


__all__ = ["NamedServicesMcpBridge"]
