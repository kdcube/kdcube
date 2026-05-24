---
id: ks:docs/service/comm/comm-recording-event-sinks-README.md
title: "Comm Recording And Event Sinks"
summary: "Reference for recording selected ChatCommunicator envelopes and handing bounded batches to event sinks across host and isolated runtimes."
tags: ["service", "comm", "recording", "event-sinks", "sdk", "runtime"]
keywords: ["comm record", "send recorded events", "ChatCommunicator recording", "comm event sink", "comm event selector", "iso runtime recorded events merge"]
see_also:
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md
  - ks:docs/service/streams/telemetry-README.md
  - ks:docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - ks:docs/sdk/bundle/bundle-firewall-README.md
  - ks:docs/exec/README-iso-runtime.md
---
# Comm Recording And Event Sinks

`ChatCommunicator` can record selected post-firewall comm envelopes into a
bounded in-memory buffer and hand selected batches to an event sink. Telemetry
is one possible sink. The comm feature itself is generic and can also feed
diagnostics, artifacts, tests, operational summaries, or bundle-specific
forwarding.

The core API is:

```python
comm.record(filter=None, scope=None)
with comm.recording(filter=None, scope=None):
    ...
comm.set_event_sink(sink)
result = await comm.send_recorded_events(filter=None)
```

Applications and bundles choose which envelopes to record. The sink chooses how
to deliver a batch: REST endpoint, stream, local artifact, debug collector, or a
bundle-provided adapter.

## Quick Start

```python
EVENT_SELECTOR = {
    "include": {
        "types": ["accounting.usage", "chat.conversation.turn.completed"],
        "socket_events": ["chat_service", "chat_complete", "chat_error"],
    },
    "privacy": {
        "include_data": False,
    },
}


async def event_sink(batch: list[dict], *, comm, filter=None) -> dict:
    # Forward the batch to the bundle or platform collector.
    return {"sent": len(batch)}


comm.record(EVENT_SELECTOR, scope={"owner": "workflow"}, mode="replace", max_events=200)
comm.set_event_sink(event_sink)

...

result = await comm.send_recorded_events(EVENT_SELECTOR)
```

With no configured sink, `send_recorded_events(...)` returns a disabled no-op
result and leaves the buffer intact.

## Filter Boundaries

Recording and send selectors reuse the same `EventFilterInput` vocabulary that
the outbound comm firewall already uses:

```text
user_type
user_id
EventFilterInput:
  type
  route
  socket_event
  agent
  step
  status
  broadcast
  route_key = route or socket_event
data
```

The vocabulary is shared. The boundaries are different:

| Filter | Boundary | Result |
| --- | --- | --- |
| `event_filter` / `IEventFilter` | bundle -> client relay | allows or suppresses client-visible delivery |
| `record(filter=...)` | post-firewall comm envelope -> in-memory buffer | keeps or skips a recorded item |
| `send_recorded_events(filter=...)` | recorded buffer -> configured event sink batch | includes or skips an item from a send batch |

Recording is post-firewall. Events blocked by the outbound firewall are not
recorded.

## API Reference

### `record`

```python
comm.record(filter=None, *, scope=None, mode="append", max_events=None)
```

Enables bounded recording of future post-firewall comm envelopes on this
communicator.

Parameters:

| Parameter | Meaning |
| --- | --- |
| `filter` | Selector, callable, `IEventFilter`, string shorthand, or list shorthand. `None` records all post-firewall envelopes. |
| `scope` | JSON-serializable owner tag for this recording selector. It is copied to matching recorded items and propagated to portable runtimes. Nonserializable scopes raise `ValueError`. |
| `mode` | `"append"` adds another scoped selector and keeps existing recorded items. `"replace"` clears existing scoped selectors and clears the buffer before recording continues. |
| `max_events` | Maximum retained events. When omitted, the communicator uses the runtime default. When the buffer exceeds the limit, oldest items are dropped and the dropped counter is incremented. |

Serializable selector values propagate into portable and isolated runtimes.
Python callables and `IEventFilter` instances are process-local.

Multiple `record(...)` calls are additive by default. An event is recorded when
it matches at least one active selector. The recorded item lists every matching
scope.

### `stop_recording`

```python
comm.stop_recording()
```

Disables future recording on this communicator. Existing recorded items remain
available until cleared or sent successfully.

### `recording`

```python
with comm.recording(filter=None, scope=None, mode="append", max_events=None):
    ...
```

Temporarily adds a scoped recording selector and restores the previous
recording configuration on exit. Events recorded inside the block remain in the
buffer.

The context manager also supports `async with`:

```python
async with comm.recording(
    selector,
    scope={"owner": "workflow"},
    sink=sink,
    send_on_exit=True,
) as rec:
    ...

result = rec.result
```

`send_on_exit=True` is only useful with `async with`; it calls
`send_recorded_events(...)` before restoring the previous sink and recording
configuration.

Context parameters:

| Parameter | Meaning |
| --- | --- |
| `filter`, `scope`, `mode`, `max_events` | Same meaning as `record(...)`. |
| `sink` | Optional temporary sink installed for the scope and restored on exit. |
| `send_on_exit` | When used with `async with`, sends the selected recorded batch on exit. |
| `send_filter` | Optional send-time filter. Defaults to the scope's `filter`. |
| `clear_on_success` | Passed to `send_recorded_events(...)` when `send_on_exit=True`. |

### `recording_config`

```python
config = comm.recording_config()
```

Returns:

```json
{
  "enabled": true,
  "filter": {
    "include": {
      "types": ["accounting.usage"]
    }
  },
  "scopes": [
    {
      "scope": {
        "owner": "workflow"
      },
      "filter": {
        "include": {
          "types": ["accounting.usage"]
        }
      }
    }
  ],
  "max_events": 1000,
  "recorded": 12,
  "dropped": 0
}
```

`filter` contains the combined portable selector form. `scopes` contains the
portable scoped selector list. Nonportable process-local selectors are omitted
from runtime propagation.

### `export_recorded_events`

```python
items = comm.export_recorded_events(filter=None)
```

Returns a filtered snapshot of recorded items without clearing the buffer.

### `clear_recorded_events`

```python
removed = comm.clear_recorded_events(filter=None)
```

Clears all recorded items when `filter` is `None`. With a filter, only matching
items are removed. The return value is the number of removed items.

### `dump_recorded_events`

```python
ok = comm.dump_recorded_events(path)
```

Writes a JSON side file:

```json
{
  "items": [],
  "dropped": 0
}
```

The standard isolated-runtime file name is `comm_recorded_events.json`.

### `merge_recorded_events`

```python
comm.merge_recorded_events(items)
```

Merges recorded items into the communicator buffer and deduplicates by
`record_id`.

### `merge_recorded_events_from_file`

```python
comm.merge_recorded_events_from_file(path)
```

Reads a JSON side file with an `items` array and merges it into the communicator
buffer. Missing files and invalid files are ignored.

### `set_event_sink`

```python
comm.set_event_sink(sink)
```

Installs a batch sink used by `send_recorded_events(...)`.

Sink callable shape:

```python
async def sink(batch: list[dict], *, comm: ChatCommunicator, filter=None) -> dict:
    ...
```

The callable may also be synchronous. Return `{"sent": n}` or `{"accepted": n}`
to report the number of accepted items. When the sink returns no count, the
whole batch is treated as sent.

### `send_recorded_events`

```python
result = await comm.send_recorded_events(
    filter=None,
    *,
    clear_on_success=True,
    sink=None,
)
```

Snapshots the recorded buffer, applies the optional send filter, and sends the
resulting batch through the provided sink or the configured communicator sink.

Result shape:

```json
{
  "ok": true,
  "sent": 2,
  "skipped": 0,
  "disabled": false,
  "sink_result": {
    "sent": 2
  }
}
```

Behavior:

| Case | Result |
| --- | --- |
| Empty selected batch | `ok=true`, `sent=0`, `disabled=false` |
| No configured sink | `ok=true`, `sent=0`, `disabled=true`, buffer remains intact |
| Sink accepts full batch and `clear_on_success=True` | Sent items are cleared from the buffer |
| Sink accepts partial batch | Buffer remains intact |
| Sink raises | `ok=false`, error is returned, buffer remains intact |

Sink failures are logged and converted into a result. They do not raise through
the user-facing chat or tool path.

## Selector Shape

Serializable selectors use this shape:

```yaml
any:
  - include: {}
include:
  types: []
  routes: []
  socket_events: []
  agents: []
  steps: []
  statuses: []
  broadcast: null
exclude:
  types: []
  routes: []
  socket_events: []
  agents: []
  steps: []
  statuses: []
  broadcast: null
privacy:
  include_data: false
  data_keys: []
  include_delta_text: false
limits:
  max_events: null
```

Matching fields:

| Selector key | Event field |
| --- | --- |
| `types`, `type` | `EventFilterInput.type` |
| `routes`, `route` | `EventFilterInput.route` |
| `socket_events`, `socket_event` | `EventFilterInput.socket_event` |
| `route_keys`, `route_key` | `EventFilterInput.route_key` |
| `agents`, `agent` | `EventFilterInput.agent` |
| `steps`, `step` | `EventFilterInput.step` |
| `statuses`, `status` | `EventFilterInput.status` |
| `broadcast` | `EventFilterInput.broadcast` |

