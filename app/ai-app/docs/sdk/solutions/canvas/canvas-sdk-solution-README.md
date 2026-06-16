---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
title: "Canvas SDK Solution"
summary: "Reusable collaborative canvas component for KDCube bundles: versioned board storage, object resolver registry, ReAct instructions/tool core, and embeddable UI component source."
status: draft
tags: ["sdk", "solutions", "canvas", "react", "events", "resolvers", "ui-components", "data-bus"]
updated_at: 2026-06-14
keywords:
  [
    "canvas sdk",
    "canvas resolver",
    "canvas patch",
    "canvas board",
    "object proxy",
    "composition bundle",
    "canvas ui component",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-module-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Canvas SDK Solution

`kdcube_ai_app.apps.chat.sdk.solutions.canvas` is the reusable collaborative
board component for bundles that need a live spatial working surface. The
canvas stores pins/cards, annotations, layout, trash state, and revisions. The
object represented by a pin remains owned by its original subsystem.

The SDK owns canvas mechanics. The composition bundle owns product routes,
auth, mounted widgets, and subsystem resolver registration.

For the bundle-level mounting checklist, read
[Bundle Subsystem Integration](../../bundle/bundle-subsystem-integration-README.md).
Canvas integration is a full subsystem integration: API routes, Data Bus
handler, UI component source, tools, instructions, event policies, resolver
registry, storage config, visibility, and tests must be wired together.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.solutions.canvas
  storage.py              Versioned canvas documents, card content hosting,
                          projection, retention, and UI event envelopes
  api.py                  Bundle route/operation helpers for read, write,
                          patch, upload, search, pin read, and object action
  tools.py                Reusable Semantic Kernel ReAct write-tool module and
                          cnv: event-source reader; include it through
                          surfaces.as_consumer when the model should call
                          canvas.patch
  tools_core.py           Reusable canvas read/patch execution core
  instructions.py         Stable additional ReAct instructions for canvas
  ids.py                  Timestamp-bearing canvas/message id helpers
  storage_utils.py        Storage-safe segment helper
  events/resolver.py      Canvas object resolver registry and built-in
                          canvas-owned artifact resolver/handoff resolvers
  events/policies.py      Reusable canvas timeline/compaction/ANNOUNCE policy
                          helpers and generic canvas policy ids
  events/focus_policies.py
                          Reusable focused-card context projection helpers and
                          generic canvas focus policy ids
  search/                 Generic pin-board search: CanvasPinSearch service +
                          card→Document mapping over the SQLite+vector hybrid
                          index; index-on-update, read-only economical search
  ui/component/src        Reusable React canvas board component source
```

`search/` is a reusable mechanism, not bundle-specific: any bundle that mounts the
canvas gets pin search by constructing `CanvasPinSearch(entrypoint)` and calling
`index`/`clear` on canvas update and `search` on query. It derives the embedder and
the economical guard from the host entrypoint. See
[Pin Integration → Pin Search](./pin-integration-README.md#pin-search).

The SDK package does not know provider object semantics, memory semantics,
or ReAct artifact download transport details. It only stores canonical refs and
routes object actions through registered resolvers. Bundles configure storage
names, event ids, and Data Bus subject names through `bundle_props.canvas`; the
tool implementation remains in the SDK.

## Storage Model

A canvas document is a versioned board state:

```json
{
  "schema": "kdcube.canvas.v1",
  "canvas_id": "cnv:<user-or-scope>:main",
  "canvas_name": "main",
  "revision": 17,
  "bounds": {"x": 0, "y": 0, "w": 1680, "h": 1080},
  "cards": [
    {
      "id": "U_2026-06-08-09-10-11_ab12",
      "kind": "user.text",
      "title": "Observation",
      "logical_path": "cnv:<canvas-owned-object>",
      "rect": {"x": 40, "y": 40, "w": 238, "h": 112}
    },
    {
      "id": "O_2026-06-08-09-12-02_cd34",
      "kind": "object.ref",
      "logical_path": "acme:ticket:ticket_2026-06-08-09-01-20",
      "rect": {"x": 320, "y": 120, "w": 260, "h": 120}
    }
  ]
}
```

The card identity for canvas editing is `id`. The represented object identity
is the canonical ref, normally in `logical_path`, `ref`, or `object_ref`
depending on the client payload. A subsystem object keeps its original
namespace when pinned:

| Object | Canonical ref kept by canvas | Owner |
| --- | --- | --- |
| ReAct/chat artifact | `fi:conv_<conversation>/turn_.../outputs/file.md` | ReAct artifact layer |
| Memory | `mem:record:<memory-id>` | Memory named-service provider |
| Provider object | `acme:ticket:<object-id>` | Provider subsystem |
| Canvas user text/upload | `cnv:<canvas-object>` | Canvas storage owner |
| Knowledge/source row | `repo:<repo>/<path>` or `so:<source>` | Knowledge/source subsystem |

Canvas does not rehost external objects just because they are pinned. Rehosting
is an explicit action owned by the target subsystem, for example attaching a
chat file to a provider-owned object.

## Configuring Storage

The SDK `CanvasStore` is configurable so a composition bundle can preserve its
existing storage and event names while using SDK mechanics:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.storage import CanvasStore

store = CanvasStore.from_scope(
    scope,
    bundle_id="my-bundle@1-0",
    artifact_prefix="canvas",
    origin_prefix="my_bundle.canvas",
    state_event_source_id="my_bundle.canvas.state",
    ui_event_type="my_bundle.canvas.patch.applied",
    artifact_resolver_name="sdk.canvas.artifact_storage",
    handoff_resolver_names={
        "acme": "my_bundle.provider_objects",
    },
)
```

Use `handoff_resolver_names` only to name resolvers owned elsewhere. It is not
a place to implement object semantics.

The scope passed to `CanvasStore` must come from the shared runtime identity
contract. In entrypoint methods that run from REST/widget/tool calls, use
`BaseEntrypoint.runtime_identity()` and pass the resulting tenant/project/user
into the store. In Data Bus handlers, do not assume `comm_context` has the
full chat-turn shape; copy `ctx.tenant`, `ctx.project`, and actor user fields
from the Data Bus message into the canvas operation payload before creating the
store. Missing this step can make a canvas patch fail with a store/identity
error or write under the wrong tenant/project scope.

## Resolver Ownership

Canvas has a resolver registry because pins are object proxies. The resolver
for a ref namespace owns object actions:

```text
canvas.object_action(ref="acme:ticket:...", action="open")
  -> namespace_for_ref("acme:ticket:...") == "acme"
  -> owner_key_from_ref("acme:ticket:...") == "acme:ticket"
  -> provider resolver handles open
  -> provider widget decides dirty-state behavior
```

The first URI segment before `:` is the routing namespace. Everything before
the last `:` is the provider-owned owner key/subnamespace. Canvas may display
that owner key, but it dispatches resolver calls by the root namespace only.
That is why `mem:record:<id>` routes through the configured `mem` resolver and
`task:issue:<id>` routes through the configured `task` resolver.

Core contract:

| Canvas responsibility | Resolver responsibility |
| --- | --- |
| Store card rect, title cache, description, comments, trash state, revision. | Interpret the canonical object ref. |
| Call `resolver.object_action(...)` for preview/open/download/rehost. | Return bounded preview, UI event, download transport, or rehost result. |
| Keep unknown refs as valid unresolved pins. | Register later and make those pins live. |
| Render board map/legend for ReAct through canvas policies. | Provide domain-specific representation for the underlying object. |

Built-in SDK resolver support:

| Resolver | Namespace | Meaning |
| --- | --- | --- |
| `CanvasArtifactResolver` | `cnv:` | Reads canvas-owned artifact refs from canvas artifact storage. |
| `NamespaceHandoffResolver` | any | Declares that another subsystem owns the namespace. |
| `NamedServiceCanvasObjectResolver` | configured | Calls the namespace-owning bundle's named-service API endpoint. |
| `CanvasObjectResolverRegistry` | all | Dispatches actions to registered resolvers by namespace. |

Composition bundles can register named-service resolvers from bundle config:

```yaml
named_services:
  namespaces:
    acme:
      provider:
        bundle_id: acme-provider@1-0
        provider: acme.ticket
        operation: named_service
```

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    register_configured_named_service_canvas_resolvers,
)

