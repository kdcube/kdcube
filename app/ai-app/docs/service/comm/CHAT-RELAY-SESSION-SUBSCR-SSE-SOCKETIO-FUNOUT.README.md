---
id: ks:docs/service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md
title: "Chat Relay Session Subscr SSE Socketio Funout"
summary: "SSE/Socket.IO relay design: session subscriptions, tenant/project SSE subscriptions, and fan-out via Redis."
tags: ["service", "comm", "relay", "sse", "redis"]
keywords: ["session subscription", "project subscription", "fanout", "redis pubsub", "SSE relay", "Socket.IO"]
see_also:
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/data-bus-README.md
  - ks:docs/service/README-monitoring-observability.md
---
# Redis-based Chat Relay & SSE Fan-Out

> Note: This document focuses on SSE fan-out, but the same relay/channeling
> design is used by Socket.IO as well. See:
> - [Socket.IO transport](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py)
> - [Comm integrations overview](README-comm.md)

> Boundary: this relay is for transient client delivery. Durable non-chat
> bundle messages, such as canvas patches or issue updates, belong to the
> planned [Data Bus](data-bus-README.md) and should be routed to Data Bus Redis
> Streams, not to this Pub/Sub relay.

This document describes the architecture of the **chat relay** in the KDCube AI App, specifically:

* How **Redis Pub/Sub** is used as a relay between orchestrators/workers and the web app.
* How the **SSE hub** dynamically subscribes to per-session channels.
* How the SSE hub can also subscribe to tenant/project channels for compact
  non-chat updates such as dashboard snapshots.
* The problems we were fighting (high traffic, wrong subscription semantics, sync I/O).
* What did **not** work and what the final working design looks like.

---

## Goals & Problems We Were Fighting

### 1. Too much traffic on shared channels

Originally, chat events were published to a **small set of shared channels** (e.g. `kdcube_orchestrator_dramatiq` or a handful of fixed channels). This caused:

* Every web process that subscribed had to receive **events for all sessions**.
* Each process then had to **filter in-process** by `session_id` and ignore ~99% of messages.
* With many sessions, this becomes noisy: unnecessary Redis traffic and extra CPU.

We wanted:

* Per-session routing on Redis, so each process only receives the events it actually needs to deliver.
* The ability to **shard** channels by session without changing orchestrator logic too much.

### 2. Need for fully async I/O

The platform requirement: **no sync I/O** in the hot path.

* We had a **sync Redis client** for publishing, while subscription was async via `redis.asyncio`.
* This was inconsistent and could block the event loop or at least be conceptually wrong for our platform.

We changed to **async publish** via `redis.asyncio.Redis` as well.

### 3. Dynamic subscriptions that actually work

We wanted dynamic subscriptions:

* When the first client of a session connects:

    * Subscribe to `chat.events.<session_id>` on Redis.
* When the last client of that session disconnects:

    * Unsubscribe from that channel.

This should avoid subscribing to channels for sessions that have no active SSE clients.

The subtle bug we hit:

* We started the listener **before any real `SUBSCRIBE`** happened, using `subscribe_add([])` as a “warm-up”.
* `redis.asyncio` requires that `subscribe()` / `psubscribe()` be called **before** you start consuming messages.
* This led to:
  `pubsub connection not set: did you forget to call subscribe() or psubscribe()?`

The fix was to **start the listener only after the first real subscription**.

---

## Key Components

### `ServiceCommunicator`

File: `kdcube_ai_app/infra/orchestration/app/communicator.py`

Responsibilities:

* Unified **Redis Pub/Sub helper**.
* Async **publisher** API: `pub()`.
* Async **subscriber** API: `subscribe()`, `subscribe_add()`, `unsubscribe_some()`.
* Single **listener task** per process: `start_listener(on_message)`.

Channel naming:

* Orchestrator identity prefix, e.g. `kdcube.relay.chatbot`.
* Logical channel names like `chat.events` or `chat.events.<session_id>`.
* Final Redis channel:

  ```text
  <orchestrator_identity>.<logical_channel>
  # e.g. kdcube.relay.chatbot.chat.events.6d88a4fb-...
  ```

