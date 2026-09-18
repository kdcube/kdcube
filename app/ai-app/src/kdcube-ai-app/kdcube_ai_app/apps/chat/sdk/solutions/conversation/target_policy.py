# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Application ceiling and user narrowing for hosted conversation reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import _agent_tool_connections


@dataclass(frozen=True)
class ConversationTargetPolicy:
    configured: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()


def configured_conversation_targets(
    bundle_props: Mapping[str, Any] | None, agent_id: str
) -> tuple[str, ...]:
    """Finite cross-application ceiling declared for this hosted agent."""
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


__all__ = ["ConversationTargetPolicy", "configured_conversation_targets"]
