# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Scheduling a subagent as a first-class, fair-scheduled turn.

The child is a turn submitted to the cluster like any reactive-event turn:
its inception is a ``subagent.charter`` event authored onto the CHILD
conversation's lane WITH a task payload, admitted atomically together with
the processor wakeup through the gateway's enqueue-time backpressure (the
same ``AtomicChatQueueManager`` admission chat ingress uses). The promoter
resolves the wakeup, reads the charter event, and runs the described turn
through the same gates, throttling, and per-user capacity as every other
turn. Completions travel the other direction:
``subagent.converged``/``subagent.failed`` on the PARENT lane with a task
payload describing the parent's continuation turn — published durably
first, with the wakeup going through the same admission.

The in-memory part of a delegate — the fork projection — travels via the
seed persist (:func:`persist_child_seed_timeline`), never via the queue
payload. The task payload stays small; the seed is as-of delegate time, so
queue-time staleness is correct by construction.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.charter import SubagentCharter
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
    SUBAGENT_CHARTER_EVENT_KIND,
    SUBAGENT_EVENT_SOURCE_ID,
    ParentLaneAddress,
    prepare_subagent_event,
    publish_subagent_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.fork import FORK_HEADER_BLOCK_TYPE

# The bundle_call_context key the promoted payload carries the subagent
# assignment under; the workflow reads it to apply the child overrides.
SUBAGENT_CALL_CONTEXT_KEY = "subagent"
SUBAGENT_TASK_SOURCE = "react.subagent.delegate"
SUBAGENT_COMPLETION_TASK_SOURCE = "react.subagent.completion"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _event_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def build_charter_external_event(
    *,
    charter: SubagentCharter,
    parent: ParentLaneAddress,
    child_turn_id: str,
) -> Dict[str, Any]:
    """The accepted-external-event body the child's promoted turn opens with."""
    return {
        "event_id": _event_id("subch"),
        "type": SUBAGENT_CHARTER_EVENT_KIND,
        "event_source_id": SUBAGENT_EVENT_SOURCE_ID,
        "logical_path": f"ev:{child_turn_id}.subagent.charter",
        "timestamp": _utc_iso(),
        "reactive": False,
        "agent_id": parent.agent_id or "main",
        "payload": {
            "mime": "text/markdown",
            "event": {
                "text": charter.charter_text(),
                "charter": charter.to_dict(),
                "parent_conversation_id": parent.conversation_id,
                "parent_turn_id": parent.turn_id,
            },
        },
    }


def build_completion_external_event(
    *,
    semantic_type: str,
    text: str,
    facts: Dict[str, Any],
    parent: ParentLaneAddress,
    continuation_turn_id: str,
) -> Dict[str, Any]:
    """The accepted-external-event body the parent's continuation turn opens with."""
    return {
        "event_id": _event_id("subco"),
        "type": semantic_type,
        "event_source_id": SUBAGENT_EVENT_SOURCE_ID,
        "logical_path": f"ev:{continuation_turn_id}.{semantic_type}",
        "timestamp": _utc_iso(),
        "reactive": False,
        "agent_id": parent.agent_id or "main",
        "payload": {
            "mime": "text/markdown",
            "event": {
                "text": text,
                **dict(facts or {}),
            },
        },
    }


def _base_payload_dict(parent_payload: Any) -> Dict[str, Any]:
    if parent_payload is None:
        return {}
    try:
        return parent_payload.model_dump()
    except Exception:
        return dict(parent_payload) if isinstance(parent_payload, dict) else {}


