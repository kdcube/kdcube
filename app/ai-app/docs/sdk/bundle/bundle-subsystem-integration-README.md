---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
title: "Bundle Subsystem Integration"
summary: "Concrete checklist for mounting reusable SDK subsystems inside a bundle: entrypoint mixins, APIs, widgets, tools, event policies, object resolvers, named service providers, config, visibility, storage, and runtime verification."
tags: ["sdk", "bundle", "subsystem", "integration", "memory", "canvas", "widgets", "tools", "events", "resolvers", "named-service-provider"]
keywords:
  [
    "bundle subsystem integration",
    "integrate SDK subsystem in bundle",
    "memory subsystem bundle integration",
    "canvas subsystem bundle integration",
    "subsystem widget visibility",
    "SDK component mounting checklist",
    "entrypoint mixin widget api tools events resolvers",
  ]
updated_at: 2026-06-08
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
---
# Bundle Subsystem Integration

Use this page when a bundle mounts an existing SDK subsystem such as memory,
canvas, tasks, Telegram, delivery, or a future reusable component.

A subsystem is not only a widget. It is a vertical slice:

```text
entrypoint class/mixin
  -> decorated APIs/widgets/jobs/Data Bus handlers
  -> configuration defaults and deployment overrides
  -> optional static UI source
  -> optional agent tools and skills
  -> optional event-source policies and resolvers
  -> optional named service provider/client surfaces
  -> storage/schema/user-scope hooks
  -> runtime verification
```

If one layer is missing, the integration can appear mounted but fail at runtime.
Examples: the widget icon appears but route visibility rejects the user; a
shared UI builds but its APIs are not exposed; a canvas pin renders but no
resolver can open/download the object; ReAct has a tool but no instructions or
event policies explain the object model.

## The Rule

Mount a reusable subsystem by wiring the whole subsystem contract. Do not copy
one file or one widget and assume the rest follows.

For each subsystem, answer these questions:

| Layer | Required Question | Failure If Skipped |
| --- | --- | --- |
| Entrypoint | Which mixin/base/decorators declare the subsystem surfaces? | Widget/API/job/handler is invisible to the platform manifest. |
| Config | Which `configuration_defaults()` keys enable the subsystem and its surfaces? | Defaults disable the subsystem even though code is imported. |
| Visibility | Which `visibility.*`, `enabled.*`, and decorator defaults control access? | Route returns "not visible to this user" or appears for the wrong users. |
| UI source | Is this a method-rendered widget, source-folder widget, or shared SDK source? | Widget route returns placeholder JSON/HTML or blank static assets. |
| APIs | Which operation aliases does the UI call? Are those aliases declared by the subsystem? | Widget loads but all operations fail with undefined/hidden operation. |
| Agent tools | Which tool modules must be included in `tools_descriptor.py`? | Agent sees context but cannot act on it. |
| Instructions/skills | Which stable instructions and skills describe the subsystem object model? | Agent guesses wrong operations or edits the wrong owner. |
| Event policies | Which event source modules render timeline/ANNOUNCE/compaction blocks? | Context is lost or appears as generic JSON. |
| Namespace rehosters | Which namespace rehosters are registered for `react.pull` on owner refs such as `mem:` or `cnv:`? | Agent sees refs but cannot import exact content into its workspace. |
| Resolvers | Which namespace/object resolvers are registered, and who owns each namespace? | Pins/refs are visible but cannot preview/open/download/rehost. |
| Named service provider | Does the subsystem expose `provider.about`, capabilities, object operations, actions, relations, and transport adapters through one provider/client contract? | Widgets, agents, canvas, and scene hosts hardcode bundle-specific routes and duplicate ownership logic. |
| Storage | Which store/schema/user-scope hooks does the subsystem need? | State is lost, cross-user data leaks, or first request fails schema checks. |
| Runtime identity | Which shared helper supplies tenant/project/user/fingerprint for REST, Data Bus, tools, and jobs? | One transport works while another writes to `default/default`, fails store creation, or loses user scope. |
| Transport | Does UI use REST operations, Data Bus, comm stream, or all three? | UI mutations hang, duplicate, or never reach the owning bundle. |
| Tests | Which manifest, route, UI, tool, event, and resolver checks prove it is live? | Integration appears done but fails during real use. |

