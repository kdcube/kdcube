# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import inspect
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

from kdcube_ai_app.apps.chat.sdk.util import normalize_artifact_visibility
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import block_matches_event_source


REACT_POLICY_PHASES = {
    "tool_call_validation",
    "block_production",
    "timeline_projection",
    "compaction_projection",
    "announce_production",
}

PolicyFn = Callable[..., Any]
REACT_EVENT_POLICIES_ATTR = "__kdcube_react_event_policies__"


def _normalize_react_phase(value: Any) -> str:
    react_phase = str(value or "").strip()
    return react_phase if react_phase in REACT_POLICY_PHASES else ""


@dataclass(frozen=True)
class ReactEventPolicy:
    """Registered ReAct event policy implementation."""

    event_policy_id: str
    react_phase: str
    fn: PolicyFn
    description: str = ""


@dataclass(frozen=True)
class ReactEventPolicyBinding:
    """Binding from one event source to a named policy in one ReAct phase."""

    react_phase: str
    event_policy_id: str
    fn: PolicyFn | None = None
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def apply(self, target: Any, **context: Any) -> Any:
        """Apply this policy to the supplied mutable target.

        Policy functions mutate the target they receive. For block production
        the target is usually raw result rows or a block-builder context. For
        timeline/compaction projection it is the mutable timeline block list
        chosen by the existing ReAct caller.
        """
        if self.fn is None:
            return target
        result = self.fn(target, **context, **self.params)
        return target if result is None else result

    async def apply_async(self, target: Any, **context: Any) -> Any:
        """Apply this policy and await coroutine results when necessary."""
        if self.fn is None:
            return target
        result = self.fn(target, **context, **self.params)
        if inspect.isawaitable(result):
            result = await result
        return target if result is None else result


@dataclass(frozen=True)
class ReactEventPolicies:
    """Event-source policy bindings grouped by ReAct lifecycle phase."""

    tool_call_validation: tuple[ReactEventPolicyBinding, ...] = ()
    block_production: tuple[ReactEventPolicyBinding, ...] = ()
    timeline_projection: tuple[ReactEventPolicyBinding, ...] = ()
    compaction_projection: tuple[ReactEventPolicyBinding, ...] = ()
    announce_production: tuple[ReactEventPolicyBinding, ...] = ()

    @classmethod
    def from_specs(
        cls,
        policies: Sequence[Mapping[str, Any]] | None,
        *,
        registry: Mapping[str, ReactEventPolicy] | None = None,
    ) -> "ReactEventPolicies":
        buckets: dict[str, list[ReactEventPolicyBinding]] = {phase: [] for phase in REACT_POLICY_PHASES}
        event_policies = _event_policy_registry(registry)
        for spec in _iter_policy_specs(policies):
            binding = _binding_from_spec(spec, event_policies=event_policies)
            if binding and binding.react_phase in buckets:
                buckets[binding.react_phase].append(binding)
        return cls(
            tool_call_validation=tuple(buckets["tool_call_validation"]),
            block_production=tuple(buckets["block_production"]),
            timeline_projection=tuple(buckets["timeline_projection"]),
            compaction_projection=tuple(buckets["compaction_projection"]),
            announce_production=tuple(buckets["announce_production"]),
        )

    def for_react_phase(self, react_phase: str) -> tuple[ReactEventPolicyBinding, ...]:
        return tuple(getattr(self, react_phase, ()) or ())

    def apply_react_phase(self, react_phase: str, target: Any, **context: Any) -> Any:
        current: Any = target
        for binding in self.for_react_phase(react_phase):
            current = binding.apply(current, react_phase=react_phase, **context)
            if current is None:
                current = target
        return current

    async def apply_react_phase_async(self, react_phase: str, target: Any, **context: Any) -> Any:
        current: Any = target
        for binding in self.for_react_phase(react_phase):
            current = await binding.apply_async(current, react_phase=react_phase, **context)
            if current is None:
                current = target
        return current


def react_event_policy_definition(
    event_policy_id: str,
    *,
    react_phase: str,
    fn: PolicyFn,
    description: str = "",
) -> ReactEventPolicy:
    """Define one registered ReAct event policy implementation."""
    event_policy_id = str(event_policy_id or "").strip()
    react_phase = _normalize_react_phase(react_phase)
    if not event_policy_id:
        raise ValueError("event_policy_id must be non-empty")
    if not react_phase:
        raise ValueError(f"unknown ReAct policy phase: {react_phase}")
    return ReactEventPolicy(
        event_policy_id=event_policy_id,
        react_phase=react_phase,
        fn=fn,
        description=str(description or "").strip(),
    )


def react_event_policy(
    event_policy_id: str,
    *,
    react_phase: str,
    description: str = "",
) -> Callable[[PolicyFn], PolicyFn]:
    """Decorate a function as a discoverable ReAct event policy."""

    def _decorate(fn: PolicyFn) -> PolicyFn:
        policy = react_event_policy_definition(
            event_policy_id,
            react_phase=react_phase,
            fn=fn,
            description=description or (getattr(fn, "__doc__", "") or "").strip(),
        )
        existing = list(getattr(fn, REACT_EVENT_POLICIES_ATTR, ()) or ())
        existing.append(policy)
        setattr(fn, REACT_EVENT_POLICIES_ATTR, tuple(existing))
        return fn

    return _decorate


