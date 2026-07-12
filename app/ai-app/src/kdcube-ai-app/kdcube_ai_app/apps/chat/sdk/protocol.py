# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/protocol.py
from __future__ import annotations
from typing import Any, Dict, Optional, Literal, List
from datetime import datetime
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID


# -----------------------------
# History / client-side request
# -----------------------------

class ChatHistoryMessage(BaseModel):
    role: str = Field(default="user")
    content: str
    timestamp: Optional[str] = None

def _iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


class _ProtoBase(BaseModel):
    """Base that exposes `.dump_model()` as requested (alias of model_dump)."""
    def dump_model(self) -> Dict[str, Any]:
        return self.model_dump()


class ServiceCtx(_ProtoBase):
    request_id: str
    tenant: Optional[str] = None
    project: Optional[str] = None
    user: Optional[str] = None
    user_obj: Optional[ExternalEventUser] = None


class ConversationCtx(_ProtoBase):
    session_id: str
    conversation_id: str
    turn_id: str|None = None


class EventCtx(_ProtoBase):
    agent: Optional[str] = None
    step: str
    status: Literal["started", "running", "completed", "error", "skipped"]
    title: Optional[str] = None


class DeltaPart(_ProtoBase):
    text: str
    marker: str = "answer"
    index: int = 0


class ChatEnvelope(_ProtoBase):
    """
    Unified protocol envelope used everywhere on the server and sent to the client as-is.
    """
    type: Literal["chat.start", "chat.step", "chat.delta", "chat.complete", "chat.error", "chat.service"]
    timestamp: str = Field(default_factory=_iso_now)
    service: ServiceCtx
    conversation: ConversationCtx
    event: EventCtx
    data: Any | None = None          # generic payload from bundles/processors
    delta: Optional[DeltaPart] = None

    @staticmethod
    def start(service: ServiceCtx, conv: ConversationCtx, *, message: str, queue_stats: Dict[str, Any] | None = None) -> "ChatEnvelope":
        return ChatEnvelope(
            type="chat.start",
            service=service,
            conversation=conv,
            event=EventCtx(step="turn", status="started", title="Turn Started", agent="user"),
            data={"message": message, "queue_stats": queue_stats or {}},
        )

    @staticmethod
    def step(service: ServiceCtx, conv: ConversationCtx, *, step: str, status: EventCtx.model_fields["status"].annotation, title: str | None = None, data: Any = None, agent: str | None = None) -> "ChatEnvelope":
        return ChatEnvelope(
            type="chat.step",
            service=service,
            conversation=conv,
            event=EventCtx(step=step, status=status, title=title, agent=agent),
            data=data,
        )

    @staticmethod
    def delta(service: ServiceCtx, conv: ConversationCtx, *, text: str, index: int, marker: DeltaPart.model_fields["marker"].annotation = "answer") -> "ChatEnvelope":
        return ChatEnvelope(
            type="chat.delta",
            service=service,
            conversation=conv,
            event=EventCtx(step="stream", status="running", title="Assistant Delta", agent="assistant"),
            delta=DeltaPart(text=text, index=int(index), marker=marker),
        )

    @staticmethod
    def complete(service: ServiceCtx, conv: ConversationCtx, *, data: Any = None, agent: str | None = "answer_generator") -> "ChatEnvelope":
        return ChatEnvelope(
            type="chat.complete",
            service=service,
            conversation=conv,
            event=EventCtx(step="stream", status="completed", title="Turn Completed", agent=agent),
            data=data or {},
        )

    @staticmethod
    def error(service: ServiceCtx, conv: ConversationCtx, *, error: str, title: str | None = "Workflow Error", step: str = "workflow") -> "ChatEnvelope":
        return ChatEnvelope(
            type="chat.error",
            service=service,
            conversation=conv,
            event=EventCtx(step=step, status="error", title=title, agent=None),
            data={"error": error},
        )


# ---- event payload used between ingress and processor ----

class ExternalEventRequest(_ProtoBase):
    external_events: List[Dict[str, Any]] = Field(default_factory=list)
    chat_history: List[Dict[str, Any]] = Field(default_factory=list)
    operation: Optional[str] = None
    invocation: Optional[Literal["sync", "async"]] = None
    payload: Any | None = None   # ← generic pass-through to bundle
    request_id: Optional[str] = None


