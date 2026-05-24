# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional


STATS_SCHEMA = "kdcube.telemetry.v1"
DEFAULT_EVENT_TENANT = "default"
DEFAULT_EVENT_PROJECT = "main"

STATS_COMM_EVENT_TYPES = [
    "accounting.usage",
    "chat.complete",
    "chat.error",
    "chat.conversation.accepted",
    "chat.conversation.turn.completed",
    "kdcube.copilot.workflow.turn.started",
    "kdcube.copilot.workflow.turn.completed",
    "kdcube.copilot.workflow.turn.failed",
    "kdcube.copilot.mcp.call",
    "queue.continuation.accepted",
    "react.tool.call",
    "react.skill.read",
    "timeline.external.accepted",
]

STATS_COMM_DATA_KEYS = [
    "agent_costs",
    "attachment_count",
    "attachments_count",
    "breakdown",
    "chat_input_kind",
    "chars",
    "cost_total_usd",
    "citation_count",
    "citations_count",
    "duration_ms",
    "error_code",
    "error_count",
    "exception_type",
    "file_count",
    "input_kind",
    "input_tokens",
    "latency_ms",
    "mcp_address",
    "mcp_endpoint",
    "mcp_name",
    "message_len",
    "message_kind",
    "missing",
    "missing_count",
    "model_or_service",
    "output_tokens",
    "produced_file_count",
    "provider",
    "query_len",
    "requested_count",
    "reported_values",
    "resolved_count",
    "result_count",
    "service_type",
    "skills",
    "tool",
    "tool_call_id",
    "tool_family",
    "tool_id",
    "top_k",
]

STATS_COMM_EVENT_SELECTOR: Dict[str, Any] = {
    "include": {"types": STATS_COMM_EVENT_TYPES},
    "privacy": {"data_keys": STATS_COMM_DATA_KEYS},
}

TelemetrySender = Callable[[str, Dict[str, Any], Dict[str, str], float], Awaitable[Dict[str, Any]] | Dict[str, Any]]


@dataclass(frozen=True)
class StatsTelemetryTarget:
    """REST target for posting stats telemetry batches."""

    endpoint_url: str
    token: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0

    @property
    def url(self) -> str:
        return str(self.endpoint_url or "").strip()

    def request_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in dict(self.headers or {}).items()})
        has_authorization = any(str(key).lower() == "authorization" for key in headers)
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        elif not has_authorization:
            raise ValueError("StatsTelemetryTarget requires a bearer token or Authorization header")
        return headers


def configure_stats_event_recording(
    comm: Any,
    sink: Callable[..., Any],
    *,
    selector: Mapping[str, Any] | None = None,
    scope: Any = None,
    mode: str = "replace",
    max_events: int = 500,
) -> Any:
    """Configure a communicator to record stats-relevant events and use a sink."""
    comm.record(
        selector or STATS_COMM_EVENT_SELECTOR,
        scope=scope,
        mode=mode,
        max_events=max_events,
    )
    comm.set_event_sink(sink)
    return comm


class StatsTelemetrySink:
    """Callable event sink for ``ChatCommunicator.send_recorded_events``.

    The sink maps recorded comm envelopes to ``kdcube.telemetry.v1`` events and
    posts them as one bounded batch to a REST endpoint.
    """

    def __init__(
        self,
        target: StatsTelemetryTarget,
        *,
        source_kube: str = "",
        source_component: str = "comm",
        source_bundle: str = "",
        sender: Optional[TelemetrySender] = None,
    ) -> None:
        self.target = target
        self.source_kube = str(source_kube or "")
        self.source_component = str(source_component or "comm")
        self.source_bundle = str(source_bundle or "")
        self.sender = sender

    async def __call__(self, batch: List[Dict[str, Any]], *, comm: Any = None, filter: Any = None) -> Dict[str, Any]:
        del filter
        events = recorded_comm_batch_to_telemetry(
            batch,
            comm=comm,
            default_tenant=DEFAULT_EVENT_TENANT,
            default_project=DEFAULT_EVENT_PROJECT,
            source_kube=self.source_kube,
            source_component=self.source_component,
            source_bundle=self.source_bundle,
        )
        if not events:
            return {
                "ok": True,
                "accepted": len(batch or []),
                "sent": len(batch or []),
                "telemetry_events": 0,
                "skipped_records": len(batch or []),
            }

        payload = {"events": events}
        result = await self._send(payload)
        unwrapped = dict(result or {})
        ok = bool(unwrapped.get("ok", True)) if isinstance(unwrapped, Mapping) else True
        sent_records = len(batch or []) if ok else 0
        return {
            "ok": ok,
            "accepted": sent_records,
            "sent": sent_records,
            "telemetry_events": len(events),
            "telemetry_result": unwrapped,
        }

    async def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sender = self.sender or _default_http_sender
        url = self.target.url
        if not url:
            raise ValueError("StatsTelemetryTarget.endpoint_url is required")
        result = sender(
            url,
            payload,
            self.target.request_headers(),
            float(self.target.timeout_seconds or 10.0),
        )
        if inspect.isawaitable(result):
            result = await result
        return dict(result or {})


