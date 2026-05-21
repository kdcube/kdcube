---
id: ks:docs/service/comm/comm-recording-event-sinks-README.md
title: "Comm Recording And Event Sinks"
summary: "Design and implementation notes for recording selected comm envelopes and forwarding bounded batches from ChatCommunicator to telemetry or other event sinks across host and isolated runtimes."
tags: ["service", "comm", "recording", "event-sinks", "telemetry", "sdk", "runtime"]
keywords: ["comm record", "send telemetry", "ChatCommunicator recording", "comm event sink", "comm event selector", "iso runtime recorded events merge", "kdcube telemetry"]
see_also:
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md
  - ks:docs/service/streams/telemetry-README.md
  - ks:docs/sdk/bundle/bundle-firewall-README.md
  - ks:docs/exec/README-iso-runtime.md
---
# Comm Recording And Event Sinks

Status: initial implementation.

This document defines the OSS platform feature for recording selected
`ChatCommunicator` envelopes and making those records available to bounded
event sinks. Telemetry is one important sink, but it is not the feature
boundary. The same recorded buffer can also feed local diagnostics, artifacts,
tests, operational summaries, or bundle-specific forwarding.

Applications and bundles decide which events are useful. Sinks decide how a
selected batch is delivered: local callback, REST endpoint, stream, artifact
file, or bundle-provided adapter.

## Problem

Bundles already emit most operational signals through `self.comm`:

- `start`
- `step`
- `delta`
- `event`
- `service_event`
- `complete`
- `error`

Today those events are primarily client-facing. Some are also useful as
telemetry or other operational records, but producers should not emit a second
parallel event for every signal and should not synchronously wait for an
external sink on every low-level event.

The platform needs a small SDK primitive:

```python
comm.record(filter=None)
await comm.send_telemetry(filter=None)
```

The primitive should:

- record selected already-enveloped comm activity in memory during a
  request/workflow/tool run
- keep the recording bounded
- allow a second filter at send time
- send or expose a batch to a configurable sink
- preserve and merge recordings from isolated runtimes back into the host
  communicator
- let bundles override or wrap the sink behavior

`send_telemetry(...)` is the telemetry-oriented convenience method over the
generic recorded-event buffer. It should not be described as the whole feature
in platform docs. A telemetry sink may choose to normalize recorded items to
`kdcube.telemetry.v1`, but recording itself is not telemetry-specific.

## Existing Concepts To Reuse

### Outbound Firewall: `IEventFilter`

The current bundle event filter is an outbound firewall. It runs inside
`ChatCommunicator.emit(...)` before the event is published to Redis/SSE/Socket.IO.

It receives:

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

This vocabulary is good and should be reused for recording filters. The
semantics are not the same:

| Filter | Boundary | Result |
| --- | --- | --- |
| `event_filter` / `IEventFilter` | bundle -> client relay | allow or suppress client-visible delivery |
| `record(filter=...)` | comm envelope -> in-memory recording buffer | keep or skip a recorded item |
| `send_telemetry(filter=...)` | recorded buffer -> telemetry-oriented sink batch | include or skip from a send batch |

The first implementation should record post-firewall events only. That keeps
recording aligned with current activity listener behavior: events blocked by
the outbound firewall do not become recorded telemetry by accident. If a later
use case needs pre-firewall observability, it must be an explicit policy mode.

### Activity Listeners

`ChatCommunicator` already supports in-process activity listeners:

```python
comm.add_activity_listener(cb)
comm.remove_activity_listener(cb)
```

Listeners receive already-enveloped activity after `emit(...)` passes the
outbound firewall. Tests verify that listeners do not see filtered events.

The recording feature should use the same envelope point in the pipeline, but
should be first-class state on `ChatCommunicator` rather than asking every
bundle to install a listener.

### Delta Cache Export And Merge

`ChatCommunicator` already records deltas in `_delta_cache` and exposes:

```python
get_delta_aggregates(...)
clear_delta_aggregates(...)
export_delta_cache(...)
dump_delta_cache(path)
merge_delta_cache(items)
merge_delta_cache_from_file(path)
```

Isolated runtimes write `delta_aggregates.json` into the runtime output
directory. The host merges it back:

```python
tool_manager.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
```

The recorded-event buffer should follow the same pattern:

```python
export_recorded_events(...)
dump_recorded_events(path)
merge_recorded_events(items)
merge_recorded_events_from_file(path)
```

Suggested file name:

```text
comm_recorded_events.json
```