def tool_call_validation_policy(event_policy_id: str, *, description: str = "") -> Callable[[PolicyFn], PolicyFn]:
    """Register an event policy for the pre-execution tool-call validation phase.

    This phase runs after ReAct binds visible `ref:` parameters and before the
    generic `react.tool.call` block is emitted. Policies receive one mutable
    call-validation target for the occurrence:

    - `tool_id`, `event_source_id`, `tool_call_id`, `event_id`;
    - `base_params` and mutable `final_params`;
    - `state`, `turn_id`, `outdir`, `workdir`;
    - `blocks`: timeline blocks to emit before execution;
    - `notice_rows`: notices to emit through the standard ReAct notice helper;
    - `state_updates`: updates to merge into the ReAct state;
    - `retry_decision` and `stop`: set both to prevent execution and ask the
      model/runtime to retry;
    - `decision_raw_reason`, `write_timeline_local`, and `log_rows`: optional
      caller actions needed by existing ReAct mechanics.

    Policies own validation rules for a tool family. The shared external-tool
    handler only applies the target actions.
    """
    return react_event_policy(event_policy_id, react_phase="tool_call_validation", description=description)


def block_production_policy(event_policy_id: str, *, description: str = "") -> Callable[[PolicyFn], PolicyFn]:
    """Register an event policy for the block-production ReAct phase.

    For tool-backed event sources, this phase starts after the generic
    `react.tool.call` block has already been emitted by the harness. The policy
    receives one mutable result-production accumulator for the occurrence:

    - `ok`, `error`, `ret`, `raw`: normalized tool result and raw response;
    - `blocks`: result-side timeline block candidates;
    - `result_items`: ordinary tool result rows for the shared ReAct
      artifact/result builder. These rows mirror the primary `items` loop used
      by `external.py` and can represent JSON/text/file result surfaces. A
      row may explicitly declare artifact behavior with fields such as
      `artifact_kind`, `visibility`, `write_artifact`,
      `analyze_write_output`, `emit_hosted_file`, `resolve_file_path`,
      `default_mime`, `sources_used`, and `artifact_path_mode`;
    - `source_rows`: exploration rows for the sources pool;
    - `artifact_rows`: file/artifact rows to be consumed by the standard
      artifact block builders. Rows may be already-hosted records or
      runtime-produced file metadata such as exec-style `raw.items`; this field
      is for file/artifact UI blocks, not for search/source-pool rows;
    - `declared_file_items`: explicit file rows derived from
      `{artifact_type:"files", files:[...]}` and fed to the declared-file
      artifact loop;
    - `snapshot_refs`: read-only snapshot payload refs for later projection,
      ANNOUNCE, or compaction. These refs point to state produced outside
      ReAct, or by a tool, and are not an editable state channel. Use a
      source-specific event such as `event.canvas` for mutually writable JSON
      state;
    - `announce_candidates`: data for a later ANNOUNCE phase;
    - `notice_rows`: explicit notice rows to emit through the ReAct notice
      helper. Policies own the decision that an error/warning notice exists;
      the caller only emits these rows with the existing notice transport;
    - `source_rows_merge`: true when `source_rows` should be merged into the
      ReAct sources pool;
    - `result_items_produced`: true when `result_items` fully replaces the
      legacy generic item derivation for this result;
    - `declared_file_items_produced`: true when `declared_file_items` fully
      replaces the legacy declared-file derivation for this result;
    - `notice_rows_produced`: true when this policy set owns error/warning
      notice production for this result, even if the resulting row list is
      empty.

    Multiple block-production policies may run on the same accumulator. Each
    policy should read only the result surface it owns and append to the
    matching accumulator field.
    """
    return react_event_policy(event_policy_id, react_phase="block_production", description=description)


def timeline_projection_policy(event_policy_id: str, *, description: str = "") -> Callable[[PolicyFn], PolicyFn]:
    """Register an event policy for the timeline-projection ReAct phase."""
    return react_event_policy(event_policy_id, react_phase="timeline_projection", description=description)


def compaction_event_policy(event_policy_id: str, *, description: str = "") -> Callable[[PolicyFn], PolicyFn]:
    """Register an event policy for the compaction-projection ReAct phase."""
    return react_event_policy(event_policy_id, react_phase="compaction_projection", description=description)


def announce_event_policy(event_policy_id: str, *, description: str = "") -> Callable[[PolicyFn], PolicyFn]:
    """Register an event policy for the announce-production ReAct phase."""
    return react_event_policy(event_policy_id, react_phase="announce_production", description=description)


def get_react_event_policies(obj: Any) -> tuple[ReactEventPolicy, ...]:
    policies = getattr(obj, REACT_EVENT_POLICIES_ATTR, ()) if obj is not None else ()
    return tuple(p for p in policies if isinstance(p, ReactEventPolicy))


