# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/emitters.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable, Dict, List, Tuple
import os, logging, time

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatEnvelope, ServiceCtx, ConversationCtx, _iso_now
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
                "ORCHESTRATOR_IDENTITY",
                f"kdcube_orchestrator_{os.environ.get('ORCHESTRATOR_TYPE', 'dramatiq')}",
            ),
        )
        self._channel = channel

    # ---------- publish ----------

    def _pub(self, env: ChatEnvelope, *, target_sid: Optional[str], session_id: Optional[str]):
        # Always publish TYPE-MAPPED event so the relay only emits
        event = _EVENT_MAP[env.type]
        self._comm.pub(
            event=event,
            data=env.dump_model(),
            target_sid=target_sid,
            session_id=session_id,
            channel=self._channel,
        )

    def emit_envelope(self, env: ChatEnvelope, *, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        """Low-level: publish a prebuilt envelope."""
        self._pub(env, target_sid=target_sid, session_id=session_id)

    def emit_start(self, service: ServiceCtx, conv: ConversationCtx, *, message: str, queue_stats: Dict[str, Any] | None = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        self._pub(ChatEnvelope.start(service, conv, message=message, queue_stats=queue_stats), target_sid=target_sid, session_id=session_id)

    def emit_step(self, service: ServiceCtx, conv: ConversationCtx, *, step: str, status: str, title: Optional[str] = None, data: Any = None, agent: Optional[str] = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        self._pub(ChatEnvelope.step(service, conv, step=step, status=status, title=title, data=data, agent=agent), target_sid=target_sid, session_id=session_id)

    def emit_delta(self, service: ServiceCtx, conv: ConversationCtx, *, text: str, index: int, marker: str = "answer", target_sid: Optional[str] = None, session_id: Optional[str] = None):
        self._pub(ChatEnvelope.delta(service, conv, text=text, index=index, marker=marker), target_sid=target_sid, session_id=session_id)

    def emit_complete(self, service: ServiceCtx, conv: ConversationCtx, *, data: Any = None, target_sid: Optional[str] = None, session_id: Optional[str] = None):
        self._pub(ChatEnvelope.complete(service, conv, data=data), target_sid=target_sid, session_id=session_id)

    def emit_error(self, service: ServiceCtx, conv: ConversationCtx, *, error: str, title: Optional[str] = "Workflow Error", step: str = "workflow", target_sid: Optional[str] = None, session_id: Optional[str] = None):
        self._pub(ChatEnvelope.error(service, conv, error=error, title=title, step=step), target_sid=target_sid, session_id=session_id)

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

        def emit_start(self, message: str, queue_stats: Dict[str, Any] | None = None): self._p.emit_start(self._svc, self._conv, message=message, queue_stats=queue_stats, target_sid=self._sid, session_id=self._room)
        def emit_step(self, step: str, status: str, *, title: Optional[str] = None, data: Any = None, agent: Optional[str] = None): self._p.emit_step(self._svc, self._conv, step=step, status=status, title=title, data=data, agent=agent, target_sid=self._sid, session_id=self._room)
        def emit_delta(self, text: str, index: int, *, marker: str = "answer"): self._p.emit_delta(self._svc, self._conv, text=text, index=index, marker=marker, target_sid=self._sid, session_id=self._room)
        def emit_complete(self, data: Any | None = None): self._p.emit_complete(self._svc, self._conv, data=data or {}, target_sid=self._sid, session_id=self._room)
        def emit_error(self, error: str, *, title: Optional[str] = "Workflow Error", step: str = "workflow"): self._p.emit_error(self._svc, self._conv, error=error, title=title, step=step, target_sid=self._sid, session_id=self._room)

        def make_emitters(self) -> tuple[Callable[[str, str, Dict[str, Any] | None], Awaitable[None]], Callable[[str, int, Dict[str, Any] | None], Awaitable[None]]]:
            """
            Returns (step_emitter, delta_emitter) that workflows expect.
            """
            async def step_emitter(step: str, status: str, payload: Dict[str, Any] | None = None):
                p = payload or {}
                self.emit_step(step, status, title=p.get("title"), data=p.get("data") if "data" in p else p, agent=p.get("agent"))

            async def delta_emitter(text: str, idx: int, meta: Dict[str, Any] | None = None):
                marker = (meta or {}).get("marker", "answer")
                self.emit_delta(text, idx, marker=marker)

            return step_emitter, delta_emitter

    def bind(self, *, service: ServiceCtx, conversation: ConversationCtx, target_sid: Optional[str] = None, session_id: Optional[str] = None) -> "_Bound":
        return ChatRelayCommunicator._Bound(self, service, conversation, target_sid=target_sid, session_id=session_id)

    # ---------- subscribe / relay ----------

    async def subscribe(self, callback):
        await self._comm.subscribe(self._channel)
        await self._comm.start_listener(callback)

    async def unsubscribe(self):
        await self._comm.stop_listener()

@dataclass
class ChatCommunicator:
    """
    Unified chat communicator that:
      - knows your service & conversation context
      - builds standard envelopes
      - publishes via a transport emitter (relay/socket/etc)
    """
    emitter: Any                             # ChatRelayEmitter | SocketIOEmitter | NoopEmitter
    service: Dict[str, Any]                  # {request_id, tenant, project, user}
    conversation: Dict[str, Any]             # {session_id, conversation_id, turn_id, socket_id?}
    room: Optional[str] = None               # default fan-out room (session_id)
    target_sid: Optional[str] = None         # optional exact socket target

    def __post_init__(self):
        # default room = session_id
        self.room = self.room or self.conversation.get("session_id")
        self.target_sid = self.target_sid or self.conversation.get("socket_id")
        self._delta_cache: dict[Tuple[str, str, str, str, str, str], _DeltaAggregate] = {}

    # ---------- low-level ----------
    async def emit(self, event: str, data: dict):
        await self.emitter.emit(
            event=event,
            data=data,
            room=self.room,
            target_sid=self.target_sid,
            session_id=self.conversation.get("session_id"),
        )

    # ----- internal buffer helpers -----
    def _record_delta(self, *, text: str, index: int, agent: str, marker: str, format: str, artifact_name: str):
        if not text:
            return
        conv_id = (self.conversation or {}).get("conversation_id") or ""
        turn_id = (self.conversation or {}).get("turn_id") or ""
        key = (conv_id, turn_id, agent or "assistant", marker or "answer", format, artifact_name)
        agg = self._delta_cache.get(key)
        if not agg:
            agg = _DeltaAggregate(conversation_id=conv_id, turn_id=turn_id,
                                  agent=agent or "assistant", marker=marker or "answer",
                                  format=format, artifact_name=artifact_name)
            self._delta_cache[key] = agg
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
        for (cid, tid, a, m, f, an), agg in self._delta_cache.items():
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
            cid, tid, _, _, _, _ = k
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
        def _k(it: dict) -> tuple[str, str, str, str, str, str]:
            return (
                it.get("conversation_id") or "",
                it.get("turn_id") or "",
                it.get("agent") or "assistant",
                it.get("marker") or "answer",
                it.get("format") or "markdown",
                it.get("artifact_name") or "Unknown",
            )

        for it in (items or []):
            key = _k(it)
            agg = self._delta_cache.get(key)
            if not agg:
                agg = _DeltaAggregate(
                    conversation_id=key[0], turn_id=key[1],
                    agent=key[2], marker=key[3],
                    format=key[4], artifact_name=key[5]
                )
                self._delta_cache[key] = agg
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
            data = _json.loads(p.read_text(encoding="utf-8")) or {}
            items = data.get("items") or []
            self.merge_delta_cache(items)
        except Exception:
            pass

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
                self._record_delta(text=text, index=idx, agent=agent, marker=marker, format=format, artifact_name=artifact_name)
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
    async def start(self, *, message: str, queue_stats: Optional[dict] = None):
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
        await self.emit("chat_step", env)

    async def delta(self, *, text: str, index: int, marker: str = "answer", agent: str = "assistant", completed: bool = False, **kwargs):
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
            self._record_delta(text=text, index=index, agent=agent, marker=marker,
                               format=kwargs.get("format"), artifact_name=kwargs.get("artifact_name"))
        except Exception:
            pass

        await self.emit("chat_delta", env)

    async def complete(self, *, data: dict):
        env = self._base_env("chat.complete")
        env["event"].update({"agent": "answer_generator", "step": "stream", "status": "completed", "title": "Turn Completed"})
        env["data"] = data or {}
        await self.emit("chat_complete", env)

    async def error(self, *, message: str, data: Optional[dict] = None):
        env = self._base_env("chat.error")
        env["event"].update({"step": "workflow", "status": "error", "title": "Workflow Error"})
        env["data"] = {"error": message, **(data or {})}
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
                pass

        socket_event = route or "chat_step"
        await self.emit(socket_event, env)

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
        }

class _RelayEmitterAdapter:
    """
    Async adapter that lets ChatCommunicator 'await emitter.emit(...)' while
    internally publishing via ChatRelayCommunicator's ServiceCommunicator.
    """
    def __init__(self, relay: ChatRelayCommunicator):
        self._relay = relay

    async def emit(self, event: str, data: dict, *, room: Optional[str] = None,
                   target_sid: Optional[str] = None, session_id: Optional[str] = None):
        try:
            # Route to the relay’s pub/sub channel. 'session_id' takes priority, else fall back to room.
            self._relay._comm.pub(  # underlying transport publisher
                event=event,
                data=data,
                target_sid=target_sid,
                session_id=session_id or room,
                channel=self._relay._channel,
            )
        except Exception as e:
            logger.error(f"Relay emit failed for event '{event}': {e}")