## Implemented Baseline

The first implementation adds:

- `comm.record(filter=None, mode="append", max_events=None)`
- `comm.stop_recording()`
- `comm.export_recorded_events(filter=None)`
- `comm.clear_recorded_events(filter=None)`
- `comm.dump_recorded_events(path)`
- `comm.merge_recorded_events(items)`
- `comm.merge_recorded_events_from_file(path)`
- `comm.set_event_sink(sink)`
- `comm.set_telemetry_sink(sink)`
- `await comm.send_telemetry(filter=None, clear_on_success=True, sink=None)`

The recorded buffer is generic. `send_telemetry(...)` is the telemetry-named
convenience over that buffer and uses the configured event sink callback. With
no configured sink it returns a disabled/no-op result and leaves the buffer
intact.

Serializable recording selectors are propagated into portable/isolated
runtimes through `CommSpec.recording`. Isolated runtimes dump
`comm_recorded_events.json` next to `delta_aggregates.json`, and the host
merges it back into the host communicator.

## API

### Start Recording

```python
comm.record(filter=None, *, mode="append", max_events=None)
```

Meaning:

- enables recording on this `ChatCommunicator`
- optionally installs a recording selector
- records future comm envelopes that pass the selector
- keeps existing recorded items when `mode="append"`
- clears existing recorded items first when `mode="replace"`
- uses communicator/runtime default maximum when `max_events` is omitted

The method should be synchronous and cheap. Recording happens later in
`emit(...)` after the outbound firewall allows the event.

Examples:

```python
# Record all post-firewall comm envelopes with default redaction and bounds.
comm.record()

# Record only selected logical types.
comm.record({
    "include": {
        "types": ["accounting.usage", "chat.conversation.turn.completed"],
        "socket_events": ["chat_service", "chat_complete", "chat_error"],
    }
})
```

### Send Telemetry Convenience

```python
result = await comm.send_telemetry(filter=None, *, clear_on_success=True, sink=None)
```

Meaning:

- snapshots recorded items
- applies the optional send filter
- sends the resulting batch through a telemetry-oriented sink
- clears sent items on success when `clear_on_success=True`
- returns a structured result such as accepted/skipped/sent/error counts

The method should not recompute or block on rollups. It is a batch handoff.
The default sink may be disabled/no-op. Bundles can override behavior by:

- passing `sink=...` for a call
- setting a communicator event sink callback
- subclassing/wrapping `send_telemetry(...)`

Example:

```python
await comm.send_telemetry({
    "include": {
        "types": ["accounting.usage", "workflow.step", "tool.invoke"],
    }
})
```

Other sink helpers can be added later without changing `record(...)`, for
example a future generic `send_records(...)` if multiple first-class sink
families appear.

### Export And Merge

```python
items = comm.export_recorded_events(filter=None)
comm.clear_recorded_events(filter=None)
ok = comm.dump_recorded_events(outdir / "comm_recorded_events.json")
comm.merge_recorded_events(items)
comm.merge_recorded_events_from_file(outdir / "comm_recorded_events.json")
```

These are required for host/isolated runtime parity.

## Filter Shape

Recording and send filters should use a serializable selector first. A Python
callable may be accepted for in-process use, but it cannot be propagated across
isolated runtimes.

Proposed selector:

```yaml
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

Matching uses the existing `EventFilterInput` vocabulary:

- `type`
- `route`
- `socket_event`
- `route_key`
- `agent`
- `step`
- `status`
- `broadcast`

Default match behavior:

- if `include` is empty, include all post-firewall events
- apply `exclude` after `include`
- `broadcast: null` means either broadcast value
- list values are exact string matches in the first implementation

The implementation should keep the selector compact and deterministic. Regex or
callable policies can be added later if needed, but the cross-runtime path
should remain serializable.

## Recorded Item Shape

The recorded buffer should store compact, privacy-filtered items, not raw
client payload copies.

Recommended shape:

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
  "data": {},
  "metrics": {},
  "privacy": {
    "contains_content": false,
    "data_redacted": true
  }
}
```

Rules:

- `record_id` should be stable enough for merge dedupe inside one run. A hash
  over timestamp/type/route/context/event metadata is acceptable for MVP.
- raw delta text is excluded by default; keep marker/index/completed/text length
  if useful.
- `accounting.usage` may preserve bounded `data.breakdown` because that is
  accounting telemetry, not prompt/answer content.
- error events must avoid stack traces and raw prompt/answer content.
- high-cardinality values such as user prompt text, file names, tool arguments,
  or exception bodies must not become dimensions.

