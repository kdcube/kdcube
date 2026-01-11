# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/emitters.py
from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable, Dict, List, Tuple, Set, Iterable
import os, logging, time

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import IEventFilter, EventFilterInput
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatEnvelope, ServiceCtx, ConversationCtx, ChatTaskRouting, _iso_now, ChatTaskPayload
)
from kdcube_ai_app.apps.chat.sdk.util import ensure_event_markdown
from kdcube_ai_app.infra.orchestration.app.communicator import ServiceCommunicator

logger = logging.getLogger(__name__)

# map protocol type → client socket event
_EVENT_MAP = {
    "chat.start": "chat_start",
    "chat.step": "chat_step",
    "chat.delta": "chat_delta",
    "chat.complete": "chat_complete",
    "chat.error": "chat_error",
    "chat.service": "chat_service",
}

# inside chat/emitters.py (anywhere above ChatCommunicator)
def _now_ms() -> int:
    return int(time.time() * 1000)

@dataclass
class _DeltaChunk:
    ts: int
    idx: int
    text: str

@dataclass
class _DeltaAggregate:
    conversation_id: str
    turn_id: str
    agent: str
    marker: str
    ts_first: int = 0
    ts_last: int = 0
    format: Optional[str] = None
    artifact_name: Optional[str] = None
    title: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    chunks: List[_DeltaChunk] = field(default_factory=list)

    def append(self, *, ts: int, idx: int, text: str):
        if not self.ts_first:
            self.ts_first = ts
        self.ts_last = ts
        self.chunks.append(_DeltaChunk(ts=ts, idx=idx, text=text))

    def merged_text(self) -> str:
        # preserve original order by idx, then ts
        return "".join([c.text for c in sorted(self.chunks, key=lambda c: (c.idx, c.ts))])


