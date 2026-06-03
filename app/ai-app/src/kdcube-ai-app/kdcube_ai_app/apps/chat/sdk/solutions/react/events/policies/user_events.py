# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import block_production_policy


def _append_user_event_block(
    target: MutableMapping[str, Any],
    *,
    physical_type: str,
    prompt_origin: str | None = None,
) -> MutableMapping[str, Any]:
    blocks = target.setdefault("blocks", [])
    block_factory = target.get("block_factory")
    if not isinstance(blocks, list) or not callable(block_factory):
        return target
    meta = dict(target.get("meta") if isinstance(target.get("meta"), Mapping) else {})
    if prompt_origin:
        meta["prompt_origin"] = prompt_origin
    blocks.append(block_factory(
        type=physical_type,
        author=str(target.get("author") or "user"),
        turn_id=str(target.get("turn_id") or ""),
        ts=str(target.get("ts") or ""),
        mime=str(target.get("mime") or "text/markdown"),
        text=str(target.get("text") or ""),
        path=str(target.get("path") or ""),
        meta=meta,
    ))
    target["blocks_produced"] = True
    return target


@block_production_policy(event_policy_id="react.block_production.user_prompt_default")
def user_prompt_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the compatibility `user.prompt` block for `event.user.prompt`."""
    if not isinstance(target, MutableMapping):
        return target
    target["block_type"] = "event.user.prompt"
    return _append_user_event_block(
        target,
        physical_type="user.prompt",
        prompt_origin="external_event_lane",
    )


@block_production_policy(event_policy_id="react.block_production.user_followup_default")
def user_followup_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the compatibility `user.followup` block for `event.user.followup`."""
    if not isinstance(target, MutableMapping):
        return target
    target["block_type"] = "event.user.followup"
    return _append_user_event_block(target, physical_type="user.followup")


@block_production_policy(event_policy_id="react.block_production.user_steer_default")
def user_steer_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the compatibility `user.steer` block for `event.user.steer`."""
    if not isinstance(target, MutableMapping):
        return target
    target["block_type"] = "event.user.steer"
    return _append_user_event_block(target, physical_type="user.steer")


@block_production_policy(event_policy_id="react.block_production.user_attachment_default")
def user_attachment_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce compatibility `user.attachment.*` blocks for attachment events."""
    if not isinstance(target, MutableMapping):
        return target
    blocks = target.setdefault("blocks", [])
    block_factory = target.get("block_factory")
    attachments = target.get("attachments")
    if not isinstance(blocks, list) or not callable(block_factory):
        return target
    if not isinstance(attachments, list) or not attachments:
        target["blocks_produced"] = True
        return target
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_user_attachment_blocks

    produced = build_user_attachment_blocks(
        turn_id=str(target.get("turn_id") or ""),
        ts=str(target.get("ts") or ""),
        user_attachments=[dict(item) for item in attachments if isinstance(item, Mapping)],
        block_factory=block_factory,
        path_root=str(target.get("path_root") or ""),
        synthetic_physical_root=str(target.get("physical_root") or "") or None,
        meta_extra=dict(target.get("meta_extra") if isinstance(target.get("meta_extra"), Mapping) else {}),
    )
    blocks.extend(produced)
    target["blocks_produced"] = True
    return target