Publish (simplified):

```python
async def pub(self, event, target_sid, data, channel="chat.events", session_id=None):
    message = {
        "target_sid": target_sid,
        "session_id": session_id,
        "event": event,
        "data": data,
        "timestamp": time.time(),
    }

    logical_channel = channel
    if session_id and channel == "chat.events":
        logical_channel = f"{channel}.{session_id}"

    full_channel = self._fmt_channel(logical_channel)
    await self._ensure_async()
    payload = json.dumps(message, ensure_ascii=False)
    await self._aioredis.publish(full_channel, payload)
```

Subscribe / dynamic add:

```python
async def subscribe_add(self, channels, *, pattern=False):
    await self._ensure_async()
    if self._pubsub is None:
        self._pubsub = self._aioredis.pubsub()

    if isinstance(channels, str):
        channels = [channels]

    formatted = [self._fmt_channel(ch) for ch in channels]
    new_channels = [ch for ch in formatted if ch not in self._subscribed_channels]
    if not new_channels:
        return

    self._subscribed_channels.extend(new_channels)

    if pattern:
        await self._pubsub.psubscribe(*new_channels)
    else:
        await self._pubsub.subscribe(*new_channels)
```

Listener:

```python
async def start_listener(self, on_message: Callable[[dict], Any]):
    if not self._pubsub:
        raise RuntimeError("Call subscribe() before start_listener().")

    async def _loop():
        try:
            async for payload in self.listen():
                res = on_message(payload)
                if asyncio.iscoroutine(res):
                    await res
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ServiceCommunicator] listener error: %s", e)

    if self._listen_task and not self._listen_task.done():
        return

    self._listen_task = asyncio.create_task(_loop(), name="service-communicator-listener")
```

> **Important invariant**
> `start_listener()` must be called **after** at least one `subscribe()` / `subscribe_add()` that actually calls Redis `SUBSCRIBE` or `PSUBSCRIBE`.

---

### `SSEHub` – Redis → SSE fan-out

File: `kdcube_ai_app/apps/chat/ingress/sse/chat.py` (inside `SSEHub` class)

Responsibilities:

* Maintain an in-process map of active SSE clients per `session_id`.
* Dynamically subscribe to Redis channels when the first client for a session arrives.
* Unsubscribe when the last client for that session disconnects.
* Receive messages from `ServiceCommunicator` and fan them out as SSE frames.

Per-client structure:

```python
@dataclass(frozen=True)
class Client:
    tenant: Optional[str]
    project: Optional[str]
    session_id: str
    stream_id: Optional[str]      # for DM/targeted messages
    queue: asyncio.Queue[str]     # SSE frames queue
    project_events: bool = False  # opt into tenant/project SSE topic
```

Register logic (key part):

```python
async def register(self, client: Client):
    async with self._lock:
        lst = self._by_session.setdefault(client.session_id, [])
        was_empty = not lst
        lst.append(client)

    if was_empty:
        # First client for this session → subscribe + start listener
        session_ch = f"chat.events.{client.session_id}"
        await self.chat_comm._comm.subscribe_add(session_ch)
        logger.info("[SSEHub] subscribe for session=%s channel=%s", client.session_id, session_ch)

        # Ensure listener is running (safe to call multiple times)
        await self.chat_comm._comm.start_listener(self._on_relay)

    logger.info(
        "[SSEHub] register session=%s total=%d",
        client.session_id,
        len(self._by_session[client.session_id]),
    )
```

Unregister logic:

* When the last client for a session is removed, we compute `remove_channel = "chat.events.<session>"`.
* Call `unsubscribe_some(remove_channel)` on the communicator.
* That removes the channel from `_subscribed_channels` and calls `UNSUBSCRIBE` / `PUNSUBSCRIBE`.

Relay callback (fan-out with reconnection fallback):