## Sink Contract

The comm layer should not know every collector or sink API. It should call a
small sink interface.

Suggested callable:

```python
async def sink(batch: list[dict], *, comm: ChatCommunicator, filter=None) -> dict:
    ...
```

Possible sinks:

- no-op disabled sink
- HTTP REST batch sink
- Redis/Kafka stream sink
- local artifact/debug sink
- bundle-provided wrapper that enriches and forwards to another sink

`send_telemetry(...)` should suppress or bound sink failures by default. A
sink failure must not crash or hang a user-facing chat/tool path.

## Runtime Propagation

### Host Runtime

The host `ChatCommunicator` owns the recording buffer. Bundles call:

```python
comm.record(...)
...
await comm.send_telemetry(...)
```

### Isolated Python Runtime

The iso runtime rebuilds a communicator from `COMM_SPEC`. Today this preserves
basic comm identity and relay delivery. The recording feature should add:

- serializable recording selector in `CommSpec`
- recording enabled/max-events state in `CommSpec`
- `dump_recorded_events(OUT_DIR / "comm_recorded_events.json")` at the same
  safe boundaries where `delta_aggregates.json` is dumped
- host-side `merge_recorded_events_from_file(...)` after isolated execution

The existing delta path is the model:

```text
host comm
  -> export COMM_SPEC
  -> iso runtime rebuilds ChatCommunicator
  -> iso comm records deltas/events
  -> iso dumps delta_aggregates.json / comm_recorded_events.json
  -> host merges files into host comm
```

For the exact supervisor write, cancellation, and host-merge semantics, see
[ISO Runtime: Comm State Side-File Handoff](../../exec/README-iso-runtime.md#comm-state-side-file-handoff).

### External/Docker/Fargate Runtimes

Any runtime that copies the output directory back to the host can use the same
file handoff. This avoids requiring a direct telemetry connection from sandboxed
or remote runtimes.

## Pipeline Placement

First implementation placement:

```text
ChatCommunicator.emit(...)
  -> build EventFilterInput
  -> outbound firewall allow_event(...)
  -> publish to relay
  -> touch task activity
  -> record selected privacy-filtered item
  -> notify activity listeners
```

Recording after relay publish means telemetry does not change client delivery.
Recording before activity listeners keeps listeners and recorders observing the
same post-firewall envelope stream.

If implementation needs stricter "record even if relay publish failed" behavior,
that should be a separate policy flag.

## Implementation Checklist

1. Done: add serializable selector helpers in the comm SDK package.
2. Done: add recording state and bounded buffer counters to
   `ChatCommunicator`.
3. Done: implement record/export/clear/dump/merge APIs.
4. Done: add `_record_activity(...)` inside `emit(...)` after successful
   outbound filter/publish.
5. Done: add `send_telemetry(...)` with configurable event sink callback and
   no-op default.
6. Done: extend `CommSpec` and `_export_comm_spec_for_runtime()` with
   serializable recording state.
7. Done: rebuild recording state in runtime bootstrap/iso runtime.
8. Done: dump `comm_recorded_events.json` wherever `delta_aggregates.json` is
   dumped.
9. Done: merge `comm_recorded_events.json` in host execution code after
   isolated runtime completion.
10. Done: add focused tests for filters, bounded recording, send filtering,
    sink override, outbound-firewall interaction, and export/merge.

## Test Cases

Required unit tests:

- `comm.record()` records post-firewall `start/step/delta/event/service_event`
  metadata.
- denied outbound events are not recorded by default.
- selector include/exclude filters by `type`, `route`, `socket_event`, `agent`,
  `step`, `status`, and `broadcast`.
- default privacy redacts delta text and arbitrary data.
- `accounting.usage` can preserve bounded breakdown data.
- recording buffer respects `max_events` and increments dropped counters.
- `send_telemetry(filter=...)` sends only selected recorded items.
- a provided sink callback is used and can be wrapped by bundles.
- no-op sink returns disabled/skipped without raising.
- export/merge dedupes by `record_id`.
- `comm_recorded_events.json` is dumped in iso runtime and merged by host.

## Open Decisions

- Whether the default recording selector should remain "all post-firewall
  metadata" or narrow to `event`, `service_event`, `complete`, and `error`.
- Whether to add a pre-firewall recording mode later.
- Whether the first telemetry sink should normalize to
  `kdcube.telemetry.v1` inside comm or leave normalization to the sink.