register_configured_named_service_canvas_resolvers(
    registry,
    namespaces=self.bundle_prop("named_services.namespaces", {}) or {},
    tenant=tenant,
    project=project,
    logger=_log,
)
```

The helper registers `NamedServiceCanvasObjectResolver` for each configured
namespace. The resolver calls the owning bundle operation through the
request-bound local operation bridge, so canvas and chat object actions share
the same current user/session context. Canvas does not duplicate owner
implementations.

## ReAct Integration

Canvas contributes stable additional instructions:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.instructions import (
    CANVAS_REACT_ADDITIONAL_INSTRUCTIONS,
)
```

The instructions explain:

- `[CANVAS BOARD]` ANNOUNCE is live board state.
- `[CANVAS FOCUSED CONTEXT]` is turn-local selected/multi-selected card
  context for batch operations on the attached board.
- Individual pins dragged from canvas into chat render as their proxied objects
  (`acme:`, `mem:record:<id>`, `fi:`, `cnv:`, etc.), not as canvas-focus
  events.
- `react.pull(paths=["cnv:<name>@<revision>"])` imports exact hidden board
  state into the ReAct workspace; inspect the returned `fi:` or physical path.
- `canvas.patch` is the only agent write path.
- Agents do not move or resize existing cards.
- Proxy card underlying objects stay owned by their source subsystem.

