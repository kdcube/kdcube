# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Framework-neutral runtime bindings for resolved delegated Gateway tools."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources.models import (
    AuthoritySource,
    AvailabilityReason,
    EffectiveResidentInventory,
    ResidentToolDescriptor,
    ResourceBinding,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig

_CARD_UNBINDABLE = frozenset(
    {
        AvailabilityReason.CARD_MISSING,
        AvailabilityReason.CARD_AUTHORITY_UNAVAILABLE,
        AvailabilityReason.CARD_REVOKED,
        AvailabilityReason.CARD_EXPIRED,
        AvailabilityReason.SCOPE_MISMATCH,
        AvailabilityReason.DUPLICATE_CARD_SCOPE,
    }
)
_TRUSTED_PROCESS_AUTH_TYPE = "trusted_process"

GatewayBearerProvider = Callable[
    [Mapping[str, Any], str], Awaitable[str | None]
]


class ResidentRuntimeBindingError(ValueError):
    """The effective inventory cannot produce an unambiguous runtime binding."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def delegated_mcp_bindings_from_catalog(
    catalog: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return exact resource, server, and alias ids governed by a Card.

    Application-authority MCP rows stay on their direct runtime path. Rows
    explicitly marked ``delegated_card`` are removed from that path and
    exposed only through the live aggregate Gateway.
    """

    resources: set[str] = set()
    servers: set[str] = set()
    aliases: set[str] = set()
    rows = catalog.get("mcp") if isinstance(catalog, Mapping) else None
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("authority_source") or "").strip() != "delegated_card":
            continue
        resource = str(row.get("resource_id") or "").strip()
        server = str(row.get("server_id") or "").strip()
        alias = str(row.get("alias") or "").strip()
        if resource:
            resources.add(resource)
        if server:
            servers.add(server)
        if alias:
            aliases.add(alias)
    return tuple(sorted(resources)), tuple(sorted(servers)), tuple(sorted(aliases))


def remove_direct_delegated_mcp_bindings(
    config: AgentToolConfig,
    *,
    server_ids: Iterable[str],
    aliases: Iterable[str],
) -> AgentToolConfig:
    """Remove Card-governed MCP rows from the uncredentialed direct path."""

    if not isinstance(config, AgentToolConfig):
        raise ResidentRuntimeBindingError("agent_tool_config_invalid")
    servers = {str(value or "").strip() for value in server_ids}
    delegated_aliases = {str(value or "").strip() for value in aliases}
    servers.discard("")
    delegated_aliases.discard("")

    def _is_delegated_tool_id(tool_id: Any) -> bool:
        value = str(tool_id or "").strip()
        return any(
            value == alias or value.startswith(f"mcp.{alias}.")
            for alias in delegated_aliases
        )

    return AgentToolConfig(
        tool_specs=[dict(spec) for spec in config.tool_specs],
        mcp_tool_specs=[
            dict(spec)
            for spec in config.mcp_tool_specs
            if not (
                isinstance(spec, Mapping)
                and str(spec.get("server_id") or "").strip() in servers
            )
        ],
        tool_runtime={
            key: value
            for key, value in config.tool_runtime.items()
            if not _is_delegated_tool_id(key)
        },
        tool_traits={
            key: dict(value)
            for key, value in config.tool_traits.items()
            if not _is_delegated_tool_id(key)
        },
        allowed_plugins=[
            alias for alias in config.allowed_plugins if alias not in delegated_aliases
        ],
        allowed_tool_names_by_alias={
            alias: None if names is None else list(names)
            for alias, names in config.allowed_tool_names_by_alias.items()
            if alias not in delegated_aliases
        },
        tool_claim_policies=[
            policy
            for policy in config.tool_claim_policies
            if not _is_delegated_tool_id(getattr(policy, "tool_name", ""))
        ],
    )


