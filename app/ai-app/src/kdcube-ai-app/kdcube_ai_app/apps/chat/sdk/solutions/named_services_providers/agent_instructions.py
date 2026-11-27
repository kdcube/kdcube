# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Agent-neutral composition of the named-services instruction block.

One mechanism, reachable from ANY agent harness, that compiles the dynamic
named-services section of a system prompt: the teaching block for the agent's
tool surface plus the roster of ``as_consumer``-connected namespaces, each
rendered with its discovery-published ``intro``.

Two teaching surfaces exist:

- ``surface="react"`` — the ReAct block (``react.pull``/``conv:fi:`` workspace
  mechanics, ``named_services.*`` tool ids). Used by ``BaseWorkflow``.
- ``surface="bridge"`` — transport-neutral: teaches by OPERATION NAME
  (``provider_about``, ``object_schema``, ``object_action``, ...), the stable
  named-service contract vocabulary, with the agent's own file tools named via
  ``pull_tool``/``read_tool``. For agents bound through MCP or framework tool
  bindings (e.g. hosted LangGraph agents).

The sync composer takes prefetched intros; the async gatherer also performs the
canonical discovery read (``RedisNamedServiceDiscovery`` + intros) when given
``redis``/``tenant``/``project`` or a prebuilt ``discovery`` object.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    connected_named_service_namespaces,
    render_named_service_namespace_roster,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.instructions import (
    NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS,
    named_services_bridge_instructions,
)

LOGGER = logging.getLogger(__name__)

_SURFACES = ("react", "bridge")


def compose_named_service_agent_instructions(
    bundle_props: Mapping[str, Any] | None,
    *,
    client_id: Any,
    intros: Mapping[str, Mapping[str, str]] | None = None,
    surface: str = "react",
    namespaces: Sequence[str] | None = None,
    pull_tool: str = "pull_files",
    read_tool: str = "read_file",
    operations: Mapping[str, str] | None = None,
) -> str:
    """Compose teaching block + namespace roster for one agent's surface.

    ``namespaces`` overrides the connected-namespace derivation for callers that
    know their namespace set from another source (e.g. tool introspection);
    the default derives it from the agent's ``as_consumer`` config. Empty
    namespace set composes to an empty string — no roster, no teaching.
    """
    key = str(surface or "react").strip().lower()
    if key not in _SURFACES:
        known = ", ".join(_SURFACES)
        raise KeyError(f"Unknown named-service instruction surface: {surface!r}. Known: {known}")
    if namespaces is None:
        namespaces = connected_named_service_namespaces(bundle_props, client_id=client_id)
    namespaces = [str(ns) for ns in (namespaces or []) if str(ns or "").strip()]
    if not namespaces:
        return ""
    if key == "react":
        teaching = NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS
    else:
        teaching = named_services_bridge_instructions(
            pull_tool=pull_tool,
            read_tool=read_tool,
            operations=dict(operations) if operations else None,
        )
    roster = render_named_service_namespace_roster(namespaces, intros)
    return f"{teaching}\n\n{roster}" if roster else teaching


async def named_service_agent_instruction_block(
    *,
    bundle_props: Mapping[str, Any] | None,
    client_id: Any,
    surface: str = "react",
    namespaces: Sequence[str] | None = None,
    intros: Mapping[str, Mapping[str, str]] | None = None,
    discovery: Any = None,
    redis: Any = None,
    tenant: str | None = None,
    project: str | None = None,
    pull_tool: str = "pull_files",
    read_tool: str = "read_file",
    operations: Mapping[str, str] | None = None,
) -> str:
    """Gather intros through discovery and compose the instruction block.

    The one call any agent harness needs. Intro sources, in order: explicit
    ``intros``; a prebuilt ``discovery`` object; ``redis`` + ``tenant`` +
    ``project`` (builds the canonical ``RedisNamedServiceDiscovery``). With
    none of them the roster renders bare namespace names — still valid.
    """
    if namespaces is None:
        namespaces = connected_named_service_namespaces(bundle_props, client_id=client_id)
    namespaces = [str(ns) for ns in (namespaces or []) if str(ns or "").strip()]
    if not namespaces:
        return ""

    if intros is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import RedisNamedServiceDiscovery, fetch_namespace_intros

            if discovery is None and redis is not None and str(tenant or "").strip() and str(project or "").strip():
                discovery = RedisNamedServiceDiscovery(redis, tenant=str(tenant), project=str(project))
            intros = await fetch_namespace_intros(discovery, namespaces)
        except Exception:
            LOGGER.warning("[named_services] roster intro fetch failed", exc_info=True)
            intros = {}

    return compose_named_service_agent_instructions(
        bundle_props,
        client_id=client_id,
        intros=intros,
        surface=surface,
        namespaces=namespaces,
        pull_tool=pull_tool,
        read_tool=read_tool,
        operations=operations,
    )


__all__ = [
    "compose_named_service_agent_instructions",
    "named_service_agent_instruction_block",
]