```python
async def _on_relay(self, message: dict):
    # message = { event, data, target_sid?, session_id? }
    event = message.get("event")
    data = message.get("data") or {}
    target_sid = message.get("target_sid")
    room = message.get("session_id")

    if not event or not room:
        return

    async with self._lock:
        recipients = list(self._by_session.get(room, []))

    if not recipients:
        return

    frame = _sse_frame(event, data, event_id=str(uuid.uuid4()))

    if target_sid:
        # DM: only client with matching stream_id.
        # Client reuses stream_id across reconnects, so this should
        # always match as long as the session is the same.
        delivered = False
        for c in recipients:
            if c.stream_id and c.stream_id == target_sid:
                self._enqueue(c, frame)
                delivered = True
        if not delivered:
            logger.warning(
                "target stream_id=%s not found among %d client(s) for session=%s; "
                "message dropped (connected: %s)",
                target_sid, len(recipients), room,
                [c.stream_id for c in recipients],
            )
    else:
        for c in recipients:
            self._enqueue(c, frame)
```

> **Reconnection safety:** The client reuses its `stream_id` across reconnects
> (generated once per session, not per connection). This ensures the processor's
> `target_sid` stays valid after reconnect. If a mismatch still occurs, the message
> is dropped with a warning log (not silently) so it can be diagnosed.

---

### SSE `/sse/stream` endpoint

For each HTTP SSE connection:

* Resolve/validate `UserSession`.
* Create a bounded `asyncio.Queue[str]`.
* Wrap it in a `Client(session_id, stream_id, queue)`.
* Call `app.state.sse_hub.register(client)`.
* Return a `StreamingResponse` whose generator:

    * Sends initial `ready` event.
    * Then loops:

        * `frame = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)`
        * On timeout, send `: keepalive`.

When the request is cancelled / disconnected, we call:

```python
await app.state.sse_hub.unregister(client)
```

### Tenant/project SSE events

Session broadcast and project broadcast are separate primitives:

| Primitive | Subscribers | Publisher | Use case |
| --- | --- | --- | --- |
| `comm.service_event(..., broadcast=False)` | current `KDC-Stream-ID` when present, otherwise current session fanout | request-bound bundle/workflow code | direct reply to the tab/window that called an operation |
| `comm.service_event(..., broadcast=True)` | all SSE clients in the current user session | request-bound bundle/workflow code | update every tab for the same user session |
| `comm.project_event(...)` | SSE clients that opened `/sse/stream` with `project_events=true` for the same tenant/project | request-bound or headless code with a `ChatCommunicator` | compact tenant/project updates such as stats snapshots |

Client opt-in:

```ts
const url = new URL(`${baseUrl}/sse/stream`);
url.searchParams.set("user_session_id", sessionId);
url.searchParams.set("stream_id", streamId);
url.searchParams.set("tenant", tenant);
url.searchParams.set("project", project);
url.searchParams.set("project_events", "true");

const es = new EventSource(url.toString(), { withCredentials: true });
es.addEventListener("chat_service", (event) => {
  const env = JSON.parse(event.data);
  if (env.type === "my.bundle.snapshot") {
    // apply compact project snapshot
  }
});
```

Server-side publisher:

```python
await comm.project_event(
    type="my.bundle.snapshot",
    step="snapshot",
    status="completed",
    title="Snapshot updated",
    data={"snapshot": snapshot},
    auto_markdown=False,
)
```

Implementation points:

- Server publisher: `kdcube_ai_app/apps/chat/emitters.py`
  (`ChatCommunicator.project_event`, `ChatRelayCommunicator.emit_project`).
- SSE subscription and fanout:
  `kdcube_ai_app/apps/chat/ingress/sse/chat.py`
  (`project_events` query parameter, `SSEHub._on_relay`).

Project events use the Redis channel
`{tenant}:{project}:chat.events.__project__` and are only delivered to clients
that explicitly opted in. They are meant for compact, debounced updates. Do not
publish raw logs, raw telemetry events, prompts, or unbounded payloads through
this path.

---

## End-to-End Flow

### Architecture diagram (two-instance deployment)