@dataclass(frozen=True)
class GatewayRuntimeConnection:
    """One card-bound Gateway connection shared by all its resource rows."""

    server_id: str
    alias: str
    transport: str
    endpoint: str
    access_id: str
    card_revision: int
    identity_scope: str
    resource_ids: tuple[str, ...]
    tool_names: tuple[str, ...]

    def to_connection_dict(self) -> dict[str, Any]:
        """Return descriptor-like public metadata for the existing MCP resolver."""

        return {
            "kind": "mcp",
            "server_id": self.server_id,
            "alias": self.alias,
            "url": self.endpoint,
            "transport": self.transport,
            "delegated": True,
            # Existing consented-bearer lookup resolves the stable resident
            # card from a resource it holds. All ids are retained for a host
            # adapter that can bind the exact access_id directly.
            "resource": self.resource_ids[0],
            "resources": list(self.resource_ids),
            "access_id": self.access_id,
            "card_revision": self.card_revision,
            "identity_scope": self.identity_scope,
            "allowed": list(self.tool_names),
        }

    def to_mcp_tool_spec(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "alias": self.alias,
            "tools": list(self.tool_names),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "alias": self.alias,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "access_id": self.access_id,
            "card_revision": self.card_revision,
            "identity_scope": self.identity_scope,
            "resource_ids": list(self.resource_ids),
            "tool_names": list(self.tool_names),
        }


