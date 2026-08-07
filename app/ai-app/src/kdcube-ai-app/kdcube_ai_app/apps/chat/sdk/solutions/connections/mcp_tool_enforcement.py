# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connected-account enforcement for PLAIN MCP tools.

A plain ``@mcp`` tool (an MCPServer tool on a managed bundle surface, with no
named-service registration behind it) declares which connected-account
provider claims each of its operations needs and enforces them at execution
time with ONE call. The declaration format is the existing application tool
shape parsed by :class:`ToolClaimPolicy.from_tool_config`::

    "my_tool": {
        "label": "...",
        "description": "...",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "slack", "claims": ["slack:search"]},
                ],
            },
        },
    }

``claims`` are the PROVIDER's own claim vocabulary - the claims a connected
account of that Delegated-to-KDCube provider row can actually hold
(``slack:search``, ``gmail:read``). The tool body then runs the check first::

    denial = await enforce_tool_requirements(
        request,
        tool_name="my_tool",
        operation="search",
        requirements=my_tool_requirements,
    )
    if denial is not None:
        return denial
    # proceed with the real provider work

The check resolves each required claim through the same account broker the
named-services door uses, so a plain MCP tool answers with the SAME demand
ordering and the SAME consent envelopes:

- every claim resolves -> ``None`` (proceed);
- the grantor has ZERO accounts on the backing provider -> the gate-2
  connect-first denial (``reason=connect_required`` with the guided connect
  plan and the agent hand-off);
- an account exists but cannot satisfy the call -> the account-level consent
  the resolver produced (``claim_upgrade_required``, ``agent_grant_required``,
  ``reconnect_required``, ``account_required``).