Ingress and processor run as **separate uvicorn instances**. They communicate via Redis.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Client (Browser)                                │
│  EventSource(/sse/stream)  ←──SSE frames──  HTTP POST(/sse/chat) ──→  │
└─────────┬──────────────────────────────────────────────┬────────────────┘
          │ SSE                                          │ HTTP POST
          ▼                                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    INGRESS (uvicorn instance)                           │
│                                                                         │
│  ┌──────────┐    ┌─────────────────────┐    ┌────────────────────────┐  │
│  │ SSEHub   │◀───│ ChatRelayCommunicator│◀───│ ServiceCommunicator   │  │
│  │          │    │ (subscribe side)     │    │ (Redis pubsub listener)│  │
│  │ _on_relay│    │ acquire/release      │    │ subscribe_add()       │  │
│  │ fan-out  │    │ session channels     │    │ start_listener()      │  │
│  └──┬───────┘    └─────────────────────┘    └──────────┬─────────────┘  │
│     │                                                   │               │
│     │ enqueue SSE frames                   SUBSCRIBE to │               │
│     ▼                                                   ▼               │
│  ┌──────────┐                              ┌────────────────────────┐  │
│  │ Client   │                              │ Redis Pub/Sub          │  │
│  │ queue    │                              │ channel:               │  │
│  │ → SSE    │                              │ {identity}.{t}:{p}:    │  │
│  │   stream │                              │ chat.events.{session}  │  │
│  └──────────┘                              └──────────┬─────────────┘  │
│                                                       ▲               │
│  HTTP POST /sse/chat:                                  │               │
│    → enqueue task to Redis Queue ──────────────────────┼───────────┐   │
└─────────────────────────────────────────────────────────┼───────────┼───┘
                                                         │           │
                                                 PUBLISH │    BRPOP  │
                                                         │           ▼
┌─────────────────────────────────────────────────────────┼───────────────┐
│                   PROCESSOR (uvicorn instance)          │               │
│                                                         │               │
│  ┌──────────────┐    ┌─────────────────────┐    ┌──────┴────────────┐  │
│  │ Chat Handler │───▶│ ChatCommunicator    │───▶│ ChatRelayCommunic.│  │
│  │ (agentic     │    │ emit_delta()        │    │ (publish side)    │  │
│  │  workflow)   │    │ emit_complete()     │    │ _pub_async()      │  │
│  └──────────────┘    │ target_sid=stream_id│    └───────────────────┘  │
│                      └─────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key invariant:** Both instances must use the same `orchestrator_identity`
(env `CB_RELAY_IDENTITY`, default `kdcube.relay.chatbot`) so the Redis channel
names match between PUBLISH and SUBSCRIBE.

### Sequence diagram for one event

```mermaid
sequenceDiagram
    participant Browser
    participant Ingress as Ingress (SSE + HTTP)
    participant Redis as Redis
    participant Proc as Processor

    Note over Browser,Ingress: SSE connect: register(client)<br>→ subscribe_add("chat.events.S")<br>→ start_listener()

    Browser->>Ingress: POST /sse/chat (stream_id=X, message)
    Ingress->>Redis: LPUSH task queue
    Proc->>Redis: BRPOPLPUSH (claim task)
    Proc->>Proc: execute agentic workflow

    Proc->>Redis: PUBLISH chat.events.S {event, target_sid=X}
    Redis-->>Ingress: pubsub message on chat.events.S
    Ingress->>Ingress: _on_relay → fan-out to client with stream_id=X
    Ingress->>Browser: SSE frame via EventSource
```

### Reconnection sequence (stream_id reuse)

The client reuses its `stream_id` across reconnects. This keeps the processor's
`target_sid` valid:

```mermaid
sequenceDiagram
    participant Browser
    participant Ingress as Ingress
    participant Redis as Redis
    participant Proc as Processor

    Note over Browser: Connected with stream_id=AAA

    Browser->>Ingress: POST /sse/chat (stream_id=AAA)
    Ingress->>Redis: LPUSH task (socket_id=AAA)
    Proc->>Redis: BRPOPLPUSH (claim)

    Note over Browser: Network glitch → SSE drops
    Note over Browser: Reconnect — reuses stream_id=AAA

    Browser->>Ingress: SSE reconnect (stream_id=AAA)
    Ingress->>Ingress: register(AAA), subscribe

    Proc->>Redis: PUBLISH {target_sid=AAA}
    Redis-->>Ingress: message arrives
    Ingress->>Ingress: target_sid=AAA matches client AAA ✓
    Ingress->>Browser: SSE frame delivered
```

---

## What Did Not Work & Why

### 1. Starting the listener before subscribing

**Broken pattern:**

```python
# Old SSEHub.start()
await self.chat_comm._comm.subscribe_add([])   # "ensure _pubsub exists"
await self.chat_comm._comm.start_listener(self._on_relay)
```

* `subscribe_add([])` does **not** call `SUBSCRIBE` / `PSUBSCRIBE`.
* `self._pubsub` existed but had **no underlying connection** bound.
* When `listen()` was called, `redis.asyncio` raised:

  > `pubsub connection not set: did you forget to call subscribe() or psubscribe()?`

So the listener never received messages, not even `subscribe` control frames.

**Fix:**

* Remove the `subscribe_add([])` warm-up call.
* Call `subscribe_add(session_ch)` in `SSEHub.register()` when the first client for a session arrives.
* Only then call `start_listener()` (idempotent; safe if already running).

### 2. Single shared channel for all events

Earlier design relied on a small number of shared channels, something like:

```text
kdcube.relay.chatbot.chat.events
```

And every event for every session went there.

Problems:

* Every chat process receives all events.
* SSEHub has to inspect `session_id` for every message and drop most of them.
* This is inefficient at large scale.

**Improved design:**

* Shard by session:

  ```text
  chat.events.<session_id>
  → kdcube.relay.chatbot.chat.events.<session_id>
  ```

* Each process only subscribes to the channels for sessions it actually serves.

* When no client is listening for a session, we unsubscribe and stop receiving events.

---

## Why Dynamic Subscriptions Make Sense Here

* **High number of sessions:** we can’t afford a static subscription to thousands of per-session channels on every process.
* **SSE is already session-scoped:** one SSE connection is tightly bound to a `session_id`. It’s natural to tie Redis subscriptions to that.
* **Dynamic lifecycle:** sessions come and go; we subscribe on demand and clean up automatically when the last tab closes.

We still keep a **single listener task per process**:

* Simplifies error handling.
* Avoids spawning one listener task per channel.
* Pub/Sub is designed to handle multiple channels on a single connection.

Dynamic subscription + single listener + in-process fan-out gives:

* Low Redis chatter.
* Good isolation per session.
* Simple delivery to many browser tabs in the same session.

---

## Summary

* We use **Redis Pub/Sub** as a relay between orchestrator/workers and the web app.
* `ServiceCommunicator` provides async publish + dynamic subscribe APIs and a single background listener task.
* `SSEHub` maintains per-session client lists, performs **dynamic per-session subscriptions**, and fans out messages to SSE clients.
* The main bug we hit was starting the listener before any real `SUBSCRIBE`, which led to `pubsub connection not set` and no messages delivered.
* The working pattern is:

    * Subscribe to at least one channel.
    * Then start the listener.
    * Dynamically add/remove channels as sessions appear/disappear.

---

## Diagnosing Connection Problems

When a client connects but does not receive backward traffic (processor results), use these log entries to trace the chain. Each step must succeed for messages to flow.

### Step 1: Verify SSE client is registered

**Log to find:** `[SSEHub] register session=... stream_id=...`

```
[SSEHub] register session=abc123 stream_id=xyz789 tenant=tenant-a project=project-a total_now=1
```

If this log is missing, the client did not connect or the SSE endpoint rejected it (check auth, capacity).

### Step 2: Verify Redis channel subscription

**Log to find:** `[ChatRelayCommunicator] acquire session=...`

