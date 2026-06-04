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
    compaction_event_policy,
    timeline_projection_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.rendering_common import (
    project_event_blocks_as_text,
)


@block_production_policy(event_policy_id="react.block_production.canvas_default")
def canvas_event_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the default durable timeline block for collaborative canvas state.

    Default output: one `event.canvas` block at the event's `ev:` logical path.
    The JSON block body keeps the canvas revision in `ret` and preserves common
    result surfaces. Unlike `event.snapshot`, canvas represents mutually
    writable domain state; updates should still happen through a bundle API/tool
    that writes the authoritative canvas store and emits a later canvas event.
    """
    if not isinstance(target, MutableMapping):
        return target
    target["block_type"] = "event.canvas"
    _normalize_event_payload_target(target)
    _apply_standard_event_surface_policies(target)
    _default_event_block(target)
    target["blocks_produced"] = True
    return target


@compaction_event_policy(event_policy_id="react.compaction_projection.canvas_default")
@timeline_projection_policy(event_policy_id="react.timeline_projection.canvas_default")
def canvas_event_default_render_policy(
    timeline: list[MutableMapping[str, Any]],
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Render canvas event JSON as a compact collaborative-board fact."""
    return project_event_blocks_as_text(
        timeline,
        block_types={"event.canvas"},
        label="[CANVAS EVENT]",
        semantic="collaborative editable board event; current board projection belongs in ANNOUNCE",
        policy_id=str(context.get("react_phase") or "react.timeline_projection.canvas_default"),
        include_ret_preview=False,
        source=context.get("source"),
        call_meta=context.get("call_meta"),
    )