def discover_react_event_policies(owner: ModuleType | Mapping[str, Any] | Any) -> dict[str, ReactEventPolicy]:
    """Discover decorated ReAct event policies from a module or mapping."""
    values = vars(owner).values() if isinstance(owner, ModuleType) else (
        owner.values() if isinstance(owner, Mapping) else vars(owner).values()
    )
    out: dict[str, ReactEventPolicy] = {}
    for obj in values:
        for policy in get_react_event_policies(obj):
            out[policy.event_policy_id] = policy
    return out


@announce_event_policy(event_policy_id="react.announce_production.identity")
@compaction_event_policy(event_policy_id="react.compaction_projection.identity")
@timeline_projection_policy(event_policy_id="react.timeline_projection.identity")
@block_production_policy(event_policy_id="react.block_production.identity")
@tool_call_validation_policy(event_policy_id="react.tool_call_validation.identity")
def identity_policy(target: Any, **_: Any) -> Any:
    """Leave the supplied block-production or block-transform target unchanged."""
    return target


@block_production_policy(event_policy_id="react.block_production.no_timeline")
def no_timeline_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Mark an event occurrence as handled without producing timeline blocks.

    Use this for registered external-event sources that should travel through
    the ordered lane and bundle callbacks, but should not become durable ReAct
    timeline history. The caller still advances the external-event cursor; the
    default event block fallback is suppressed by `blocks_produced=True`.
    """
    if not isinstance(target, MutableMapping):
        return target
    blocks = target.setdefault("blocks", [])
    if isinstance(blocks, list):
        blocks.clear()
    target["blocks_produced"] = True
    target["timeline_blocks_suppressed"] = True
    return target


def _external_event_target_lines(target: Mapping[str, Any], *, snapshot: bool = False) -> list[str]:
    accepted_event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    event_payload = accepted_event.get("payload") if isinstance(accepted_event.get("payload"), Mapping) else {}
    data = event_payload.get("event") if isinstance(event_payload.get("event"), Mapping) else {}
    event_ref = str(event_payload.get("event_ref") or "").strip()
    event_source_id = str(target.get("event_source_id") or accepted_event.get("event_source_id") or "").strip()
    logical_path = str(target.get("logical_path") or accepted_event.get("logical_path") or "").strip()
    hosted_uri = str(target.get("hosted_uri") or accepted_event.get("hosted_uri") or "").strip()
    story_id = str(target.get("story_id") or accepted_event.get("story_id") or "").strip()
    reactive = bool(target.get("reactive") if "reactive" in target else accepted_event.get("reactive"))
    text = str(target.get("text") or "").strip()
    if not text and data:
        text = str(data.get("request") or data.get("summary") or data.get("title") or "").strip()

    lines = ["[SNAPSHOT EVENT]" if snapshot else "[TIMELINE EVENT]"]
    if event_source_id:
        lines.append(f"event_source_id: {event_source_id}")
    if logical_path:
        lines.append(f"logical_path: {logical_path}")
    if hosted_uri:
        lines.append(f"hosted_uri: {hosted_uri}")
    if story_id:
        lines.append(f"story_id: {story_id}")
    lines.append(f"reactive: {'true' if reactive else 'false'}")
    if event_ref:
        lines.append(f"event_ref: {event_ref}")
    if text:
        lines.append("")
        lines.append(text)
    if data:
        try:
            lines.append("")
            lines.append("data:")
            lines.append(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        except Exception:
            pass
    return lines


def _event_block_meta(target: Mapping[str, Any]) -> dict[str, Any]:
    accepted_event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    meta = dict(target.get("meta") or {})
    for key, value in {
        "event_id": target.get("event_id") or accepted_event.get("event_id"),
        "event_source_id": target.get("event_source_id") or accepted_event.get("event_source_id"),
        "event_type": target.get("block_type") or accepted_event.get("type") or "event.external",
        "logical_path": target.get("logical_path") or accepted_event.get("logical_path"),
        "hosted_uri": target.get("hosted_uri") or accepted_event.get("hosted_uri"),
        "story_id": target.get("story_id") or accepted_event.get("story_id"),
        "reactive": target.get("reactive") if "reactive" in target else accepted_event.get("reactive"),
    }.items():
        if value is not None and (not isinstance(value, str) or value.strip()):
            meta[key] = value
    return meta


def _event_block_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    accepted_event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    event_payload = accepted_event.get("payload") if isinstance(accepted_event.get("payload"), Mapping) else {}
    sentinel = object()
    ret = target.get("ret", sentinel)
    if ret is sentinel:
        if "ret" in event_payload:
            ret = event_payload.get("ret")
        elif "event" in event_payload:
            ret = event_payload.get("event")
        elif event_payload.get("event_ref"):
            ret = {"event_ref": event_payload.get("event_ref")}
        else:
            ret = None
    event_ref = event_payload.get("event_ref")
    error = target.get("error")
    if error is None:
        error = accepted_event.get("error")
    if error is None:
        error = event_payload.get("error")
    if error is None and isinstance(ret, Mapping):
        error = ret.get("error")
    ok_value = target.get("ok")
    if ok_value is None:
        ok_value = event_payload.get("ok") if "ok" in event_payload else accepted_event.get("ok", True)
    ok = bool(ok_value)
    if error is not None:
        ok = False
    payload: dict[str, Any] = {
        "event_id": target.get("event_id") or accepted_event.get("event_id"),
        "event_source_id": target.get("event_source_id") or accepted_event.get("event_source_id"),
        "event_type": target.get("block_type") or accepted_event.get("type") or "event.external",
        "logical_path": target.get("logical_path") or accepted_event.get("logical_path"),
        "hosted_uri": target.get("hosted_uri") or accepted_event.get("hosted_uri"),
        "story_id": target.get("story_id") or accepted_event.get("story_id"),
        "reactive": target.get("reactive") if "reactive" in target else accepted_event.get("reactive"),
        "mime": event_payload.get("mime") or accepted_event.get("mime"),
        "status": "success" if ok else "error",
        "ok": ok,
    }
    if ret is not None:
        payload["ret"] = ret
    if event_ref:
        payload["event_ref"] = event_ref
    if error is not None:
        payload["error"] = error
    surfaces = _event_block_surfaces(target)
    if surfaces:
        payload["surfaces"] = surfaces
    return {
        key: value
        for key, value in payload.items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }


def _event_block_surfaces(target: Mapping[str, Any]) -> dict[str, Any]:
    """Return standard production surfaces preserved in an event block body."""
    surfaces: dict[str, Any] = {}
    for key in (
        "source_rows",
        "artifact_rows",
        "declared_file_items",
        "snapshot_refs",
        "announce_candidates",
        "notice_rows",
    ):
        rows = target.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            values = [
                dict(row) if isinstance(row, Mapping) else row
                for row in rows
                if row not in (None, "")
            ]
            if values:
                surfaces[key] = values
    for key in (
        "source_rows_merge",
        "result_items_produced",
        "declared_file_items_produced",
        "notice_rows_produced",
    ):
        if key == "source_rows_merge" and "source_rows" not in surfaces:
            continue
        if key == "declared_file_items_produced" and "declared_file_items" not in surfaces:
            continue
        if key == "notice_rows_produced" and "notice_rows" not in surfaces:
            continue
        if key == "result_items_produced" and "result_items" not in surfaces:
            continue
        if target.get(key) is True:
            surfaces[key] = True
    return surfaces


def _normalize_event_payload_target(target: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Normalize an authored event target into the tool-result-style accumulator."""
    target = tool_default_block_production_policy(target)
    accepted_event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    event_payload = accepted_event.get("payload") if isinstance(accepted_event.get("payload"), Mapping) else {}
    event_source_id = str(target.get("event_source_id") or accepted_event.get("event_source_id") or "").strip()
    event_id = str(target.get("event_id") or accepted_event.get("event_id") or "").strip()
    if event_source_id:
        target.setdefault("tool_id", event_source_id)
    if event_id:
        target.setdefault("tool_call_id", event_id)

    ret_missing = "ret" not in target or target.get("ret") is None
    if ret_missing:
        if "ret" in event_payload:
            target["ret"] = event_payload.get("ret")
        elif "event" in event_payload:
            target["ret"] = event_payload.get("event")
        elif event_payload.get("event_ref"):
            target["ret"] = {"event_ref": event_payload.get("event_ref")}
        else:
            target["ret"] = {}

    raw_missing = "raw" not in target or target.get("raw") is None
    if raw_missing:
        target["raw"] = {
            "ok": event_payload.get("ok", accepted_event.get("ok", True)),
            "ret": target.get("ret"),
            "error": event_payload.get("error", accepted_event.get("error")),
            "event": dict(accepted_event),
        }

    if target.get("ok") is None:
        target["ok"] = event_payload.get("ok", accepted_event.get("ok", True))
    if target.get("error") is None:
        target["error"] = event_payload.get("error", accepted_event.get("error"))
    if not target.get("summary"):
        ret = target.get("ret")
        if isinstance(ret, Mapping):
            target["summary"] = str(ret.get("summary") or ret.get("title") or "").strip()
    return target