def recorded_comm_batch_to_telemetry(
    items: Iterable[Mapping[str, Any]],
    *,
    comm: Any = None,
    default_tenant: str = "default",
    default_project: str = "main",
    source_kube: str = "",
    source_component: str = "comm",
    source_bundle: str = "",
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for item in items or []:
        events.extend(
            recorded_comm_item_to_telemetry(
                item,
                comm=comm,
                default_tenant=default_tenant,
                default_project=default_project,
                source_kube=source_kube,
                source_component=source_component,
                source_bundle=source_bundle,
            )
        )
    return events


def recorded_comm_item_to_telemetry(
    item: Mapping[str, Any],
    *,
    comm: Any = None,
    default_tenant: str = "default",
    default_project: str = "main",
    source_kube: str = "",
    source_component: str = "comm",
    source_bundle: str = "",
) -> List[Dict[str, Any]]:
    typ = _safe(item.get("type"), max_len=128)
    if not typ:
        return []
    ctx = _context(
        item,
        comm=comm,
        default_tenant=default_tenant,
        default_project=default_project,
        source_kube=source_kube,
        source_component=source_component,
        source_bundle=source_bundle,
    )
    if typ == "react.tool.call":
        return [_tool_event(item, ctx)]
    if typ == "react.skill.read":
        return _skill_events(item, ctx)
    if typ == "kdcube.copilot.mcp.call":
        return [_mcp_event(item, ctx)]
    if typ == "accounting.usage":
        return [_accounting_event(item, ctx)]
    if typ in {
        "chat.conversation.accepted",
        "queue.continuation.accepted",
        "timeline.external.accepted",
    }:
        return [_chat_message_event(item, ctx)]
    if typ in {
        "kdcube.copilot.workflow.turn.started",
        "kdcube.copilot.workflow.turn.completed",
        "kdcube.copilot.workflow.turn.failed",
        "chat.conversation.turn.completed",
        "chat.complete",
    }:
        return [_workflow_event(item, ctx)]
    if typ == "chat.error" or _status(item) in {"error", "failed"}:
        return [_error_event(item, ctx)]
    return [_comm_event(item, ctx)]


def _chat_message_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    ev = _mapping(item.get("event"))
    typ = _safe(item.get("type"), max_len=128)
    input_kind = _chat_input_kind(data, typ)
    metrics = _metrics(data)
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="chat.message",
        dimensions={
            "role": "user",
            "input_kind": input_kind,
            "type": typ,
            "status": status,
            "agent": _safe(ev.get("agent") or "user", max_len=128),
            "step": _safe(ev.get("step") or "chat.user.message", max_len=128),
            "source_bundle": ctx["source_bundle"],
        },
        metrics=metrics,
        status=status,
    )


def _tool_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    ev = _mapping(item.get("event"))
    tool = _safe(data.get("tool_id") or data.get("tool") or ev.get("step") or "unknown", max_len=160)
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="tool.invoke",
        dimensions={
            "tool": tool,
            "type": _safe(item.get("type"), max_len=128),
            "status": status,
            "agent": _safe(ev.get("agent"), max_len=128),
            "step": _safe(ev.get("step"), max_len=128),
            "source_bundle": ctx["source_bundle"],
        },
        metrics=_metrics(data, include_latency=True),
        status=status,
        error_kind=_error_kind(data, status),
    )


