---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
title: "User Settings Solution"
summary: "The per-user, per-app, per-key settings construct over user_bundle_props: the storage model, the merge-write/clamp semantics that make user choices safe, the two shipped stores (memory preferences and the agent selection record), and how settings reach runtime and UI."
status: current
tags: ["sdk", "solutions", "user-settings", "user_bundle_props", "preferences", "agent-selection", "storage"]
updated_at: 2026-07-06
keywords:
  [
    "user_bundle_props",
    "per-user settings",
    "UserSettingsStore",
    "UserAgentSelectionStore",
    "memory preferences",
    "agent_selection key",
    "merge-write",
    "clamp on write",
    "cache_policy",
    "pending delta",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/constructs/user-settings-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
---
# User Settings Solution

User settings are the platform's home for **durable user choices**: what a
signed-in user decided about how an app behaves for them, kept across
conversations and devices, applied fresh on every turn. One construct carries
all of them — a per-user, per-app, per-key record store — and two shipped
stores exercise it end to end: memory preferences and the agent selection
record.

## The storage model

Everything rides one table, `user_bundle_props`, living in the tenant/project
schema (`kdcube_<tenant>_<project>`):

| Column | Meaning |
| --- | --- |
| `user_id` | The owning user (writes are always single-actor). |
| `bundle_id` | The app the setting belongs to — a real app id, or a store-defined marker for platform-wide settings. |
| `key` | The setting record's name inside the store's namespace. |
| `value_json` | The record (JSONB), shaped and versioned by the owning store. |
| `subsystem` | Which store owns the row (`memory`, `agents`, …; default `bundle`). |
| `created_at` / `updated_at` | Row lifecycle. |

Primary key `(user_id, bundle_id, key)`; a supporting index over
`(user_id, subsystem, bundle_id, key, updated_at DESC)` serves store scans.
Each store creates the table idempotently (`ensure_schema`), so any one of
them bootstraps the construct.

A **store** is a thin, typed layer over this table that owns one record shape:
its `subsystem`, its key convention, its `value_json` schema (with a
`schema_version`), its defaults, and its write semantics. The generic core
lives in `kdcube_ai_app/apps/chat/sdk/solutions/user_settings/` —
`UserSettingsStore` (`store.py`) carries the table access and conventions, and
concrete stores subclass it (the agent selection record in
`agent_selection.py`). Apps add their own settings by adding a store, never by
writing rows ad hoc — the
[user-settings recipe](../../../recipes/constructs/user-settings-README.md)
walks the steps.

## The two shipped stores

### Memory preferences (`subsystem='memory'`)

`UserMemoryStore.get_user_preferences` / `set_user_preferences` keep the user's
memory posture: `memory_enabled` (participate in durable memory at all) and
`memory_scope` (single-channel vs identity-family reads), plus `updated_by` and
free `metadata`. Convention worth copying when a setting is platform-wide
rather than per-app: the row uses **`bundle_id='*'`** and `key='preferences'`,
so one record governs the user's memory behavior across every app. An absent
row reads as the permissive defaults (enabled, family scope), and writes merge
over the stored record so toggling one field never clobbers the other. Memory
semantics themselves are owned by
[User Memories Overview](../../memory/user-memories-overview-README.md).

### The agent selection record (`subsystem='agents'`)

`UserAgentSelectionStore` keeps everything a user decided about one configured
agent: `key='agent_selection:<agent_id>'` under the **real** `bundle_id`, one
record per (user, app, agent):

```json
{
  "schema_version": 1,
  "disabled": {
    "tools": {"gmail": true, "web_tools": ["web_fetch"]},
    "mcp": {"knowledge": ["kb_fetch"]},
    "named_services": {"task": true},
    "skills": ["public.docx-press"]
  },
  "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
  "cache_policy": {"model_switch": "confirm", "capability_toggle": "accept"},
  "pending": {
    "model": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    "apply": "next_conversation",
    "since_conversation_id": "conv-42",
    "created_at": "2026-07-06T12:00:00Z"
  },
  "updated_at": "2026-07-06T12:00:00Z"
}
```

- `disabled` — DENY-lists per category (python tool groups whole or per tool,
  MCP servers whole or per tool, named-service namespaces, skills). Absent
  entry = enabled; absent record = the full configured set.
- `model` — the single PICK from the admin-declared `supported_models` list;
  absent = the configured default model runs.
- `cache_policy` — the user's standing cold-cache policy per change class
  (`accept`, `confirm`, `defer_cold`, `defer_conversation`); admin config
  supplies only the default and the allowed set.
- `pending` — one deferred selection change awaiting its trigger (a different
  conversation, or a cold cache); the runtime promotes it into the active
  record when the trigger fires.

Selection semantics (what the record means at runtime) are owned by
[How To Construct A ReAct Agent](../../agents/react/how/how-to-construct-react-agent-README.md);
the cache consequences by [Context Caching](../../agents/react/context-caching-README.md).

## The semantics that make user settings safe

These rules hold for every store and are what distinguish the construct from a
generic KV:

- **Config grants, the user chooses within the grant.** Writes are clamped
  against the live inventory/allowed set at write time (out-of-inventory tool
  names, models outside `supported_models`, policies outside the admin-allowed
  set — all stripped), and reads recompute `effective = configured ∩ chosen`,
  so a stale stored choice for a since-removed config entry is a harmless
  no-op.
- **Defaults flow from config, never from storage.** An absent row (or field)
  means "the configured default", so new config entries apply to everyone
  immediately and no migration ever back-fills rows.
- **Merge-writes, never clobbering siblings.** A write carries only what
  changed (a partial patch); the store merges it over the stored record.
  Toggling one tool never touches the model pick; setting the model never
  touches the deny-lists. This is also the concurrency model: two interleaved
  partial writes both land because neither rewrites the other's fields.
- **Per-turn reads, fail-open.** The runtime reads the record fresh at the
  turn's application point and treats every failure (missing pool, store
  error, malformed record) as "use the configured behavior" — a broken
  settings store never breaks the agent.
- **Versioned records.** `schema_version` in `value_json` lets a store evolve
  its shape without table changes.

**What belongs here:** durable per-user choices an app should honor on every
turn — toggles, picks, standing policies, notification/scope preferences.
**What stays out:** per-conversation state (that lives with the conversation —
timeline payload, conversation state rows) and **secrets of any kind** —
tokens and credentials live in the user secret store behind the connections
stack, never in `value_json`.

## How settings reach runtime and UI

```text
UI (widget / composer menu)
  ├─ read op   (agent_capabilities, memories_widget_preferences)
  │     → config-derived inventory/defaults + the user's current record
  ├─ write op  (agent_selection_update, memories_widget_preferences_update)
  │     → partial merge-write, clamped server-side
  └─ optimistic UI + debounced merge-writes (only changed fields travel)

runtime (per turn)
  └─ application point reads the record fresh and applies it fail-open
     (agent selection: BaseWorkflow.apply_user_agent_selection narrows the
      tool/skill configs, applies the model pick, honors cache_policy/pending;
      memory: announce/tools honor memory_enabled + memory_scope)
```

The ops pattern: read ops piggyback the current record on the config-derived
payload (one round-trip for the picker); write ops accept partial patches and
return the clamped record for reconciliation; both declare visibility
explicitly (registered users and above — an undeclared operation is open to
all callers). The chat-side client detail (state branch, debounce,
flush-on-send) is owned by
[Chat Engine](../../npm/components-core/chat-engine-README.md).
