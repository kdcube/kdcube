# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-owned application targets for hosted conversation reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from connection_hub.delegated_credentials.application_resources import (
    APPLICATION_RESOURCE_PREFIX,
    ApplicationResource,
    ApplicationResourceError,
)

from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import _agent_tool_connections


@dataclass(frozen=True)
class ConversationTargetPolicy:
    configured: tuple[str, ...] = ()


def configured_conversation_targets(
    bundle_props: Mapping[str, Any] | None, agent_id: str
) -> tuple[str, ...]:
    """Application selectors declared for this hosted agent."""
    targets: set[str] = set()
    for connection in _agent_tool_connections(bundle_props, agent_id=agent_id):
        if str(connection.get("kind") or "").strip().lower() != "named_service":
            continue
        namespaces = connection.get("namespaces")
        conv = namespaces.get("conv") if isinstance(namespaces, Mapping) else None
        configured = conv.get("targets") if isinstance(conv, Mapping) else None
        if isinstance(configured, (list, tuple)):
            targets.update(
                target.strip() for target in configured
                if isinstance(target, str) and target.strip() and target.strip() != "*"
            )
    return tuple(sorted(targets))


def configured_conversation_target_rows(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str,
) -> tuple[dict[str, str], ...]:
    """Render configured targets without losing typed resource selectors."""

    rows: list[dict[str, str]] = []
    for target in configured_conversation_targets(bundle_props, agent_id):
        if not target.startswith(APPLICATION_RESOURCE_PREFIX):
            rows.append({"bundle_id": target})
            continue
        try:
            resource = ApplicationResource.parse(target)
        except ApplicationResourceError:
            # Descriptor validation at the Control Card producer is the final
            # authority gate. Do not advertise malformed rows before it.
            continue
        rows.append(
            {
                "bundle_id": resource.application,
                "resource": resource.resource,
            }
        )
    return tuple(rows)


__all__ = [
    "ConversationTargetPolicy",
    "configured_conversation_target_rows",
    "configured_conversation_targets",
]
