# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Agent-capabilities provider/adapter contract.

Makes the Capabilities widget + per-user (per-conversation) selection work with
ANY agent implementation. ReAct is the first adapter; ``simple_model_pick`` is
the generic one non-ReAct ports declare by config. See ``provider.py``.
"""

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.provider import (
    AgentCapabilitiesProvider,
    CapabilityBlocks,
    ConversationCaps,
    ModelPick,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.registry import (
    DEFAULT_PROVIDER_KIND,
    capability_provider_kind,
    register_capability_provider,
    registered_provider_kinds,
    resolve_capability_provider,
)

# Importing the package registers the built-in providers (ReAct default +
# generic model-pick). Both register lazily — agent_inventory is only imported
# inside the provider methods, so registration stays cheap.
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import react_provider  # noqa: F401
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities import simple_model_pick  # noqa: F401
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.react_provider import (
    PROVIDER_KIND as REACT_PROVIDER_KIND,
    ReactCapabilitiesProvider,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capabilities.simple_model_pick import (
    PROVIDER_KIND as SIMPLE_MODEL_PICK_KIND,
    SimpleModelPickProvider,
)

__all__ = [
    "AgentCapabilitiesProvider",
    "CapabilityBlocks",
    "ConversationCaps",
    "ModelPick",
    "DEFAULT_PROVIDER_KIND",
    "REACT_PROVIDER_KIND",
    "ReactCapabilitiesProvider",
    "SIMPLE_MODEL_PICK_KIND",
    "SimpleModelPickProvider",
    "register_capability_provider",
    "registered_provider_kinds",
    "capability_provider_kind",
    "resolve_capability_provider",
]