def external_event_text(event: Any) -> str:
    """Return the user-visible text carried by one accepted external event."""
    if not isinstance(event, dict):
        return ""
    event_type = str(event.get("type") or "").strip()
    if event_type not in {"event.user.prompt", "event.user.followup", "event.user.steer"}:
        return ""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    body = payload.get("event")
    if isinstance(body, dict):
        text = body.get("text") or body.get("message") or body.get("request") or ""
    else:
        text = body or ""
    return str(text or "").strip()


def external_events_text(events: Any) -> str:
    """Return the first user-visible text from an external event list."""
    if not isinstance(events, list):
        return ""
    for event in events:
        text = external_event_text(event)
        if text:
            return text
    return ""


def external_event_attachment_payloads(events: Any) -> List[Dict[str, Any]]:
    """Return attachment bodies carried by `event.user.attachment.*` events."""
    if not isinstance(events, list):
        return []
    out: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if not str(event.get("type") or "").startswith("event.user.attachment"):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        body = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        if body:
            out.append(dict(body))
    return out


def hosted_external_event_attachments(events: Any) -> List[Dict[str, Any]]:
    """Return only hosted attachment bodies from accepted external events."""
    return [
        item
        for item in external_event_attachment_payloads(events)
        if str(item.get("hosted_uri") or "").strip()
    ]


def external_event_request_start_label(request: Any) -> str:
    """
    Return a neutral start label for UI/diagnostics.

    Reactive domain events do not have to carry text. This helper first returns
    real prompt-like text when it exists; otherwise it uses the first event's
    structural/source identity. It is not part of the authored request model.
    """
    events = getattr(request, "external_events", None)
    if events is None and isinstance(request, dict):
        events = request.get("external_events")
    text = external_events_text(events)
    if text:
        return text
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            event_source_id = str(event.get("event_source_id") or "").strip()
            if event_type and event_source_id:
                return f"{event_type} ({event_source_id})"
            if event_type:
                return event_type
            if event_source_id:
                return event_source_id
    operation = getattr(request, "operation", None)
    if operation is None and isinstance(request, dict):
        operation = request.get("operation")
    if operation:
        return f"operation={operation}"
    return ""


def _accepted_event_agent_persona(event: Any) -> Dict[str, Any]:
    """The agent persona an accepted external event carries, or ``{}``.

    A turn triggered by a helper-authored event (a subagent completion) carries
    the ``authored_by: "agent"`` contract in its facts: ``agent_title`` (the
    helper's display name) and, when the helper reported one, ``handoff`` (its
    own message back to the delegating agent). Scans the event and its nested
    ``payload.event`` body — the same surfaces the client reads — so the label
    and the persona resolve off one shape."""
    if not isinstance(event, dict):
        return {}
    candidates = [event]
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    body = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    if body:
        candidates.append(body)
    for candidate in candidates:
        if str(candidate.get("authored_by") or "").strip().lower() != "agent":
            continue
        persona: Dict[str, Any] = {
            "authored_by": "agent",
            "agent_title": str(candidate.get("agent_title") or ""),
        }
        handoff = str(candidate.get("handoff") or "").strip()
        if handoff:
            persona["handoff"] = handoff
        return persona
    return {}


def external_event_request_start_persona(request: Any) -> Dict[str, Any]:
    """Persona fields for a turn whose triggering event a helper authored.

    Returns ``{authored_by: "agent", agent_title, handoff?}`` when the turn was
    opened by a subagent completion event, so the continuation turn renders as
    the helper persona instead of the user; ``{}`` for user-authored turns.
    Rides onto ``chat.start.data`` next to ``message`` (the live surface the
    client reads the persona from)."""
    events = getattr(request, "external_events", None)
    if events is None and isinstance(request, dict):
        events = request.get("external_events")
    if not isinstance(events, list):
        return {}
    for event in events:
        persona = _accepted_event_agent_persona(event)
        if persona:
            return persona
    return {}


class ExternalEventRouting(_ProtoBase):
    bundle_id: str
    session_id: str
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    socket_id: Optional[str] = None


class ExternalEventActor(_ProtoBase):
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None


class ExternalEventUser(_ProtoBase):
    user_type: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    fingerprint: Optional[str] = None
    roles: List[str] = []
    permissions: List[str] = []
    timezone: Optional[str] = None
    utc_offset_min: Optional[int] = None
    identity_authority: Dict[str, Any] = {}


class ExternalEventConfig(_ProtoBase):
    values: Dict[str, Any] = {}