class GatewayRuntimeHeadersProvider:
    """Resolve one card-bound bearer immediately before a Gateway request.

    The provider retains only public connection coordinates and the host
    callback. A bearer exists only as a local value while the MCP transport is
    being opened; it never enters descriptor-derived service configuration or
    serializable runtime globals.
    """

    __slots__ = ("_bearer_provider", "_connections", "_user_subject", "_turn_token_provider")

    def __init__(
        self,
        connections: Iterable[GatewayRuntimeConnection],
        *,
        user_subject: str,
        bearer_provider: GatewayBearerProvider,
        turn_token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        subject = str(user_subject or "").strip()
        if not subject or not callable(bearer_provider):
            raise ResidentRuntimeBindingError(
                "gateway_runtime_authorization_binding_invalid"
            )
        rows = tuple(connections)
        self._connections = _connection_index(rows)
        self._user_subject = subject
        self._bearer_provider = bearer_provider
        # W40: the turn token naming this conversation, renewed by its provider.
        self._turn_token_provider = turn_token_provider

    def __repr__(self) -> str:
        return (
            "GatewayRuntimeHeadersProvider("
            f"server_ids={tuple(sorted(self._connections))!r})"
        )

    async def __call__(self, server_id: str) -> Mapping[str, str]:
        connection = self._connections.get(str(server_id or "").strip())
        if connection is None:
            raise RuntimeError("gateway_runtime_authorization_unavailable")
        try:
            token = str(
                await self._bearer_provider(
                    connection.to_connection_dict(),
                    self._user_subject,
                )
                or ""
            ).strip()
            if (
                not token
                or len(token) > 16_384
                or "\r" in token
                or "\n" in token
            ):
                raise ValueError
        except Exception:
            raise RuntimeError(
                "gateway_runtime_authorization_unavailable"
            ) from None
        headers = {"Authorization": f"Bearer {token}"}
        if self._turn_token_provider is not None:
            try:
                turn_token = str(await self._turn_token_provider() or "").strip()
            except Exception:
                turn_token = ""
            if not turn_token or len(turn_token) > 4096 or "\r" in turn_token or "\n" in turn_token:
                # Without it the guard would apply the Card's defaults, not this
                # conversation's choice: refuse the call instead.
                raise RuntimeError("gateway_runtime_turn_token_unavailable")
            headers[TURN_TOKEN_HEADER] = turn_token
        return headers


# W40: the header the delegated guard reads (surface_guard.TURN_TOKEN_HEADER).
TURN_TOKEN_HEADER = "x-connection-hub-turn"


@dataclass(frozen=True)
class GatewayRuntimePlan:
    """One effective Gateway observation translated for every runtime."""

    connections: tuple[GatewayRuntimeConnection, ...]
    _index: Mapping[str, GatewayRuntimeConnection] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        rows = tuple(self.connections)
        object.__setattr__(self, "connections", rows)
        object.__setattr__(self, "_index", _connection_index(rows))

    def native_tool_config(self, config: AgentToolConfig) -> AgentToolConfig:
        return apply_gateway_runtime_connections(config, self.connections)

    def native_services_config(self, configured: Any = None) -> dict[str, Any]:
        return merge_gateway_services_config(configured, self.connections)

    def connection_descriptors(self) -> list[dict[str, Any]]:
        return gateway_connection_descriptors(self.connections)

    def tool_overrides(self) -> dict[str, tuple[str, ...]]:
        return gateway_tool_overrides(self.connections)

    def auth_headers_provider(
        self,
        *,
        user_subject: str,
        bearer_provider: GatewayBearerProvider,
        turn_token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> GatewayRuntimeHeadersProvider:
        return GatewayRuntimeHeadersProvider(
            self.connections,
            user_subject=user_subject,
            bearer_provider=bearer_provider,
            turn_token_provider=turn_token_provider,
        )


@dataclass
class _ConnectionAccumulator:
    binding: ResourceBinding
    access_id: str
    card_revision: int
    identity_scope: str
    resources: set[str]
    tools: set[str]


def _connection_index(
    connections: Iterable[GatewayRuntimeConnection],
) -> dict[str, GatewayRuntimeConnection]:
    result: dict[str, GatewayRuntimeConnection] = {}
    aliases: set[str] = set()
    for connection in connections:
        if not isinstance(connection, GatewayRuntimeConnection):
            raise ResidentRuntimeBindingError("gateway_runtime_connection_invalid")
        if not all(
            str(value or "").strip()
            for value in (
                connection.server_id,
                connection.alias,
                connection.transport,
                connection.endpoint,
                connection.access_id,
            )
        ):
            raise ResidentRuntimeBindingError("gateway_runtime_binding_incomplete")
        if connection.server_id in result:
            raise ResidentRuntimeBindingError("gateway_runtime_server_id_collision")
        if connection.alias in aliases:
            raise ResidentRuntimeBindingError("gateway_runtime_alias_collision")
        if not connection.resource_ids or not connection.tool_names:
            raise ResidentRuntimeBindingError("gateway_runtime_binding_empty")
        result[connection.server_id] = connection
        aliases.add(connection.alias)
    return result


def gateway_runtime_connections(
    inventory: EffectiveResidentInventory,
    *,
    meta_tools_by_access_id: Mapping[str, Iterable[ResidentToolDescriptor]] | None = None,
) -> tuple[GatewayRuntimeConnection, ...]:
    """Collapse one resident Card's resource rows into one Gateway connection.

    Only effective model-facing operations enter ``tool_names``. Unavailable
    rows can retain the caller-self meta-tool for precise recovery, while a
    missing, revoked, expired, or scope-mismatched card creates no connection.
    """

    if not isinstance(inventory, EffectiveResidentInventory):
        raise ResidentRuntimeBindingError("resident_inventory_invalid")
    meta = dict(meta_tools_by_access_id or {})
    groups: dict[str, _ConnectionAccumulator] = {}
    access_by_server: dict[str, str] = {}

    for resource in inventory.resources:
        if (
            resource.authority_source is not AuthoritySource.DELEGATED_CARD
            or resource.binding.mode != "gateway"
            or not resource.access_id
            or resource.reason in _CARD_UNBINDABLE
        ):
            continue
        binding = resource.binding
        if not all(
            str(value or "").strip()
            for value in (
                binding.server_id,
                binding.alias,
                binding.transport,
                binding.endpoint,
            )
        ):
            raise ResidentRuntimeBindingError("gateway_runtime_binding_incomplete")
        prior_access = access_by_server.setdefault(binding.server_id, resource.access_id)
        if prior_access != resource.access_id:
            raise ResidentRuntimeBindingError("gateway_runtime_server_id_collision")
        group = groups.get(resource.access_id)
        if group is None:
            group = _ConnectionAccumulator(
                binding=binding,
                access_id=resource.access_id,
                card_revision=resource.card_revision,
                identity_scope=resource.identity_scope,
                resources=set(),
                tools=set(),
            )
            groups[resource.access_id] = group
        elif (
            group.binding != binding
            or group.card_revision != resource.card_revision
            or group.identity_scope != resource.identity_scope
        ):
            raise ResidentRuntimeBindingError("gateway_runtime_card_binding_mismatch")
        if resource.resource_id in group.resources:
            raise ResidentRuntimeBindingError("gateway_runtime_resource_duplicate")
        group.resources.add(resource.resource_id)
        for tool in resource.tools:
            if not tool.available:
                continue
            if not tool.name or tool.name in group.tools:
                raise ResidentRuntimeBindingError("gateway_runtime_tool_duplicate")
            group.tools.add(tool.name)

    unknown_meta = set(meta) - set(groups)
    if unknown_meta:
        raise ResidentRuntimeBindingError("gateway_runtime_meta_card_unknown")
    for access_id, rows in meta.items():
        group = groups[access_id]
        for tool in rows:
            if not isinstance(tool, ResidentToolDescriptor) or not tool.name:
                raise ResidentRuntimeBindingError("gateway_runtime_meta_tool_invalid")
            if tool.name in group.tools:
                raise ResidentRuntimeBindingError("gateway_runtime_tool_duplicate")
            group.tools.add(tool.name)

    if len(groups) > 1:
        raise ResidentRuntimeBindingError("gateway_runtime_multiple_resident_cards")

    return tuple(
        GatewayRuntimeConnection(
            server_id=group.binding.server_id,
            alias=group.binding.alias,
            transport=group.binding.transport,
            endpoint=group.binding.endpoint,
            access_id=group.access_id,
            card_revision=group.card_revision,
            identity_scope=group.identity_scope,
            resource_ids=tuple(sorted(group.resources)),
            tool_names=tuple(sorted(group.tools)),
        )
        for group in sorted(groups.values(), key=lambda item: item.binding.server_id)
        if group.resources and group.tools
    )


def apply_gateway_runtime_connections(
    config: AgentToolConfig,
    connections: Iterable[GatewayRuntimeConnection],
) -> AgentToolConfig:
    """Add the dynamic Gateway connections to native ReAct's tool config."""

    if not isinstance(config, AgentToolConfig):
        raise ResidentRuntimeBindingError("agent_tool_config_invalid")
    rows = tuple(connections)
    _connection_index(rows)
    existing_servers = {
        str(spec.get("server_id") or "").strip()
        for spec in config.mcp_tool_specs
        if isinstance(spec, Mapping)
    }
    existing_aliases = set(config.allowed_tool_names_by_alias)
    added_servers: set[str] = set()
    added_aliases: set[str] = set()
    mcp_specs = [dict(spec) for spec in config.mcp_tool_specs]
    plugins = list(config.allowed_plugins)
    allowed = {
        alias: (None if names is None else list(names))
        for alias, names in config.allowed_tool_names_by_alias.items()
    }
    tool_runtime = dict(config.tool_runtime)
    for connection in rows:
        if (
            connection.server_id in existing_servers
            or connection.server_id in added_servers
        ):
            raise ResidentRuntimeBindingError("gateway_runtime_server_id_collision")
        if connection.alias in existing_aliases or connection.alias in added_aliases:
            raise ResidentRuntimeBindingError("gateway_runtime_alias_collision")
        added_servers.add(connection.server_id)
        added_aliases.add(connection.alias)
        mcp_specs.append(connection.to_mcp_tool_spec())
        if connection.alias not in plugins:
            plugins.append(connection.alias)
        allowed[connection.alias] = list(connection.tool_names)
        for tool_name in connection.tool_names:
            tool_id = f"mcp.{connection.alias}.{tool_name}"
            configured_runtime = tool_runtime.get(tool_id)
            if configured_runtime not in (None, "none"):
                raise ResidentRuntimeBindingError(
                    "gateway_runtime_execution_mode_conflict"
                )
            tool_runtime[tool_id] = "none"
    return AgentToolConfig(
        tool_specs=[dict(spec) for spec in config.tool_specs],
        mcp_tool_specs=mcp_specs,
        tool_runtime=tool_runtime,
        tool_traits={key: dict(value) for key, value in config.tool_traits.items()},
        allowed_plugins=plugins,
        allowed_tool_names_by_alias=allowed,
        tool_claim_policies=list(config.tool_claim_policies),
    )


def gateway_connection_descriptors(
    connections: Iterable[GatewayRuntimeConnection],
) -> list[dict[str, Any]]:
    """One descriptor-like input shared by all current MCP runtime adapters."""

    rows = tuple(connections)
    _connection_index(rows)
    return [row.to_connection_dict() for row in rows]


def gateway_services_config(
    connections: Iterable[GatewayRuntimeConnection],
) -> dict[str, Any]:
    """Return the credential-free native MCP service registry.

    Gateway catalogs are live Card views, so their global MCP cache is
    disabled. The auth marker is declarative: only the trusted supervisor can
    satisfy it with ``GatewayRuntimeHeadersProvider``.
    """

    rows = tuple(connections)
    _connection_index(rows)
    return {
        "mcpServers": {
            row.server_id: {
                "transport": row.transport,
                "url": row.endpoint,
                "auth": {"type": _TRUSTED_PROCESS_AUTH_TYPE},
                "ttl_seconds": 0,
            }
            for row in rows
        }
    }


def _configured_mcp_servers(configured: Any) -> dict[str, dict[str, Any]]:
    if configured in (None, ""):
        return {}
    value = configured
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise ResidentRuntimeBindingError("mcp_services_config_invalid") from None
    if not isinstance(value, Mapping):
        raise ResidentRuntimeBindingError("mcp_services_config_invalid")
    if "mcpServers" in value:
        value = value.get("mcpServers")
    elif "servers" in value:
        value = value.get("servers")
    if not isinstance(value, Mapping):
        raise ResidentRuntimeBindingError("mcp_services_config_invalid")
    result: dict[str, dict[str, Any]] = {}
    for raw_server_id, raw_config in value.items():
        server_id = str(raw_server_id or "").strip()
        if not server_id or not isinstance(raw_config, Mapping):
            raise ResidentRuntimeBindingError("mcp_services_config_invalid")
        result[server_id] = copy.deepcopy(dict(raw_config))
    return result


def merge_gateway_services_config(
    configured: Any,
    connections: Iterable[GatewayRuntimeConnection],
) -> dict[str, Any]:
    """Merge dynamic Gateway endpoints with descriptor-owned MCP services."""

    servers = _configured_mcp_servers(configured)
    dynamic = gateway_services_config(connections)["mcpServers"]
    overlap = set(servers) & set(dynamic)
    if overlap:
        raise ResidentRuntimeBindingError("gateway_runtime_server_id_collision")
    servers.update(dynamic)
    return {"mcpServers": servers}


def gateway_runtime_plan(
    inventory: EffectiveResidentInventory,
    *,
    meta_tools_by_access_id: Mapping[
        str, Iterable[ResidentToolDescriptor]
    ] | None = None,
) -> GatewayRuntimePlan:
    """Build one immutable runtime translation from one effective inventory."""

    return GatewayRuntimePlan(
        gateway_runtime_connections(
            inventory,
            meta_tools_by_access_id=meta_tools_by_access_id,
        )
    )


def bind_gateway_runtime_context(
    runtime_context: Any,
    plan: GatewayRuntimePlan,
    *,
    user_subject: str,
    bearer_provider: GatewayBearerProvider,
    turn_token_provider: Callable[[], Awaitable[str]] | None = None,
) -> None:
    """Bind a credential-free plan and its per-call resolver to one turn.

    Runtime contexts are rebuilt for each turn. The callback can close over a
    trusted service, but no bearer is fetched or retained by this operation.
    """

    if runtime_context is None or not isinstance(plan, GatewayRuntimePlan):
        raise ResidentRuntimeBindingError("gateway_runtime_context_invalid")
    provider = plan.auth_headers_provider(
        user_subject=user_subject,
        bearer_provider=bearer_provider,
        turn_token_provider=turn_token_provider,
    )
    setattr(runtime_context, "resident_gateway_runtime_plan", plan)
    setattr(runtime_context, "resident_mcp_auth_headers_provider", provider)


def gateway_tool_overrides(
    connections: Iterable[GatewayRuntimeConnection],
) -> dict[str, tuple[str, ...]]:
    """Return exact per-server survivors for permission-grammar runtimes."""

    rows = tuple(connections)
    _connection_index(rows)
    return {row.server_id: row.tool_names for row in rows}


__all__ = [
    "GatewayRuntimeConnection",
    "GatewayRuntimeHeadersProvider",
    "GatewayRuntimePlan",
    "ResidentRuntimeBindingError",
    "apply_gateway_runtime_connections",
    "bind_gateway_runtime_context",
    "delegated_mcp_bindings_from_catalog",
    "gateway_connection_descriptors",
    "gateway_runtime_connections",
    "gateway_runtime_plan",
    "gateway_services_config",
    "gateway_tool_overrides",
    "merge_gateway_services_config",
    "remove_direct_delegated_mcp_bindings",
]
