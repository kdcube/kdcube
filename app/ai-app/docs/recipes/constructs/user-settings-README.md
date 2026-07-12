---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/constructs/user-settings-README.md
title: "Recipe: App User Settings"
summary: "Steps to give an app durable user settings over user_bundle_props: choose the setting scope, define its typed key and record, clamp/merge on write, expose explicit operations, save deliberate UI drafts, and apply the setting at runtime."
status: current
tags: ["recipes", "constructs", "user-settings", "user_bundle_props", "store", "operations"]
updated_at: 2026-07-12
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
exact conversation key.

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

The complete shipped exemplar is `UserAgentSelectionStore`
(`kdcube_ai_app/apps/chat/sdk/solutions/user_settings/agent_selection.py`):
a structured record with its own deep merge, clamped against the live
inventory on write.

The two invariants to copy exactly: **merge-writes** (one write carries only
what changed and preserves sibling fields from its read snapshot) and
**clamp-on-write** (a choice outside what config grants is stripped or
ignored; reads additionally recompute effective = configured ∩ chosen).
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
is open to ALL callers. Shipped exemplars:
`agent_capabilities`/`agent_selection_update` (entrypoint base) and
`memories_widget_preferences`/`memories_widget_preferences_update`.

## 5. UI round-trip

Read once (lazy, on surface open); keep edits as a local draft; save only on an
explicit user command; send the exact scope plus only the changed fields; then
reconcile from the returned clamped record. The chat composer uses
`conversation_id` and exposes **Save changes**. A chat-originated
`capabilities.open` command carries that same id into the served widget. An
independently mounted Capabilities widget omits it and therefore edits the
user baseline from which future conversations start. Never switch those
scopes silently in a host UI.

The composer "+" menu and the memories widget are the two shipped
round-trips; the chat engine's capabilities branch
(`loadAgentCapabilities` / `updateAgentSelection` /
`saveAgentSelectionChanges`) is the client-side pattern to copy.

## 6. Apply per turn, fail open

Read the exact scoped record fresh at the runtime application point and treat
every failure as "use the configured behavior":

```python
try:
    settings = await store.get_settings(user_id=..., bundle_id=...)
except Exception:
    settings = {}   # configured defaults; the turn always proceeds
```

Shipped application points: `BaseWorkflow.apply_user_agent_selection` (agent
selection: narrowing, model pick, cold-cache policy) and the memory
announce/tools honoring `memory_enabled` + `memory_scope`.
