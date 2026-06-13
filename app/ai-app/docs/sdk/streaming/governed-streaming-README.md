---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/governed-streaming-README.md
title: "Governed Streaming"
summary: "How streaming sniffers, gates, overseers, and interruption paths keep user-visible streaming aligned with runtime policy."
tags: ["sdk", "streaming", "governance", "react", "steer", "multi-action"]
keywords:
  - "stream governance"
  - "stream sniffer"
  - "stream gate"
  - "overseer"
  - "StreamPolicyViolation"
  - "steer interrupt"
  - "gated transmitting"
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/channeled-streamer-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/llm-streaming-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/online-strategic-governance-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
---
# Governed Streaming

Governed streaming is the runtime pattern used when streamed model output must
be observed before it is allowed to become user-visible output or a live widget
update.

The core rule is:

```text
sniff first, transmit only after policy allows
```

This matters because several SDK streams are not plain text. A model stream can
contain channels, JSON decisions, widget payloads, code, final answers, and
tool actions. Some of those lanes can be useful as soon as they stream. Others
must be held until the runtime knows the move is legal.

## Actors

```text
Model provider
  owns: provider stream chunks
  emits: token deltas

ModelServiceBase.stream_model_text_tracked
  owns: accounting, provider normalization, final text assembly
  calls: on_delta(piece), on_complete(ret)

stream_with_channels
  owns: channel parser state
  sees: <channel:name>...</channel:name>
  routes: per-channel chunks to subscribers

Sniffer / subscriber
  owns: lane-specific parser state
  examples: TimelineStreamer, exec code streamer, widget streamers
  detects: action identity, tool id, final-answer lane, widget payload paths

Gate
  owns: buffered user-visible deltas for one action/lane
  states: pending -> allowed | denied

Overseer
  owns: policy state for the current streamed round
  receives: detected moves from sniffers
  decides: allow or deny

Sink
  owns: user-visible stream target
  examples: chat timeline, canvas, code widget, final-answer stream
```

Data ownership is explicit:

```text
Provider.chunk
  -> stream_with_channels.channel_body
  -> Sniffer.detected_move
  -> Overseer.policy_decision
  -> Gate.buffered_delta
  -> Sink.visible_delta
```

There is no magic path from model text to UI. Every visible delta crosses a
gate when a gate is installed.

## Gated Transmitting State Machine

```text
                          policy allows
                   +--------------------------+
                   |                          v
             +-----------+    allow     +-----------+
delta -----> |  pending  | -----------> |  allowed  | -----> sink
             |  buffer   |              | pass thru |
             +-----------+              +-----------+
                   |
                   | deny
                   v
             +-----------+
             |  denied   | -----> drop buffered and future deltas
             +-----------+
```

While a gate is `pending`, the sink sees nothing from that lane. When the
overseer allows the action, the gate flushes buffered deltas and then becomes a
pass-through. When the overseer denies the action, the gate clears the buffer
and suppresses future deltas for that lane.

This gives two useful properties:

1. The user does not see streamed output from a move that the harness will later
   reject.
2. The runtime can stop the model stream as soon as the bad move is identified,
   instead of waiting for the full malformed or incompatible payload to finish.

## Sniffers Report Moves Early

A sniffer should report a move as soon as it has enough identity to ask the
overseer for a decision. It should not wait for the whole channel to close if
the decision can be made earlier.

For a JSON action lane, the important identity can be:

```text
root.action
root.tool_call.tool_id
```

Example streamed shape:

```json
{
  "action": "call_tool",
  "tool_call": {
    "tool_id": "react.write",
    "params": {
      "path": "outputs/report.md",
      "content": "large content..."
    }
  }
}
```

The sniffer can identify the move when it has read `action=call_tool` and
`tool_call.tool_id=react.write`. It does not need to wait for
`params.content`.

Caveat: a streaming sniffer can only classify what it has already seen. If a
model emits huge fields before `tool_call.tool_id`, the sniffer cannot know the
tool identity until `tool_id` arrives. Prompting and schema examples should
keep action identity early.

## Interrupt Path 1: Steer

Steer is an external user/runtime event that interrupts an active phase from
outside the model stream.

```text
User / external event source
  owns: steer event text + sequence
       |
       v
React external-event watcher
  sees: event type steer/followup
       |
       v
React active phase task
  owns: model-generation or tool-execution async task
       |
       | cancel task
       v
asyncio.CancelledError
       |
       v
React runtime
  stashes interrupted generation snapshot
  enters steer-finalize mode
  persists steer interruption blocks
```

Implementation anchors:

- `ReactSolverV2._run_cancellable_phase(...)`
- `ReactSolverV2._watch_external_events_during_phase(...)`
- `ReactSolverV2._interrupt_active_phase_for_steer(...)`
- `ReactSolverV2._enter_steer_finalize_mode(...)`

Steer cancellation is task cancellation. The active phase is already running as
an `asyncio.Task`; the watcher asks that task to stop.

## Interrupt Path 2: Harness / Stream Policy

Harness policy interruption is different. It is not an external event and it is
not task cancellation. It is raised from inside the streaming callback path.

```text
Model stream chunk
  owns: partial channel text
       |
       v
stream_with_channels
  parses channel tags and calls channel subscribers
       |
       v
Sniffer / TimelineStreamer
  parses JSON action incrementally
  detects: action=call_tool, tool_id=react.write
       |
       v
Overseer.observe_action_signal
  checks current move against already accepted moves
       |
       +-- allow --> gate.allow() -> flush/pass through
       |
       +-- deny  --> gate.deny()
                    raise StreamPolicyViolation
                         |
                         v
stream_with_channels
  re-raises StreamPolicyViolation
                         |
                         v
React decision phase
  catches StreamPolicyViolation
  stashes interrupted generation snapshot
  records protocol violation block
  retries or exits according to round budget
```

Implementation anchors:

- `streaming/versatile_streamer_v3.py::stream_with_channels`
- `streaming/stream_policy.py::StreamPolicyViolation`
- `solutions/widgets/canvas.py::TimelineStreamer`
- `solutions/react/v3/action_overseer.py::RoundActionOverseer`
- `solutions/react/v3/runtime.py::_decision_node_impl`

`stream_with_channels` intentionally re-raises `StreamPolicyViolation`. Other
subscriber failures are ignored so one UI helper cannot break the stream, but a
policy violation is not a helper failure. It is a governance decision.

Some provider/client wrappers may log an `on_delta` callback failure and still
return the model's full raw text. The ReAct harness must still treat the
overseer as authoritative in that case. If the post-stream parser later sees a
denied `complete` or denied tool action in the full text, runtime routing is
rebuilt from the overseer's accepted action table, and denied actions are
recorded as protocol notices. The later parsed packet cannot overwrite an
already accepted earlier tool action.

## Example: Search Then Write

Policy example: ReAct tool strategy traits.

```text
Action #0:
  tool_id = web_tools.web_search
  strategy = exploration

Action #1:
  tool_id = react.write
  strategy = exploitation

Matrix:
  exploration -> exploitation = no
```

Timeline:

```text
t0  model starts <channel:action> for web_search
t1  sniffer sees action=call_tool + tool_id=web_tools.web_search
t2  overseer has no prior accepted action -> allow
t3  search action stream is visible; search will run

t4  model starts next <channel:action>
t5  sniffer sees action=call_tool + tool_id=react.write
t6  overseer checks accepted web_search against candidate react.write
t7  matrix rejects exploration -> exploitation
t8  react.write gate is denied; buffered write output is dropped
t9  StreamPolicyViolation interrupts the model stream
t10 runtime records protocol_violation.multi_action_bundle_strategy_incompatible
```

The user does not wait for a large `react.write` payload to finish. The model
stream is stopped as soon as the bad second move is identified.

If the model-service wrapper reports the policy exception as a callback failure
instead of stopping immediately, the fallback is still the same from the ReAct
harness perspective:

```text
overseer accepted:
  #0 web_tools.web_search

overseer denied:
  #1 complete / final_answer

post-stream parser may see:
  final_answer = "done"

runtime routes:
  #0 web_tools.web_search
  protocol_violation for #1
  no final-answer exit
```

## What The User Sees

For the search-then-write example:

```text
visible:
  web_search action / trace
  web_search result when executed
  protocol notice that react.write was incompatible and was not shown or run

not visible:
  partial react.write JSON
  partial react.write content
  final answer text that depends on unseen search results
```

The agent gets another round after the search result is visible. If it still
needs to write, it can emit `react.write` in that later round.

## Design Requirements For New Sniffers

When adding a new streaming sniffer:

1. Identify the earliest stable move identity.
2. Report that identity to the overseer immediately.
3. Send user-visible deltas only through a gate.
4. Treat `pending` as buffer-only.
5. Treat `allowed` as flush and pass-through.
6. Treat `denied` as drop and stop the governed stream.
7. Preserve enough raw/interrupted data for debugging and replay.

The policy should live outside the sniffer. The sniffer knows how to parse its
lane. The overseer knows whether the detected move is allowed.