## Composition Bundle

A composition bundle is the bundle that mounts several subsystems and presents
them as one product. It owns composition, not subsystem semantics.

Example:

```text
task-tracker@1-0 composition bundle
  memory subsystem        SDK memory owns mem: storage, APIs, widget, tools
  canvas subsystem        SDK canvas owns board storage, canvas tools, canvas UI
  issue subsystem         bundle/task subsystem owns task: issues and issue UI
  ReAct artifact layer    SDK ReAct owns fi: artifacts and file resolver behavior
```

The composition bundle should:

- inherit or mix in the subsystem entrypoint classes that declare surfaces
- set safe `configuration_defaults()`
- add deployment-visible config/secrets templates when operators must choose
  values
- include SDK tool/event/skill modules by descriptor
- register object resolvers from the subsystem that owns each namespace
- register named service providers when the subsystem is meant to be called by
  canvas, chat, widgets, agents, MCP clients, scheduled jobs, or other bundles
- assemble UI widgets and main views from subsystem UI components
- keep product policy local, such as "which widgets are shown in this bundle"
  and "which agent ids are allowed"

The composition bundle should not:

- reimplement the memory widget API if the memory mixin already declares it
- teach canvas how to download `fi:` or open `task:` objects directly
- duplicate subsystem event rendering policies in unrelated bundle files
- hide inherited widgets with restrictive visibility defaults unless that is an
  explicit product policy

## Integration Checklist

### 1. Entrypoint Surface

Use the subsystem entrypoint/mixin that declares the platform surfaces.

Memory example:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithMemory,
)


class MyEntrypoint(BaseEntrypointWithMemory):
    ...
```

The memory mixin contributes:

- `@ui_widget(alias="memories", ...)`
- `memories_widget_*` operations
- memory preferences, reconciliation, snapshots, and maintenance APIs
- memory configuration defaults
- memory job handling hooks

Canvas example:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.api import canvas_api
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools import CanvasToolsPlugin
```

Canvas currently uses bundle-provided API methods that call SDK helpers and a
SDK tool module included from `tools_descriptor.py`. The reusable surface should
stay in `kdcube_ai_app.apps.chat.sdk.solutions.canvas`; product-specific issue
or task semantics stay in the product subsystem.

Canvas storage must be created with request-bound identity from the shared
entrypoint/runtime helper, not with a bundle-private copy. Use
`self.runtime_identity()` from `BaseEntrypoint` for normal REST/widget/tool
calls. For Data Bus handlers, also copy the `DataBusContext` scope into the
operation payload before calling the same SDK helper:

```python
async def handle_canvas_patch_data_bus(self, ctx, message):
    payload = dict(message.payload or {})
    payload.setdefault("tenant", ctx.tenant)
    payload.setdefault("project", ctx.project)
    actor = dict(message.actor or {})
    if actor.get("user_id"):
        payload.setdefault("user_id", actor["user_id"])
    return self._apply_canvas_patch_payload(payload)
```

This is required because Data Bus messages carry tenant/project as stream
metadata, while the actor object carries user fields. Treat both as part of the
canvas storage scope.

### 2. Configuration Defaults

Every mounted subsystem needs explicit code defaults. Do not rely on ad hoc
descriptor state from one local runtime.

Memory example:

```python
def configuration_defaults(self):
    return {
        "memory": {
            "enabled": True,
            "announce": {"enabled": True, "scope_filter": "current_bundle"},
            "tools": {"enabled": True, "allow_write": True},
            "widget": {"enabled": True, "allow_write": True},
        },
        "ui": {
            "widgets": {
                "memories": {
                    "enabled": True,
                    "src_folder": "sdk://context/memory/ui/widget/memories",
                    "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                },
            },
        },
    }
```

When using an inherited widget, the alias must match the inherited
`@ui_widget(alias=...)`. Config under `ui.widgets.memories` does not create a
new widget; it only tells the platform how to build/serve the already declared
`memories` widget surface.

### 3. Visibility And Enablement

There are three different gates. Check all three.

| Gate | Path | Meaning |
| --- | --- | --- |
| Surface enablement | `enabled.widget.<alias>` | Hides the widget surface entirely. |
| Static app enablement | `ui.widgets.<alias>.enabled` | Enables the source-folder static widget app. |
| Route visibility | `visibility.widget.<alias>.user_types` and `.roles` | Controls who can fetch the widget route. |

`user_types: []` means no user-type restriction. A non-empty list restricts the
route. If a decorator has `user_types_config="visibility.widget.memories.user_types"`,
then this config overrides the decorator default whenever present.

Do not set widget visibility by copying an API visibility list. The widget
iframe route and the widget operations can have different visibility rules.
For example, a widget may be visible to any authenticated user while individual
write APIs remain restricted by their own decorators or bundle policy.

Bad pattern:

```yaml
visibility:
  widget:
    memories:
      user_types: ["registered", "paid", "privileged"]
```

This fails if the current session user type is not one of those exact SDK user
types, even if the user has an admin role.

Safer authenticated-bundle widget pattern:

```yaml
visibility:
  widget:
    memories:
      user_types: []
```

Use `roles` only for an explicit product restriction, and verify the session
contains the same `kdcube:role:...` value.

### 4. Static UI Source

A widget can be served three ways:

| Shape | Required Config | Use When |
| --- | --- | --- |
| Method-rendered widget | `@ui_widget(alias=...)` only | Small placeholder or legacy HTML. |
| Source-folder widget | `@ui_widget(alias=...)` plus `ui.widgets.<alias>.src_folder` | Bundle-owned widget app. |
| SDK shared/source widget | `@ui_widget(alias=...)` plus `sdk://...` source or `shared_sources` | Reuse an SDK widget source inside this bundle. |

For source-folder widgets:

- Vite/HTML assets must use relative paths, normally `base: './'`
- widget code must get `baseUrl`, tenant, project, bundle id, and auth from the
  runtime config bridge
- widget operation URLs must target the KDCube frame/runtime origin, not the
  embedding host page

### 5. API Surface Used By UI

Before adding wrapper APIs, check whether the subsystem already declares the
operations its widget uses.

Memory widget examples:

```text
memories_widget_data
memories_widget_get
memories_widget_create
memories_widget_update
memories_widget_delete
memories_widget_preferences
memories_widget_reconcile_*
```

If the subsystem declares those APIs through a mixin, the consuming bundle
should inherit the mixin/base and configure it. Do not duplicate wrappers in
the bundle unless the subsystem intentionally exposes extension hooks instead
of concrete APIs.

If a wrapper is necessary, it must:

- be declared on the entrypoint with `@api(...)`
- use request-bound identity from the runtime context
- forward to the subsystem service without bypassing its auth/scope checks
- keep the same payload and error shape expected by the reused UI component

### 6. Agent Tools, Skills, And Instructions

A UI subsystem and an agent subsystem are separate integrations. If the agent
should use the subsystem, mount the agent surface too.

Checklist:

- add SDK tool modules in `tools_descriptor.py`
- add local or SDK skills in `skills_descriptor.py`
- add stable additional instructions from the subsystem, not ad hoc prompt text
- keep mutable per-turn data in ANNOUNCE/timeline policies, not cached system
  instructions
- document which tool operations are allowed for each object type

The connection pattern for reusable subsystem instructions is explicit:

1. Import the SDK instruction from the subsystem module, for example
   `kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions`.
2. Compose that stable instruction with the bundle/domain instruction.
3. Pass the result as `additional_instructions` to ReAct construction.
4. Register tools, event policies, skills, and resolvers separately. The
   instruction explains semantics; it does not expose `canvas.patch`, render
   `[CANVAS BOARD]`, or register `event.canvas.focus` by itself.

Canvas example:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions import (
    CANVAS_REACT_ADDITIONAL_INSTRUCTIONS,
)

TOOLS_SPECS = [
    {
        "module": "kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools",
        "class": "CanvasToolsPlugin",
    },
]

ADDITIONAL_INSTRUCTIONS = (
    PRODUCT_REACT_ADDITIONAL_INSTRUCTIONS
    + "\n\n"
    + CANVAS_REACT_ADDITIONAL_INSTRUCTIONS
)
```

The canvas tool documentation should describe canvas collaboration. It should
not mention product-specific task operations. Task operations belong to task
tools and task instructions.

### 7. Events, Policies, And ANNOUNCE

If the subsystem creates external events or tool results that ReAct should
understand, mount the policy module that renders them.

Do not conflate tools and events.

| Surface | Descriptor | Meaning |
| --- | --- | --- |
| Tool visibility | `tools_descriptor.py` / `TOOLS_SPECS` | Callable functions the model may invoke. |
| Event visibility | `events_descriptor.py` / `EVENT_SOURCE_SPECS` | Event sources, policies, event-source readers, and namespace rehosters the runtime may use. |

These surfaces are cumulative. Tool modules are also scanned for their own
event declarations because tool calls produce events. Event-only modules are
added through `EVENT_SOURCE_SPECS`; they do not replace tool module events and
they do not expose new model-callable tools.

If one Python module contains both callable tools and event decorators, choose
the descriptor based on the intended surface:

- list it in `TOOLS_SPECS` only when the model should be able to call its
  tools;
- list it in `EVENT_SOURCE_SPECS` when the runtime only needs its event
  declarations, policies, readers, or rehosters.

Reusable SDK subsystems should prefer a clean split: callable tools in a tool
module, and owner-domain event readers/rehosters/policies in an event module.
For example, a bundle can mount the canvas `cnv:` namespace rehoster through
`events_descriptor.py` without exposing `canvas.patch` as an agent tool.

For each event-producing subsystem, define:

| Policy | Purpose |
| --- | --- |
| timeline/block projection | Compact, durable fact on the timeline. |
| ANNOUNCE projection | Current live context for the active turn. |
| compaction projection | What survives pruning. |
| resolver/rehoster | How refs from that subsystem are previewed, acted on, or materialized into the ReAct workspace. |

Do not dump raw subsystem JSON by default. The policy should render the object
model that the agent needs to act: ids, refs, status, revision, selected/focused
state, and a bounded preview.

If the subsystem owns a logical namespace whose exact payload can be imported
into the ReAct workspace, register a namespace rehoster:

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster

@artifact_namespace_rehoster(
    namespace="mem",
    resolver_name="{alias}.memory_rehoster",
)
async def rehost_memory_ref(*, uri, **context):
    ...
```

The rehoster resolves the owner-domain object and returns a materialized
workspace artifact row. The model calls `react.pull(paths=["mem:..."])`; after
that it reads or searches the returned `fi:`/physical workspace path. It does
not call `memory.read_memory(...)` unless that function is also explicitly
exposed as a normal tool.

Use `kind="react.event_source_reader"` for runtime/policy read sources that are
not direct model tools. Use `kind="react.tool"` only for actual tool-call
sources, such as `canvas.patch`.

Example:

```python
# events_descriptor.py
EVENT_SOURCE_SPECS = [
    {
        "module": "kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver",
        "alias": "canvas",
    },
]
```