Matching rules:

| Rule | Behavior |
| --- | --- |
| Empty `include` | Includes all post-firewall events |
| Non-empty `include` | Includes only events matching every populated include criterion |
| Non-empty `exclude` | Removes events matching every populated exclude criterion |
| `broadcast: null` | Matches either broadcast value |
| List values | Exact string/value match |

String shorthand:

```python
comm.record("accounting.usage")
```

is equivalent to:

```python
comm.record({"include": {"types": ["accounting.usage"]}})
```

List shorthand:

```python
comm.record(["accounting.usage", "chat.conversation.turn.completed"])
```

is equivalent to:

```python
comm.record({
    "include": {
        "types": ["accounting.usage", "chat.conversation.turn.completed"]
    }
})
```

Combined selectors use `any`:

```python
comm.record({"any": [
    {"include": {"types": ["accounting.usage"]}},
    {"include": {"types": ["chat.conversation.turn.completed"]}},
]})
```

## Recorded Item Shape

Recorded items are compact, privacy-filtered copies of comm envelope metadata:

```json
{
  "record_id": "commrec_...",
  "recorded_at_ms": 1770000000000,
  "socket_event": "chat_service",
  "broadcast": false,
  "type": "accounting.usage",
  "route": "chat_service",
  "route_key": "chat_service",
  "service": {
    "request_id": "...",
    "tenant": "...",
    "project": "...",
    "user": "...",
    "bundle_id": "..."
  },
  "conversation": {
    "session_id": "...",
    "conversation_id": "...",
    "turn_id": "..."
  },
  "event": {
    "agent": "...",
    "step": "accounting",
    "status": "completed",
    "title": "..."
  },
  "recording": {
    "scopes": [
      {
        "owner": "workflow"
      }
    ]
  },
  "data": {},
  "metrics": {},
  "privacy": {
    "contains_content": false,
    "data_redacted": true
  }
}
```

Privacy behavior:

| Source | Recorded by default |
| --- | --- |
| Envelope service/conversation/event metadata | Yes |
| Arbitrary `data` payload | No |
| Delta text | No |
| Delta marker/index/completion metadata | Yes, when present |
| Numeric fields from `data` | Yes, in `metrics` |
| `accounting.usage` bounded accounting payload | Yes, for the accounting fields supported by the recorder |

Use selector privacy controls to include additional bounded data:

```python
comm.record({
    "include": {"types": ["workflow.step"]},
    "privacy": {"data_keys": ["duration_ms", "result_code"]},
})
```

Avoid high-cardinality or content-bearing dimensions such as raw prompts, file
names, tool arguments, stack traces, and answer text.

## Runtime Propagation

### Host Runtime

The host communicator owns the primary recording buffer:

```python
comm.record(...)
...
await comm.send_recorded_events(...)
```

### Portable And Isolated Runtimes

When recording is enabled with a portable selector, the runtime comm spec
contains:

```json
{
  "recording": {
    "enabled": true,
    "filter": {},
    "scopes": [
      {
        "scope": {
          "owner": "workflow"
        },
        "filter": {}
      }
    ],
    "max_events": 1000
  }
}
```

Runtime bootstrap reconstructs the communicator and calls `comm.record(...)`
for each scoped selector. Nonportable selectors, such as Python callables, do
not cross the runtime boundary.

The context manager object and event sink callback are live Python objects and
are not serialized. What serializes is the active recording state:

- portable selector
- JSON-serializable scope
- `max_events`

If that state is active when the host exports `COMM_SPEC`, the isolated runtime
rebuilds the same scoped recording policy. The child records into its own
buffer and writes `comm_recorded_events.json`; the host merges that file and
sends through the host sink.

A platform child runtime, such as a tool launched with
`TOOL_RUNTIME[tool_id] = "local"`, can also call `comm.record(...)` or
`async with comm.recording(...)` itself. Those child-added scopes are local to
the child communicator, but matching recorded items are written to
`comm_recorded_events.json` and merged by the host.

`send_recorded_events(...)` inside the child only sends when the child has a
sink configured there. Host sink callbacks are not serialized into the child.

Isolated runtimes write `comm_recorded_events.json` next to
`delta_aggregates.json`. The write is best-effort and occurs at the same safe
side-file boundaries used for delta aggregates. The host merges the side file
after isolated execution:

```text
host comm
  -> export COMM_SPEC with recording state
  -> runtime rebuilds ChatCommunicator
  -> runtime comm records selected envelopes
  -> runtime dumps comm_recorded_events.json
  -> host merges comm_recorded_events.json into host comm
```