def _skill_events(item: Mapping[str, Any], ctx: Mapping[str, str]) -> List[Dict[str, Any]]:
    data = _mapping(item.get("data"))
    ev = _mapping(item.get("event"))
    skills = data.get("skills") if isinstance(data.get("skills"), list) else []
    if not skills:
        skills = [{"id": "unknown", "name": "unknown", "status": _status(item)}]
    out: List[Dict[str, Any]] = []
    for index, skill in enumerate(skills):
        skill_map = _mapping(skill)
        skill_id = _safe(
            skill_map.get("id")
            or skill_map.get("path")
            or skill_map.get("local_id")
            or "unknown",
            max_len=160,
        )
        skill_name = _safe(
            skill_map.get("name")
            or skill_map.get("local_id")
            or skill_id,
            max_len=160,
        )
        out.append(
            _base_event(
                item,
                ctx,
                name="skill.read",
                suffix=f"skill:{index}:{skill_id}",
                dimensions={
                    "skill_id": skill_id,
                    "skill_name": skill_name,
                    "agent": _safe(ev.get("agent") or "react.read", max_len=128),
                    "status": _safe(skill_map.get("status") or _status(item), max_len=80),
                    "tool": _safe(data.get("tool_id") or "react.read", max_len=128),
                    "source_bundle": ctx["source_bundle"],
                },
                metrics=_metrics(data),
                status=_status(item),
            )
        )
    return out


def _mcp_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    endpoint = _safe(data.get("mcp_endpoint") or data.get("tool") or "unknown", max_len=160)
    address = _safe(data.get("mcp_address") or data.get("mcp_name"), max_len=180)
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="mcp.call",
        dimensions={
            "mcp_address": address,
            "mcp_endpoint": endpoint,
            "status": status,
            "source_bundle": ctx["source_bundle"],
        },
        metrics=_metrics(data, include_latency=True),
        status=status,
        error_kind=_error_kind(data, status, default_kind="mcp_error"),
        data={"reported_values": _reported_values(data.get("reported_values"))},
    )


def _accounting_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    breakdown = _accounting_breakdown(data.get("breakdown"))
    first = breakdown[0] if breakdown else {}
    metrics = _metrics(data)
    if "cost_total_usd" in data:
        metrics["cost_total_usd"] = _number(data.get("cost_total_usd"))
    conversation = _mapping(item.get("conversation"))
    stable_identity = {
        "tenant": ctx.get("tenant") or "",
        "project": ctx.get("project") or "",
        "source_bundle": ctx.get("source_bundle") or "",
        "user_id": ctx.get("user_id") or "",
        "session_id": _safe(conversation.get("session_id"), max_len=160),
        "conversation_id": _safe(conversation.get("conversation_id"), max_len=160),
        "turn_id": _safe(conversation.get("turn_id"), max_len=160),
        "breakdown": breakdown,
        "cost_total_usd": metrics.get("cost_total_usd"),
    }
    return _base_event(
        item,
        ctx,
        name="accounting.usage",
        stable_identity=stable_identity,
        dimensions={
            "service_type": _safe(first.get("service_type") or data.get("service_type"), max_len=80),
            "provider": _safe(first.get("provider") or data.get("provider"), max_len=128),
            "model_or_service": _safe(first.get("model_or_service") or data.get("model_or_service"), max_len=160),
            "agent": _safe(first.get("agent"), max_len=128),
            "source_bundle": ctx["source_bundle"],
        },
        metrics=metrics,
        status=_status(item),
        data={"breakdown": breakdown},
    )


def _workflow_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    ev = _mapping(item.get("event"))
    typ = _safe(item.get("type"), max_len=128)
    workflow = _safe(ev.get("agent") or typ.rsplit(".", 2)[0] or "workflow", max_len=160)
    step = _safe(ev.get("step") or typ.rsplit(".", 1)[-1] or "step", max_len=160)
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="workflow.step",
        dimensions={
            "workflow": workflow,
            "step": step,
            "status": status,
            "type": typ,
            "run_id": _safe(data.get("run_id") or data.get("thread_id"), max_len=160),
            "source_bundle": ctx["source_bundle"],
        },
        metrics=_metrics(data, include_latency=True),
        status=status,
    )


def _error_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    data = _mapping(item.get("data"))
    ev = _mapping(item.get("event"))
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="error",
        dimensions={
            "error_kind": _error_kind(data, status) or _safe(item.get("type"), max_len=128) or "error",
            "surface": _safe(ev.get("step") or item.get("socket_event"), max_len=160),
            "type": _safe(item.get("type"), max_len=128),
            "source_bundle": ctx["source_bundle"],
        },
        metrics=_metrics(data, include_latency=True),
        status=status,
        error_kind=_error_kind(data, status) or "error",
    )


