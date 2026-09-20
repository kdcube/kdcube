---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
title: "User Settings Solution"
summary: "The typed user-settings construct over user_bundle_props: durable preference records, explicit scope, merge and clamp semantics, configured fallbacks, and the boundary between PostgreSQL preferences and Connection Hub capability authority."
status: current
tags: ["sdk", "solutions", "user-settings", "user_bundle_props", "preferences", "conversation-settings", "storage"]
updated_at: 2026-09-20
keywords:
  [
    "user_bundle_props",
    "per-user settings",
    "UserSettingsStore",
    "UserAgentSelectionStore",
    "memory preferences",
    "agent preferences",
    "conversation-scoped settings",
    "merge-write",
    "cache_policy",
    "pending delta",
    "legacy capability seed",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/capabilities-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/constructs/user-settings-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
---
# User Settings Solution

User settings are the platform home for durable, non-secret user preferences:
what a signed-in user chose about how an app behaves, available across devices
and read at the runtime boundary that owns the choice. Each setting family
defines its scope, typed key, defaults, and write semantics.

Capability authority is not a user-settings record. A hosted agent's current
capabilities are the live intersection of its descriptor Control Card and the
user's resident agent Card. PostgreSQL stores model, instruction,
presentation, and cache preferences. The complete distinction is owned by
[Agent Capability Control And Selection](capabilities-README.md).

## Storage model

All preference families use `user_bundle_props` in the tenant/project schema
(`kdcube_<tenant>_<project>`):

| Column | Meaning |
| --- | --- |
| `user_id` | The owning user; writes are single-actor. |
| `bundle_id` | The app the preference belongs to, or a store-defined marker for a platform-wide preference. |
| `key` | The typed address inside that store, including exact entity or conversation scope when needed. |
| `value_json` | The versioned preference record. |
| `subsystem` | The owning store (`memory`, `agents`, and app-defined names). |
| `created_at` / `updated_at` | Record lifecycle. |

The primary key is `(user_id, bundle_id, key)`. A store is a thin typed layer
over this table. It owns one `subsystem`, key convention, `schema_version`,
defaults, normalization, and merge behavior. `UserSettingsStore` in
`kdcube_ai_app.apps.chat.sdk.solutions.user_settings.store` provides the
generic record access. Apps add a concrete store rather than writing rows ad
hoc; see the [App User Settings recipe](../../../recipes/constructs/user-settings-README.md).

Secrets, credential handles, Card authority, turn logs, timelines, cache
warmness, summaries, and artifacts do not belong in `value_json`.

## Shipped stores

### Memory preferences (`subsystem='memory'`)

`UserMemoryStore.get_user_preferences` and `set_user_preferences` keep
`memory_enabled`, `memory_scope`, `updated_by`, and bounded metadata. This
platform-wide preference uses `bundle_id='*'` and `key='preferences'`. An
absent row reads as the declared defaults, and a partial write preserves
sibling fields. Memory semantics live in
[User Memories Overview](../../memory/user-memories-overview-README.md).

### Agent preferences (`subsystem='agents'`)

`UserAgentSelectionStore` retains its historical class name but now owns
preference fields, not live capability authority. It uses two keys under the
real `bundle_id`:

| Scope | Key | Current responsibility |
| --- | --- | --- |
| User/app/agent baseline | `agent_selection:<agent_id>` | Default model, instruction and presentation picks; standing cache policy; a `next_conversation` pending preference delta. |
| Conversation | `conversation:<conversation_id>:agent_selection:<agent_id>` | Materialized model, instruction and presentation picks for that conversation; a `when_cold` pending preference delta. |

The baseline is inserted into a conversation with `ON CONFLICT DO NOTHING` on
first materialization. The conversation then owns its preference values while
future conversations can start from a changed baseline.

```json
{
  "schema_version": 1,
  "model": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
  "instructions": "concise",
  "presentation": {"tool_catalog": "compact"},
  "cache_policy": {"model_switch": "confirm", "capability_toggle": "confirm"},
  "pending": null,
  "updated_at": "2026-09-20T12:05:00Z"
}
```

- `model` is one choice from `supported_models`; absent or stale means the
  configured default.
- `instructions` is one declared instruction profile; absent or stale means
  the declared default.
- `presentation` contains declared presentation facets such as compact or full
  tool and skill forms.
- `cache_policy` is the user's standing policy within the administrator's
  allowed set.
- `pending` carries a deferred preference delta and its trigger.

The record schema still understands `disabled` for compatibility with
pre-Control-Card installations. `get_legacy_capability_seed` reads only the
user baseline and supplies that deny map once when the resident agent Card is
first created. The migration converts it to an equivalent positive Card
selection. Current capability reads and writes then use Connection Hub; a
conversation row is never a second capability authority source.

## Preference semantics

- **Configuration bounds each preference.** Model, instruction, presentation,
  and policy writes are normalized against their declared option sets. A stale
  choice falls back to the configured preference value.
- **Scope is explicit.** A request with `conversation_id` addresses that
  conversation's preferences. An unscoped request addresses the baseline used
  for future materialization. Hosts must label those scopes honestly.
- **Partial writes preserve siblings.** Updating a model does not erase the
  instruction profile or cache policy. Writes to one exact key are
  last-writer-wins unless an app adds stronger coordination.
- **Materialization is race-safe.** Insert-if-absent cannot replace a
  simultaneous explicit write.
- **Preference reads use configured fallbacks.** Missing storage or malformed
  preference data does not break the turn. This fail-open rule applies only to
  behavior preferences. It does not apply to capability authority, consent,
  identity, or other security boundaries.
- **Records are versioned.** `schema_version` evolves the JSON shape without a
  table migration for every field.

## Runtime and UI flow

```text
composer / capabilities widget
  -> agent_capabilities
       live Card capability projection + typed preferences
  -> local draft
  -> explicit Save changes
  -> agent_selection_update
       capability draft -> resident Connection Hub Card
       preference fields -> user_bundle_props at the explicit scope

turn start
  -> resolve current Control Card and resident Card (fail closed)
  -> read scoped preferences (configured fallback on failure)
  -> narrow executable capabilities, then apply preference overlays
```

The capability picker can save both halves in one operation, but that transport
convenience does not merge their authority models. Capability changes revise
the resident Card immediately. Model, instruction, presentation, and deferred
cache behavior retain their explicit baseline or conversation scope.
