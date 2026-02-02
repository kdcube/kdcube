# ReAct Decision Context (ReactContext + Journal + Prompt Layout)

This document explains **what the ReAct decision agent sees** each round, how that context is constructed, and why it is arranged the way it is. It aligns with the actual runtime wiring in:

- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/react.py`](react.py)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/decision.py`](agents/decision.py)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/context.py`](context.py)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/context/journal.py`](../context/journal.py)
- [`kdcube_ai_app/apps/chat/sdk/runtime/scratchpad.py`](../scratchpad.py)
- [`kdcube_ai_app/apps/chat/sdk/solutions/chatbot/scratchpad.py`](../../solutions/chatbot/scratchpad.py)

Also see the existing companion docs:
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/react-operational-context-structure-README.md`](react-operational-context-structure-README.md)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/react-state-machine.md`](react-state-machine.md)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/react-budget.md`](react-budget.md)
- [with_context example bundle](../../../examples/bundles/with_context@2026-02-01-23-25/README.md)
- [`kdcube_ai_app/apps/chat/sdk/context/memory/memories-README.md`](../../../context/memory/memories-README.md)

---

## 1) Big Picture: How the Decision Agent Gets Context

The Decision agent does **not** read the whole system state directly. It sees a **structured prompt** that is assembled from:

1) **System instructions** (policies + tool catalogs + skills + protocols) from [`decision.py`](agents/decision.py).
2) **Human message** containing the **Operational Digest** (journal + session log + optional full artifacts).
3) **Multimodal attachments** (from `show_artifacts` or user attachments) as separate message blocks.

### High-level flow

```
Coordinator -> ReactSolver -> ReactContext
                     |            |
                     |            +-- build_turn_session_journal()
                     |            +-- build_operational_digest()
                     |
                     +-- decision module creates SYSTEM + HUMAN messages
```

Implementation: [`decision.py`](agents/decision.py)

Key components:

- **Coordinator** (planner): [`react/agents/coordinator.py`](agents/coordinator.py)
- **Decision agent** (ReAct loop): [`react/agents/decision.py`](agents/decision.py)
- **ReAct runtime** (state machine + orchestration): [`react.py`](react.py)
- **Context data model**: [`react/context.py`](context.py)
- **Journal renderer**: [`context/journal.py`](../context/journal.py)
- **Turn log** (per-turn structured log): [`runtime/scratchpad.py`](../scratchpad.py)

---

## 2) System Instruction Layout (Decision Agent)

The system prompt is built in [`react/agents/decision.py`](agents/decision.py) via:

- `create_cached_system_message([...])`
- Blocks are *cached* or *non-cached* explicitly.

### System message blocks

```
SYSTEM MESSAGE
|--  Block A (cached)
|  |--  sys_1 (core policies, safety, constraints, responsibilities)
|  |--  sys_2 (output format + JSON rules)
|  `--  2-channel output protocol (thinking + structured JSON)
|
|--  Block B (cached)
|  |--  [AVAILABLE COMMON TOOLS] (formatted catalog)
|  |--  [ACTIVE SKILLS] (only if show_skills was requested last round)
|  |--  [AVAILABLE INFRASTRUCTURE TOOLS]
|  |--  [SKILL CATALOG]
|  `--  Wrap-up instruction block (if wrap-up round)
|
`--  Block C (NOT cached)
   `--  time_evidence_reminder (current date/time + timezone guardrail)
```

**Dynamic sections**:

- **[ACTIVE SKILLS]** appears only when `show_skills` was requested in the previous round and is resolved/loaded by [`react.py`](react.py) before calling `react_decision_stream`.
- **Wrap-up** only appears when the wrap-up gate is active (`is_wrapup_round`).

Reference example (real prompt snapshots):
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys1`](agents/ctx/1/sys1)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys2`](agents/ctx/1/sys2)
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys3`](agents/ctx/1/sys3)

---

## 3) Human Message Layout (Decision Agent)

The human message is built in [`react/agents/decision.py`](agents/decision.py):

```python
message_blocks = [{"text": operational_digest, "cache": True}]
message_blocks.extend(build_attachment_message_blocks(attachments))
```

### Human message structure

```
HUMAN MESSAGE
|--  Block 1 (cached)
|  |--  Operational Digest
|  |   |--  Turn Session Journal (prefix, stable)
|  |   |--  Session Log Summary (append-only)
|  |   `--  [FULL CONTEXT ARTIFACTS] (only when show_artifacts was requested)
|  `--  Loop Rounds metadata (iteration_index, max_iterations)
|
`--  Block 2..N (NOT cached)
   `--  ATTACHMENTS (multimodal blocks + metadata)
```