`tools_core.py` is the reusable implementation behind ReAct tools:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools_core import (
    patch_canvas_for_agent,
    read_canvas_for_agent,
)
```

The SDK tool module exposes one direct model tool and one namespace rehoster:

| Source id | `kind` | How the model uses it |
| --- | --- | --- |
| `canvas.patch` | `react.tool` | Calls `canvas.patch(...)` to create a new content revision. |
| `cnv` rehoster | `artifact_namespace_rehoster` | Calls `react.pull(paths=["cnv:<name>@<revision>"])`; the rehoster materializes exact canvas state/content as returned `fi:` artifacts. |

A bundle still defines its own SK plugin class and event-source policy ids when
it wraps the SDK core directly:

```python
class CanvasTools:
    @event_source(event_source_id="{alias}.patch", policies=[...])
    @kernel_function(name="patch", description="...")
    async def patch(...):
        result = await patch_canvas_for_agent(
            tool_scope=scope(),
            store=CanvasStore.from_scope(scope(), bundle_id=BUNDLE_ID),
            bundle_id=BUNDLE_ID,
            data_bus_subject="my_bundle.canvas.patch",
            operations=operations,
            canvas_name=canvas_name,
            base_revision=base_revision,
            event_agent_id="main",
            event_surface="canvas",
        )
        ...
```

This split keeps SDK mechanics reusable while allowing each bundle to choose
its tool alias, policy ids, and agent-facing wording.

## Event Policy Integration

Canvas contributes generic ReAct projection helpers:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.policies import (
    append_canvas_tool_fact_block,
    produce_canvas_announce_blocks,
    project_canvas_state_blocks,
    project_canvas_tool_result_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.focus_policies import (
    produce_canvas_focus_announce_blocks,
    project_canvas_focus_blocks,
)
```

The SDK also registers default generic policy ids such as
`canvas.timeline_projection.state`, `canvas.announce.board_map`, and
`canvas.announce.focus`. A composition bundle can use those ids directly, or it
can keep bundle-specific ids and wrap the helpers:

```python
@timeline_projection_policy(event_policy_id="my_bundle.timeline_projection.canvas_state")
def my_canvas_state_policy(timeline, *, source, react_phase="timeline_projection", **kwargs):
    return project_canvas_state_blocks(
        timeline,
        source=source,
        react_phase=react_phase,
        policy_prefix="my_bundle",
        default_event_source_id="my_bundle.canvas.state",
        canvas_meta_keys=("my_bundle_canvas", "canvas"),
        **kwargs,
    )
```

This is the intended SDK boundary:

| Layer | Owns |
| --- | --- |
| Canvas SDK policies | Text shape for `[CANVAS STATE]`, `[CANVAS BOARD]`, `[CANVAS FOCUS]`, and `[CANVAS FOCUSED CONTEXT]`. Focus means selected/multi-selected cards on a board. |
| Composition bundle wrappers | Event-source ids, event-policy ids, storage prefixes, and compatibility metadata keys. |
| Domain resolvers | Object-specific previews, open/download/rehost actions for refs such as `acme:`, `mem:record:<id>`, `fi:`, and `repo:`. |

## Data Bus Integration

`patch_canvas_for_agent(...)` first tries `comm.data_bus.publish_and_wait(...)`
when the runtime provides it. If no data-bus publisher exists, it falls back to
direct `store.patch(...)`. The subject is supplied by the composition bundle:

```text
subject = "<bundle>.canvas.patch"
object_ref = "cnv:<scope>:<name>"
```

The bundle-side data-bus handler must apply the same SDK `api.patch(...)` or
`store.patch(...)` path so UI, tools, and REST operations create the same
revision shape.

## UI Component

The SDK ships source for a reusable React component:

```text
sdk/solutions/canvas/ui/component/src
  CanvasBoard.tsx
  canvasModel.ts
  canvasTypes.ts
  contextTypes.ts
  ingress.ts
  ingressBridge.ts
```

