"""Reusable ReAct tools for the SDK canvas solution.

Bundles normally include this module in `tools_descriptor.py` with alias
`canvas` and `use_sk: true`. The exported tool ids are:

```text
canvas.read
canvas.patch
```

The tools bind to the current bundle runtime at call time. Bundle-specific
transport and storage names are supplied through `bundle_props.canvas`; the
tool implementation itself remains part of the SDK canvas solution.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Mapping

import semantic_kernel as sk

from kdcube_ai_app.apps.chat.sdk.events import event_source, event_source_declaration
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.storage import DEFAULT_CANVAS_NAME, CanvasStore
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools_core import (
    DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS,
    canonicalize_canvas_operations_for_context as _canonicalize_canvas_operations_for_context,
    patch_canvas_for_agent,
    read_canvas_for_agent,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import default_tool_event_policies
from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import error, ok, scope

from .events.policies import (  # noqa: F401 - imported so the event subsystem discovers default policies
    canvas_announce_policy,
    canvas_patch_block_policy,
    canvas_read_block_policy,
    canvas_state_projection_policy,
    canvas_tool_projection_policy,
)

try:
    from semantic_kernel.functions import kernel_function
except Exception:  # pragma: no cover - semantic-kernel compatibility fallback
    from semantic_kernel.utils.function_decorator import kernel_function


DEFAULT_CANVAS_TOOL_CONFIG: Dict[str, Any] = {
    "artifact_prefix": "canvas",
    "origin_prefix": "canvas",
    "state_event_source_id": "canvas.state",
    "ui_event_type": "canvas.patch.applied",
    "artifact_resolver_name": "canvas.bundle_artifact_storage",
    "handoff_resolver_names": {},
    "revision_retention": 80,
    "data_bus_subject": "canvas.patch",
    "event_agent_id": "canvas",
    "event_surface": "canvas",
}


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canvas_config(tool_scope: Mapping[str, Any]) -> Dict[str, Any]:
    bundle_props = _mapping(tool_scope.get("bundle_props"))
    configured = _mapping(bundle_props.get("canvas"))
    if not configured:
        sdk_props = _mapping(bundle_props.get("sdk"))
        configured = _mapping(sdk_props.get("canvas"))
    cfg = dict(DEFAULT_CANVAS_TOOL_CONFIG)
    cfg.update(configured)
    handoff = cfg.get("handoff_resolver_names")
    cfg["handoff_resolver_names"] = dict(handoff) if isinstance(handoff, Mapping) else {}
    try:
        cfg["revision_retention"] = int(cfg.get("revision_retention") or 80)
    except Exception:
        cfg["revision_retention"] = 80
    return cfg


def _store_from_scope(tool_scope: Mapping[str, Any]) -> CanvasStore:
    cfg = _canvas_config(tool_scope)
    return CanvasStore.from_scope(
        tool_scope,
        bundle_id=str(cfg.get("bundle_id") or tool_scope.get("bundle_id") or ""),
        artifact_prefix=str(cfg.get("artifact_prefix") or "canvas"),
        origin_prefix=str(cfg.get("origin_prefix") or "canvas"),
        state_event_source_id=str(cfg.get("state_event_source_id") or "canvas.state"),
        ui_event_type=str(cfg.get("ui_event_type") or "canvas.patch.applied"),
        artifact_resolver_name=str(cfg.get("artifact_resolver_name") or "canvas.bundle_artifact_storage"),
        handoff_resolver_names=dict(cfg.get("handoff_resolver_names") or {}),
        revision_retention=int(cfg.get("revision_retention") or 80),
    )


def _canvas_tool_policies(name: str) -> list[dict[str, Any]]:
    block_policy_id = {
        "patch": "canvas.block_production.patch_result",
        "read": "canvas.block_production.read_result",
    }.get(name)
    policies = list(default_tool_event_policies())
    if block_policy_id:
        policies.append({
            "react_phase": "block_production",
            "event_policy_id": block_policy_id,
        })
        policies.append({
            "react_phase": "timeline_projection",
            "event_policy_id": "canvas.timeline_projection.tool_result",
        })
        policies.append({
            "react_phase": "compaction_projection",
            "event_policy_id": "canvas.compaction_projection.tool_result",
        })
        policies.append({
            "react_phase": "announce_production",
            "event_policy_id": "canvas.announce.board_map",
        })
    return policies


def list_event_sources() -> list[Any]:
    return [
        event_source_declaration(
            event_source_id=f"{{alias}}.{name}",
            policies=_canvas_tool_policies(name),
            description=description,
            kind="react.tool",
        )
        for name, description in DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS.items()
    ]


class CanvasTools:
    @event_source(
        event_source_id="{alias}.patch",
        policies=_canvas_tool_policies("patch"),
        description=(
            "Patch a named collaborative canvas and return the new revision/event. "
            "Canvas cards remain pins. To put a file, report, attachment, memory, "
            "source row, link, or agent-authored text on the board, first produce "
            "or identify its canonical ref, then call this tool with new_card using "
            "that logical_path. Producing a file alone never updates the canvas. "
            "Suggestion is placement/state, not a card kind: use placement=suggested "
            "for output that waits for the user to accept or arrange. Focused cards "
            "are priority context, not an edit request by themselves. Agents do not "
            "move, resize, or arrange existing cards; they only contribute content "
            "suggestions, comments, replacement suggestions, deletion suggestions, "
            "or new suggested cards."
        ),
        kind="react.tool",
    )
    @kernel_function(
        name="patch",
        description=(
            "Patch a named collaborative canvas. Use this when the user asks you to "
            "pin an output on the canvas, create a floating suggestion, suggest "
            "deletion, suggest replacement, or comment on a card. The canvas is "
            "updated only by this explicit patch call: if you create a file/report/"
            "output first, call canvas.patch afterwards with a new_card whose "
            "logical_path points at that produced canonical ref. Persisted cards "
            "are refs only; any content supplied in a patch operation is stored as "
            "a versioned canvas-owned object and replaced by a canvas-owned ref. "
            "Pick the card kind by content: file for artifacts, memory for mem: "
            "refs, source/search.result for search results, agent.text only for "
            "assistant-authored text. Use placement=suggested for bot output that "
            "is still waiting for the user to accept, arrange, or discard. Do not "
            "move, resize, or arrange existing cards. Use focused cards as priority "
            "context and the full canvas map/legend for awareness."
        ),
    )
    async def patch(
        self,
        operations: Annotated[
            str,
            (
                "JSON array of canvas patch operations. Supported ops: new_card, update_card, "
                "replace_card, suggest_deletion, delete_card, comment_card. "
                "For artifact delivery use new_card with the semantic kind of the object "
                "(file, memory, source, search.result, agent.text for text only), title/summary, "
                "mime, and logical_path set to the produced/resolved fi:/ext:/mem:/so:/task: ref. "
                "Use placement=suggested for pending bot suggestions. For replacement suggestions, use "
                "replace_card with the target card_id; omit mode or use mode=suggested to create "
                "a floating replacement, and use mode=in_place only for explicit overwrite requests."
            ),
        ],
        canvas_name: Annotated[str, "Named canvas to patch. Defaults to main."] = DEFAULT_CANVAS_NAME,
        canvas_id: Annotated[str, "Explicit canvas id. Leave empty to use canvas:<user_id>:<canvas_name>."] = "",
        story_id: Annotated[str, "Optional story/ticket context for this patch."] = "",
        base_revision: Annotated[int | None, "Expected current canvas revision for optimistic concurrency."] = None,
        reason: Annotated[str, "Short reason for the patch, recorded in canvas history."] = "",
        actor: Annotated[str, "Actor label stored in canvas history. Defaults to agent."] = "agent",
    ) -> Annotated[
        Dict[str, Any],
        "Envelope {ok,error,ret}. ret includes canvas_id, revision, canvas_ref, latest_ref, changed, projection, ui_event, and event.canvas.",
    ]:
        try:
            tool_scope = scope()
            cfg = _canvas_config(tool_scope)
            result = await patch_canvas_for_agent(
                tool_scope=tool_scope,
                store=_store_from_scope(tool_scope),
                bundle_id=str(cfg.get("bundle_id") or tool_scope.get("bundle_id") or ""),
                data_bus_subject=str(cfg.get("data_bus_subject") or "canvas.patch"),
                operations=operations,
                canvas_name=canvas_name,
                canvas_id=canvas_id,
                story_id=story_id,
                base_revision=base_revision,
                reason=reason,
                actor=actor,
                event_agent_id=str(cfg.get("event_agent_id") or "canvas"),
                event_surface=str(cfg.get("event_surface") or "canvas"),
            )
            if not result.get("ok"):
                return error("canvas_patch_failed", str(result.get("error") or "canvas patch failed"))
            return ok({
                "canvas_name": result["canvas_name"],
                "canvas_id": result["canvas_id"],
                "revision": result["revision"],
                "canvas_ref": result["canvas_ref"],
                "latest_ref": result["latest_ref"],
                "changed": result.get("changed") or [],
                "changed_cards": result.get("changed_cards") or [],
                "projection": result.get("projection") or {},
                "ui_event": result.get("ui_event") or {},
                "event": result["event"],
            })
        except Exception as exc:
            return error("canvas_patch_failed", str(exc))

    @event_source(
        event_source_id="{alias}.read",
        policies=_canvas_tool_policies("read"),
        description="Read a canvas by canvas: URI and return agent_view plus exact state.",
        kind="react.tool",
    )
    @kernel_function(
        name="read",
        description=(
            "Read a canvas board and agent_view. Prefer ANNOUNCE for current canvas "
            "awareness; use this when the map/legend is insufficient and you need "
            "exact card ids, coordinates, refs, or hidden metadata before patching. "
            "This tool refreshes the canvas view in ANNOUNCE and records only a "
            "small timeline fact; it does not edit the board. Focused context from "
            "ANNOUNCE remains the user's priority selection, while the full board "
            "is used for surrounding spatial context."
        ),
    )
    async def read(
        self,
        uri: Annotated[
            str,
            "Canvas URI to read, for example canvas:main@7. ext: revision refs are accepted as a lower-level fallback.",
        ] = "",
        canvas_name: Annotated[str, "Named canvas to read. Defaults to main."] = DEFAULT_CANVAS_NAME,
        canvas_id: Annotated[str, "Explicit canvas id. Leave empty to use canvas:<user_id>:<canvas_name>."] = "",
        story_id: Annotated[str, "Optional story/ticket context."] = "",
        revision: Annotated[int | None, "Specific revision to read. Leave empty for latest."] = None,
    ) -> Annotated[
        Dict[str, Any],
        "Envelope {ok,error,ret}. ret includes canvas_uri, canvas_id, revision, agent_view, canvas JSON, projection, canvas_ref, latest_ref.",
    ]:
        try:
            result = read_canvas_for_agent(
                store=_store_from_scope(scope()),
                uri=uri,
                canvas_name=canvas_name,
                canvas_id=canvas_id,
                story_id=story_id,
                revision=revision,
            )
            return ok(result)
        except Exception as exc:
            return error("canvas_read_failed", str(exc))


kernel = sk.Kernel()
tools = CanvasTools()
kernel.add_plugin(tools, "canvas")


__all__ = [
    "CanvasTools",
    "kernel",
    "list_event_sources",
    "tools",
    "_canonicalize_canvas_operations_for_context",
]