```python
# orchestrator/workflow.py
from .. import events_descriptor, tools_descriptor

react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tools_descriptor.TOOLS_SPECS,
    event_source_specs=events_descriptor.EVENT_SOURCE_SPECS,
)
```

This makes `cnv:` refs importable through `react.pull` when the canvas resolver
is registered. It does not make `canvas.patch` callable unless the canvas tool
module is also listed in `TOOLS_SPECS`.

### 8. Object Resolvers

Resolvers belong to the subsystem that owns the object namespace.

| Namespace | Owner | Resolver Belongs In |
| --- | --- | --- |
| `mem:` | memory module | `sdk/context/memory/events/resolver.py` |
| `fi:` | ReAct artifact/event layer | `sdk/solutions/react/events/resolver.py` |
| `task:` | task/issue subsystem | the task subsystem package |
| `cnv:` | canvas module | `sdk/solutions/canvas/events/resolver.py` |
| provider-defined refs | named-service provider, MCP/search surface, or explicit rehoster | the owning subsystem package |

Canvas cards store only the canonical object ref. They do not store download
URLs, `rn:` handles, or transport-specific second links. When the user clicks
Open/Preview/Download/Rehost, canvas asks the registered resolver for that
namespace.

**Owned vs foreign namespaces.** Write a concrete resolver only for namespaces
your bundle owns. For a namespace **another bundle** owns (e.g. a composition
bundle showing `task:` pins from a task bundle), do **not** write a resolver —
declare `named_services.namespaces.<ns>.provider` and let the generic
namespace-service resolver call the owner over the in-runtime bridge. Discovery
is configured, not automatic. See
[Namespace Services](../namespace-services/README.md) for the provider/consumer
contract.

### 9. Storage And Schema

Each subsystem owns its storage:

- memory owns `user_memory_*` Postgres tables
- canvas owns canvas board/revision/card-content storage
- task subsystem owns task records and task-owned attachments
- ReAct owns `fi:` turn artifacts

The composition bundle should configure stores and call schema provisioning
through subsystem hooks. It should not share one artifact namespace across
unrelated subsystems just because both store files.

### 10. Transport

Choose transport by interaction shape:

| Interaction | Transport |
| --- | --- |
| direct widget command with immediate response | bundle `/operations/...` API |
| durable subsystem mutation with worker handling | Data Bus `data_bus.publish` |
| progress/result event back to current browser peer | request-bound comm stream with `KDC-Stream-ID` |
| conversation user prompt and attached context | chat submit with `external_events[]` |
| cross-widget open/focus request | subsystem resolver emits Data Bus or comm UI event |

Do not introduce raw WebSocket/SSE endpoints inside a subsystem. Use the shared
platform transports.

## Memory Integration Example

Minimal composition:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithMemory,
)