The component expects bundle-provided operations:

```ts
type CanvasRead = (payload: CanvasReadInput) => Promise<CanvasReadResponse>
type CanvasPatch = (payload: CanvasPatchInput) => Promise<CanvasPatchResponse>

<CanvasBoard
  canvas={canvas}
  selectedCanvasName="main"
  patchCanvas={patchCanvas}
  readCanvas={readCanvas}
  onAttachCanvas={...}
  onAttachCard={...}
  onDropContext={...}
  namespaceStyles={...}
  infoHtml={...}
/>
```

The UI component does not know how to download a `fi:` file or open an
`acme:` object. It sends object actions to the bundle, and the bundle dispatches
through the resolver registry.

`namespaceStyles` is presentation metadata keyed by the root namespace, not by
card type and not by subnamespace. For example, `mem:record:<id>` should use
the style configured for `mem`, while `task:issue:<id>` and
`task:attachment:<id>` should both use the style configured for `task`.
The same root-namespace style should be preserved as an object moves from
search result to chat context chip to canvas card.
In bundle configuration this metadata lives at top-level `config.namespace_styles`
so chat, canvas, and any other namespace-aware surface can consume the same
contract.

### Inline Editing

User-authored card content is edited in place, never through a browser
`prompt`/`confirm` dialog:

- creating a new `user.text` card opens a draft card directly on the board;
- a card description is edited from a pencil control next to the description
  heading and saved with a tick;
- comments use the same editor.

All three use a shared markdown editor with a Raw / Rendered switch. The
component carries a small self-contained markdown renderer (headings, bold,
italic, inline code, fenced code, links, blockquotes, and lists), so descriptions
and comments render as markdown without pulling a markdown library into the
shared source.

### Board Help (`infoHtml`)

The board exposes an info (ⓘ) control that opens an HTML help panel telling scene
users what the board is for. The `infoHtml` prop is the bundle-provided HTML.
When the host passes no `infoHtml`, the component renders a built-in default that
explains the general concept plus the canvas built-ins — user text, attachments,
and chat pins (conversations and files produced or attached in chat). A bundle
overrides it to also describe scene-specific pins such as task or memory pins.

The help HTML travels on the canvas integration surface, not a platform-wide
config endpoint. `api.list_canvases(...)` takes an `info_html` argument and echoes
it on the `canvas_list` response; the SDK stays config-agnostic, so the mounting
bundle reads its own `config.canvas.info_html` and passes it in:

```python
@api(method="POST", alias="canvas_list", route="operations", ...)
async def canvas_list(self, data=None, **kwargs):
    payload = payload_from_call(data, **kwargs)
    return self._canvas_service().operation(
        "canvas_list", payload,
        lambda *, user_id, story_id: canvas_api.list_canvases(
            store=self._canvas_store(payload, user_id=user_id),
            user_id=user_id, story_id=story_id,
            info_html=self.bundle_prop("canvas.info_html"),
        ),
    )
```

The pinboard widget reads `info_html` off the `canvas_list` response it already
calls on load and passes it through as `infoHtml`. The HTML is bundle-authored and
trusted; do not feed untrusted user input into it.

## Provider Adapter Pattern

Provider bundles should keep thin adapter modules:

```text
provider@1-0/canvas/storage.py          -> SDK CanvasStore with provider defaults
provider@1-0/canvas/api.py              -> SDK canvas API re-exports
provider@1-0/canvas/events/resolver.py  -> SDK resolver registry plus provider handoffs
provider@1-0/canvas/tools.py            -> local SK decorators, SDK tool core execution
provider@1-0/events/canvas_policies.py  -> provider ids wrapping SDK canvas policy helpers
provider@1-0/events/canvas_focus_policies.py
                                      -> provider ids wrapping SDK focus helpers
```

This lets provider routes and tests stay stable while the reusable canvas code
lives in the SDK. Provider-specific object resolvers remain in the provider
bundle because `acme:` is owned by the provider subsystem, not by canvas.

## Integration Checklist

1. Create a store adapter with bundle-specific storage/event names.
2. Register resolver handoffs and implemented resolvers for mounted domains.
3. Expose bundle operations that call SDK `api.py`.
4. Expose ReAct `canvas.*` tools using SDK `tools_core.py` and local policy ids.
5. Add `CANVAS_REACT_ADDITIONAL_INSTRUCTIONS` to the agent instructions.
6. Mount the UI component and pass read/patch/object-action functions.
7. Keep cards as canonical refs; never store transport-only download handles as
   card identity.