def build_child_task_payload(
    *,
    parent_payload: Any,
    charter: SubagentCharter,
    parent: ParentLaneAddress,
    child_conversation_id: str,
    child_turn_id: str,
    subagent_context: Dict[str, Any],
) -> Dict[str, Any]:
    """The ``ExternalEventPayload``-shaped task the charter promotion runs.

    Identity (actor/user/config/accounting) is the parent's — the child
    conversation belongs to the same tenant/project/user. Routing is the
    child's, with ``session_id = child conversation id`` (webhook-ingress
    derivation: the turn has no live socket room of its own) and the fresh
    child turn id minted at delegate time, so the promoted turn folds the
    lane charter event targeted at exactly that turn.
    """
    data = _base_payload_dict(parent_payload)
    task_id = str(uuid.uuid4())

    meta = dict(data.get("meta") or {})
    meta["task_id"] = task_id
    meta["created_at"] = time.time()
    data["meta"] = meta

    routing = dict(data.get("routing") or {})
    routing["session_id"] = child_conversation_id
    routing["conversation_id"] = child_conversation_id
    routing["turn_id"] = child_turn_id
    routing["socket_id"] = None
    routing.setdefault("bundle_id", "")
    data["routing"] = routing

    if not isinstance(data.get("actor"), dict) or not data.get("actor"):
        data["actor"] = {"tenant_id": parent.tenant, "project_id": parent.project}
    if not isinstance(data.get("user"), dict) or not data.get("user"):
        data["user"] = {"user_type": "registered", "user_id": parent.user_id}

    charter_event = build_charter_external_event(
        charter=charter, parent=parent, child_turn_id=child_turn_id,
    )
    data["request"] = {
        "external_events": [charter_event],
        "chat_history": [],
        "operation": None,
        "invocation": None,
        "payload": {"source": SUBAGENT_TASK_SOURCE},
        "request_id": task_id,
    }
    data["continuation"] = {"is_continuation": False}
    data["event"] = {
        "kind": "external_event",
        "type": SUBAGENT_CHARTER_EVENT_KIND,
        "agent_id": parent.agent_id or "main",
        "event_source_id": SUBAGENT_EVENT_SOURCE_ID,
        "event_id": charter_event["event_id"],
        "logical_path": charter_event["logical_path"],
        "reactive": False,
        "source": SUBAGENT_TASK_SOURCE,
    }

    accounting = dict(data.get("accounting") or {})
    envelope = dict(accounting.get("envelope") or {})
    env_meta = dict(envelope.get("metadata") or {})
    env_meta["subagent"] = {
        "parent_conversation_id": parent.conversation_id,
        "parent_turn_id": parent.turn_id,
        "charter_goal": charter.summary_line(),
    }
    envelope["metadata"] = env_meta
    accounting["envelope"] = envelope
    data["accounting"] = accounting

    call_context = dict(data.get("bundle_call_context") or {})
    call_context[SUBAGENT_CALL_CONTEXT_KEY] = dict(subagent_context or {})
    data["bundle_call_context"] = call_context
    return data