For supervisor write, cancellation, and host-merge details, see
[ISO Runtime: Comm State Side-File Handoff](../../exec/README-iso-runtime.md#comm-state-side-file-handoff).

### External/Docker/Fargate Runtimes

Any runtime that copies the output directory back to the host can use the same
side-file handoff. The isolated runtime does not need a direct event-sink
connection.

## Pipeline Placement

Recording happens inside `ChatCommunicator.emit(...)` after outbound firewall
approval and relay publish, before activity listeners:

```text
ChatCommunicator.emit(...)
  -> build EventFilterInput
  -> event_filter.allow_event(...)
  -> publish to relay
  -> touch task activity
  -> record selected privacy-filtered item
  -> notify activity listeners
```

This keeps recording aligned with the client-visible comm stream and with
activity listeners. Events suppressed by the outbound firewall are absent from
the recording buffer.

## Examples

### Record all post-firewall metadata

```python
comm.record(scope={"owner": "workflow"}, mode="replace", max_events=500)
```

### Record selected accounting and turn completion events

```python
selector = {
    "include": {
        "types": ["accounting.usage", "chat.conversation.turn.completed"],
    }
}

comm.record(selector, scope={"owner": "workflow"}, mode="replace", max_events=200)
```

### Send only a subset of recorded events

```python
await comm.send_recorded_events({
    "include": {
        "types": ["accounting.usage"],
    }
})
```

### Override the sink for one send

```python
result = await comm.send_recorded_events(selector, sink=debug_sink)
```

### Use a bundle wrapper sink

```python
async def sink(batch: list[dict], *, comm, filter=None) -> dict:
    payload = {
        "source": "bundle",
        "events": batch,
    }
    await bundle_api.post("/events/batch", json=payload)
    return {"sent": len(batch)}


comm.set_event_sink(sink)
```

### Use the SDK stats telemetry sink

The SDK includes a reusable sink adapter for stats-style collectors:

```yaml
telemetry_sink:
  endpoint_url: "https://stats.example.internal/telemetry/events"
  auth:
    type: "bearer"
    token_ref: "secret:stats-telemetry-token"
```

```python
from kdcube_ai_app.apps.chat.sdk.comm.sink import (
    STATS_COMM_EVENT_SELECTOR,
    StatsTelemetrySink,
    StatsTelemetryTarget,
    configure_stats_event_recording,
)

sink = StatsTelemetrySink(
    StatsTelemetryTarget(
        endpoint_url=telemetry_sink_config["endpoint_url"],
        token=resolved_telemetry_token,
    ),
    source_bundle="my.bundle@1",
)

configure_stats_event_recording(
    comm,
    sink,
    selector=STATS_COMM_EVENT_SELECTOR,
    scope={"owner": "workflow", "bundle": "my.bundle@1"},
)

...

await comm.send_recorded_events(STATS_COMM_EVENT_SELECTOR)
```

The adapter maps known comm event types into `kdcube.telemetry.v1` names before
posting a bounded batch to the configured endpoint. The endpoint is a plain
POST URL; auth is supplied as a bearer token or explicit headers in
`StatsTelemetryTarget`. The target must include a token or an `Authorization`
header; unauthenticated sends fail before the HTTP request.

| Comm type | Telemetry name |
| --- | --- |
| `react.tool.call` | `tool.invoke` |
| `react.skill.read` | `skill.read` |
| `kdcube.copilot.mcp.call` | `mcp.call` |
| `accounting.usage` | `accounting.usage` |

Unknown selected comm records become `comm.event`.

For `mcp.call`, keep the exposed MCP server identity and the API called inside
that server separate:

- `mcp_address`: stable bundle/MCP route or server identity
- `mcp_endpoint`: API/tool name called inside that MCP server

If an MCP server intentionally wants to surface a bounded value, such as a
search query label, it may include `reported_values=[{"concept": "...",
"value": "..."}]`. The stats adapter truncates this list and forwards it as
explicit metadata. It must not be used to copy raw prompts, answers, or tool
arguments by default.

## Tests

Focused coverage lives in
`kdcube_ai_app/apps/chat/sdk/tests/bundle/test_event_streaming.py` and verifies:

- post-firewall recording
- selector include/exclude matching
- privacy defaults
- bounded buffers and dropped counters
- sink handoff and no-op sink behavior
- send-time filtering
- export, dump, merge, and dedupe
- runtime comm spec propagation for portable selectors

The stats adapter coverage lives in
`kdcube_ai_app/apps/chat/sdk/tests/comm/test_stats_telemetry_sink.py`.
