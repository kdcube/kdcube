---
id: ks:docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
title: "Bundle Event Recording And Sinks"
summary: "Bundle-facing guide for recording selected comm events, configuring event sinks, and sending recorded batches from workflows, APIs, MCP endpoints, jobs, and tools across host and isolated runtimes."
tags: ["sdk", "bundle", "comm", "recording", "event-sinks", "tools", "runtime", "mcp", "api", "jobs"]
keywords: ["bundle event recording", "comm record", "send recorded events", "set event sink", "bundle telemetry sink", "tool comm recording", "isolated runtime recorded events"]
see_also:
  - ks:docs/service/comm/comm-recording-event-sinks-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-chat-stream-events-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md
  - ks:docs/exec/README-iso-runtime.md
---
# Bundle Event Recording And Sinks

Bundles can record selected events that already pass through
`ChatCommunicator` and send those records as bounded batches to a sink.

This is the bundle-facing usage guide. The lower-level communicator API
reference is
[Comm Recording And Event Sinks](../../service/comm/comm-recording-event-sinks-README.md).

## Mental Model

Recording is not a second event bus. A bundle emits normal comm events once.
The communicator can then keep a compact, privacy-filtered copy of selected
post-firewall envelopes in memory:

```text
bundle / tool emits through comm
        |
        v
ChatCommunicator.emit(...)
  -> outbound firewall
  -> client stream / relay
  -> record selected item into bounded buffer
        |
        v
bundle sends recorded batch to configured sink
```

The event sink is a batch handoff point. It can forward to REST, a stream, local
diagnostics, a bundle-owned store, or another adapter.

Core calls:

```python
async with comm.recording(
    selector,
    scope={"owner": "workflow"},
    mode="replace",
    max_events=200,
    sink=sink,
    send_on_exit=True,
) as rec:
    ...

result = rec.result
```

## Ownership Rule

Configure recording at the outer execution boundary that owns the invocation:

- workflow `run(...)`, `pre_run_hook(...)`, or `post_run_hook(...)`
- decorated `@api(...)` handler
- decorated `@mcp(...)` handler
- decorated `@on_message` handler
- decorated `@on_job` handler when the job has a communicator context

Tools should normally just emit useful comm events. The outer workflow or
handler records and sends the batch.

## Scoped Additive Recording

`comm.record(...)` adds a scoped recording selector to the current
communicator. The scope is JSON-serializable metadata that identifies who owns
that selector.

If one caller does this:

```python
comm.record(FILTER_1, scope={"owner": "workflow"}, mode="replace")
```

and later a nested tool on the same communicator does this:

```python
comm.record(FILTER_2, scope={"owner": "tool", "name": "web_search"})
```

then both selectors are active. Future events are recorded when they match
either selector. Each recorded item includes the matching scopes:

```json
{
  "recording": {
    "scopes": [
      {
        "owner": "workflow"
      },
      {
        "owner": "tool",
        "name": "web_search"
      }
    ]
  }
}
```

`mode` controls scoped selector registration and the buffer:

| Call | Existing buffer | Active selectors |
| --- | --- | --- |
| `comm.record(FILTER_2, scope=S2)` | kept | existing selectors plus `S2` |
| `comm.record(FILTER_2, scope=S2, mode="append")` | kept | existing selectors plus `S2` |
| `comm.record(FILTER_2, scope=S2, mode="replace")` | cleared first | only `S2` |
| `comm.stop_recording()` | kept | recording disabled |

Practical rule:

- configure the outer workflow/handler scope with `mode="replace"`
- let nested code add a serializable scope only when it truly owns extra
  recording policy
- use `send_recorded_events(filter=...)` to send a narrower batch
- keep scopes stable and JSON-safe, for example
  `{"owner": "tool", "name": "web_search"}`

## Minimal Pattern

