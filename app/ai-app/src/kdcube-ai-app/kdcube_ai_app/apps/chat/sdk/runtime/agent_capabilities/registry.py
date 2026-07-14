# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Registry that resolves an ``AgentCapabilitiesProvider`` for an agent.

Resolution: a bundle may declare
``surfaces.as_consumer.agents.<id>.capability_provider: <kind>``; otherwise the
default kind is used. The default is ``"react"`` so every existing bundle keeps
ReAct behavior with zero config. A provider registers a factory
``(bundle_props, agent_id) -> AgentCapabilitiesProvider``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities._config import agent_config_block
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.provider import (
    AgentCapabilitiesProvider,
)

DEFAULT_PROVIDER_KIND = "react"

ProviderFactory = Callable[..., AgentCapabilitiesProvider]

_REGISTRY: Dict[str, ProviderFactory] = {}


def register_capability_provider(kind: str, factory: ProviderFactory) -> None:
    """Register a provider factory under ``kind`` (idempotent overwrite)."""
    _REGISTRY[str(kind)] = factory


def registered_provider_kinds() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def capability_provider_kind(
    bundle_props: Any, agent_id: str, *, default: str = DEFAULT_PROVIDER_KIND
) -> str:
    """The declared provider kind for ``agent_id`` (``default`` when unset)."""
    block = agent_config_block(bundle_props, agent_id)
    kind = block.get("capability_provider")
    return str(kind).strip() if isinstance(kind, str) and kind.strip() else default


def resolve_capability_provider(
    bundle_props: Any, agent_id: str, *, default: str = DEFAULT_PROVIDER_KIND
) -> Optional[AgentCapabilitiesProvider]:
    """Resolve the provider for ``agent_id``. Returns ``None`` when neither the
    declared kind nor the default is registered (caller keeps current behavior)."""
    kind = capability_provider_kind(bundle_props, agent_id, default=default)
    factory = _REGISTRY.get(kind) or _REGISTRY.get(default)
    if factory is None:
        return None
    return factory(bundle_props=bundle_props, agent_id=agent_id)