Where `attachments` are either:

- user attachments, or
- **modal attachments extracted from show_artifacts** (limited to a small cap, see `ReactContext.materialize_show_artifacts`).

The "Loop Rounds" lines are appended after the Operational Digest in the same cached text block.

Reference example:
- [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/usr`](agents/ctx/1/usr)

---

## 4) Operational Digest Structure (What the Decision Agent Reads)

The Operational Digest is built by `build_operational_digest()`:

```
Operational Digest
|--  Turn Session Journal            (MUST be prefix)
|--  Session Log (recent events)     (append-only, prefix-friendly)
`--  [FULL CONTEXT ARTIFACTS]        (only if show_artifacts requested)
```

The **journal must remain the prefix** to preserve cache behavior across rounds (see `build_operational_digest()` comment).

### 4.1 Turn Session Journal -- Section Order

`build_turn_session_journal()` renders a strict order (oldest -> newest):

```
Turn Session Journal
|--  Prior Turns (oldest -> newest)
|  `--  Each turn uses TurnView.to_solver_presentation()
|     - User prompt summary/inventorization
|     - Objective, gate route, solver logs
|     - Produced slots (deliverables) grouped by status
|     - Assistant response summary/inventory
|     - Turn summary
|
|--  Current Turn (live)
|  |--  USER PROMPT STRUCTURAL/SEMANTIC INVENTORIZATION SUMMARY
|  |--  CONTEXT USED (digest of past turns)
|  |--  OBJECTIVE
|  |--  GATE
|  |--  SOLVER.COORDINATOR DECISION (planner guidance)
|  |--  TURN MEMORIES -- CHRONOLOGICAL
|  |--  USER FEEDBACK -- CHRONOLOGICAL
|  |--  SOLVER.TURN CONTRACT SLOTS (to fill)
|  |--  SOLVER.REACT.EVENTS (oldest -> newest)
|  |--  SOLVER.CURRENT ARTIFACTS (oldest -> newest)
|  |--  FILES (CURRENT) -- OUT_DIR-relative paths
|  |--  EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS
|  |--  TURN SOURCES POOL (global SIDs)
|  |--  SOLVER.CURRENT TURN PROGRESS SNAPSHOT
|  |--  Current Slots (if any)
|  `--  Budget Snapshot
|
`--  (End of journal)
```

### 4.2 Full Context Artifacts (show_artifacts)

When the decision JSON includes `show_artifacts`, the runtime **rebuilds** the journal and appends:

```
[FULL CONTEXT ARTIFACTS (show_artifacts)]
### <context_path> [artifact]
meta: time=..., kind=..., format=..., mime=..., filename=..., type=...
content:
```text
<full artifact content>
```
```

Key behavior:
- Only artifacts listed in `show_artifacts` are shown in full.
- Multimodal-supported artifacts (images/PDFs) are **attached separately** as modal blocks and only *defined* in the text (content not embedded).
- A cap is enforced on modal attachments (default 2, deduped by MIME) in `ReactContext.materialize_show_artifacts()`.
- A `sources_pool[SID,...]` slice is a valid show_artifacts path and is rendered as a compact sources artifact.

---

## 5) show_artifacts and show_skills (Dynamic Context Sections)

### show_artifacts (full content staging)

- Set by the decision agent in JSON (`show_artifacts: [...]`).
- Applied **next round**: runtime resolves paths via `ReactContext.materialize_show_artifacts()`.
- Full content appears under `[FULL CONTEXT ARTIFACTS (show_artifacts)]` in the journal.
- Modal attachments (image/pdf) are added to the **human message** as separate message blocks.
- The staging is **one-shot**: `show_artifacts` is cleared after the journal is built.

### show_skills (skill staging)

- Set by the decision agent in JSON (`show_skills: [...]`).
- Resolved to skill IDs by the skills subsystem in [`react.py`](react.py).
- Injected **next round** into the system message under `[ACTIVE SKILLS]`.
- The staging is **one-shot**: `show_skills` is cleared after the journal is built.

---

## 6) Turn Log Concept (Scratchpad -> Journal)

The **Turn Log** is a structured log of a single turn. It is created and stored on the `TurnScratchpad`:

- `TurnLog` entries include: `time`, `area`, `msg`, and optional `data`.
- Common areas: `user`, `user.prompt.summary`, `solver`, `react`, `summary`, `feedback`, etc.

The journal uses the **turn log and turn summary** to render each prior turn's solver presentation (via `TurnView` in [`chatbot/scratchpad.py`](../../solutions/chatbot/scratchpad.py)).

Key effect:
- Historical turns show **deliverables (slots)** and **summaries**, not full artifacts.
- Only **slots** are persisted as durable deliverables between turns.
- Intermediate artifacts are **current-turn only** (see `ReactContext.artifacts`).

---

## 7) Artifacts vs Slots (What is Visible in History)

- **Artifacts** = intermediate tool results produced *in the current turn only*.
- **Slots** = registered deliverables (visible and searchable across turns).

Historical turns do **not** expose tool artifacts. They expose:

- `turn_<id>.slots.<slot_name>.<leaf>`
- `turn_<id>.assistant.completion.*`
- `turn_<id>.user.prompt.*`

This is why the journal shows summaries for historical deliverables and why full content must be staged with `show_artifacts` (current turn only) or via `sources_pool` for original source text.

---

## 8) Context Growth Across Rounds (Journal Evolution)

Every decision/tool round **appends new events and artifacts**; the journal grows monotonically:

```
Round N
  |--  SOLVER.REACT.EVENTS: append decision + tool outcomes
  |--  SOLVER.CURRENT ARTIFACTS: append new artifacts
  |--  TURN SOURCES POOL: grows as SIDs are added
  `--  CURRENT TURN PROGRESS SNAPSHOT: updated counts

Round N+1
  `--  Journal re-rendered with the above appended data
```

If `show_artifacts` / `show_skills` are requested in Round N, their content appears **only in Round N+1**.

---

## 9) Caching Strategy (Why Context Is Arranged This Way)

ReAct uses aggressive prompt caching to reduce token cost and latency.
The layout is designed so that **large prefixes remain stable** across rounds.

### Key caching decisions

1) **System prompt split into cached blocks**
   - Stable instructions + tool catalogs can be cached.
   - Time evidence reminder is **non-cached** because it changes each round.

2) **Operational Digest keeps journal as prefix**
   - `build_operational_digest()` explicitly preserves the journal as the prefix.
   - The session log summary is append-only (prefix-friendly).
   - Full context artifacts are appended at the end.

3) **show_artifacts and attachments are late-bound**
   - Full artifacts and modal attachments are added only when requested.
   - They are appended after the session log, so they do not invalidate the journal prefix.

4) **Session Log Summary is append-only**
   - Built in `build_session_log_summary()` with "oldest->newest" timeline.
   - New events are appended without rewriting older lines.

This layout preserves **long prefix caches** even as the round-by-round content grows.

---

## 10) Example Prompt Snapshot (Reference)

The directory below contains a real prompt capture from a first-round decision:

- System blocks: [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys1`](agents/ctx/1/sys1), [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys2`](agents/ctx/1/sys2), [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/sys3`](agents/ctx/1/sys3)
- Human message: [`kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ctx/1/usr`](agents/ctx/1/usr)