```python
EVENT_SELECTOR = {
    "include": {
        "types": [
            "accounting.usage",
            "chat.conversation.turn.completed",
            "react.tool.call",
            "react.skill.read",
        ],
    },
    "privacy": {
        "include_data": False,
    },
}


async def sink(batch: list[dict], *, comm, filter=None) -> dict:
    await send_to_collector(batch)
    return {"sent": len(batch)}


class MyBundle(BaseEntrypoint):
    async def run(self, **kwargs):
        async with self.comm.recording(
            EVENT_SELECTOR,
            scope={"owner": "workflow", "bundle": self.config.ai_bundle_spec.id},
            mode="replace",
            max_events=500,
            sink=sink,
            send_on_exit=True,
        ):
            return await self._run_impl(**kwargs)
```

For base workflows where the recording span crosses lifecycle hooks, configure
and send explicitly:

```python
self.comm.record(EVENT_SELECTOR, scope={"owner": "workflow"}, mode="replace")
self.comm.set_event_sink(sink)
...
await self.comm.send_recorded_events(EVENT_SELECTOR)
```

For base workflows that already expose lifecycle hooks, configure in the early
hook and send in the late hook.

## Runtime Case Matrix

Use this matrix to decide where to open recording scopes and where to send.

| Case | Where scope is opened | What is recorded | Scope handoff | Where send runs |
| --- | --- | --- | --- | --- |
| Chat turn / normal workflow | workflow boundary, usually `async with self.comm.recording(..., mode="replace", sink=..., send_on_exit=True)` or early/late lifecycle hooks | host comm events from workflow, React, and in-process tools; platform child tool events after side-file merge | host active scopes are exported to platform child runtimes launched inside the scope | host workflow boundary |
| `@api(...)` operation | inside the handler | events emitted by that operation and nested tools | same as workflow when the handler launches platform child tools | host API handler |
| `@mcp(...)` endpoint | inside the handler | events emitted by that MCP request and nested work | same as `@api(...)` | host MCP handler; batch once per request |
| `@on_message` | inside the message handler when `self.comm` is bound | events emitted by that message handler and nested tools | same as workflow when child tools are launched inside the scope | host message handler |
| `@on_job` | inside the job handler when `self.comm` exists | job-handler events and nested tools | same as workflow when child tools are launched inside the scope | host job handler |
| `@cron(...)` | normally nowhere; cron is headless | no request/session comm events by default | none | do not send through comm recording; enqueue `@on_job` or write durable operational facts |
| In-process tool | inside the tool with `get_comm()` / `_COMMUNICATOR` when the tool owns extra policy | events emitted by that tool into the same host buffer | no runtime boundary; scope is live in host process | outer host workflow/API/MCP/job sends |
| Platform local subprocess tool, `TOOL_RUNTIME[tool_id] = "local"` | host scope before launch; child tool may also open `async with comm.recording(...)` | events emitted by child comm into child buffer | host active scopes serialize via `COMM_SPEC`; child-added scopes stay child-local but are copied onto recorded items; child writes `comm_recorded_events.json`; host merges | host after merge |
| Platform Docker/Fargate isolated tool | same as local subprocess | same as local subprocess | same side-file handoff when the runtime returns the output directory | host after merge |
| `@venv(...)` helper | not automatic | no comm recording by default | no comm spec/side-file protocol by default | not available unless the parent implements an explicit protocol |
| arbitrary subprocess spawned by tool | not automatic | no comm recording by default | no comm inheritance | parent tool should emit through host/child `comm`, or child returns JSON for parent to emit |

## Multi-Scope Matching

Multiple active scopes are expected. An event is recorded once when it matches
one or more active selectors. The recorded item carries every matching scope:

```json
{
  "type": "my.bundle.web_search",
  "recording": {
    "scopes": [
      {
        "owner": "workflow"
      },
      {
        "owner": "tool",
        "name": "web_search"
      }
    ]
  }
}
```

Scope behavior by boundary:

| Boundary | Behavior |
| --- | --- |
| host workflow scope + in-process tool scope | both scopes are live on the same communicator; matching events carry one or both scopes |
| host workflow scope + platform local subprocess tool | host scopes serialize into the child before launch; child-added scopes apply only in the child; merged records carry the scopes that matched in the child |
| host sends after child merge | host sends the merged recorded items through the host sink |
| child calls `send_recorded_events(...)` | sends only if the child configured its own sink; host sink is not serialized |

