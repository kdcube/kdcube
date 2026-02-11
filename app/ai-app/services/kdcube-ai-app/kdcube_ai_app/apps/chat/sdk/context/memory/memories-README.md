# Memory Subsystem (Chat SDK)

**Last Updated:** 2026-01-25

---

## Overview

The memory subsystem captures per-turn preferences and facts, assistant-originated signals, and user feedback.
It then presents the most relevant memory slices to downstream agents without reloading full turn logs.

Core goals:
- Preserve per-turn preferences and facts as compact fingerprints.
- Prevent repeated assistant promotions via assistant signals.
- Keep feedback visible as a memory-like signal.
- Keep retrieval fast (index-only where possible).

---

## Terminology (stable names)

1) Turn Memories (per-turn, user-originated)
   - Source: ctx-reconciler (assertions/exceptions/facts extracted from user input).
   - Stored in turn fingerprint (`artifact:turn.fingerprint.v1`).
   - Represents: contextual preferences and facts expressed in the turn.

2) Assistant Signals (per-turn, assistant-originated)
   - Source: turn summarizer via a dynamic assistant-signal spec.
   - Stored in turn fingerprint and tagged `assistant_signal`.
   - Represents: what the assistant already promoted/claimed (promo guard).

3) User Feedback (per-turn)
   - Source: gate feedback + manual feedback injection.
   - Stored as its own artifact (`artifact:turn.log.reaction`).
   - Represents: user corrections / satisfaction / issues with prior output.

---

## Artifacts and Storage

Below are the memory-related artifacts and where their content lives.

- `artifact:turn.fingerprint.v1`
  - Storage: index-only (no S3 blob).
  - Stored in `conv_messages.text` as JSON for fast retrieval.
  - Tags:
    - `artifact:turn.fingerprint.v1`
    - `assistant_signal` (when assistant_signals exist)
    - `assistant_signal:<key>` (normalized key)

Fingerprint access points (all supported):
- From `artifact:turn.fingerprint.v1` (index-only JSON payload)

- `artifact:turn.log.reaction`
  - Storage: index-only (no S3 blob).
  - Stored in `conv_messages.text` as a serialized JSON block prefixed with `[turn.log.reaction]`.
  - Tag `origin:user` vs `origin:machine` distinguishes source.

- `conversation.memory.bucket.v1`
  - Storage: S3 (hosted_uri points to blob).
  - Indexed by unique tag `mem:bucket:<bucket_id>`.
  - Disabled when `LONG_MEMORIES_LOG=1` or `MEMORY_RECONCILE_ENABLED=0`.

---

## Workflow (high-level)

1) Before gate
   - Load recent turn logs (compressed views).
   - Load TURN MEMORIES (last N + delta window).
   - Load USER FEEDBACK (conversation-scoped).
   - Load ASSISTANT SIGNALS (user-scoped, cross-conversation) but only pass to final answer generator.

2) Ctx-reconciler
   - Reads TURN MEMORIES section and chooses relevant turn_ids
     (`local_memories_turn_ids`) based on current objective and freshness.
   - Output: objective + assertions/exceptions/facts for current turn,
     plus selected memory turn ids for downstream.

3) Post-ctx-reconciler
   - Turn memories are filtered to selected memory turn_ids.
   - Feedback remains unfiltered (conversation-scoped).
   - Downstream agents see only the filtered TURN MEMORIES.

---

## Schematic View (what each agent sees)

Gate (before ctx-reconciler)

[CURRENT TURN]
[PRIOR TURNS (newest->oldest) - COMPRESSED VIEWS]
[TURNS CANDIDATES TABLE]
[USER FEEDBACK — CHRONOLOGICAL (newest->oldest; scope=conversation)]
[TURN MEMORIES — CHRONOLOGICAL (newest->oldest; scope=conversation)]

Ctx-reconciler (same as gate; must pick relevant memory turn_ids)

[CURRENT TURN]
[PRIOR TURNS (newest->oldest) - COMPRESSED VIEWS]
[TURNS CANDIDATES TABLE]
[USER FEEDBACK — CHRONOLOGICAL (newest->oldest; scope=conversation)]
[TURN MEMORIES — CHRONOLOGICAL (newest->oldest; scope=conversation)]
  -> choose local_memories_turn_ids here

Post-ctx-reconciler (downstream agents)

[CURRENT TURN]
[PRIOR TURNS (newest->oldest) - COMPRESSED VIEWS]
[TURNS CANDIDATES TABLE]
[USER FEEDBACK — CHRONOLOGICAL (newest->oldest; scope=conversation)]
[TURN MEMORIES — CHRONOLOGICAL (newest->oldest; scope=conversation)]
  -> only selected memory turn_ids remain

Final answer generator only (adds assistant signals)

[TURN MEMORIES — CHRONOLOGICAL (selected; scope=conversation)]
[USER FEEDBACK — CHRONOLOGICAL (scope=conversation)]
[ASSISTANT SIGNALS — CHRONOLOGICAL (scope=user_cross_conversation)]