def _comm_event(item: Mapping[str, Any], ctx: Mapping[str, str]) -> Dict[str, Any]:
    ev = _mapping(item.get("event"))
    data = _mapping(item.get("data"))
    status = _status(item)
    return _base_event(
        item,
        ctx,
        name="comm.event",
        dimensions={
            "type": _safe(item.get("type"), max_len=128),
            "agent": _safe(ev.get("agent"), max_len=128),
            "step": _safe(ev.get("step"), max_len=128),
            "status": status,
            "source_bundle": ctx["source_bundle"],
        },
        metrics=_metrics(data, include_latency=True),
        status=status,
    )


def _base_event(
    item: Mapping[str, Any],
    ctx: Mapping[str, str],
    *,
    name: str,
    dimensions: Mapping[str, Any],
    metrics: Mapping[str, Any],
    status: str,
    suffix: str = "",
    stable_identity: Optional[Mapping[str, Any]] = None,
    error_kind: str = "",
    data: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    conversation = _mapping(item.get("conversation"))
    event_id = _event_id(item, name=name, suffix=suffix, stable_identity=stable_identity)
    timestamp = _record_timestamp(item)
    return {
        "schema": STATS_SCHEMA,
        "event_id": event_id,
        "event_type": "metric",
        "name": name,
        "origin": "comm.recorded",
        "tenant": ctx["tenant"],
        "project": ctx["project"],
        "timestamp": timestamp,
        "timezone": "UTC",
        "source_kube": ctx.get("source_kube") or "",
        "source_component": ctx.get("source_component") or "comm",
        "source_bundle": ctx.get("source_bundle") or "",
        "user_id": ctx.get("user_id") or "",
        "user_type": ctx.get("user_type") or "",
        "session_id": _safe(conversation.get("session_id"), max_len=160),
        "conversation_id": _safe(conversation.get("conversation_id"), max_len=160),
        "turn_id": _safe(conversation.get("turn_id"), max_len=160),
        "value": 1,
        "tags": {},
        "dimensions": _clean_mapping(dimensions),
        "metrics": _clean_metrics(metrics),
        "status": status or "success",
        "error_kind": error_kind or None,
        "privacy": {
            "contains_content": False,
            "content_retention": "none",
            "source_data_redacted": bool(_mapping(item.get("privacy")).get("data_redacted")),
        },
        "meta": {
            "record_id": _safe(item.get("record_id"), max_len=180),
            "comm_type": _safe(item.get("type"), max_len=128),
            "socket_event": _safe(item.get("socket_event"), max_len=128),
            "route_key": _safe(item.get("route_key"), max_len=128),
        },
        "data": dict(data or {}),
    }


def _context(
    item: Mapping[str, Any],
    *,
    comm: Any,
    default_tenant: str,
    default_project: str,
    source_kube: str,
    source_component: str,
    source_bundle: str,
) -> Dict[str, str]:
    service = _mapping(item.get("service"))
    return {
        "tenant": _safe(service.get("tenant") or getattr(comm, "tenant", None) or default_tenant, max_len=80) or "default",
        "project": _safe(service.get("project") or getattr(comm, "project", None) or default_project, max_len=80) or "main",
        "user_id": _safe(service.get("user") or getattr(comm, "user_id", None), max_len=160),
        "user_type": _safe(getattr(comm, "user_type", None), max_len=80),
        "source_kube": _safe(source_kube, max_len=120),
        "source_component": _safe(source_component or "comm", max_len=120),
        "source_bundle": _safe(
            source_bundle
            or service.get("bundle_id")
            or _bundle_from_scopes(item)
            or "unknown",
            max_len=180,
        ),
    }


def _bundle_from_scopes(item: Mapping[str, Any]) -> str:
    recording = _mapping(item.get("recording"))
    scopes = recording.get("scopes")
    if not isinstance(scopes, list):
        return ""
    for scope in scopes:
        if isinstance(scope, Mapping) and scope.get("bundle"):
            return str(scope.get("bundle"))
    return ""


def _event_id(
    item: Mapping[str, Any],
    *,
    name: str,
    suffix: str = "",
    stable_identity: Optional[Mapping[str, Any]] = None,
) -> str:
    if stable_identity is not None:
        seed = {
            "name": name,
            "suffix": suffix,
            "identity": stable_identity,
        }
        digest = hashlib.sha256(json.dumps(seed, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return f"comm_{digest[:32]}"

    record_id = _safe(item.get("record_id"), max_len=200)
    seed = {
        "record_id": record_id,
        "name": name,
        "suffix": suffix,
        "type": item.get("type"),
        "conversation": item.get("conversation"),
        "event": item.get("event"),
    }
    digest = hashlib.sha256(json.dumps(seed, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"comm_{digest[:32]}"


def _record_timestamp(item: Mapping[str, Any]) -> str:
    value = item.get("recorded_at_ms")
    try:
        dt = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _status(item: Mapping[str, Any]) -> str:
    ev = _mapping(item.get("event"))
    status = _safe(ev.get("status"), max_len=80)
    return status or "success"


def _metrics(data: Mapping[str, Any], *, include_latency: bool = False) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {"value": 1}
    if include_latency:
        latency = data.get("latency_ms", data.get("duration_ms"))
        if latency is not None:
            metrics["latency_ms"] = _number(latency)
    if "message_len" not in data and "chars" in data:
        metrics["message_len"] = _number(data.get("chars"))
    aliases = {
        "attachments_count": "attachment_count",
        "file_count": "produced_file_count",
        "citations_count": "citation_count",
    }
    for source_key, target_key in aliases.items():
        if target_key not in data and source_key in data:
            metrics[target_key] = _number(data.get(source_key))
    for key in (
        "active_seconds",
        "attachment_count",
        "bytes",
        "cache_1h_write_tokens",
        "cache_5m_write_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_total_usd",
        "citation_count",
        "input_tokens",
        "message_len",
        "output_tokens",
        "produced_file_count",
        "read_ms",
        "thinking_tokens",
    ):
        if key in data:
            metrics[key] = _number(data.get(key))
    return metrics


def _chat_input_kind(data: Mapping[str, Any], typ: str) -> str:
    raw = (
        data.get("input_kind")
        or data.get("chat_input_kind")
        or data.get("message_kind")
        or data.get("continuation_kind")
        or ""
    )
    text = str(raw or "").strip().lower()
    if typ == "chat.conversation.accepted" and not text:
        return "message"
    if text in {"", "regular", "user", "prompt"}:
        return "message"
    if text in {"message", "followup", "steer"}:
        return text
    if "followup" in text:
        return "followup"
    if "steer" in text:
        return "steer"
    return "message"


def _accounting_breakdown(value: Any) -> List[Dict[str, Any]]:
    raw_items: List[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, Mapping):
        raw_items = [value]
    else:
        raw_items = []
    out: List[Dict[str, Any]] = []
    for item in raw_items[:200]:
        if not isinstance(item, Mapping):
            continue
        line: Dict[str, Any] = {}
        for key in ("service_type", "provider", "model_or_service", "agent", "tier"):
            text = _safe(item.get(key), max_len=160)
            if text:
                line[key] = text
        if "service_type" not in line:
            text = _safe(item.get("service"), max_len=160)
            if text:
                line["service_type"] = text
        if "model_or_service" not in line:
            text = _safe(item.get("model"), max_len=160)
            if text:
                line["model_or_service"] = text
        for key in (
            "input_tokens",
            "output_tokens",
            "tokens",
            "embedding_tokens",
            "cache_creation_tokens",
            "cache_write_tokens",
            "cache_5m_write_tokens",
            "cache_1h_write_tokens",
            "cache_read_tokens",
            "thinking_tokens",
            "search_queries",
            "search_results",
            "cost_per_1k_requests",
            "cost_usd",
            "cost_total_usd",
        ):
            if key in item:
                line[key] = _number(item.get(key))
        if line.get("service_type") == "embedding" and "embedding_tokens" not in line and "tokens" in line:
            line["embedding_tokens"] = line["tokens"]
        if line:
            out.append(line)
    return out


def _reported_values(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        concept = _safe(item.get("concept"), max_len=80)
        reported_value = _safe(item.get("value"), max_len=500)
        if concept and reported_value:
            out.append({"concept": concept, "value": reported_value})
    return out


def _error_kind(data: Mapping[str, Any], status: str, default_kind: str = "error") -> str:
    if status not in {"error", "failed"} and not data.get("error_count") and not data.get("exception_type"):
        return ""
    return _safe(
        data.get("error_code")
        or data.get("exception_type")
        or default_kind,
        max_len=160,
    )


def _clean_mapping(value: Mapping[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, val in dict(value or {}).items():
        text = _safe(val, max_len=180)
        if text:
            out[str(key)] = text
    return out


def _clean_metrics(value: Mapping[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, val in dict(value or {}).items():
        try:
            out[str(key)] = _number(val)
        except Exception:
            continue
    if "value" not in out:
        out["value"] = 1.0
    return out


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe(value: Any, *, max_len: int = 160) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).strip().split())
    return text[:max_len]


def _number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


async def _default_http_sender(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout_seconds: float) -> Dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=float(timeout_seconds or 10.0)) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        if not response.content:
            return {}
        return dict(response.json())