class ChatRelayCommunicator:
    """
    Single interface for publishing and subscribing chat events (via Redis).
    The web Socket.IO relay subscribes and forwards to browser clients.
    """

    def __init__(
            self,
            *,
            redis_url: Optional[str] = None,
            channel: str = "chat.events",
            orchestrator_identity: Optional[str] = None,
            comm: Optional[ServiceCommunicator] = None,
    ):
        redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._comm = comm or ServiceCommunicator(
            redis_url=redis_url,
            orchestrator_identity=orchestrator_identity
                                  or os.environ.get(
                "CB_RELAY_IDENTITY") or "kdcube.relay.chatbot",
        )
        self._channel = channel

        self._callbacks: Set[Callable[[dict], Awaitable[None]]] = set()
        self._session_refcounts: Dict[str, int] = {}
        self._listener_started = False
        self._sub_lock = asyncio.Lock()

    def _base_channel(self, tenant: str | None = None, project: str | None = None) -> str:
        # keep old behavior if tenant/project not provided
        if tenant or project:
            t = tenant or "-"
            p = project or "-"
            return f"{t}:{p}:{self._channel}"
        return self._channel

    # ---------- publish ----------
    def _session_channel(self, session_id: str, tenant: str | None = None, project: str | None = None) -> str:
        base = self._base_channel(tenant, project)
        return f"{base}.{session_id}"

    def _derive_session_id(self, env: ChatEnvelope, explicit: str | None) -> str | None:
        if explicit:
            return explicit

        # Try to read from the envelope's conversation
        try:
            conv = getattr(env, "conversation", None)
            if isinstance(conv, dict):
                return conv.get("session_id")
            return getattr(conv, "session_id", None)
        except Exception:
            return None

    async def _pub(self, env: ChatEnvelope, *, target_sid: Optional[str], session_id: Optional[str]):
        # Always publish TYPE-MAPPED event so the relay only emits
        event = _EVENT_MAP[env.type]
        sid = self._derive_session_id(env, session_id)
        await self._comm.pub(
            event=event,
            data=env.dump_model(),
            target_sid=target_sid,
            session_id=sid,
            # channel=self._channel,
            channel=self._session_channel(sid,
                                          tenant=env.service.tenant,
                                          project=env.service.project),
        )
    async def emit_envelope(self, env: ChatEnvelope, *, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        """Low-level: publish a prebuilt envelope."""
        await self._pub(env, target_sid=target_sid, session_id=session_id)

    async def emit_start(self, service: ServiceCtx, conv: ConversationCtx, *, message: str, queue_stats: Dict[str, Any] | None = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        await self._pub(ChatEnvelope.start(service, conv, message=message, queue_stats=queue_stats), target_sid=target_sid, session_id=session_id)

    async def emit_step(self, service: ServiceCtx, conv: ConversationCtx, *, step: str, status: str, title: Optional[str] = None, data: Any = None, agent: Optional[str] = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        await self._pub(ChatEnvelope.step(service, conv, step=step, status=status, title=title, data=data, agent=agent), target_sid=target_sid, session_id=session_id)

    async def emit_delta(self, service: ServiceCtx, conv: ConversationCtx, *, text: str, index: int, marker: str = "answer", target_sid: Optional[str] = None, session_id: Optional[str] = None):
        await self._pub(ChatEnvelope.delta(service, conv, text=text, index=index, marker=marker), target_sid=target_sid, session_id=session_id)

    async def emit_complete(self, service: ServiceCtx, conv: ConversationCtx, *, data: Any = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        await self._pub(ChatEnvelope.complete(service, conv, data=data), target_sid=target_sid, session_id=session_id)

    async def emit_error(self, service: ServiceCtx, conv: ConversationCtx, *, error: str, title: Optional[str] = "Workflow Error", step: str = "workflow", target_sid: Optional[str] = None, session_id: Optional[str] = None):
        await self._pub(ChatEnvelope.error(service, conv, error=error, title=title, step=step), target_sid=target_sid, session_id=session_id)

    async def emit_conv_status(
            self,
            svc: ServiceCtx,
            conv: ConversationCtx,
            routing: ChatTaskRouting,
            *,
            state: str,
            updated_at: str,
            completion: str | None = None,
            current_turn_id: str | None = None,
            target_sid: str | None = None,
    ):
        payload = {
            "type": "conv.status",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": {
                "request_id": svc.request_id,
                "tenant": svc.tenant,
                "project": svc.project,
                "user": svc.user,
                "bundle_id": routing.bundle_id
            },
            "conversation": {
                "session_id": conv.session_id,
                "conversation_id": conv.conversation_id,
                "turn_id": conv.turn_id,
            },
            "event": {"step": "conv.state", "status": state},
            "data": {
                "state": state,
                "updated_at": updated_at,
                **({"current_turn_id": current_turn_id} if current_turn_id else {}),
                **({"completion": completion} if completion else {}),
                "origin_stream_id": routing.socket_id
            },
        }
        session_id = routing.session_id

        await self._comm.pub(
            event="conv_status",
            data=payload,
            target_sid=target_sid,
            session_id=session_id,
            channel=self._session_channel(session_id,
                                          tenant=svc.tenant,
                                          project=svc.project),

        )

    def add_listener(self, cb: Callable[[dict], Awaitable[None]]):
        if cb:
            self._callbacks.add(cb)

    # optional, if you want symmetry
    def remove_listener(self, cb: Callable[[dict], Awaitable[None]]):
        try:
            self._callbacks.discard(cb)
        except Exception:
            pass

    async def _ensure_listener(self):
        alive_fn = getattr(self._comm, "listener_alive", None)

        if self._listener_started and alive_fn and not alive_fn():
            logger.warning(
                "[ChatRelayCommunicator] listener flag TRUE but task DEAD; resetting. "
                "relay_id=%s comm_id=%s",
                id(self), id(self._comm)
            )
            self._listener_started = False

        if self._listener_started:
            return

        await self._comm.start_listener(self._dispatch)
        self._listener_started = True

    async def _dispatch(self, message: dict):
        # Fan-out to every registered transport callback
        for cb in list(self._callbacks):
            try:
                await cb(message)
            except Exception:
                logger.exception("Relay callback failed")

    async def acquire_session_channel(self, session_id: str, tenant: str, project: str, *, callback=None):
        if not session_id:
            return
        async with self._sub_lock:
            if callback:
                self.add_listener(callback)
            ch = self._session_channel(session_id, tenant=tenant, project=project)
            logger.info(
                "[ChatRelayCommunicator] acquire session=%s count_before=%s channel=%s "
                "tenant=%s project=%s relay_id=%s comm_id=%s listener_started=%s",
                session_id, self._session_refcounts.get(session_id, 0), ch,
                tenant, project, id(self), id(self._comm), self._listener_started
            )
            count = self._session_refcounts.get(session_id, 0)
            if count == 0:
                await self._comm.subscribe_add(ch)

            self._session_refcounts[session_id] = count + 1
            await self._ensure_listener()

    async def release_session_channel(self, session_id: str, tenant: str, project: str):
        if not session_id:
            return
        async with self._sub_lock:
            count = self._session_refcounts.get(session_id, 0)
            ch = self._session_channel(session_id, tenant=tenant, project=project)
            logger.info(
                "[ChatRelayCommunicator] release session=%s count_before=%s channel=%s "
                "tenant=%s project=%s relay_id=%s comm_id=%s",
                session_id, self._session_refcounts.get(session_id, 0), ch,
                tenant, project, id(self), id(self._comm)
            )
            if count <= 1:
                self._session_refcounts.pop(session_id, None)
                await self._comm.unsubscribe_some(self._session_channel(session_id, tenant=tenant, project=project))
            else:
                self._session_refcounts[session_id] = count - 1

    async def subscribe(self, callback):
        """
        Legacy: global subscribe.
        """
        self.add_listener(callback)
        await self._comm.subscribe(self._channel)
        await self._ensure_listener()

    async def unsubscribe(self):
        await self._comm.stop_listener()
        self._listener_started = False
        self._callbacks.clear()

    async def emit_conversation_status(
            self,
            request_id: str,
            tenant: str,
            project: str,
            bundle_id: str,
            user_id: str,
            session_id: str,
            conversation_id: str,
            state: str,
            updated_at: str | None = None,
            turn_id: str | None = None,
            target_sid: str | None = None,
    ):
        if not updated_at:
            updated_at = datetime.utcnow().isoformat() + "Z"
        payload = {
            "type": "conv.status",
            "timestamp": updated_at,
            "service": {
                "request_id":request_id,
                "tenant": tenant,
                "project": project,
                "user": user_id,
                "bundle_id": bundle_id
            },
            "conversation": {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
            "event": {"step": "conv.state", "status": state},
            "data": {
                "state": state,
                "updated_at": updated_at,
                **({"current_turn_id": turn_id} if turn_id else {}),
            },
        }
        session_id = session_id

        await self._comm.pub(
            event="conv_status",
            data=payload,
            target_sid=target_sid,
            session_id=session_id,
            channel=self._channel,
        )
    # ---------- binding helpers (nice for processors) ----------

    class _Bound:
        def __init__(self, parent: "ChatRelayCommunicator", service: ServiceCtx, conv: ConversationCtx, *, target_sid: Optional[str], session_id: Optional[str]):
            self._p = parent
            self._svc = service
            self._conv = conv
            self._sid = target_sid
            self._room = session_id

        @property
        def service(self) -> ServiceCtx:
            return self._svc

        @property
        def conversation(self) -> ConversationCtx:
            return self._conv

        @property
        def target_sid(self) -> Optional[str]:
            return self._sid

        @property
        def session_id(self) -> Optional[str]:
            return self._room

        async def emit_start(self, message: str, queue_stats: Dict[str, Any] | None = None): await self._p.emit_start(self._svc, self._conv, message=message, queue_stats=queue_stats, target_sid=self._sid, session_id=self._room)
        async def emit_step(self, step: str, status: str, *, title: Optional[str] = None, data: Any = None, agent: Optional[str] = None): await self._p.emit_step(self._svc, self._conv, step=step, status=status, title=title, data=data, agent=agent, target_sid=self._sid, session_id=self._room)
        async def emit_delta(self, text: str, index: int, *, marker: str = "answer"): await self._p.emit_delta(self._svc, self._conv, text=text, index=index, marker=marker, target_sid=self._sid, session_id=self._room)
        async def emit_complete(self, data: Any | None = None): await self._p.emit_complete(self._svc, self._conv, data=data or {}, target_sid=self._sid, session_id=self._room)
        async def emit_error(self, error: str, *, title: Optional[str] = "Workflow Error", step: str = "workflow"): await self._p.emit_error(self._svc, self._conv, error=error, title=title, step=step, target_sid=self._sid, session_id=self._room)

    def bind(self, *, service: ServiceCtx, conversation: ConversationCtx, target_sid: Optional[str] = None, session_id: Optional[str] = None) -> "_Bound":
        return ChatRelayCommunicator._Bound(self, service, conversation, target_sid=target_sid, session_id=session_id)

    async def emit(
            self,
            *,
            event: str,
            data: dict,
            tenant: str,
            project: str,
            room: Optional[str] = None,
            target_sid: Optional[str] = None,
            session_id: Optional[str] = None,
    ):
        sid = session_id or room
        if not sid:
            # best-effort fallback; your system seems to expect session sharding
            try:
                sid = ((data or {}).get("conversation") or {}).get("session_id")
            except Exception:
                sid = None

        await self._comm.pub(
            event=event,
            data=data,
            target_sid=target_sid,
            session_id=sid,
            channel=self._session_channel(sid, tenant=tenant, project=project),
        )


@dataclass
class ChatCommunicator:
    """
    Unified chat communicator that:
      - knows your service & conversation context
      - builds standard envelopes
      - publishes via a transport emitter (relay/socket/etc)
    """
    emitter: ChatRelayCommunicator
    tenant: str
    project: str
    user_id: str
    user_type: str
    service: Dict[str, Any]                  # {request_id, tenant, project, user}
    conversation: Dict[str, Any]             # {session_id, conversation_id, turn_id, socket_id?}
    room: Optional[str] = None               # default fan-out room (session_id)
    target_sid: Optional[str] = None         # optional exact socket target
    event_filter: Optional[IEventFilter] = None

    def __post_init__(self):
        # default room = session_id
        self.room = self.room or self.conversation.get("session_id")
        self.target_sid = self.target_sid or self.conversation.get("socket_id")
        self._delta_cache: dict[Tuple[str, str, str, str, str, str, str], _DeltaAggregate] = {}
        # self.event_filter: IEventFilter = self.event_filter or DefaultEventFilter()

    # ---------- low-level ----------
    def _build_filter_input(self, socket_event: str, data: dict | None, broadcast: bool) -> EventFilterInput:
        # tolerant extraction: works for enveloped and semi-enveloped payloads
        d = data or {}
        ev = d.get("event") or {}

        return EventFilterInput(
            socket_event=socket_event,
            type=d.get("type") or ev.get("type"),
            agent=ev.get("agent"),
            step=ev.get("step"),
            status=ev.get("status"),
            broadcast=broadcast,
            route=d.get("route")
        )
    async def emit(self, event: str, data: dict, broadcast: bool = False):
        # Single choke point
        try:
            if self.event_filter:
                ev_in = self._build_filter_input(event, data, broadcast)

                if not self.event_filter.allow_event(
                    user_type=self.user_type,
                    user_id=self.user_id,
                    event=ev_in,
                    data=data,
                ):
                    return
        except Exception:
            # fail-open: don't break runtime if a custom filter crashes
            pass

        await self.emitter.emit(
            event=event,
            data=data,
            room=self.room,
            target_sid=None if broadcast else self.target_sid,
            session_id=self.conversation.get("session_id"),
            tenant=self.tenant,
            project=self.project,
        )

    # ----- internal buffer helpers -----
    def _record_delta(self, *, text: str, index: int, agent: str, marker: str, format: str, artifact_name: str, title: Optional[str] = None, extra: Optional[dict] = None):
        if not text:
            return
        conv_id = (self.conversation or {}).get("conversation_id") or ""
        turn_id = (self.conversation or {}).get("turn_id") or ""
        title_norm = title or ""
        key = (conv_id, turn_id, agent or "assistant", marker or "answer", format, artifact_name, title_norm)
        agg = self._delta_cache.get(key)
        if not agg:
            agg = _DeltaAggregate(conversation_id=conv_id, turn_id=turn_id,
                                  agent=agent or "assistant", marker=marker or "answer",
                                  format=format, artifact_name=artifact_name, title=title_norm)
            self._delta_cache[key] = agg
        if extra:
            for k, v in extra.items():
                if k not in agg.extra and v is not None:
                    agg.extra[k] = v
        agg.append(ts=_now_ms(), idx=int(index), text=text)

    def get_delta_aggregates(self, *, conversation_id: str | None = None,
                             turn_id: str | None = None,
                             agent: str | None = None,
                             marker: str | None = None,
                             merge_text: bool = True) -> list[dict]:
        """
        Returns a list of dicts:
          {agent, marker, conversation_id, turn_id, ts_first, ts_last, text, chunks:[{ts, idx, text}]}
        Filter by any of the fields if provided.
        """
        out = []
        for (cid, tid, a, m, f, an, title), agg in self._delta_cache.items():
            if conversation_id and cid != conversation_id: continue
            if turn_id and tid != turn_id: continue
            if agent and a != agent: continue
            if marker and m != marker: continue
            out.append({
                "conversation_id": cid,
                "turn_id": tid,
                "agent": a,
                "marker": m,
                "format": f,
                "artifact_name": an,
                "title": title,
                "extra": dict(agg.extra or {}),
                "ts_first": agg.ts_first,
                "ts_last": agg.ts_last,
                "text": agg.merged_text() if merge_text else "",
                "chunks": [{"ts": c.ts, "idx": c.idx, "text": c.text} for c in agg.chunks],
            })
        # order by first appearance
        out.sort(key=lambda r: (r["ts_first"], r["agent"], r["marker"]))
        return out

    def clear_delta_aggregates(self, *, conversation_id: str | None = None,
                               turn_id: str | None = None):
        """Clear cache for a specific turn (or everything if not specified)."""
        if not conversation_id and not turn_id:
            self._delta_cache.clear()
            return
        keys = list(self._delta_cache.keys())
        for k in keys:
            cid, tid, *_ = k
            if conversation_id and cid != conversation_id: continue
            if turn_id and tid != turn_id: continue
            self._delta_cache.pop(k, None)

    # ---------- export / persist / merge ----------
    def export_delta_cache(self, *, conversation_id: str | None = None,
                           turn_id: str | None = None,
                           agent: str | None = None,
                           marker: str | None = None,
                           merge_text: bool = False) -> list[dict]:
        """
        Snapshot the delta cache (optionally filtered). Default merge_text=False
        to keep raw chunks; the host can re-merge deterministically.
        """
        return self.get_delta_aggregates(
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent=agent,
            marker=marker,
            merge_text=merge_text,
        )

    def dump_delta_cache(self, path) -> bool:
        """
        Write the current delta cache to a JSON file:
          {"items":[ ...aggregates... ]}
        Returns True on success, False on failure.
        """
        try:
            from pathlib import Path
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {"items": self.export_delta_cache(merge_text=False)}
            import json as _json
            p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def merge_delta_cache(self, items: list[dict]) -> None:
        """
        Merge exported aggregates (items) into this communicator's cache.
        Deduplicates chunks by (idx, ts, text) per aggregate key.
        """
        def _k(it: dict) -> tuple[str, str, str, str, str, str, str]:
            return (
                it.get("conversation_id") or "",
                it.get("turn_id") or "",
                it.get("agent") or "assistant",
                it.get("marker") or "answer",
                it.get("format") or "markdown",
                it.get("artifact_name") or "Unknown",
                it.get("title") or "",
            )

        for it in (items or []):
            key = _k(it)
            agg = self._delta_cache.get(key)
            if not agg:
                agg = _DeltaAggregate(
                    conversation_id=key[0], turn_id=key[1],
                    agent=key[2], marker=key[3],
                    format=key[4], artifact_name=key[5], title=key[6]
                )
                self._delta_cache[key] = agg
            if isinstance(it.get("extra"), dict):
                for k, v in it["extra"].items():
                    if k not in agg.extra and v is not None:
                        agg.extra[k] = v
            # dedupe chunks
            existing = {(c.idx, c.ts, c.text) for c in agg.chunks}
            for c in (it.get("chunks") or []):
                idx = int(c.get("idx") or 0)
                ts  = int(c.get("ts") or 0)
                txt = c.get("text") or ""
                sig = (idx, ts, txt)
                if sig in existing:
                    continue
                agg.append(ts=ts, idx=idx, text=txt)
            # keep text merged ordering stable
            agg.chunks.sort(key=lambda c: (c.idx, c.ts))

    def merge_delta_cache_from_file(self, path) -> None:
        """Load {"items":[...]} and merge into this comm."""
        try:
            from pathlib import Path
            import json as _json
            p = Path(path)
            if not p.exists():
                return
            data = _json.loads(p.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list):
                self.merge_delta_cache(items)
        except Exception:
            return

    # ---------- envelopes ----------
    def _base_env(self, typ: str) -> Dict[str, Any]:
        return {
            "type": typ,
            "timestamp": _iso_now(),
            "ts": int(time.time() * 1000),
            "service": dict(self.service or {}),
            "conversation": {
                "session_id": self.conversation.get("session_id"),
                "conversation_id": self.conversation.get("conversation_id"),
                "turn_id": self.conversation.get("turn_id"),
            },
            "event": {"step": "event", "status": "update"},
        }

    async def emit_enveloped(self, env: dict):
        # sniff and record deltas coming through the generic path
        try:
            if (env or {}).get("type") in ("chat.delta", "chat.assistant.delta"):
                d = (env or {}).get("delta") or {}
                text = (d.get("text") or env.get("text") or "")
                idx  = int(d.get("index") or env.get("idx") or 0)
                marker = (d.get("marker") or "answer")
                eve = env.get("event") or {}
                agent  = eve.get("agent") or "assistant"
                format  = eve.get("format") or "markdown"
                artifact_name  = eve.get("artifact_name") or "Unknown"
                title = eve.get("title") or None
                extra = (env or {}).get("extra")
                self._record_delta(
                    text=text,
                    index=idx,
                    agent=agent,
                    marker=marker,
                    format=format,
                    artifact_name=artifact_name,
                    title=title,
                    extra=extra if isinstance(extra, dict) else None,
                )
        except Exception:
            pass

        typ = (env or {}).get("type")
        route = {
            "chat.start": "chat_start",
            "chat.step": "chat_step",
            "chat.delta": "chat_delta",
            "chat.complete": "chat_complete",
            "chat.error": "chat_error",
        }.get(typ, "chat_step")
        await self.emit(route, env)

    # ---------- high-level helpers ----------
    async def start(self, *, message: str, queue_stats: Optional[dict] = None) -> None:
        env = self._base_env("chat.start")
        env["event"] = {"step": "turn", "status": "started", "title": "Turn Started"}
        env["data"] = {"message": message, "queue_stats": queue_stats or {}}
        await self.emit("chat_start", env)

    async def step(self, *, step: str, status: str, title: Optional[str] = None,
                   agent: Optional[str] = None, data: Optional[dict] = None, markdown: Optional[str] = None):
        env = self._base_env("chat.step")
        env["event"].update({"step": step, "status": status, "title": title, "agent": agent})
        env["data"] = data or {}
        if markdown:
            env["event"]["markdown"] = markdown
        await self.emit("chat_step", env, broadcast=False)

    async def delta(self, *, text: str, index: int,
                    marker: str = "answer", agent: str = "assistant",
                    completed: bool = False, **kwargs):
        env = self._base_env("chat.delta")
        env["event"].update({"agent": agent, "step": "stream", "status": "running", "title": "Assistant Delta"})
        env["delta"] = {"text": text, "marker": marker, "index": int(index), "completed": completed }
        # back-compat mirrors
        env["text"] = text
        env["idx"] = int(index)
        if kwargs:
            env["extra"] = kwargs

        # record before sending
        try:
            self._record_delta(text=text, index=index,
                               agent=agent,
                               marker=marker,
                               format=kwargs.get("format"),
                               artifact_name=kwargs.get("artifact_name"),
                               title=kwargs.get("title"),
                               extra=kwargs)
        except Exception:
            pass

        # TODO: remove after integration of subsystem channel is completed
        await self.emit("chat_delta", env, broadcast=False)
        if marker == "subsystem":
            try:
                env_tool = copy.deepcopy(env)
                env_tool["delta"]["marker"] = "tool"
                env_tool["text"] = text
                env_tool["idx"] = int(index)
                try:
                    self._record_delta(
                        text=text,
                        index=index,
                        agent=agent,
                        marker="tool",
                        format=kwargs.get("format"),
                        artifact_name=kwargs.get("artifact_name"),
                        title=kwargs.get("title"),
                        extra=kwargs,
                    )
                except Exception:
                    pass
                await self.emit("chat_delta", env_tool, broadcast=False)
            except Exception:
                pass

    async def complete(self, *, data: dict):
        env = self._base_env("chat.complete")
        env["event"].update({"agent": "answer_generator", "step": "stream", "status": "completed", "title": "Turn Completed"})
        env["data"] = data or {}
        await self.emit("chat_complete", env)

    async def error(self, *, message: str, data: Optional[dict] = None, agent: str | None = None, step: str = "chat.error", title: str = "Workflow Error"):
        env = self._base_env("chat.error")
        env["event"].update({ "agent": agent, "step": step, "status": "error", "title": title })
        env["data"] = { "error": message, **(data or {}) }
        await self.emit("chat_error", env)

    async def event(
            self,
            *,
            agent: str | None,
            type: str,                   # e.g. "chat.followups"
            title: str | None = None,
            step: str = "event",
            data: dict | None = None,
            markdown: str | None = None,
            route: str | None = None,    # optional override for socket event name
            status: str = "update",      # e.g. "started" | "completed" | "update"
            auto_markdown: bool = True, # try to fill in event.markdown if missing
            broadcast: bool = False,
    ):
        """
        Generic typed chat event with full wrapping (service/conversation).

        - everything payload-like goes into env["data"].
        - if `route` not given, emit on type-derived socket event: type.replace(".", "_").
        - no 'compose' handling, no 'chat_step' routing here.
        """
        env = self._base_env(type)
        env["event"].update({
            "agent": agent,
            "title": title,
            "status": status,
            "step": step,
        })
        env["data"] = data or {}
        if markdown:
            env["event"]["markdown"] = markdown
        elif auto_markdown:
            try:
                ensure_event_markdown(env)  # fills env['event']['markdown'] if missing
            except Exception:
                import traceback
                print(traceback.format_exc())

        socket_event = _EVENT_MAP.get(route) or "chat_step"
        if route:
            env["route"] = route
        await self.emit(event=socket_event, data=env, broadcast=broadcast)

    async def service_event(
            self,
            *,
            type: str,                      # logical type, e.g. "accounting.usage"
            step: str,                      # e.g. "accounting" / "workflow"
            status: str,                    # "started" | "running" | "completed" | "error" | "skipped"
            title: str | None = None,
            data: dict | None = None,
            agent: str | None = None,
            markdown: str | None = None,
            auto_markdown: bool = True,
            broadcast: bool = False,
    ) -> None:
        """
        Emit a service-level event over the `chat_service` socket route, using the
        communicator's own service / conversation context.

        This is intentionally minimal: you only provide semantic bits (type, step, status, etc.);
        tenant/project/user/session/turn are taken from `self`.
        """
        # base envelope with timestamp, ts, service, conversation, event
        env = self._base_env(type)
        env["event"].update({
            "agent": agent,
            "title": title,
            "status": status,
            "step": step,
        })
        env["data"] = data or {}

        if markdown:
            env["event"]["markdown"] = markdown
        elif auto_markdown:
            try:
                ensure_event_markdown(env)
            except Exception:
                import traceback
                print(traceback.format_exc())

        # let downstream know which socket route this came through
        env["route"] = "chat_service"

        await self.emit(
            event="chat_service",   # actual Socket.IO / pubsub event name
            data=env,
            broadcast=broadcast,
        )

    def _export_comm_spec_for_runtime(self) -> dict:
        """
        Produce a minimal, process-safe JSON spec to rebuild a communicator in the runtime.
        We try to extract redis_url/channel from the relay-based emitter; fall back to env/defaults.
        """
        comm = self

        # Defaults if we can't introspect
        channel   = "chat.events"
        service   = {}
        conversation = {}
        room = None
        target_sid = None
        user_id = None
        user_type = None
        tenant = None
        project = None

        if comm is not None:
            # payloads for identity
            try:
                service = dict(comm.service or {})
            except Exception:
                pass
            try:
                conversation = dict(comm.conversation or {})
            except Exception:
                pass
            room = getattr(comm, "room", None)
            target_sid = getattr(comm, "target_sid", None)
            user_id = getattr(comm, "user_id", None)
            user_type = getattr(comm, "user_type", None)
            tenant = getattr(comm, "tenant", None)
            project = getattr(comm, "project", None)

            # Try to extract redis details from Relay adapter
            try:
                emitter = getattr(comm, "emitter", None)
                relay = getattr(emitter, "_relay", None)
                # ChatRelayCommunicator has _comm (ServiceCommunicator) and _channel
                if relay is not None:
                    channel = getattr(relay, "_channel", channel)
            except Exception:
                pass

        return {
            "channel": channel,
            "service": service,
            "conversation": conversation,
            "room": room,
            "target_sid": target_sid,
            "user_id": user_id,
            "user_type": user_type,
            "tenant": tenant,
            "project": project,
        }

def build_relay_from_env() -> ChatRelayCommunicator:

    settings = get_settings()
    redis_url = settings.REDIS_URL
    channel = os.getenv("CHAT_RELAY_CHANNEL", "chat.events")
    return ChatRelayCommunicator(redis_url=redis_url, channel=channel)

def build_comm_from_comm_context(
        task: ChatTaskPayload,
        *,
        relay: Optional[ChatRelayCommunicator] = None,
        event_filter: Optional[IEventFilter] = None,
) -> ChatCommunicator:
    # if not provided, create a NEW relay instance
    relay = relay or build_relay_from_env()

    session_id = task.routing.session_id
    socket_id = task.routing.socket_id
    request_id = task.request.request_id

    svc = ServiceCtx(
        request_id=request_id,
        tenant=task.actor.tenant_id,
        project=task.actor.project_id,
        user=task.user.user_id or task.user.fingerprint,
        user_obj=task.user,
    )

    conv = ConversationCtx(
        session_id=session_id,
        conversation_id=(task.routing.conversation_id or session_id),
        turn_id=task.routing.turn_id,
    )
    # IMPORTANT: new communicator instance every time
    return ChatCommunicator(
        emitter=relay,
        service=svc.model_dump(),
        conversation=conv.model_dump(),
        room=session_id,
        target_sid=socket_id,
        tenant=task.actor.tenant_id,
        project=task.actor.project_id,
        user_id=task.user.user_id,
        user_type=task.user.user_type,
        event_filter=event_filter,
    )