def _apply_standard_event_surface_policies(target: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Extract the common tool-result surfaces from an authored event result."""
    for policy_name in (
        "exploration_results_block_production_policy",
        "hosted_artifacts_block_production_policy",
        "declared_file_items_block_production_policy",
        "snapshot_refs_block_production_policy",
        "announce_candidates_block_production_policy",
    ):
        policy = globals().get(policy_name)
        if callable(policy):
            policy(target)
    return target


def _default_event_block(target: MutableMapping[str, Any], *, snapshot: bool = False) -> None:
    blocks = target.setdefault("blocks", [])
    block_factory = target.get("block_factory")
    if not callable(block_factory):
        return
    accepted_event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    event_id = str(target.get("event_id") or accepted_event.get("event_id") or "").strip()
    event_source_id = str(target.get("event_source_id") or accepted_event.get("event_source_id") or "").strip()
    event_type = str(target.get("block_type") or accepted_event.get("type") or ("event.snapshot" if snapshot else "event.external")).strip()
    logical_path = str(target.get("logical_path") or accepted_event.get("logical_path") or target.get("path") or "").strip()
    event_path = logical_path or str(target.get("path") or "").strip()
    common_meta = _event_block_meta({**target, "block_type": event_type})
    result_payload = _event_block_payload({**target, "block_type": event_type})
    blocks.append(block_factory(
        type=event_type,
        author=str(target.get("author") or "user"),
        turn_id=str(target.get("turn_id") or ""),
        ts=str(target.get("ts") or ""),
        mime="application/json",
        text=json.dumps(result_payload, ensure_ascii=False, indent=2, default=str),
        path=event_path,
        meta={
            **common_meta,
            "event_id": event_id,
            "event_source_id": event_source_id,
            "event_type": event_type,
            "event_occurrence": True,
        },
    ))


@block_production_policy(event_policy_id="react.block_production.tool_default")
def tool_default_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Initialize the common mutable result-production target for ordinary tools.

    This policy does not create durable blocks yet. For tool-backed event
    sources, the generic `react.tool.call` block is emitted by the harness before
    execution. Block-production policies run after the tool returns and describe
    only the result surfaces: result blocks, source rows, artifact rows,
    snapshot refs, announce candidates, and explicit production markers.
    """
    if not isinstance(target, MutableMapping):
        return target
    target.setdefault("blocks", [])
    target.setdefault("result_items", [])
    target.setdefault("source_rows", [])
    target.setdefault("artifact_rows", [])
    target.setdefault("declared_file_items", [])
    target.setdefault("snapshot_refs", [])
    target.setdefault("announce_candidates", [])
    target.setdefault("notice_rows", [])
    target.setdefault("source_rows_merge", False)
    target.setdefault("result_items_produced", False)
    target.setdefault("declared_file_items_produced", False)
    target.setdefault("notice_rows_produced", False)
    return target


def _target_tool_id(target: Mapping[str, Any]) -> str:
    return str(target.get("tool_id") or target.get("event_source_id") or "").strip()


def _target_summary(target: Mapping[str, Any]) -> str:
    return str(target.get("summary") or "").strip()


def _target_error(target: Mapping[str, Any]) -> Any:
    err = target.get("error")
    if err is not None:
        return err
    err = target.get("call_error")
    if err is not None:
        return err
    raw = target.get("raw")
    if isinstance(raw, Mapping):
        if raw.get("call_error") is not None:
            return raw.get("call_error")
        if raw.get("error") is not None:
            return raw.get("error")
        if raw.get("status") == "error":
            return {
                "code": "tool_error",
                "message": _target_summary(target) or "Tool execution failed",
                "where": _target_tool_id(target),
            }
    return None


def _target_call_error(target: Mapping[str, Any]) -> Any:
    err = target.get("call_error")
    if err is not None:
        return err
    raw = target.get("raw")
    if isinstance(raw, Mapping):
        return raw.get("call_error")
    return None


def _citation_sources_from_content(value: Any) -> list[Any]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any

        return list(extract_citation_sids_any(value) or [])
    except Exception:
        return []


def _default_write_mime(tool_id: str) -> str:
    try:
        from kdcube_ai_app.apps.chat.sdk.tools.tools_insights import default_mime_for_write_tool

        return str(default_mime_for_write_tool(tool_id) or "").strip()
    except Exception:
        return ""


def _append_notice_row(
    target: MutableMapping[str, Any],
    *,
    code: str,
    message: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    rows = target.setdefault("notice_rows", [])
    target["notice_rows_produced"] = True
    if not isinstance(rows, list):
        return
    payload = {
        "code": str(code or "").strip(),
        "message": str(message or "").strip(),
        "extra": dict(extra or {}),
    }
    if payload["code"] and payload["message"]:
        rows.append(payload)


def _append_call_error_notice(target: MutableMapping[str, Any]) -> None:
    err = _target_call_error(target)
    if not err:
        return
    message = (err.get("message") if isinstance(err, Mapping) else str(err)) or "tool call failed"
    _append_notice_row(
        target,
        code="tool_call_error",
        message=message,
        extra={"tool_id": _target_tool_id(target), "error": err},
    )


def _result_item(
    target: Mapping[str, Any],
    *,
    output: Any,
    error: Any = None,
    artifact_id: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    tool_id = _target_tool_id(target)
    return {
        "artifact_id": artifact_id or tool_id,
        "output": output,
        "summary": _target_summary(target) if summary is None else str(summary or ""),
        "error": _target_error(target) if error is None else error,
    }


def _append_result_item(target: MutableMapping[str, Any], item: Mapping[str, Any]) -> None:
    items = target.setdefault("result_items", [])
    if isinstance(items, list):
        items.append(dict(item))
    target["result_items_produced"] = True
    err = item.get("error") if isinstance(item, Mapping) else None
    if err:
        artifact_id = str(item.get("artifact_id") or _target_tool_id(target) or "").strip()
        message = (err.get("message") if isinstance(err, Mapping) else str(err)) or "Tool execution failed"
        _append_notice_row(
            target,
            code="tool_result_error",
            message=message,
            extra={"tool_id": _target_tool_id(target), "artifact_id": artifact_id, "error": err},
        )


@block_production_policy(event_policy_id="react.block_production.generic_result_item")
def generic_result_item_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the ordinary non-exec result item used by the shared builder.

    This mirrors the old `external.py` default for non-exec tools:
    one row with `artifact_id=<tool_id>`, `output=<tool output>`,
    `summary=<tool summary>`, and the normalized tool/call error. The shared
    artifact/result loop still owns the final meta, binary, visible text, and
    hosting blocks.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    _append_call_error_notice(target)
    item = _result_item(target, output=target.get("ret"))
    if target.get("source_rows_merge") is True:
        item.update(
            {
                "artifact_kind": "file",
                "artifact_path_mode": "sources_pool",
            }
        )
    _append_result_item(target, item)
    return target


@block_production_policy(event_policy_id="react.block_production.write_tool_result")
def write_tool_result_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce the ordinary rendering/write-tool result item.

    This mirrors the old write-tool branch in `external.py`: on success the
    produced artifact is the requested `params.path`; on failure the row carries
    the normalized error and remains an internal/error result for the shared
    artifact/result builder.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    _append_call_error_notice(target)
    final_params = target.get("final_params") if isinstance(target.get("final_params"), Mapping) else {}
    error = _target_error(target)
    output = target.get("ret")
    visibility = "internal" if error is not None else "external"
    if error is None:
        path = str(final_params.get("path") or "").strip()
        if path:
            output = path
    item = _result_item(target, output=output, error=error)
    sources_used = _citation_sources_from_content(final_params.get("content"))
    default_mime = _default_write_mime(_target_tool_id(target))
    item.update(
        {
            "artifact_kind": "file",
            "visibility": visibility,
            "write_artifact": error is None,
            "analyze_write_output": error is None,
            "emit_hosted_file": error is None,
            "resolve_file_path": error is None,
        }
    )
    if sources_used:
        item["sources_used"] = sources_used
    if default_mime:
        item["default_mime"] = default_mime
    _append_result_item(target, item)
    return target


def _looks_like_declared_file(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    return bool(
        row.get("path")
        or row.get("physical_path")
        or row.get("local_path")
        or row.get("artifact_path")
        or row.get("logical_path")
        or row.get("hosted_uri")
        or row.get("rn")
        or row.get("key")
    )


def _declared_file_rows_from_result(value: Any) -> list[dict[str, Any]]:
    data = value
    if isinstance(data, Mapping) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if not isinstance(data, Mapping):
        return []
    if str(data.get("artifact_type") or "").strip() != "files":
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [dict(row) for row in files if _looks_like_declared_file(row)]


@block_production_policy(event_policy_id="react.block_production.declared_file_items")
def declared_file_items_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Produce declared-file rows from `{artifact_type:"files", files:[...]}`.

    This mirrors the old `_declared_files_to_tool_items()` path in
    `external.py`. The policy does not host or render files itself; it produces
    `declared_file_items` for the shared declared-file artifact loop.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    rows = _declared_file_rows_from_result(target.get("ret"))
    if not rows:
        rows = _declared_file_rows_from_result(target.get("raw"))
    items = target.setdefault("declared_file_items", [])
    tool_id = _target_tool_id(target)
    default_summary = _target_summary(target)
    if isinstance(items, list):
        for idx, row in enumerate(rows):
            filename = str(row.get("filename") or "").strip()
            raw_path = str(
                row.get("physical_path")
                or row.get("path")
                or row.get("local_path")
                or row.get("artifact_path")
                or row.get("logical_path")
                or ""
            ).strip()
            if not filename:
                filename = PurePosixPath(raw_path).name or f"file_{idx + 1}"
            mime = str(row.get("mime") or row.get("mime_type") or "").strip() or "application/octet-stream"
            description = str(row.get("description") or row.get("summary") or filename or "").strip()
            value = {
                "type": "file",
                "path": raw_path,
                "filename": filename,
                "mime": mime,
                "description": description,
            }
            for key in (
                "hosted_uri",
                "rn",
                "key",
                "size",
                "size_bytes",
                "local_path",
                "physical_path",
                "logical_path",
                "artifact_path",
            ):
                if row.get(key) not in ("", None):
                    value[key] = row[key]
            already_hosted = bool(
                row.get("hosted") is True
                or row.get("already_hosted") is True
                or ((row.get("hosted_uri") or row.get("rn") or row.get("key")) and not raw_path)
            )
            hosted_record = {
                "slot": str(row.get("slot") or row.get("artifact_id") or "").strip(),
                "key": row.get("key") or "",
                "filename": filename,
                "mime": mime,
                "size": row.get("size") if row.get("size") is not None else row.get("size_bytes"),
                "tool_id": tool_id,
                "description": description,
                "owner_id": row.get("owner_id") or "",
                "rn": row.get("rn") or "",
                "hosted_uri": row.get("hosted_uri") or "",
                "physical_path": row.get("physical_path") or raw_path,
            }
            artifact_id = str(
                row.get("artifact_id")
                or row.get("slot")
                or row.get("resource_id")
                or f"{tool_id}_file_{idx + 1}"
            ).strip()
            items.append(
                {
                    "artifact_id": artifact_id,
                    "output": value,
                    "artifact_kind": "file",
                    "summary": description or default_summary,
                    "filepath": raw_path,
                    "visibility": normalize_artifact_visibility(row.get("visibility"), default="external"),
                    "already_hosted": already_hosted,
                    "emitted": bool(row.get("emitted")),
                    "hosted_record": hosted_record,
                }
            )
    target["declared_file_items_produced"] = True
    return target


def _source_like_rows_from_result(value: Any) -> list[dict[str, Any]]:
    data = value
    if isinstance(data, Mapping) and "ret" in data:
        data = data.get("ret")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []
    if isinstance(data, Mapping):
        if isinstance(data.get("exploration_results"), list):
            data = data.get("exploration_results")
        elif isinstance(data.get("source_rows"), list):
            data = data.get("source_rows")
        elif isinstance(data.get("items"), list):
            data = data.get("items")
        elif isinstance(data.get("results"), list):
            data = data.get("results")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, Mapping):
            continue
        if row.get("url") or row.get("content") is not None or row.get("sid") is not None:
            rows.append(dict(row))
    return rows


def _ret_mapping(target: MutableMapping[str, Any]) -> Mapping[str, Any]:
    ret = target.get("ret")
    if isinstance(ret, Mapping):
        return ret
    raw = target.get("raw")
    if isinstance(raw, Mapping):
        raw_ret = raw.get("ret")
        if isinstance(raw_ret, Mapping):
            return raw_ret
    return {}


@block_production_policy(event_policy_id="react.block_production.exploration_results")
def exploration_results_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Extract exploration result rows from a raw tool result target.

    The target is the full result-production envelope for one tool/event
    occurrence, not pre-filtered rows. This policy looks for search/fetch-like
    result rows under `ret` and appends them to `target["source_rows"]`; it also
    sets `target["source_rows_merge"]=True` so the caller merges them into the
    ReAct sources pool.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    target["source_rows_merge"] = True
    rows = _source_like_rows_from_result(target.get("ret"))
    if not rows:
        rows = _source_like_rows_from_result(target.get("raw"))
    if not rows:
        return target
    source_rows = target.setdefault("source_rows", [])
    if isinstance(source_rows, list):
        source_rows.extend(rows)
    return target


@block_production_policy(event_policy_id="react.block_production.hosted_artifacts")
def hosted_artifacts_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Collect hosted/file artifact rows from a composite tool result.

    The policy only appends recognized artifact metadata to
    `target["artifact_rows"]`. The caller or a later production policy still
    owns canonical artifact block construction and hosting behavior.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    ret = _ret_mapping(target)
    candidates: list[Any] = []
    for key in ("hosted_artifacts", "artifact_rows", "files"):
        value = ret.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if str(ret.get("artifact_type") or "").strip() == "files" and isinstance(ret.get("files"), list):
        candidates.extend(ret.get("files") or [])
    artifact_rows = target.setdefault("artifact_rows", [])
    if isinstance(artifact_rows, list):
        artifact_rows.extend(dict(row) for row in candidates if isinstance(row, Mapping))
    return target


@block_production_policy(event_policy_id="react.block_production.snapshot_refs")
def snapshot_refs_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Collect read-only snapshot refs from a composite tool/event result.

    Snapshot refs are durable or rehostable references such as
    `fi:<turn_id>.snapshots/current.yaml` or `ext:<bundle>/snapshots/current`.
    They tell later projection/ANNOUNCE/compaction policies that a snapshot can
    be read. They do not authorize ReAct to edit that snapshot in place.
    Mutually writable state should be represented by a dedicated structural
    event, for example `event.canvas`.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    ret = _ret_mapping(target)
    refs: list[Any] = []
    if ret.get("snapshot_ref"):
        refs.append(ret.get("snapshot_ref"))
    for key in ("snapshot_refs", "snapshots"):
        value = ret.get(key)
        if isinstance(value, list):
            refs.extend(value)
    snapshot_refs = target.setdefault("snapshot_refs", [])
    if isinstance(snapshot_refs, list):
        snapshot_refs.extend(ref for ref in refs if isinstance(ref, (str, Mapping)) and ref)
    return target


@block_production_policy(event_policy_id="react.block_production.announce_candidates")
def announce_candidates_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Collect ANNOUNCE candidates from a composite tool result.

    ANNOUNCE is not stored on the timeline. This policy only records candidate
    data in the production accumulator so an explicit announce-production phase
    can decide whether to expose it in the ephemeral tail.
    """
    if not isinstance(target, MutableMapping):
        return target
    target = tool_default_block_production_policy(target)
    ret = _ret_mapping(target)
    candidates: list[Any] = []
    for key in ("announce_candidate", "announce_entry"):
        value = ret.get(key)
        if value:
            candidates.append(value)
    for key in ("announce_candidates", "announce_entries"):
        value = ret.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    announce_candidates = target.setdefault("announce_candidates", [])
    if isinstance(announce_candidates, list):
        announce_candidates.extend(candidate for candidate in candidates if candidate)
    return target


@compaction_event_policy(event_policy_id="react.compaction_projection.hide_by_segment")
@timeline_projection_policy(event_policy_id="react.timeline_projection.hide_by_segment")
def hide_by_segment_policy(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any = None,
    segments: Sequence[str] | None = None,
    replacement_text: str = "",
    hidden_reason: str = "event_source_policy",
    hidden_prune_scope: str = "",
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    """Hide this source's blocks when their temporary timeline segment matches.

    The caller must patch blocks with `_react_timeline_segment` before invoking
    this policy and remove only that mark afterwards. The policy mutates
    matching blocks inline and leaves unrelated blocks unchanged.
    """
    allowed = {str(item or "").strip() for item in (segments or ("old",)) if str(item or "").strip()}
    if not allowed:
        return timeline
    source_id = str(getattr(source, "event_source_id", "") or "").strip()
    replacement = str(replacement_text or "")
    for block in timeline or []:
        if not isinstance(block, MutableMapping):
            continue
        if source_id and not block_matches_event_source(block, source_id, call_meta=call_meta):
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), MutableMapping) else {}
        segment = str(meta.get("_react_timeline_segment") or "").strip()
        if segment not in allowed:
            continue
        meta = dict(meta)
        if hidden_prune_scope:
            meta["hidden_prune_scope"] = str(hidden_prune_scope)
        elif segment:
            meta["hidden_prune_scope"] = segment
        if hidden_reason:
            meta["hidden_reason"] = str(hidden_reason)
        meta["hidden"] = True
        block["meta"] = meta
        block["hidden"] = True
        if replacement:
            block["replacement_text"] = replacement
    return timeline


from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import canvas as _canvas_policies
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import external as _external_policies
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import snapshot as _snapshot_policies
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import user_events as _user_event_policies
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.canvas import (
    canvas_event_default_block_production_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.external import (
    external_event_default_block_production_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.snapshot import (
    snapshot_event_default_block_production_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.user_events import (
    user_attachment_default_block_production_policy,
    user_followup_default_block_production_policy,
    user_prompt_default_block_production_policy,
    user_steer_default_block_production_policy,
)


DEFAULT_REACT_EVENT_POLICIES: dict[str, ReactEventPolicy] = {}
for _policy_owner in (
    globals(),
    _external_policies,
    _snapshot_policies,
    _canvas_policies,
    _user_event_policies,
):
    DEFAULT_REACT_EVENT_POLICIES.update(discover_react_event_policies(_policy_owner))


def unknown_policy_paths(
    policies: Sequence[Mapping[str, Any]] | None,
    *,
    registry: Mapping[str, ReactEventPolicy] | None = None,
) -> list[str]:
    unknown: list[str] = []
    event_policies = _event_policy_registry(registry)
    try:
        specs = list(_iter_policy_specs(policies))
    except Exception as exc:
        return [f"policies: {exc}"]
    for idx, spec in enumerate(specs):
        react_phase = _normalize_react_phase(spec.get("react_phase"))
        if not react_phase:
            unknown.append(f"policies[{idx}].react_phase")
            continue
        event_policy_id = str(spec.get("event_policy_id") or "").strip()
        if not event_policy_id:
            unknown.append(f"policies[{idx}].event_policy_id")
            continue
        registered = event_policies.get(event_policy_id)
        if registered is None:
            unknown.append(f"{event_policy_id}")
            continue
        if registered.react_phase != react_phase:
            unknown.append(f"{event_policy_id}.react_phase:{react_phase}!={registered.react_phase}")
    return unknown


def _event_policy_registry(
    registry: Mapping[str, ReactEventPolicy] | None,
) -> dict[str, ReactEventPolicy]:
    policies = dict(DEFAULT_REACT_EVENT_POLICIES)
    for key, value in (registry or {}).items():
        event_policy_id = str(key or "").strip()
        if event_policy_id and isinstance(value, ReactEventPolicy):
            policies[event_policy_id] = value
    return policies


def _binding_from_spec(
    spec: Mapping[str, Any],
    *,
    event_policies: Mapping[str, ReactEventPolicy],
) -> ReactEventPolicyBinding | None:
    react_phase = _normalize_react_phase(spec.get("react_phase"))
    if not react_phase:
        return None
    event_policy_id = str(spec.get("event_policy_id") or "").strip()
    if not event_policy_id:
        return None
    registered = event_policies.get(event_policy_id)
    if registered is None or registered.react_phase != react_phase:
        return None
    params = spec.get("params") if isinstance(spec.get("params"), Mapping) else {}
    description = str(spec.get("description") or registered.description or "").strip()
    return ReactEventPolicyBinding(
        react_phase=react_phase,
        event_policy_id=event_policy_id,
        fn=registered.fn,
        params=dict(params),
        description=description,
    )


def _iter_policy_specs(policies: Sequence[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if policies is None:
        return []
    if isinstance(policies, Mapping) or isinstance(policies, (str, bytes)):
        raise ValueError("policies must be a list of policy binding objects")
    return [spec for spec in policies if isinstance(spec, Mapping)]
