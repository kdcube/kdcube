---
id: ks:docs/sdk/agents/react/design/note-keeping-and-working-summary-README.md
title: "Draft: Note Keeping and Working Summary"
summary: "Draft design for transient note keeping and durable working summaries in React v2."
draft: true
status: draft
tags: ["sdk", "agents", "react", "design", "summary", "announce", "timeline"]
keywords: ["working summary", "summary notes", "announce", "channel:work-summary", "cold start", "context continuity"]
see_also:
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/react-context-README.md
  - ks:docs/sdk/agents/react/session-view-README.md
  - ks:docs/sdk/agents/react/flow-README.md
  - ks:docs/sdk/agents/react/hooks-README.md
---
# Draft: Note Keeping and Working Summary

This doc is a **draft design** for how React v2 should maintain a durable
working summary without relying on a separate summarizer agent and without
forcing expensive full-session summarization on every turn.

The key decision in this draft is:

- **React itself writes the working summary**
- **ANNOUNCE is the transient input surface for summary-specific notes**
- **`channel:work-summary` is the output surface for the durable summary**

This design is intentionally aligned with the current ANNOUNCE contract instead
of inventing a second transient context surface.

---

## 1) Problem

React runs in a distributed environment.

That means:
- the next turn may land on a **hot** cache line
- or the next turn may land on a **cold** cache line
- the runtime cannot rely on “we will summarize later”

At the same time, full summarization on every turn is undesirable because:
- it adds latency
- it increases cost
- it annoys the user
- most turns do not justify a full rolling-summary update

So the system needs a way to:
- create durable continuity when needed
- do it while the current turn is still hot
- let the main React agent produce the summary itself
- avoid permanently cluttering the visible timeline with summary-input notes

---

## 2) Core idea

React already understands that **ANNOUNCE** is the working board for transient
high-frequency state:
- plan state
- temporal context
- one-time runtime notices
- current signals needed for the decision loop

This draft extends that model:

- if React decides a durable summary is needed, it calls `get_summary_notes`
- runtime injects the returned notes into a dedicated section of ANNOUNCE:
  - `INSIGHTS FOR SUMMARY`
- React then writes the summary to `channel:work-summary`
- runtime captures that channel and persists a durable summary block on timeline

So the agent experience remains coherent:
- ANNOUNCE = transient working board
- `channel:work-summary` = structured output surface for durable summary

---

## 3) Terminology

### 3.1 ANNOUNCE

The existing ephemeral tail block documented in:
- [react-announce-README.md](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md)

ANNOUNCE is:
- re-rendered each decision round
- never cached
- current-turn-only by default

### 3.2 Summary notes

Transient, summary-oriented hints placed into ANNOUNCE under a dedicated
section:
- `INSIGHTS FOR SUMMARY`

These are not the durable summary itself.

They are there to:
- expose non-visible context React explicitly asked for
- help React compose a high-quality working summary
- disappear after the summary action is complete

### 3.3 Working summary

A durable rolling summary of current work state.

This is a new timeline concept distinct from compaction summaries:

- `conv.range.summary`
  - created by compaction
  - lossy boundary summary
  - affects how older context is collapsed

- `conv.working.summary`
  - created intentionally by React
  - represents current durable session state
  - should stay visible across cold starts

---

## 4) Why React itself should write the summary

This draft explicitly prefers **React itself** over a separate summarizer model.

Reason:
- React has the best semantic understanding of the current work
- React knows which parts of the turn mattered
- React can choose the right level of detail
- React can ask for extra summary notes exactly when needed

This is better than asking another model to reconstruct intent from artifacts
“according to stories”.

Implication:
- summary generation is part of React’s responsibility
- runtime does not invent the summary content
- runtime only provides transient notes and captures the output

---

## 5) Trigger model

### 5.1 Who decides

React decides when a working summary should be refreshed.

This may happen:
- near the end of the turn
- after a major decision
- after a substantial implementation step
- after a blocker/failure worth preserving
- after a plan transition
- after any point where React judges continuity would be harmed without a summary

### 5.2 When it runs

The summary must be generated **during the same hot turn**.

It must not depend on a future turn, because a future turn may already be cold.

So the correct lifecycle is:

1. React is working on the turn
2. React decides a summary refresh is needed
3. React fetches summary notes
4. React emits `channel:work-summary`
5. runtime persists the durable working summary
6. turn continues or exits normally

### 5.3 Sequential first, parallel later

Initial implementation should be sequential.

Later, the system may support parallel execution where:
- React requests summary generation
- summary writing runs concurrently
- runtime adds the completed summary block before turn finalization

But this draft assumes **sequential** behavior first.

---

## 6) `get_summary_notes`

### 6.1 Purpose

`get_summary_notes` is a React tool whose job is:
- gather non-visible high-signal inputs needed for a summary
- expose them through ANNOUNCE
- avoid forcing React to re-read large parts of history manually

### 6.2 What it should gather

It should return a curated package, not the full rendered timeline.

Likely contents:
- previous working summary
- high-signal `react.note` blocks
- current/open plan snapshot
- plan transitions since last working summary
- unresolved blockers / repeated failures
- important refs:
  - file paths
  - artifact paths
  - commit hashes
  - turn ids
- recent notable milestones

It should prefer:
- curated signal
over
- full history replay

### 6.3 Output transport

The result of `get_summary_notes` should appear in ANNOUNCE under:

```text
[INSIGHTS FOR SUMMARY]
...
```

