---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/README.md
title: "Multi-Action Stream Governance"
summary: "How ReAct streamed action lanes are gated by an external round overseer before user-visible sinks emit chunks."
tags: ["sdk", "react", "streaming", "multi-action", "overseer", "gating"]
keywords: ["RoundActionOverseer", "ActionStreamGate", "TimelineStreamer", "ToolContentStreamerBase", "DecisionExecCodeStreamer", "StreamPolicyViolation"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/channeled-streamer-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/llm-streaming-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/streaming-widget-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
---
# Multi-Action Stream Governance

ReAct can emit more than one `<channel:action>` block in one decision round.
The runtime must not stream user-visible artifacts from a later action until it
knows that action is compatible with the actions already detected in the same
round.

The policy owner is an external per-round object: `RoundActionOverseer`. Stream
widgets remain lane-specific sniffers. They detect the JSON path they already
care about, report action identity to the overseer, and route outbound chunks
through gates.

## Actors and Owned Values

```
Model.stream
  owns: raw token chunks

Channel streamer
  owns: channel boundaries, channel_instance ids
  emits: action-channel JSON slices, code-channel slices

TimelineStreamer
  owns: action JSON path state for notes/final_answer/plan
  detects: action, tool_call.tool_id, final_answer text
  emits through: action gate or final-answer gate

ToolContentStreamerBase subclasses
  owns: JSON path state for react.write/react.patch/rendering content
  detects: tool_call.params.content and related params
  emits through: action gate

DecisionExecCodeStreamer
  owns: exec widget stream state for code channel
  emits through: action gate bound to the exec action

RoundActionOverseer
  owns: detected action table for the current ReAct round
  inputs: action index, action, tool id, resolved tool traits
  outputs: allow/deny decisions for gates

ActionStreamGate
  owns: buffered outbound deltas for one lane
  states: pending -> allowed | denied
```

## Flow

```
                 action JSON chunk
Model.stream  ----------------------->  Channel streamer
                                             |
                                             | action instance i
                                             v
              +------------------- TimelineStreamer -------------------+
              | path-sniffs action and tool_call.tool_id incrementally |
              | path-sniffs notes/final_answer/plan                    |
              +-----------------------+--------------------------------+
                                      |
                                      | report(action, tool_id, i)
                                      v
                              RoundActionOverseer
                                      |
                        +-------------+-------------+
                        |                           |
                 allow action gate          allow/deny answer gate
                        |                           |
                        v                           v
          notes/canvas/exec sinks          final-answer sink
```

The streamer does not wait for `</channel:action>`. It reports identity when
the relevant JSON path closes:

```
action                      -> enough for complete/exit
action + tool_call.tool_id  -> enough for call_tool
```

Until the overseer responds, gates buffer outbound deltas. When a gate is
allowed, it immediately flushes the buffered deltas and then passes future
deltas through. When a gate is denied, it drops the buffer and ignores later
deltas on that lane.

## Gate States

```
pending
  | emit_delta(...)
  |   buffer delta
  |
  | overseer allow
  v
allowed
  | flush buffered deltas
  | pass future deltas directly

pending
  | overseer deny
  v
denied
  | drop buffered deltas
  | suppress future deltas
  | raise StreamPolicyViolation
```

## Compatibility Policy

The first detected action in a round is allowed. Every later candidate action
is checked against the collection of actions already accepted in that same
round: for candidate `n`, compare against accepted actions `0..n-1`. A rejected
candidate is dropped while earlier accepted actions still run. The current
round-level cap is two actions, so action #3 and later are denied before matrix
checks and their buffered stream output is dropped.

Tool actions use the `strategy` trait:

```
exploration   reads/searches/inspects
exploitation  writes/mutates/produces durable output
neutral       runtime bookkeeping that does not change the answer premise
unknown       no policy signal; runs alone
```

Rules:

- Ordered judgment: the first action survives. Every later candidate is judged
  against all previously accepted actions. If any prior accepted action rejects
  the candidate by the matrix, the candidate is dropped; previous accepted
  actions still run.
- Current cap: a round may contain at most two actions. Action #3 and later are
  denied.
- The compatibility matrix is ordered: rows are previously accepted actions,
  columns are later candidate actions.

```
            later candidate
accepted    exploration  exploitation  neutral  unknown
exploration ok           no            ok       no
exploitation no          ok            ok       no
neutral     ok           ok            ok       no
unknown     no           no            no       no
```

- Unknown-strategy tools run alone. They cannot share a round with any other
  action.
- Neutral tools are compatible with final answers and with other strategy
  groups.
- Exploration tools are compatible with exploration tools.
- Exploitation tools are compatible with exploitation tools.
- Exploration and exploitation tools are not compatible in the same round
  unless a tool explicitly carries both strategy values.
- A final-answer action may appear only when all prior tool actions are neutral.
- A non-neutral tool action may not appear after a final-answer action.

## Streamers Covered

All user-visible decision streamers route through gates:

```
TimelineStreamer
  notes         -> action gate
  plan          -> action gate
  final_answer  -> final-answer gate

ReactWriteContentStreamer
  content       -> action gate

ReactPatchContentStreamer
  patch content -> action gate

RenderingWriteContentStreamer
  literal input -> action gate

DecisionExecCodeStreamer
  exec JSON/code widget -> action gate for the bound exec action
```

The final-answer lane is intentionally separate. A non-neutral tool action may
be allowed while the answer lane for that same action remains denied.

## Violation Handling

When the overseer denies an action:

1. The denied gate drops all buffered output for that lane.
2. `StreamPolicyViolation` is raised from the subscriber path.
3. The channeled streamer re-raises the policy exception.
4. ReAct stashes the interrupted generation snapshot.
5. ReAct writes a protocol-violation timeline block.
6. ReAct retries the decision if retry budget remains.

This prevents the UI from showing streamed output that the runtime later
invalidates.