class MyBundle(BaseEntrypointWithMemory):
    def configuration_defaults(self):
        return {
            "memory": {
                "enabled": True,
                "announce": {"enabled": True, "scope_filter": "current_bundle"},
                "tools": {"enabled": True, "allow_write": True},
                "widget": {"enabled": True, "allow_write": True},
            },
            "visibility": {
                "widget": {
                    "memories": {"user_types": []},
                },
            },
            "ui": {
                "widgets": {
                    "memories": {
                        "enabled": True,
                        "src_folder": "sdk://context/memory/ui/widget/memories",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                },
            },
        }
```

Validation:

- bundle manifest lists widget alias `memories`
- effective props contain `memory.enabled=true`,
  `memory.widget.enabled=true`, and `ui.widgets.memories.enabled=true`
- widget route does not return "not visible to this user"
- widget operations such as `memories_widget_data` are visible to the intended
  users
- memory store schema exists
- drag payloads use `mem:` refs when memory items are moved to chat/canvas
- ReAct memory tools are present only when `memory.tools.enabled=true`

## Canvas Integration Example

Canvas is a reusable board solution. It needs both SDK mechanics and resolver
registration from object-owning subsystems.

Composition responsibilities:

```text
entrypoint.py
  - expose canvas API routes by calling SDK canvas helpers
  - register Data Bus handler for canvas patch subject
  - register object resolvers from task, memory, fi, and canvas-owned modules

tools_descriptor.py
  - include SDK canvas tool module

events_descriptor.py
  - include SDK canvas event policy module

configuration_defaults()
  - configure canvas storage/event names
  - configure ui/main or widget shared source for canvas component
```

The task subsystem should provide the `task:` resolver. The memory subsystem
should provide the `mem:` resolver. The ReAct event/artifact layer should
provide the `fi:` resolver. Canvas should not implement those semantics.

Validation:

- canvas board loads and stores revisions
- card pins keep canonical refs (`fi:`, `mem:`, `task:`, canvas-owned refs)
- board refs use the canvas namespace, for example `cnv:main@27`; exact board
  content is imported with `react.pull(paths=["cnv:main@27"])`, then inspected
  through the returned workspace path
- object action calls route through resolver registry
- unknown refs stay pinned but expose no owner-specific actions
- ReAct timeline contains compact canvas facts and ANNOUNCE contains current
  board/focused context
- `canvas.patch` is the only model-visible canvas write tool; exact board reads
  are materialized by the `cnv:` namespace rehoster behind `react.pull`
- `canvas.patch` uses base revision and returns a new revision fact

## Common Failure Modes

| Symptom | Likely Cause | Check |
| --- | --- | --- |
| `Bundle widget memories is not visible to this user` | `visibility.widget.memories.user_types` or `.roles` excludes the session | Effective props, widget decorator config path, session user type/roles |
| Widget route returns placeholder JSON/HTML | `ui.widgets.<alias>` missing or disabled; static app not configured | Effective `ui.widgets`, widget build logs |
| Widget icon missing | `@ui_widget` surface missing, `enabled.widget.<alias>: false`, or bundle manifest stale | Manifest widgets, bundle reload |
| Widget loads but API calls fail | UI operation aliases not declared or hidden by API visibility | Manifest API endpoints and effective props |
| Agent sees context but cannot act | Tool module or skill missing | `tools_descriptor.py`, `skills_descriptor.py`, tool catalog |
| Timeline shows raw JSON | Event policy module not loaded or wrong `event_source_id` | `events_descriptor.py`, event policy ids |
| Canvas pin cannot open/download | Resolver for the ref namespace not registered | Resolver registry, canonical `object_ref` namespace |
| Same SDK component works in one bundle but not another | One bundle copied UI/config but not entrypoint/API/tool/event/resolver layers | Run this checklist layer by layer |

## Required Verification Before Claiming Done

For every mounted subsystem, verify at least:

1. manifest includes expected widgets/APIs/handlers
2. effective props enable the subsystem, widget, and UI source
3. visibility gates match intended users
4. widget route loads the static app, not a placeholder
5. widget operation aliases exist and return expected JSON
6. tools appear in the active agent catalog when enabled
7. event policies render expected timeline/ANNOUNCE blocks
8. resolver actions work for each namespace the subsystem claims
9. storage/schema provisioning runs at `on_bundle_load` or first safe use
10. reload path is documented: source edit vs descriptor/config edit vs
    platform source edit

Use this doc together with:

- [Bundle Entrypoint Classes](bundle-entrypoint-classes-README.md)
- [Bundle Widget Integration](bundle-widget-integration-README.md)
- [UI Components Lifecycle](ui-components-lifecycle-README.md)
- [Bundle Agent Integration](bundle-agent-integration-README.md)
- [Bundle Events](bundle-events-README.md)
- [Bundle Platform Integration](bundle-platform-integration-README.md)
- [Bundle Properties And Secrets Lifecycle](bundle-properties-and-secrets-lifecycle-README.md)