This is a **transient ANNOUNCE section**, not a durable block.

### 6.4 Why use ANNOUNCE instead of a second transient surface

Because React already understands ANNOUNCE as:
- the working board
- the place for current signals
- the place where transient high-frequency guidance appears

Adding another intermittent transient surface would be harder to teach and less
coherent.

So this design uses the existing ANNOUNCE mental model.

---

## 7) `channel:work-summary`

### 7.1 Purpose

`channel:work-summary` is the dedicated output channel React uses when it wants
to produce a durable working summary.

This is analogous to how React already understands other dedicated channels
such as:
- plan-like structured outputs
- code-oriented outputs

### 7.2 Runtime behavior

When runtime sees `channel:work-summary`, it should:
- capture the content
- validate it is non-empty
- persist it as a `conv.working.summary` block

The summary should not be treated as ordinary assistant completion text.

### 7.3 Summary block shape

Proposed durable block:

```json
{
  "type": "conv.working.summary",
  "author": "assistant",
  "turn_id": "<turn_id>",
  "path": "ws:<turn_id>.conv.working.summary",
  "text": "<summary markdown>",
  "meta": {
    "kind": "working_summary",
    "supersedes": "ws:<previous_turn>.conv.working.summary",
    "created_by": "react",
    "covered_turn_ids": ["turn_a", "turn_b"],
    "covered_until_turn_id": "turn_b",
    "summary_notes_digest": "<optional digest>"
  }
}
```

### 7.4 Visibility rules

Only the **latest active** working summary should be included automatically in
cold-start visible context.

Older working summaries should remain readable history but should not all stay
always visible.

---

## 8) ANNOUNCE contract extension

### 8.1 New section

This draft adds a new optional ANNOUNCE section:

```text
[INSIGHTS FOR SUMMARY]
- previous working summary: ...
- current plan: ...
- blockers: ...
- refs: ...
```

### 8.2 Persistence rule

This section must be treated as **transient only**.

Important:
- final ANNOUNCE is currently persisted on exit
- therefore `INSIGHTS FOR SUMMARY` must be stripped before final ANNOUNCE
  persistence

Otherwise:
- transient summary-input notes would leak into durable timeline state
- the working board would become polluted with stale summary-only hints

So runtime must:
- render `INSIGHTS FOR SUMMARY` while React needs it
- remove it before the persisted final ANNOUNCE block is written

This is the most important lifecycle rule in this draft.

---

## 9) Difference from compaction summary

This design must not be confused with compaction.

Current compaction already creates:
- `conv.range.summary`

That summary:
- compresses older history
- is generated by runtime compaction flow
- changes what older context stays visible

The new working summary:
- is intentionally authored by React
- reflects current durable work state
- does not define a compaction boundary
- should survive cold starts as a stable anchor

So the two summary types must remain separate.

---

## 10) Suggested authoring prompt behavior

React should be taught something like:

- If you believe current work introduced decisions, progress, blockers, or
  context that should survive a cold restart, you may refresh the working
  summary.
- Before writing a working summary, call `get_summary_notes` if you need
  high-signal historical notes that may not currently be visible.
- The notes will appear in ANNOUNCE under `INSIGHTS FOR SUMMARY`.
- Then write the summary to `channel:work-summary`.
- Do not emit the working summary as normal assistant text.

This keeps the behavior explicit and teachable.

---

## 11) Cold-start loading policy

On cold start, the visible context should include:
- latest active `conv.working.summary`
- current ANNOUNCE
- normal recent visible timeline tail

It should **not** automatically re-show all old summary notes or all old working
summaries.

So the working summary acts as the durable bridge between:
- previous hot rich context
- future cold turn startup

---

## 12) Why this design

This design is preferred because it gives:
- hot-context summary generation
- the best summarizer = React itself
- reuse of the existing ANNOUNCE mental model
- no need to teach a second transient surface
- no forced full summarization on every turn
- durable cold-start continuity

---

## 13) Open questions

This draft leaves open:

1. Exact `get_summary_notes` schema
- text-only ANNOUNCE payload
- or structured payload rendered into ANNOUNCE

2. Validation of `channel:work-summary`
- minimum format requirements
- markdown vs structured sections

3. Staleness policy
- whether runtime should also force a summary refresh in certain situations
- or whether React alone is sufficient as the trigger

4. Future parallelization
- when summary writing should become a parallel task
- how runtime waits for it before turn finalization

---

## 14) Initial implementation plan

### Step 1

Document and teach:
- `get_summary_notes`
- `INSIGHTS FOR SUMMARY`
- `channel:work-summary`

Primary files:
- [decision.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/agents/decision.py)
- [runtime.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/runtime.py)

### Step 2

Add durable block support:
- `conv.working.summary`

Primary file:
- [timeline.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/timeline.py)

### Step 3

Teach cold-start visibility:
- latest active working summary stays visible
- summary-note section in ANNOUNCE stays transient

### Step 4

Optionally add runtime backstop logic later if needed:
- if React fails to summarize often enough
- if continuity regressions appear in production

---

## 15) Draft conclusion

The proposed note-keeping model for React v2 is:

- **ANNOUNCE remains the transient working board**
- **summary-specific hints appear there in `INSIGHTS FOR SUMMARY`**
- **React writes the durable summary itself**
- **runtime captures it from `channel:work-summary`**
- **the durable result is stored as `conv.working.summary`**

This preserves the existing React mental model while adding a reliable
continuity mechanism for cold-start recovery.