```
[ChatRelayCommunicator] acquire session=abc123 count_before=0 channel=tenant-a:project-a:chat.events.abc123
```

`count_before=0` means this is the first client for this session → Redis SUBSCRIBE will be issued.
If `count_before > 0`, the channel was already subscribed (existing tab).

### Step 3: Verify pubsub subscription succeeded

**Log to find:** `[ServiceCommunicator] subscribe_add`

```
[ServiceCommunicator] subscribe_add self_id=... pubsub_id=... new=[kdcube.relay.chatbot.tenant-a:project-a:chat.events.abc123]
```

If this shows `noop`, the channel was already in `_subscribed_channels` — check if a stale subscription exists without an active listener.

### Step 4: Verify listener task is alive

**Log to find:** `[sse_stream] relay diagnostic`

```
[sse_stream] relay diagnostic session=abc123 stream_id=xyz789
  relay_id=... comm_id=... listener_started=True listener_alive=True
  subscribed_channels=[kdcube.relay.chatbot.tenant-a:project-a:chat.events.abc123]
  refcounts={'abc123': 1}
```

Check:
| Field | Expected | Problem if wrong |
|-------|----------|-----------------|
| `listener_started` | `True` | Listener was never started → `_ensure_listener` not called |
| `listener_alive` | `True` | Listener task crashed → check `[ServiceCommunicator] listener error` logs |
| `subscribed_channels` | Contains session channel | Channel not subscribed → `subscribe_add` failed or was unsubscribed |
| `refcounts` | `{session_id: 1+}` | Refcount is 0 or missing → `acquire_session_channel` not called |

### Step 5: Verify processor publishes to correct channel

**On the processor instance**, look for:

```
Publishing event 'chat_delta' to 'kdcube.relay.chatbot.tenant-a:project-a:chat.events.abc123'
  (sid=xyz789, session=abc123)
```

Compare the full channel name with the ingress's `subscribed_channels` from Step 4.
If they differ, the `orchestrator_identity` (prefix) does not match between ingress and processor.

**Common mismatch:** processor uses default `kdcube.relay.chatbot`, ingress SSE router passes
`os.getenv("CB_RELAY_IDENTITY")` explicitly. If `CB_RELAY_IDENTITY` is not set in the environment,
both default to `kdcube.relay.chatbot`. If set differently per instance, channels won't match.

### Step 6: Verify ingress receives the message

**Log to find:** `[SSEHub._on_relay] RECEIVED`

```
[SSEHub._on_relay] RECEIVED event=chat_delta session=abc123 target_sid=xyz789
  known_sessions=[abc123]
```

If this log **never appears**, the Redis pubsub message is not reaching the ingress:
- Check Redis connectivity between ingress and processor instances
- Check `orchestrator_identity` matches (Step 5)
- Check `listener_alive` (Step 4) — if `False`, the listener task crashed

If this log appears but the client still doesn't receive:
- Check `target_sid` vs connected client's `stream_id`
- If mismatched, the fallback should deliver anyway (look for "falling back to session broadcast")

### Step 7: Verify SSE frame delivery

If `_on_relay` fired but the browser doesn't show the event:
- Check the SSE connection is still open in browser DevTools (Network → EventStream)
- Check for `[sse_stream] Client disconnected` — the SSE generator may have exited
- Check the client's queue is not full: `[SSEHub] queue overflow` indicates dropped frames

### Quick diagnostic checklist

```
Is client registered?           → [SSEHub] register
Is channel subscribed?          → [ServiceCommunicator] subscribe_add
Is listener alive?              → relay diagnostic: listener_alive=True
Does processor publish?         → Publishing event ... to channel
Do channels match?              → Compare publish channel vs subscribed_channels
Does ingress receive?           → [SSEHub._on_relay] RECEIVED
Does fan-out match stream_id?   → target_sid vs connected clients
```

If the chain breaks at "Does ingress receive?", the problem is Redis pubsub wiring
(identity mismatch, dead listener, network). If it breaks at fan-out, check stream_id
lifecycle and the fallback logic.