def build_completion_task_payload(
    *,
    child_payload: Any,
    semantic_type: str,
    text: str,
    facts: Dict[str, Any],
    parent: ParentLaneAddress,
    parent_session_id: str,
    parent_user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """The parent-continuation task a completion event promotes when the
    parent lane has no live turn: fold the completion, pull the deliverable
    refs, respond to the user."""
    data = _base_payload_dict(child_payload)
    task_id = str(uuid.uuid4())
    continuation_turn_id = f"turn_{uuid.uuid4().hex[:12]}"

    meta = dict(data.get("meta") or {})
    meta["task_id"] = task_id
    meta["created_at"] = time.time()
    data["meta"] = meta

    routing = dict(data.get("routing") or {})
    routing["session_id"] = parent_session_id or parent.conversation_id
    routing["conversation_id"] = parent.conversation_id
    routing["turn_id"] = continuation_turn_id
    routing["socket_id"] = None
    routing.setdefault("bundle_id", "")
    data["routing"] = routing

    data["actor"] = {"tenant_id": parent.tenant, "project_id": parent.project}
    if isinstance(parent_user, dict) and parent_user:
        data["user"] = dict(parent_user)

    completion_event = build_completion_external_event(
        semantic_type=semantic_type,
        text=text,
        facts=facts,
        parent=parent,
        continuation_turn_id=continuation_turn_id,
    )
    data["request"] = {
        "external_events": [completion_event],
        "chat_history": [],
        "operation": None,
        "invocation": None,
        "payload": {"source": SUBAGENT_COMPLETION_TASK_SOURCE},
        "request_id": task_id,
    }
    data["continuation"] = {"is_continuation": False}
    facts = dict(facts or {})
    data["event"] = {
        "kind": "external_event",
        "type": semantic_type,
        "agent_id": parent.agent_id or "main",
        "event_source_id": SUBAGENT_EVENT_SOURCE_ID,
        "event_id": completion_event["event_id"],
        "logical_path": completion_event["logical_path"],
        "reactive": False,
        "source": SUBAGENT_COMPLETION_TASK_SOURCE,
        # The continuation turn is triggered by the helper, not the user: the
        # persona a client renders reads these off the triggering event.
        "authored_by": str(facts.get("authored_by") or "agent"),
        "agent_title": str(facts.get("agent_title") or ""),
        **({"handoff": facts["handoff"]} if str(facts.get("handoff") or "").strip() else {}),
    }

    # The continuation turn is a NORMAL parent turn: no subagent assignment
    # rides in its call context.
    call_context = dict(data.get("bundle_call_context") or {})
    call_context.pop(SUBAGENT_CALL_CONTEXT_KEY, None)
    data["bundle_call_context"] = call_context
    return data


class SubagentEnqueueRejected(RuntimeError):
    """The gateway's atomic admission declined the subagent wakeup.

    Carried up as a structured outcome (``reason`` is the gateway's
    rejection code, e.g. ``queue_size_exceeded`` / ``hard_limit_exceeded``)
    so callers can present the saturation instead of a generic failure."""

    def __init__(self, reason: str, stats: Optional[Dict[str, Any]] = None):
        self.reason = str(reason or "rejected")
        self.stats = dict(stats or {})
        super().__init__(f"subagent enqueue rejected: {self.reason}")


def build_subagent_wake_queue() -> Any:
    """The atomic chat queue manager, constructed proc-side.

    The same shared-infra admission the chat ingress uses
    (``AtomicChatQueueManager``): the lane event write and the processor
    wakeup are one Lua script, gated by enqueue-time backpressure. The
    monitor participates on the rejection path only (throttling telemetry
    for the circuit breakers), so a plain ``ThrottlingMonitor`` over the
    same gateway config keeps that telemetry live."""
    from kdcube_ai_app.infra.gateway.backpressure import create_atomic_chat_queue_manager
    from kdcube_ai_app.infra.gateway.config import get_gateway_config
    from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor

    gateway_config = get_gateway_config()
    redis_url = gateway_config.redis_url
    monitor = ThrottlingMonitor(redis_url, gateway_config)
    return create_atomic_chat_queue_manager(redis_url, gateway_config, monitor)


def build_subagent_session(
    *,
    user: Optional[Dict[str, Any]],
    session_id: str,
    source: str,
) -> Any:
    """A ``UserSession`` derived from the parent's user identity.

    The webhook-ingress derivation: the platform user is the parent's, the
    session is the target conversation, and the request context carries the
    subagent source marker (authored-by-agent)."""
    from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType

    user = dict(user or {})
    request_context = RequestContext(
        client_ip=source,
        user_agent=source,
        user_timezone=str(user.get("timezone") or "UTC"),
    )
    try:
        user_type = UserType(str(user.get("user_type") or "registered").lower())
    except Exception:
        user_type = UserType.REGISTERED
    return UserSession(
        session_id=session_id,
        user_type=user_type,
        user_id=user.get("user_id"),
        username=user.get("username"),
        fingerprint=user.get("fingerprint"),
        email=user.get("email"),
        roles=list(user.get("roles") or []),
        permissions=list(user.get("permissions") or []),
        timezone=user.get("timezone"),
        request_context=request_context,
        identity_authority=dict(user.get("identity_authority") or {}),
    )


def _build_wakeup_dict(*, lane_source: Any, event: Any, task_payload: Dict[str, Any]) -> Dict[str, Any]:
    from kdcube_ai_app.apps.chat.sdk.events.event_bus.wakeup import build_event_lane_wakeup
    from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload

    payload_model = ExternalEventPayload.model_validate(dict(task_payload or {}))
    wakeup = build_event_lane_wakeup(
        payload=payload_model,
        event=event,
        tenant=getattr(lane_source, "tenant", None),
        project=getattr(lane_source, "project", None),
        user_id=getattr(lane_source, "user_id", None),
        conversation_id=str(getattr(lane_source, "conversation_id", "") or ""),
        agent_id=str(getattr(lane_source, "agent_id", "") or "main"),
        reason="subagent_event",
    )
    return wakeup.model_dump()


async def atomic_schedule_promotable_subagent_event(
    *,
    lane_source: Any,
    queue_manager: Any,
    session: Any,
    endpoint: str,
    semantic_type: str,
    text: str,
    facts: Optional[Dict[str, Any]] = None,
    author: str = "",
    target_turn_id: Optional[str] = None,
    task_payload: Dict[str, Any],
) -> Any:
    """Lane event + processor wakeup as ONE atomic, admission-gated write.

    The prepared ``subagent.*`` event and the wakeup go through the
    gateway's atomic enqueue script: either both land (the event in the
    lane, the wakeup on the per-user-type queue) or neither does. A
    backpressure rejection raises :class:`SubagentEnqueueRejected` with the
    gateway's reason — nothing rests in the lane after a rejection."""
    event = await prepare_subagent_event(
        lane_source=lane_source,
        semantic_type=semantic_type,
        text=text,
        facts=facts,
        author=author,
        target_turn_id=target_turn_id,
        task_payload=task_payload,
    )
    success, reason, stats = await queue_manager.enqueue_chat_task_with_lane_events_atomic(
        session.user_type,
        _build_wakeup_dict(lane_source=lane_source, event=event, task_payload=task_payload),
        session,
        getattr(session, "request_context", None),
        endpoint,
        lane_log_key=lane_source.log_key,
        lane_events=[{
            "event_key": lane_source.event_key(event.message_id),
            "event": event.to_dict(),
        }],
    )
    if not success:
        raise SubagentEnqueueRejected(reason, stats)
    await lane_source.apply_atomic_publish_stream_ids(
        [event], list((stats or {}).get("lane_stream_ids") or []),
    )
    return event


async def publish_completion_with_atomic_wakeup(
    *,
    lane_source: Any,
    queue_manager: Any,
    session: Any,
    endpoint: str,
    semantic_type: str,
    text: str,
    facts: Optional[Dict[str, Any]] = None,
    author: str = "",
    target_turn_id: Optional[str] = None,
    task_payload: Dict[str, Any],
) -> tuple[Any, bool, str]:
    """A completion is never lost: durable lane publish first, then the
    admission-gated wakeup.

    The event lands in the parent lane unconditionally (the child has spent
    its budget; its report must survive any queue state). The wakeup then
    goes through the same atomic admission as every turn; a rejection is
    tolerated — the completion rests in the lane and the parent's next turn
    folds it (degraded liveness, zero loss). Returns
    ``(event, wake_admitted, reason)``."""
    event = await publish_subagent_event(
        lane_source=lane_source,
        semantic_type=semantic_type,
        text=text,
        facts=facts,
        author=author,
        target_turn_id=target_turn_id,
        task_payload=task_payload,
    )
    success, reason, _stats = await queue_manager.enqueue_chat_task_atomic(
        session.user_type,
        _build_wakeup_dict(lane_source=lane_source, event=event, task_payload=task_payload),
        session,
        getattr(session, "request_context", None),
        endpoint,
    )
    return event, bool(success), str(reason or "")


async def persist_child_seed_timeline(
    *,
    ctx_client: Any,
    model_service: Any,
    parent: ParentLaneAddress,
    fork_blocks: List[Dict[str, Any]],
    child_conversation_id: str,
    child_turn_id: str,
    bundle_id: Optional[str],
    user_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist the fork projection as the child conversation's history.

    The child's normal timeline load then finds it like any prior state.
    Returns the ``forked_from`` backref the seed carries (also stamped onto
    the persisted timeline artifact as a queryable field)."""
    if not fork_blocks:
        return None
    import copy as _copy

    from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline

    forked_from = {
        "conversation_id": parent.conversation_id,
        "turn_id": parent.turn_id,
    }
    blocks: List[Dict[str, Any]] = []
    for block in fork_blocks:
        if not isinstance(block, dict):
            continue
        block = _copy.deepcopy(block)
        if str(block.get("type") or "") == FORK_HEADER_BLOCK_TYPE:
            meta = dict(block.get("meta") or {})
            meta["child_conversation_id"] = child_conversation_id
            block["meta"] = meta
        blocks.append(block)
    child_ctx = RuntimeCtx(
        tenant=parent.tenant,
        project=parent.project,
        user_id=parent.user_id,
        user_type=user_type,
        conversation_id=child_conversation_id,
        turn_id=child_turn_id,
        bundle_id=bundle_id,
        agent_id=parent.agent_id or "main",
    )
    seed = Timeline(runtime=child_ctx, svc=model_service)
    seed.blocks = blocks
    seed.conversation_started_at = _utc_iso()
    seed.forked_from = dict(forked_from)
    await seed.persist(ctx_client)
    return forked_from
