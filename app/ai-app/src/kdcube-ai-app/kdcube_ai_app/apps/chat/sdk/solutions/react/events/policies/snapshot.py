# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    _apply_standard_event_surface_policies,
    _default_event_block,
    _normalize_event_payload_target,
    block_production_policy,
)


@block_production_policy(event_policy_id="react.block_production.snapshot_default")
def snapshot_event_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the default durable timeline block for a read-only snapshot event.

    Default output: one `event.snapshot` block at the event's `ev:` logical
    path. The JSON block body preserves the hosted/ref payload as `ret` or
    `event_ref`, plus extracted surfaces such as `snapshot_refs` and
    `announce_candidates`. The snapshot is reference material; ReAct should
    read or pull the referenced payload, not patch this block as state.
    """
    if not isinstance(target, MutableMapping):
        return target
    target["block_type"] = "event.snapshot"
    _normalize_event_payload_target(target)
    _apply_standard_event_surface_policies(target)
    _default_event_block(target, snapshot=True)
    target["blocks_produced"] = True
    return target
