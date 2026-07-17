---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-state-machine-README.md
title: "React State Machine"
summary: "React v2 state machine and decision loop control gates."
tags: ["sdk", "agents", "react", "state-machine"]
keywords: ["runtime loop", "decision gate", "state machine", "mermaid"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-budget-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
---
# ReAct v2 State Machine

This describes the current React runtime loop and its control gates.
The state-machine shape is shared by `v2` and `v3`. `v3` additionally brings:
one decision generation may emit multiple action-channel instances governed
ONLINE (the action overseer accepts the first valid action and drops later
candidates incompatible with already-accepted moves), early tool execution
(accepted tool actions may start while the generation still streams, drained
before the round closes), and steer checkpoints (a live steer cancels the
active decision and routes the turn into a finalize phase).

---

## High‑Level Loop

The ReAct loop is a state machine with these nodes:
- `decision`
- `tool_execution`
- `exit`

Each iteration starts at `decision` and typically follows:

```
decision -> tool_execution -> decision
```

The loop exits when `exit_reason` is set or the iteration/budget is exhausted.

---

## Mermaid Diagram

```mermaid
stateDiagram-v2
    [*] --> decision
    decision --> tool_execution: action=call_tool & validated
    decision --> decision: action=call_tool & infra-only (react.read/memsearch/hide)
    decision --> exit: action=complete/exit OR exit_reason
    tool_execution --> decision: done
    exit --> [*]
```

Notes:
- Infra tools (`react.read`, `react.memsearch`, `react.hide`) are handled in‑loop and return to `decision` without a tool_execution pass (react.hide uses logical paths, not queries).
- Tool call protocol validation happens inside the decision node (no separate protocol state).

---

## Decision Node (Core Rules)

The action set is `call_tool | complete | exit`. Decision output is validated
and may be rejected when:
- the response carries text before the first channel
  (`decision_preamble_before_first_channel`) or does not open with
  `<channel:thinking>` / misses required channels
- the action JSON does not parse or validate (`action_schema_error`)
- `tool_call.tool_id` is missing for `call_tool`
- the action is incompatible with the round's strategy/trait gates

If invalid, a **protocol violation block** is contributed and the loop returns
to `decision` (bounded by the retry/iteration gates).

### Streaming and failed rounds (critical)

Decision channels stream to the USER while the generation is still running:
`thinking`, root `notes`, and the action's `final_answer` string are decoded
char-level and delivered live. Validation is TWO-TIERED:

- **Online (action overseer):** each streamed action candidate is judged
  IMMEDIATELY for strategy/trait compatibility against already-accepted
  moves. Only an accepted candidate's gated output (e.g. its `final_answer`
  answer lane) streams to the user; a dropped candidate never reaches them.
- **Post hoc (schema/shape):** parsing and validating the action JSON and
  the channel shape (preamble, first-channel, required channels) run after
  generation. A round that PASSED the online gate but fails post hoc may
  therefore already have shown its content; streamed text stays visible.

Two invariants follow for the post-hoc tier:

- **Post-hoc parsing must accept whatever the streaming layer accepted.**
  A fence-dialect mismatch between the two layers produced a duplicated
  final answer (parsers fixed + regression-locked in `e9f05cec4`).
- **The retry must know what the user saw.** The `action_schema_error`
  violation notice states when the failed action's `final_answer` already
  streamed, and instructs the model to re-emit the SAME text in the
  corrected action rather than composing a new answer.

### Completion rounds
A `complete`/`exit` round carries the user-facing `final_answer` and exactly
one `<channel:summary>` for continuity; plan progress is acknowledged in
`notes` as steps become verifiable (inaccurate marks are protocol errors).

## Plan Acknowledgement Notes
The decision must acknowledge plan progress in `notes`:
- **DONE**: `✓ [n] <step>`
- **FAILED**: `✗ [n] <step> — <reason>`
- **IN PROGRESS**: `… [n] <step> — in progress` (does not change status)

Acknowledgements are appended to the turn progress log as `react.decision` blocks.
These blocks are visible to the decision on subsequent rounds so it can track prior notes.

---

## Budget + Iteration Gates

The loop uses `BudgetStateV2`:
- `exploration_budget` / `exploitation_budget`
- `explore_used` / `exploit_used`
- `max_iterations`
- `decision_rounds_used`

Hard stop:
- if `decision_rounds_used >= max_iterations` ⇒ exit with `max_iterations`

Important nuance:
- `max_iterations` is not always static for the whole turn.
- the runtime starts from `base_max_iterations`
- when the active turn consumes a live reactive external event such as `followup`, it may mint extra iteration credit
- that raises the effective `max_iterations` before the next decision gate
- the extra credit is capped by `reactive_iteration_credit_cap`

The budget snapshot is exposed to the decision agent in the timeline’s active state block.

---

## Compaction in the Loop

Compaction can happen **inside the loop** when `timeline(...)` is requested:
- if context size exceeds limits, the browser compacts and inserts a `conv.range.summary`
- the loop retries with `force_sanitize=True` on context‑limit errors

Compaction emits hooks (optional):
- `on_before_compaction` (start status)
- `on_after_compaction` (finish status + stats)

---

## Tool Execution Path

For `action=call_tool`:
1) `decision` validates tool call structure.
2) `tool_execution` executes the tool.
3) Results are converted into contribution blocks (`react.tool.call` / `react.tool.result`).

Artifacts are registered as files (kind=file or kind=display) and stored in the outdir.

---

## Exit Reasons

Common exit reasons:
- `complete`
- `max_iterations`
- `protocol_violation`
- `tool_error`

---

## Where Context Lives

All context is provided by `ContextBrowser.timeline(...)`:
- history blocks
- current user blocks
- in‑turn progress blocks
- optional sources pool / announce (tail, uncached)

See:
- `context-layout.md`
- `context-progression.md`
- `react-context-README.md`