These files illustrate:

- System prompt split into 3 blocks
- Journal structure (prior turns + current turn)
- Contract slots + budget snapshot
- No `show_artifacts` yet (first round)

---

## 11) Diagram Summary (System + Human Message)

```
SYSTEM MESSAGE
- [cached]   sys_1 + sys_2 + 2-channel protocol
- [cached]   tools + infra tools + skill catalog + skills
- [uncached] time evidence reminder

HUMAN MESSAGE
- [cached]   Operational Digest
            - Turn Session Journal (prefix)
            - Session Log Summary
            - Full Context Artifacts (show_artifacts)
- [uncached] attachments (modal blocks + metadata)
```

---

## 12) Diagram Summary (Journal Growth)

```
Round 0: journal
  Prior turns (oldest->newest)
  Current turn header
  Contract slots
  Events (empty)
  Artifacts (empty)
  Progress snapshot

Round 1: journal
  + decision event
  + tool result artifact
  + sources pool update
  + progress snapshot update

Round 2: journal
  + decision event
  + show_artifacts (if staged)
  + more artifacts / sources
```

---

## 13) Practical Implications for Documentation

When documenting or debugging ReAct context, remember:

- **Decision agent sees summaries for history** unless `show_artifacts` is used.
- **Only slots are durable** between turns; tool artifacts are current-turn only.
- **show_artifacts/show_skills are one-shot** staging mechanisms for the next round.
- **Journal prefix stability is intentional** (caching). Avoid reordering sections.
- **Sources pool (SIDs)** is global for the turn and stable across rounds; slicing is required for search/fetch artifacts.
