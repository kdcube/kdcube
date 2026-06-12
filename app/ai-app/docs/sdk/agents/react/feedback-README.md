---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/feedback-README.md
title: "Feedback"
summary: "How user and system feedback is surfaced to the agent in v2."
tags: ["sdk", "agents", "react", "feedback"]
keywords: ["feedback blocks", "timeline", "reactions", "signals"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/compaction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
---
# Feedback Integration (React v2)

This document describes how user/machine feedback is surfaced to the agent in React v2.

## Sources

Feedback is stored in two artifact forms:

- `artifact:turn.log.reaction` (authoritative reaction artifact)
- `artifact:turn.log` (turn log payload contains `turn_log.feedbacks[]`)

Fast index access (index‑only, no blob fetch):

- Reaction artifacts store the reaction JSON directly in `conv_messages.text`.
- Turn log artifacts store a compact JSON summary (turn metadata + optional feedback summary) in `conv_messages.text`.

React v2 uses **reaction artifacts** for timeline refresh (single SQL query, latest
per turn). Turn logs are used by `memsearch` and conversation fetch APIs.
No additional materialization happens during refresh beyond the running timeline
and the conversation sources pool.

## When Feedback Appears

Feedback can be shown in two places, depending on cache state:

### Cache Cold (TTL expired or disabled)

- Feedback is inserted into the relevant turn as a `turn.feedback` block.
- The feedback block is the **last block** of the turn.
- If a feedback block already exists and its text changes, it is unhidden and updated.

### Cache Hot (TTL active and fresh)

- The timeline is **not** mutated.
- Feedback appears in ANNOUNCE under `[NEW USER FEEDBACKS]` or `[NEW FEEDBACKS]`.
- When cache becomes cold, feedback is injected into the turn.

## Timeline Block Shape

```
type: "turn.feedback"
path: "ar:<turn_id>.feedback.<key>"
meta: {
  origin: "user" | "machine",
  reaction: "ok" | "not_ok" | "neutral" | null,
  confidence: <float>,
  from_turn_id: "<turn_id>"
}
```

Rendering format:

```
[USER FEEDBACK]
[ts: <feedback_ts>]
reaction: <reaction>
<feedback text>
```

## Announce Format (Cache Hot)

```
[NEW USER FEEDBACKS]
  - turn <turn_id> | turn_ts=<turn_ts> | feedback_ts=<ts> | reaction=<reaction> | text=<text>
```

If feedback was injected into the timeline (cache cold), ANNOUNCE includes:

```
  (incorporated into turn timeline)
```

## Feedback Refresh Flow

1. **On timeline load**, React v2 refreshes feedback (no mid‑turn refresh).
2. It queries **latest reaction per turn** (SQL `DISTINCT ON (turn_id)`), filtered by:
   - `artifact:turn.log.reaction` tag
   - `conversation_id`
   - `turn_id IN (turns in timeline)`
   - `ts >= last_known_feedback_ts` (when present)
3. Updates are shown in ANNOUNCE as `[NEW USER FEEDBACKS]` / `[NEW FEEDBACKS]`.
4. If cache is cold, feedback is injected into the relevant turns and ANNOUNCE
   notes “(incorporated into turn timeline)”.

`last_known_feedback_ts` is stored in the timeline payload and is updated **only when
feedback is incorporated into the timeline** (cold cache). Hot‑cache announcements
do not advance this watermark, so they continue until a cold turn incorporates them.

### Flow Diagram (Cache Hot/Cold)

```mermaid
flowchart TD
    A[Render timeline / announce] --> B[Fetch latest reaction per turn<br/>SQL DISTINCT ON (turn_id)]
    B --> C{Cache hot?}
    C -- Yes --> D[ANNOUNCE: NEW FEEDBACKS]
    C -- No --> E[Inject turn.feedback blocks]
    E --> F[ANNOUNCE: NEW FEEDBACKS + "incorporated"]
```

## Implementation Notes

- Feedback integration is handled by `Feedback` in `kdcube_ai_app/apps/chat/sdk/solutions/react/feeback.py`.
- ContextBrowser performs the refresh and decides whether to mutate the timeline
  based on cache state.
- Announce lists only feedback **new or changed since last assessment**.
- Backward compatibility:
  - `artifact:turn.log.reaction` text can be JSON or a dict‑string; both are parsed.
  - Older `artifact:turn.log` index text (markdown header / old feedback‑only JSON) is ignored by
    feedback refresh logic and only affects semantic search results.