## Entry Points

### Workflow / Chat Turn

Record at the workflow boundary:

```python
async def pre_run_hook(self, **kwargs):
    await super().pre_run_hook(**kwargs)
    self._configure_event_recording()


async def post_run_hook(self, **kwargs):
    try:
        await self._send_recorded_events()
    finally:
        return await super().post_run_hook(**kwargs)
```

The exact hook names depend on the base class. The important rule is that
recording is configured before nested agents/tools run, and sent after they
finish.

### `@api(...)`

```python
from kdcube_ai_app.infra.plugin.agentic_loader import api


@api(alias="run-report", route="operations", method="POST")
async def run_report(self, **kwargs):
    async with self.comm.recording(
        EVENT_SELECTOR,
        scope={"owner": "api", "alias": "run-report"},
        sink=sink,
        send_on_exit=True,
    ):
        result = await self.reporter.run(**kwargs)
        return {"ok": True, "ret": result}
```

If the API only reads state and emits no useful comm events, skip recording for
that operation.

### `@mcp(...)`

```python
from kdcube_ai_app.infra.plugin.agentic_loader import mcp


@mcp(alias="docs", route="operations", transport="streamable-http")
async def docs_mcp(self, **kwargs):
    async with self.comm.recording(
        EVENT_SELECTOR,
        scope={"owner": "mcp", "alias": "docs"},
        sink=sink,
        send_on_exit=True,
    ):
        return await self.mcp_server.handle(**kwargs)
```

For high-volume MCP endpoints, prefer batching one send at request completion
over sending one sink request per tool call.

### `@on_message`

```python
from kdcube_ai_app.infra.plugin.agentic_loader import on_message


@on_message
async def on_message(self, **kwargs):
    if getattr(self, "comm", None) is None:
        return await self.message_handler.run(**kwargs)

    async with self.comm.recording(
        EVENT_SELECTOR,
        scope={"owner": "on_message"},
        sink=sink,
        send_on_exit=True,
    ):
        return await self.message_handler.run(**kwargs)
```

Use the same scoped pattern as chat turns. If the message path does not bind a
communicator, the handler cannot use comm recording directly.

### `@on_job`

```python
from kdcube_ai_app.infra.plugin.agentic_loader import on_job


@on_job
async def on_job(self, job: dict, **kwargs) -> dict:
    if getattr(self, "comm", None) is not None:
        async with self.comm.recording(
            EVENT_SELECTOR,
            scope={"owner": "job", "work_kind": job.get("work_kind")},
            sink=sink,
            send_on_exit=True,
        ):
            return await self.job_runner.run(job)
    return await self.job_runner.run(job)
```

Job handlers may run without a meaningful browser peer. That does not prevent
event sink delivery, but it means client-stream delivery is not the reason to
record.

### `@cron(...)`

Cron scheduler ticks are headless. They do not have a request-bound
communicator:

```python
@cron(alias="scan", cron_expression="*/5 * * * *", span="system")
async def scan(self):
    # find due work and enqueue; do not rely on self.comm here
    await self.jobs.enqueue_due_work()
```

Record inside the `@on_job` handler that executes the due work, or write
cron-owned operational facts directly to the bundle's durable store.

## Tools

Tool modules running in the normal in-process runtime receive the current
communicator through bound globals or context helpers:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm


async def my_tool(...):
    comm = get_comm()
    if comm:
        await comm.emit_service_event(
            type="my.bundle.tool.used",
            step="tool",
            status="completed",
            data={"tool": "my_tool"},
        )
