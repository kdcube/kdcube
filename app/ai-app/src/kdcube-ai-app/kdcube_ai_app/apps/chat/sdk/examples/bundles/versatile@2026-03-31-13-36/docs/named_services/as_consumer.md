# Named Services This App Consumes

This app is a **consumer** of named-service realms it does not own. Its agent
connects to three namespaces and operates each through the generic
`named_services.*` tools. This page describes which namespaces are connected and
how, against this app's real `config/bundles.template.yaml`.

## How `as_consumer` works

Under `surfaces.as_consumer.agents.main.tools`, a `kind: named_service` tool
declares the namespaces this agent connects. Declaring a namespace there does two
things:

- it grants the agent the `named_services.*` tools (search, get, schema, upsert,
  action, …) for that namespace, scoped by the namespace's `allowed` operation
  list;
- it puts the connected namespace into the agent's roster, where it appears with
  the **published intro** that the owning provider advertises.

The agent then operates each realm through that one generic tool surface — read
the realm's schema, satisfy it — regardless of domain. `event_sources` make a
namespace's refs render into the ReAct timeline and become pullable;
`ui.canvas.resolvers` let canvas/chat object cards delegate open/preview/download
actions back to the owning provider.

## The connected namespaces

The `named_service` tool (`alias: named_services`) connects three namespaces:

| Namespace | Realm | Surfaced via |
| --- | --- | --- |
| `task` | Tasks/issues owned by a task provider app | `event_sources`, canvas resolver |
| `mem` | Durable user memory, owned by the `user-memories@2026-06-26` app | `announce` hotset, `event_sources`, canvas resolver |
| `cnv` | Context boards (canvas) | (in-process canvas surface) |

Each namespace lists the operations the agent may call under `allowed`, and may
override the generic tool strategy per operation under `tool_traits`.

### `task`

Task refs are provider-owned objects. The agent can explore and mutate them
through the generic tool surface when a task provider app is present in the
runtime:

```yaml
task:
  allowed:
    - provider.about
    - object.list
    - object.search
    - object.schema
    - object.upsert
    - object.host_file
    - object.delete
```

`task` is wired into the timeline as an `event_source` (block production + pull
via the provider's `object.get`) and into `ui.canvas.resolvers` (so a task card's
open/preview/download delegate to the task provider).

### `mem`

This app is a **pure consumer** of memory. It has **no `memory:` block that owns
a store** — the `mem` provider is the `user-memories@2026-06-26` app. The agent
connects `mem` like any other realm:

```yaml
mem:
  # object.get is intentionally not exposed as a model-callable tool;
  # mem:record refs are bound with react.pull, whose pull policy calls the
  # provider's object.get and materializes an fi: artifact to read.
  announce:
    enabled: true
    limit: 8
    scope_filter: all_user_memories
  allowed:
    - provider.about
    - object.list
    - object.search
    - object.schema
    - object.upsert
    - object.action
    - object.delete
  tool_traits:
    upsert_object:
      strategy: [neutral]
    object_action:
      strategy: [neutral]
```

`object.get` is intentionally **not** in the agent-facing allow-list: `mem:record`
refs are bound with `react.pull`, whose pull policy calls the provider's
`object.get` and materializes an `fi:` workspace artifact for `react.read`. The
`upsert_object` and `object_action` traits are `neutral` for `mem`, so a memory
write may share a round with a separate completion action.

#### The hotset announce

The `[USER MEMORY HOTSET]` injection is a **consumer concern**, so it lives on the
`mem` namespace this agent consumes — at
`as_consumer.agents.main.tools[…].namespaces.mem.announce`, **not** in a
`memory:` block and **not** gated by `memory.enabled`:

```yaml
announce:
  enabled: true
  limit: 8
  scope_filter: all_user_memories
```

`enabled` turns the injection on; `limit` caps the hotset size; `scope_filter`
selects which memories are eligible (`all_user_memories` spans the user's
memories across apps). The base workflow reads this consumer-side `announce`
first and only falls back to the legacy `memory.announce.*` block when this one is
absent.

`mem` is also wired as an `event_source` (block production + pull via the
provider's `object.get`) and into `ui.canvas.resolvers` (memory cards delegate
open/preview/download to the memory provider).

### `cnv`

Canvas is exposed to the agent as a normal namespace realm. The provider schema
explains the board/card mutation object kinds (e.g. `canvas.card`,
`canvas.card.comment`, `canvas.card.replacement`, `canvas.operation_batch`):

```yaml
cnv:
  allowed:
    - provider.about
    - object.list
    - object.search
    - object.schema
    - object.upsert
  tool_traits:
    upsert_object:
      strategy: [exploitation]
```

## Where each surface is declared

```text
surfaces.as_consumer.agents.main.tools[named_service].namespaces  # task, mem, cnv
surfaces.as_consumer.agents.main.event_sources                    # task, mem
surfaces.as_consumer.ui.canvas.resolvers                          # mem, task
```

For the generic tool set, the `tool_traits` strategies, and the schema-driven
mutation dialect that applies to every namespace here, see the SDK named-service
and ontologic-tools docs.