class ExternalEventMeta(_ProtoBase):
    task_id: str
    created_at: float
    instance_id: Optional[str] = None


class ExternalEventAccounting(_ProtoBase):
    envelope: Dict[str, Any] = {}   # whatever your accounting layer produces


class ExternalEventContinuation(_ProtoBase):
    is_continuation: bool = False
    active_turn_id: Optional[str] = None
    target_turn_id: Optional[str] = None


class AcceptedExternalEventPayload(_ProtoBase):
    """
    Body of an authored external event.

    `mime` describes the event body. The body can be inline (`event`) or a
    hosted/resolvable object (`event_ref`). The ref is resolved later by the
    target agent/runtime, normally through `react.pull`.
    """

    mime: str = "application/json"
    event: Optional[Any] = None
    event_ref: Optional[str] = None


class AcceptedExternalEvent(_ProtoBase):
    """
    Canonical shape accepted from `external_events[]`.

    The inbound request is plural-only. Each accepted item becomes one ordered
    lane occurrence and one or more timeline blocks. `logical_path` is the
    event object's `ev:` reference on the target turn timeline.
    """

    event_id: str
    type: str = "event.external"
    event_source_id: str = "react.external_event"
    logical_path: str
    hosted_uri: Optional[str] = None
    timestamp: str = Field(default_factory=_iso_now)
    reactive: bool = False
    agent_id: str = DEFAULT_REACT_AGENT_ID
    story_id: Optional[str] = None
    payload: AcceptedExternalEventPayload = Field(default_factory=AcceptedExternalEventPayload)


class ExternalEvent(_ProtoBase):
    """
    Event occurrence metadata for the processor envelope.

    This section identifies what happened (`kind`), which agent lane owns it,
    which event source/policies describe it, and whether it may wake or extend
    a runtime.
    """

    kind: str = "message"
    agent_id: str = DEFAULT_REACT_AGENT_ID
    event_source_id: Optional[str] = None
    event_id: Optional[str] = None
    sequence: Optional[int] = None
    logical_path: Optional[str] = None
    type: Optional[str] = None
    story_id: Optional[str] = None
    reactive: Optional[bool] = True
    source: Optional[str] = None
    out_of_turn: bool = False


class ExternalEventLaneRef(_ProtoBase):
    """
    Address of the ordered event lane that owns the accepted event body.

    Ready queues carry wakeups that point at this lane; they do not carry the
    user message, attachment payload, followup, steer, or authored UI event as
    the processing source.
    """

    tenant: Optional[str] = None
    project: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: str
    agent_id: str = DEFAULT_REACT_AGENT_ID
    event_id: Optional[str] = None
    sequence: Optional[int] = None
    stream_id: Optional[str] = None


class ExternalEventLaneWakeup(_ProtoBase):
    """
    Processor-ready-queue wakeup.

    The queue item says "run this lane". The actual event occurrence remains in
    the external-event stream and is folded into the timeline by the lane owner.
    """

    kind: Literal["external_event_lane_wakeup"] = "external_event_lane_wakeup"
    meta: ExternalEventMeta
    routing: ExternalEventRouting
    actor: ExternalEventActor
    user: ExternalEventUser
    event_lane: ExternalEventLaneRef
    config: Optional[ExternalEventConfig] = None
    accounting: Optional[ExternalEventAccounting] = None
    continuation: Optional[ExternalEventContinuation] = None
    event: Optional[ExternalEvent] = None
    bundle_call_context: Dict[str, Any] = Field(default_factory=dict)
    reason: str = "reactive_event"


class ExternalEventPayload(_ProtoBase):
    """
    Top-level transport/processor payload for an accepted event occurrence.

    It is not necessarily chat and not necessarily a task. A user message,
    attachment, followup, steer, authored UI event, webhook event, or background
    work item can all travel as this payload. Chat is one event kind.
    """

    meta: Optional[ExternalEventMeta] = None
    routing: Optional[ExternalEventRouting] = None
    actor: Optional[ExternalEventActor] = None
    user: Optional[ExternalEventUser] = None
    request: Optional[ExternalEventRequest] = None
    config: Optional[ExternalEventConfig] = None
    accounting: Optional[ExternalEventAccounting] = None
    continuation: Optional[ExternalEventContinuation] = None
    event: Optional[ExternalEvent] = None
    bundle_call_context: Dict[str, Any] = Field(default_factory=dict)