```

The outer workflow records this event if its selector includes it. A tool can
add a scoped selector when it owns additional recording policy:

```python
comm.record(
    TOOL_LOCAL_SELECTOR,
    scope={"owner": "tool", "name": "my_tool"},
)
```

Scopes must be JSON-serializable. Live objects, callbacks, and class instances
do not cross isolated runtime boundaries.

If the tool runs through the platform isolated execution path, including
`TOOL_RUNTIME[tool_id] = "local"`, the host exports active portable recording
scopes before launching the subprocess. A tool can also use `get_comm()` inside
that child runtime, add another serializable scope, and emit normal comm events.
Those events are recorded in the child buffer and returned through
`comm_recorded_events.json`.

Example inside a tool that may run as `TOOL_RUNTIME = "local"`:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm


async def web_search(...):
    comm = get_comm()
    if comm:
        async with comm.recording(
            {"include": {"types": ["my.bundle.web_search"]}},
            scope={"owner": "tool", "name": "web_search"},
        ):
            await comm.service_event(
                type="my.bundle.web_search",
                step="web_search",
                status="completed",
                data={"queries_count": len(queries)},
            )
    ...
```

The child-added scope does not update the host recording configuration. It only
affects events recorded inside the child run. The recorded items carry that
scope after the host merges the side file.

Normal platform-isolated tool pattern:

```text
child runtime records
  -> child writes comm_recorded_events.json
  -> host merges side file
  -> host sends through host sink
```

If the tool starts its own arbitrary subprocess, that subprocess does not
inherit the communicator, recording scopes, or sink. Keep event emission in the
parent process, or pass an explicit JSON protocol back to the parent and let the
parent emit through `comm`.

## Isolated Runtime Handoff

When the host communicator has recording enabled with a portable selector, or
an `async with comm.recording(...)` scope is active while launching isolated
execution, runtime export adds recording state to the comm spec:

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
    "max_events": 500
  }
}
```

The isolated runtime rebuilds the communicator, registers each scoped selector,
and records matching events there. It does not receive the live Python sink
callback. Instead, it writes:

```text
comm_recorded_events.json
```

next to `delta_aggregates.json`. The host merges that side file into the host
communicator after isolated execution. The host then calls
`send_recorded_events(...)` once from the outer boundary.

Nonportable recording filters, such as Python callables and live
`IEventFilter` instances, do not cross the runtime boundary.

The sink callback also does not cross the runtime boundary. Calling
`send_recorded_events(...)` inside the child only sends if the child runtime
configured its own sink. Without a child sink, it returns a disabled no-op and
leaves the child buffer for side-file handoff. The normal pattern is to record
inside the child and send from the host after merge.

## Sink Contract

Use a bounded batch callback:

```python
async def sink(batch: list[dict], *, comm, filter=None) -> dict:
    ...
    return {"sent": len(batch)}
```

Rules:

- keep the sink bounded and resilient
- return `{"sent": n}` or `{"accepted": n}`
- do not run expensive aggregation inside the sink callback on the user-facing
  path
- do not raise for collector outages unless the bundle explicitly wants the
  invocation to fail
- use durable queues, background jobs, or collector-side aggregation for heavier
  work

If the sink is absent, `send_recorded_events(...)` returns a disabled result and
leaves the buffer intact.

## Selector Practice

Prefer selectors that name semantic event types, not transport routes alone:

```python
{
    "include": {
        "types": [
            "accounting.usage",
            "chat.conversation.turn.completed",
            "react.tool.call",
            "react.skill.read",
        ]
    }
}
```

Use `socket_events` or routes when the bundle intentionally wants a transport
family:

```python
{
    "include": {
        "socket_events": ["chat_service", "chat_complete", "chat_error"]
    }
}
```

Use send-time filters for separate batches from the same recorded buffer:

```python
await comm.send_recorded_events({"include": {"types": ["accounting.usage"]}})
await comm.send_recorded_events({"include": {"types": ["react.tool.call"]}})
```

If the first call clears accepted items, the second call only sees the remaining
buffer. Use `clear_on_success=False` when multiple sink deliveries need to read
the same snapshot.

## Privacy

Recorded items are compact metadata, not raw prompt or answer copies. Default
recording redacts arbitrary `data` payloads and delta text.

Only include bounded data keys when the bundle owns the shape:

```python
{
    "include": {"types": ["my.bundle.operation"]},
    "privacy": {"data_keys": ["duration_ms", "result_code"]},
}
```

Do not record raw prompts, answer text, tool arguments, file names, stack
traces, or external provider payloads as metrics dimensions.
