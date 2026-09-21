---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/constructs/user-settings-README.md
title: "Recipe: App User Settings"
summary: "Steps to give an app durable non-security preferences over user_bundle_props: choose the scope, define a typed record, normalize and merge writes, expose explicit operations, save deliberate UI drafts, and apply configured fallbacks at runtime."
status: current
tags: ["recipes", "constructs", "user-settings", "user_bundle_props", "store", "operations"]
updated_at: 2026-09-21
keywords:
  [
    "app user settings recipe",
    "user_bundle_props store",
    "subsystem key convention",
    "merge-write clamp",
    "settings ops visibility",
    "explicit settings save",
    "conversation scoped settings",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
---
# Recipe: App User Settings

Steps to add an app's OWN durable user settings. The construct, its
semantics, and the two shipped exemplars referenced below are owned by the
[User Settings Solution](../../sdk/solutions/user-settings/user-settings-solution-README.md);
this recipe is the wiring.

## 1. Define the record + the defaults

One JSON record per (user, app, typed key): a `schema_version`, the user's
choices, nothing else. First decide whether the choice is user-wide,
app-wide, per entity, or durable for one conversation. Configuration remains
the ceiling and fallback when the scoped row or field is absent.

```json
{"schema_version": 1, "digest_enabled": true, "digest_hour": 8}
```

Keep out: secrets (user secret store) and conversation **execution state**
(turns, timeline payloads, cache warmness, summaries, artifacts). A durable
choice whose intended scope is one conversation belongs here and uses an
exact conversation key. Authorization, consent, and capability selection also
stay out: use their owning security stores and fail-closed boundaries.

## 2. Pick the subsystem/key convention

- `subsystem`: one stable name for your store (the shipped exemplars:
  `memory`, `agents`).
- `bundle_id`: your real app id for app-scoped settings; the memory-preferences
  store shows the `bundle_id='*'` convention for a platform-wide record.
- `key`: constant for a singleton record (`preferences`), parameterized for a
  per-entity family (`agent_selection:<agent_id>`), or an exact typed scope
  (`conversation:<conversation_id>:agent_selection:<agent_id>`).

## 3. A thin store over `user_bundle_props`

Subclass `UserSettingsStore`
(`kdcube_ai_app/apps/chat/sdk/solutions/user_settings/store.py`) — the generic
core already carries the pool/schema wiring, the idempotent `ensure_schema`,
and record get/put/merge by `(user_id, bundle_id, subsystem, key)`. Your class
adds the record shape and semantics:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import UserSettingsStore

class MyAppSettingsStore(UserSettingsStore):
    async def get_settings(self, *, user_id, bundle_id) -> dict:
        record = await self.get_record(user_id=user_id, bundle_id=bundle_id,
                                       subsystem="myapp", key="settings")
        # absent record -> the defaults record, never an error.
        ...

    async def set_settings(self, *, user_id, bundle_id, patch) -> dict:
        # 1. read current; 2. MERGE the partial patch over it (omitted fields
        #    keep their value); 3. CLAMP against what config allows;
        # 4. put_record (upsert of the merged whole).
        ...
```

One shipped exemplar is the preference half of `UserAgentSelectionStore`
(`kdcube_ai_app/apps/chat/sdk/solutions/user_settings/agent_selection.py`):
a structured model/instruction/presentation/cache-policy record with its own
deep merge and option normalization. Its `disabled` shape is migration
compatibility for pre-Card capability choices, not a pattern for new stores.

The two invariants to copy exactly are **merge-writes** (one write carries only
what changed and preserves sibling fields from its read snapshot) and
**normalize-on-write** (a preference outside the configured option set is
stripped or ignored, and reads resolve stale choices to declared defaults).
Concurrent writes to the same exact key are last-writer-wins; serialize them
when stronger ordering is required. Use insert-if-absent when materializing an
inherited scoped value so a first read cannot replace a simultaneous write.

## 4. Ops on the entrypoint — declare visibility

```python
@api(method="POST", alias="myapp_settings", route="operations",
     user_types=("registered", "paid", "privileged"))
async def myapp_settings(self, **kwargs):
    # read: config-derived options/defaults + the user's current record
    ...

@api(method="POST", alias="myapp_settings_update", route="operations",
     user_types=("registered", "paid", "privileged"))
async def myapp_settings_update(self, data=None, **kwargs):
    # write: partial patch -> store.set_settings; return the clamped record
    ...
```

Declare `user_types` explicitly — an operation without a declared visibility
is open to ALL callers. `memories_widget_preferences` and
`memories_widget_preferences_update` are a pure settings example.
`agent_capabilities` and `agent_selection_update` intentionally coordinate two
owners in one UI save: behavior preferences use the agent-preference records,
while capability toggles replace the named conversation's positive projection
beneath its current Connection Hub Cards. The Agent Card base is edited in
Connection Hub.

## 5. UI round-trip

Read once (lazy, on surface open); keep edits as a local draft; save only on an
explicit user command; send the exact scope plus only changed fields; then
reconcile from the returned normalized record. The chat composer uses
`conversation_id` for capability changes and for scoped model, instruction,
presentation, and cache preferences, and exposes **Save changes**. An
independently mounted capability surface without a conversation id shows the
Agent Card base read-only; preference-only settings may still expose an
explicit future-conversation baseline. Never switch scopes silently in a host
UI.

The composer "+" menu and the memories widget are the two shipped
round-trips; the chat engine's capabilities branch
(`loadAgentCapabilities` / `updateAgentSelection` /
`saveAgentSelectionChanges`) is the client-side pattern to copy.

## 6. Apply preferences at their owning boundary

Read the exact scoped preference record at the runtime application point. For
ordinary behavior preferences, a read failure means "use the configured
preference":

```python
try:
    settings = await store.get_settings(user_id=..., bundle_id=...)
except Exception:
    settings = {}   # configured defaults; the turn always proceeds
```

Shipped application points are the preference portion of
`BaseWorkflow.apply_user_agent_selection` (model, instruction, presentation,
and cache policy) and the memory announce/tools honoring `memory_enabled` and
`memory_scope`.

This fail-open fallback does not apply to security decisions. In the same
workflow, an unavailable Connection Hub capability projection closes all
selectable capabilities. Identity, consent, Card authority, and other fences
must follow their own fail-closed contracts.
