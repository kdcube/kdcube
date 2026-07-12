---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
title: "User Settings Solution"
summary: "The typed user-settings construct over user_bundle_props: application fallbacks, optional user baselines, durable conversation-scoped choices, merge-write/clamp semantics, the shipped settings stores, and how settings reach runtime and UI."
status: current
tags: ["sdk", "solutions", "user-settings", "user_bundle_props", "preferences", "agent-selection", "conversation-settings", "storage"]
updated_at: 2026-07-12
keywords:
  [
    "user_bundle_props",
    "per-user settings",
    "UserSettingsStore",
    "UserAgentSelectionStore",
    "memory preferences",
    "agent_selection key",
    "conversation-scoped settings",
    "merge-write",
    "clamp on write",
    "cache_policy",
    "pending delta",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/constructs/user-settings-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
---
# User Settings Solution

User settings are the platform's home for **durable user choices**: what a
signed-in user decided about how an app behaves for them, available across
devices and applied fresh at the owning runtime boundary. A setting family
decides its scope. Memory preferences are platform-wide. Agent selections are
durable **per conversation**; an optional user baseline can seed future
conversations. One typed record store carries all of them.

## The storage model

Everything rides one table, `user_bundle_props`, living in the tenant/project
schema (`kdcube_<tenant>_<project>`):

| Column | Meaning |
| --- | --- |
| `user_id` | The owning user (writes are always single-actor). |
| `bundle_id` | The app the setting belongs to — a real app id, or a store-defined marker for platform-wide settings. |
| `key` | The setting record's typed address inside the store's namespace. It may carry an exact scope such as `conversation:<id>:`. |
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

### Agent selection (`subsystem='agents'`)

`UserAgentSelectionStore` uses two exact keys under the **real** `bundle_id`:

| Scope | Key | Owns |
| --- | --- | --- |
| User baseline | `agent_selection:<agent_id>` | Optional initial model/capability selection for future conversations; standing `cache_policy`; a `next_conversation` pending delta. |
| Conversation | `conversation:<conversation_id>:agent_selection:<agent_id>` | The effective model/capability selection for this conversation; a `when_cold` pending delta. |

The conversation row is a full selection, not a sparse override. On its first
capability read or first turn, the store inserts the current user baseline
with `ON CONFLICT DO NOTHING`. If no baseline row exists, the seed is the
application-configured behavior: nothing user-disabled and no model override.
This freezes what that conversation uses while allowing the baseline to evolve
for future conversations. No schema change, tag column, scan, or new table is
required: the typed key is the scope.

The two settings surfaces write these keys deliberately:

- the chat picker always sends `conversation_id` and therefore writes the
  conversation key;
- the independently served Capabilities widget sends no `conversation_id` and
  therefore writes the user baseline for future conversations.

A conversation edit never updates the baseline implicitly. Conversely, a host
must label the unscoped widget as **defaults for future conversations**, not as
an expanded editor for the current conversation.

```text
configured inventory (admin ceiling)
            |
            v
user baseline: agent_selection:main
  disabled + model + standing cache_policy
            |
            | first read/turn; insert if absent
            v
conversation:conv-42:agent_selection:main
  disabled + model for conv-42
            |
            | explicit "Save changes"
            v
next turn in conv-42 reads this exact row

conv-43 starts independently from the user baseline
```

User-baseline example:

```json
{
  "schema_version": 1,
  "disabled": {},
  "model": null,
  "cache_policy": {"model_switch": "confirm", "capability_toggle": "accept"},
  "updated_at": "2026-07-12T12:00:00Z"
}
```

Conversation example:

```json
{
  "schema_version": 1,
  "disabled": {
    "tools": {"gmail": true, "web_tools": ["web_fetch"]},
    "mcp": {"knowledge": ["kb_fetch"]},
    "named_services": {"mail": ["object.action.send"]},
    "skills": ["public.docx-press"]
  },
  "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
  "updated_at": "2026-07-12T12:05:00Z"
}
```

- `disabled` — DENY-lists per category (python tool groups whole or per tool,
  MCP servers whole or per tool, named-service namespaces whole or per
  operation/action — `object.search`, `object.action.send` — skills). Absent
  entry = enabled; absent record = the full configured set. The full
  granularity map and the picker surfaces live in
  [Conversation-Scoped Agent Capabilities](capabilities-README.md).
- `model` — the single PICK from the admin-declared `supported_models` list;
  absent = the configured default model runs.
- `cache_policy` — the user's standing cold-cache policy per change class,
  stored only on the user-baseline row
  (`accept`, `confirm`, `defer_cold`, `defer_conversation`); admin config
  supplies only the default and the allowed set.
- `pending` — one deferred selection change awaiting its trigger. A
  `next_conversation` delta sits on the user baseline and is promoted before a
  different conversation is seeded. A `when_cold` delta sits on the current
  conversation and is promoted when that conversation's cache is cold.

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
- **Defaults have an explicit chain.** Config supplies the app ceiling and the
  fallback when no user-baseline row exists. A new conversation inherits the
  user baseline once, then owns its materialized selection. No migration
  back-fills rows.
- **Merge-writes, never clobbering siblings.** A write carries only what
  changed (a partial patch); the store merges it over the stored record.
  Toggling one tool never touches the model pick; setting the model never
  touches the deny-lists in that write. The UI batches one conversation draft
  behind **Save changes**. Truly concurrent writes to the same exact key are
  last-writer-wins; callers should serialize them. First conversation
  materialization is insert-if-absent, so it cannot replace a simultaneous
  user write.
- **Per-turn reads, fail-open.** The runtime reads the record fresh at the
  turn's application point and treats every failure (missing pool, store
  error, malformed record) as "use the configured behavior" — a broken
  settings store never breaks the agent.
- **Versioned records.** `schema_version` in `value_json` lets a store evolve
  its shape without table changes.

**What belongs here:** durable user choices, including a choice whose exact
scope is one conversation — toggles, picks, standing policies, and
notification/scope preferences. **What stays out:** conversation execution
state (turns, timeline payloads, cache warmness, summaries, artifacts) and
**secrets of any kind**. Tokens and credentials live in the user secret store
behind the connections stack, never in `value_json`.

## How settings reach runtime and UI

```text
UI (widget / composer menu)
  ├─ read op   (agent_capabilities, memories_widget_preferences)
  │     → config-derived inventory/defaults + the scoped current record
  ├─ local draft (model/tool/service/skill toggles)
  ├─ explicit Save changes
  │     → agent_selection_update(conversation_id, partial patch)
  │     → merge-write, clamped server-side
  └─ standalone capabilities widget (no conversation_id)
        → manages the user baseline for future conversations

runtime (per turn)
  └─ application point reads the record fresh and applies it fail-open
     (agent selection: BaseWorkflow.apply_user_agent_selection narrows the
      tool/skill configs, makes denied namespace operations/actions
      uncallable at named-service dispatch, applies the model pick, honors
      conversation selection + user-baseline cache_policy/pending;
      memory: announce/tools honor memory_enabled + memory_scope)
```

The ops pattern: read ops piggyback the current record on the config-derived
payload (one round-trip for the picker); chat writes carry `conversation_id`,
accept partial patches, and return the clamped scoped record for
reconciliation; both declare visibility
explicitly (registered users and above — an undeclared operation is open to
all callers). The chat-side client detail (state branch, draft, explicit save,
and conversation-switch race handling) is owned by
[Chat Engine](../../npm/components-core/chat-engine-README.md).
