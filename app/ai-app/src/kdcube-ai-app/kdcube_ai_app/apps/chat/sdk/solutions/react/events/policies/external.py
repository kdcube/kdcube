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


@block_production_policy(event_policy_id="react.block_production.event_default")
def external_event_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the default timeline block for an authored external event.

    This is the fallback for unregistered event sources and for registered
    sources that do not override external-event block production. The event
    payload is normalized into the same `{ok,error,ret,raw}` accumulator shape
    used for tool results, then common result-surface policies extract hosted
    artifacts, exploration rows, snapshot refs, announce candidates, and
    declared file rows. Those surfaces are stored in the durable event block
    for later projection, announce, or compaction policies.

    Default output: one `event.external`-family block at the accepted event's
    `ev:` logical path. The block body is JSON with `ok`, `status`, `ret`,
    optional `error`, optional `event_ref`, and optional `surfaces`.
    """
    if not isinstance(target, MutableMapping):
        return target
    _normalize_event_payload_target(target)
    _apply_standard_event_surface_policies(target)
    _default_event_block(target)
    target["blocks_produced"] = True
    return target


@compaction_event_policy(event_policy_id="react.compaction_projection.event_default")
@timeline_projection_policy(event_policy_id="react.timeline_projection.event_default")
def external_event_default_render_policy(
    timeline: list[MutableMapping[str, Any]],
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Render generic external-event JSON as compact model-facing event facts."""
    return project_event_blocks_as_text(
        timeline,
        block_types={"event.external", "event.external.preserved"},
        label="[TIMELINE EVENT]",
        semantic="external event occurrence recorded on the ordered conversation lane",
        policy_id=str(context.get("react_phase") or "react.timeline_projection.event_default"),
        include_ret_preview=True,
        ret_preview_limit=700,
        source=context.get("source"),
        call_meta=context.get("call_meta"),
    )
