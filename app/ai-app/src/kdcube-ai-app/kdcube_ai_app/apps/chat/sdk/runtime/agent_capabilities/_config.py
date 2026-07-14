# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Neutral bundle-config reader for the capabilities providers.

Reads ``surfaces.as_consumer.agents.<id>`` without the ReAct-specific
``_react_agent_config_blocks`` precedence grammar — a provider declares its
config under the plain consumer-agent block.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def agent_config_block(bundle_props: Any, agent_id: str) -> Dict[str, Any]:
    """The ``surfaces.as_consumer.agents.<agent_id>`` block (empty dict if absent).

    Falls back to the configured ``default_agent`` block when the specific id is
    not present, so a bundle that configures only its default agent still resolves.
    """
    props = bundle_props if isinstance(bundle_props, Mapping) else {}
    consumer = ((props.get("surfaces") or {}).get("as_consumer")) or {}
    agents = consumer.get("agents") or {}
    if not isinstance(agents, Mapping):
        return {}
    block = agents.get(agent_id)
    if isinstance(block, Mapping):
        return dict(block)
    default_id = consumer.get("default_agent")
    if default_id and isinstance(agents.get(default_id), Mapping):
        return dict(agents[default_id])
    return {}