The worked reference is the ``productivity`` MCP surface of the
``kdcube-services@1-0`` example bundle
(``examples/bundles/kdcube-services@1-0/surfaces/mcp/productivity.py``).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
    resolve_connected_account_claim,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
    get_current_user_identity,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connector_app_resolution import (
    resolve_connector_app_id,
    set_service_connector_apps,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.consent_denial import (
    connect_first_denial_for_identity,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.credential_view import (
    delegated_credential_view,
)

logger = logging.getLogger(__name__)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def bind_service_connector_apps_from_config(config: Mapping[str, Any] | None) -> None:
    """Bind the surface's provider -> connector-app declaration for this request.

    A plain MCP surface declares which connector app serves each provider in
    its own surface config block (``connector_apps: {slack: slack-demo,
    google: gmail}``), exactly like a named-services bridge does in its
    ``named_services.connector_apps`` block. Call this once per tool call
    (contextvar binding is request-scoped) before any claim resolution. A
    missing/empty declaration clears to provider-wide matching."""
    mapping = None
    if isinstance(config, Mapping):
        raw = config.get("connector_apps")
        mapping = raw if isinstance(raw, Mapping) else None
    set_service_connector_apps(mapping)


def _resolution_source(request: Any) -> Mapping[str, Any]:
    """A resolution source for the shared connected-account resolver.

    The resolver's scope reader accepts any object/mapping that carries the
    tool-module binding fields. A pure MCP surface has no bound tool module,
    so this builds one on the fly: the user identity flows from the request
    context the platform bound for this call, and the registry carries a
    settings-backed Redis client so the broker can read the Connection Hub
    provider catalog (bundle props) from the shared runtime store."""
    registry: dict[str, Any] = {}
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.redis.client import get_async_redis_client

        registry["redis"] = get_async_redis_client(get_settings().REDIS_URL)
    except Exception:
        logger.debug("[mcp-tool-enforcement] settings redis unavailable", exc_info=True)
    ctx = get_current_request_context()
    if ctx is not None:
        registry["comm_context"] = ctx
    del request  # identity rides the bound request context, not the raw request
    return {"_TOOL_SUBSYSTEM": SimpleNamespace(registry=registry)}


def _operation_claims(requirement: Mapping[str, Any], operation: str) -> list[str]:
    """The provider claims ``operation`` needs under ``requirement``.

    ``claims_by_operation`` wins when it maps the operation; otherwise the
    requirement's flat ``claims`` list applies (a plain tool usually declares
    exactly the claims it needs, so the flat list IS the operation's need)."""
    op = _clean(operation)
    by_op = requirement.get("claims_by_operation")
    if isinstance(by_op, Mapping) and by_op:
        mapped = [_clean(c) for c in (by_op.get(op) or []) if _clean(c)]
        if mapped:
            return mapped
    return [_clean(c) for c in (requirement.get("claims") or []) if _clean(c)]


async def enforce_tool_requirements(
    request: Any,
    *,
    tool_name: str,
    operation: str,
    requirements: Sequence[Mapping[str, Any]],
    account_id: str = "",
    tenant: str = "",
    project: str = "",
    hub_bundle_id: str = "connection-hub@1-0",
) -> dict[str, Any] | None:
    """Enforce a plain MCP tool's declared connected-account requirements.

    ``requirements`` is the tool's ``connected_accounts`` declaration - each
    mapping ``{provider_id, claims, claims_by_operation?, connector_app_id?}``
    (the :class:`ToolClaimRequirement` shape). For each requirement the
    operation's needed claims are resolved through the shared account broker
    under the calling user's identity and the calling client's per-account
    binding (default-closed for delegated callers).

    ``account_id`` is the account selector from the tool's own input. Passing
    it here keeps preflight and the provider body on the same resolution: an
    ambiguous call returns ``account_required``; resending that same call with
    one candidate id resolves before provider work begins.

    Returns ``None`` when every claim resolves for that account - the tool body
    proceeds.

    On the FIRST unsatisfied provider the return value is a consent envelope
    (a dict the tool returns as its MCP result; the shared chat post-processor
    renders it as the consent banner):

    1. the connect-first denial when the grantor has ZERO usable accounts on
       that provider (``reason=connect_required``; the guided plan ends in the
       agent-grant hand-off) - computed via
       :func:`connect_first_denial_for_identity` with THIS requirement passed
       explicitly, no discovery involved;
    2. otherwise the account-level consent the resolver already produced
       (``claim_upgrade_required`` / ``agent_grant_required`` /
       ``reconnect_required`` / ``account_required``).

    This is the same demand ordering the named-services door applies.

    ``tenant``/``project`` default to the bound request identity. The caller
    must have bound the surface's connector-app declaration first
    (:func:`bind_service_connector_apps_from_config`)."""
    identity = get_current_user_identity() or {}
    tenant = _clean(tenant) or _clean(identity.get("tenant_id"))
    project = _clean(project) or _clean(identity.get("project_id"))
    source = _resolution_source(request)
    op = _clean(operation)
    name = _clean(tool_name)

    for raw in requirements or ():
        if not isinstance(raw, Mapping):
            continue
        requirement = dict(raw)
        provider_id = _clean(requirement.get("provider_id"))
        if not provider_id:
            continue
        claims = _operation_claims(requirement, op)
        if not claims:
            continue
        connector_app_id = (
            _clean(requirement.get("connector_app_id"))
            or resolve_connector_app_id(provider_id)
        )
        failed: ConnectedAccountCredential | None = None
        for claim in claims:
            credential = await resolve_connected_account_claim(
                source,
                provider_id=provider_id,
                connector_app_id=connector_app_id,
                claim=claim,
                tool_name=name,
                account_id=_clean(account_id),
                connection_hub_bundle_id=hub_bundle_id,
            )
            if not credential.ok:
                failed = credential
                break
        if failed is None:
            continue
        # Demand ordering, identical to the named-services door: with ZERO
        # usable accounts on the backing provider the CONNECT demand leads
        # (the guided plan ends in the agent-grant hand-off). The requirement
        # is passed explicitly - no named-service discovery behind a plain
        # MCP tool.
        view = delegated_credential_view(request)
        grantor = _clean(view.grantor_user_id) or _clean(identity.get("user_id"))
        try:
            denial = await connect_first_denial_for_identity(
                grantor_user_id=grantor,
                agent_client_id=view.agent_client_id,
                agent_resource=view.resource,
                namespace=name,
                tool=name,
                operation=op,
                required=claims,
                missing=claims,
                tenant=tenant,
                project=project,
                hub_bundle_id=hub_bundle_id,
                requirements=[requirement],
            )
        except Exception:
            logger.warning(
                "[mcp-tool-enforcement] connect-first shaping failed (tool=%s provider=%s)",
                name, provider_id, exc_info=True,
            )
            denial = None
        if denial is not None:
            logger.info(
                "[mcp-tool-enforcement] connect leads (tool=%s operation=%s provider=%s)",
                name, op, provider_id,
            )
            return denial
        logger.info(
            "[mcp-tool-enforcement] account consent (tool=%s operation=%s provider=%s claim=%s)",
            name, op, provider_id, failed.claim,
        )
        return failed.error_envelope(where=name)
    return None


__all__ = [
    "bind_service_connector_apps_from_config",
    "enforce_tool_requirements",
]
