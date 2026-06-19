---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
title: "Namespace Services: Providers"
summary: "Transport-neutral SDK concept for bundles and platform subsystems that publish namespace service provider surfaces: namespace ownership, object operations, resolvers, capabilities, relations, and integrations over API, MCP, Data Bus, or local adapters."
status: design
tags: ["sdk", "namespace-services", "named-service-provider", "services", "namespaces", "objects", "resolvers", "mcp", "api", "data-bus", "bundles"]
updated_at: 2026-06-17
keywords:
  [
    "named service provider",
    "named service client",
    "namespace owner",
    "object_ref",
    "object action",
    "provider surface",
    "client surface",
    "transport-neutral service contract",
    "mcp object operations",
    "api object operations",
    "data bus object command",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Namespace Services: Providers

A named service provider is the SDK contract for a bundle or platform subsystem
that exposes a named semantic service to other parts of KDCube.

The provider may own one or more logical reference namespaces, object families,
relations between objects, namespace-level commands, or integration functions.
Namespace ownership is the first major use case, but the abstraction is broader
than object CRUD. A **namespaced service** is a named service provider that owns
or primarily operates on one or more logical namespaces such as `task:` or
`mem:`.

Examples:

| Provider | Typical owned refs or surface |
| --- | --- |
| task issue provider | `task:issue:...`, issue search, issue editor actions |
| memory provider | `mem:...`, memory search, memory viewer actions |
| canvas provider | `cnv:<board>`, `cnv:<board>@<revision>`, `cnv:canvas/.../objects/...`, board/card search, board/card upsert, object actions |
| ReAct artifact provider | `fi:...`, artifact preview/download/materialization |
| document/source provider | provider-owned refs such as `docs:...` or `repo:...`, document/source search and read actions |

Use **named service provider** for the top-level concept. Use **namespaced
service** for the namespace-owning subtype. Use **object resolver** for one
operation family inside a provider. Use **scene surface** for mounted UI
iframe/widget targets.

## Mental Model

```text
owner bundle/subsystem
  NamedServiceProvider
    provider.about
    provider.capabilities
    object.list / search / get / schema / upsert / delete
    object.action / resolve
    relation.list / search
    event.resolve / event.action
    block.produce
    block.render
    provider.operation
        |
        +-- local adapter
        +-- API adapter
        +-- MCP adapter
        +-- Data Bus adapter

caller bundle/widget/client runtime/scene
  NamedServiceClient
    resolves object_ref, namespace, or provider name
    chooses allowed transport
    carries AuthContext from request, Data Bus actor, or headless job
    receives bounded semantic result
```

The client asks the owner. A canvas card that stores `task:issue:BUG-123`
keeps that ref intact and asks the task issue provider to preview, open, edit,
or delete it. Canvas owns board layout; the task provider owns task meaning.

Tools, canvas object actions, chat context chips, ReAct block production,
timeline rendering, artifact/event ref resolution, MCP tools, Codex tools, and
Claude Code tools are consumers of the same provider. A domain that owns a
named service provider uses that provider as the tool, UI, render, and
integration contract.

Ingress is one way to create a caller context. It is not the only way to reach
a provider. A cron job, Data Bus handler, local bundle workflow, MCP request,
or API operation can all call the same provider through a `NamedServiceClient`
once they have a valid `AuthContext`.

## Provider Contract For All Surfaces

A provider that wants its objects to work consistently in chat, canvas,
pinboard, scene hosts, ReAct, MCP, jobs, and external clients must implement
the same semantic operations for every surface. The surface can be different;
the provider contract is not.

```text
Provider-owned namespace
  refs and object kinds:
    task:issue:<id>
    task:issue:attachment:<issue_id>/attachments/<attachment_id>/v<version>/<filename>

  cheap routing:
    provider.about
    provider.capabilities
    event.resolve
    object.resolve

  user-visible object effects:
    object.action(action=preview)
    object.action(action=open)
    object.action(action=download)
    provider-defined object.action(...)

  model/workspace materialization:
    object.get(response_mode=stream)

  model-visible projection:
    block.produce
    block.render
```

The provider must own these values:

| Provider-owned value | Why it is provider-owned |
| --- | --- |
| canonical `object_ref` grammar | Only the owner can parse ref variants, versions, attachments, and parent-child shape correctly. |
| `object_kind` per concrete ref | A namespace can contain several object families; hosts receive concrete kind from the provider. |
| `capabilities` and `actions` per ref | A task issue and a task attachment can expose different actions. |
| `default_open_effect_action` per ref | A generic click/open can mean `open` for an issue and `download` for an attachment. |
| `ui_event.target_surface` for `open` | The owner knows which UI surface can edit or inspect that object. |
| download/read policy | The owner enforces auth and returns a URL or stream under the current context. |
| block shape | The owner decides what the model sees for a task, memory, source, attachment, or future object. |

Surfaces stay generic. Chat context chips, canvas cards, pinboard items, scene
hosts, and ReAct call `object.resolve` or `object.action` and follow the
provider-returned contract. The provider response distinguishes cases such as a
`task:` attachment downloading and a `task:` issue opening an editor.

## Provider Schemas

`object.schema` is the provider-owned contract for object body fields,
filters, actions, and returned object shapes. Use it to describe both strict
validation and softer semantic guidance.

For closed values, use normal schema `enum` semantics. A caller should assume
unknown values are rejected.

For extensible values, mark the field as an open vocabulary in the description
and include provider metadata such as
`x-kdcube-known-values` or `x-kdcube-suggested-values`. This tells agents and
clients which values are already meaningful while preserving the provider's
ability to accept new normalized values.

```json
{
  "type": "string",
  "description": "Open vocabulary classification. Known values include ...",
  "examples": ["fact", "preference"],
  "x-kdcube-known-values": ["fact", "preference"]
}
```

Open vocabulary fields are still fields or facets on an object. They are not
new namespaces and not separate object families unless the provider also
advertises a concrete `object_kind`, ref grammar, capabilities, and operations
for them.

## Provider Execution Surfaces

A **provider app** is the app or subsystem that owns the namespace and
registered a provider for it. Provider code is reached through ordinary KDCube
surfaces. The provider does not need to know whether the caller was canvas,
chat, ReAct, a scene page, MCP, a job, or an external client unless it wants to
use `request.context.source` for policy or audit.

```text
Provider app startup
  executor: provider app entrypoint
  surface: on_bundle_load / startup hook
  customized: yes, provider app
  work:
    build NamedServiceRegistry
    expose named_services()
    register Discovery.entry for namespace, provider_id, operations, refs

        |
        v

Same-runtime provider call
  executor: NamedServiceClient through bundle_registry transport
  surface: provider app named_services() registry
  customized: no transport semantics; provider methods are customized
  work:
    provider = registry.provider("task.issue")
    provider.handle(ctx, NamedServiceRequest)

        |
        v

API provider call
  executor: provider app @api(alias="named_service", route="operations")
  surface: provider app operations API
  customized: yes, provider decides to expose this facade
  work:
    dispatch_named_service_api_request(registry, payload)

        |
        v

Provider operation method
  executor: NamedServiceProvider method
  surface: provider operation family
  customized: yes, namespace/domain semantics live here
  examples:
    object.resolve  -> descriptor, capabilities, default_open_effect_action
    object.action   -> open/download/provider-defined object effect
    object.get      -> JSON or streamed bytes
    object.host_file -> host caller file into provider storage
    object.upsert   -> mutate provider object under provider schema
    block.produce   -> model-visible blocks

        |
        v

Provider-owned binary operation, when needed
  executor: provider app @api(alias="issue_attachment_download", route="operations")
  surface: provider app binary/read operation
  customized: yes, provider storage and auth
  work:
    authenticate request again
    read provider-owned bytes
    return BundleBinaryResponse
```

For URL-based downloads, `object.action(action="download")` should return
metadata plus a `download_url` for a provider-owned binary operation. Build the
URL with
`kdcube_ai_app.apps.chat.sdk.infra.bundle_urls.bundle_operation_url(...)` so
providers use one SDK route builder for KDCube integration URLs. The browser
later calls that URL with its normal cookies/session, and the provider
operation enforces auth again before streaming bytes.

## Provider Surface

A provider exposes operations grouped by scope.

### Provider Operations

| Operation | Purpose |
| --- | --- |
| `provider.about` | Describe the provider, owned namespaces/object families, labels, and human-facing purpose. |
| `provider.capabilities` | Report supported operations, transports, object kinds, actions, limits, and policy hints. |
| `provider.operation` | Invoke a provider-level command that is not tied to one object, such as sync, import, rebuild index, or connect integration. |

`provider.about` lets a client understand what the provider is for
before choosing a narrower operation.

Providers that expose mutation should keep `provider.about` concise: service
purpose, base object summaries, and a short hint to call `object.schema` for
concrete payload fields. Full object schemas, capability maps, and the complete
provider spec belong to `object.schema` and `provider.capabilities`.

Providers that expose more than one searchable object space should declare
bounded `search_scopes` on the provider spec. Search scopes are registration
metadata, so consumers can render them in the agent tool catalog from service
discovery without first making a live `provider.about` call. `provider.about`
may still return richer human guidance, but the fast path for model tool
selection is the provider registration/discovery entry.

Each search scope names a namespace string that clients may pass to
`object.search`. The scope is also the search boundary: searching
`sensor:temperature` means "search objects whose provider-declared searchable
scope is `sensor:temperature`." The base namespace remains valid when the
consumer config allows `object.search`; the provider decides and documents what
the base namespace search means.

```python
@named_service_provider(
    provider_id="sensor.provider",
    namespace="sensor",
    operations={"object.search": {"transports": ["bundle_registry"]}},
    search_scopes=[
        {
            "namespace": "sensor:temperature",
            "label": "temperature readings",
            "object_kind": "sensor.temperature",
        },
        {
            "namespace": "sensor:humidity:aggr",
            "label": "humidity aggregates",
            "object_kind": "sensor.humidity.aggregate",
        },
    ],
)
class SensorProvider(NamedServiceProvider):
    ...
```

The same shape can be supplied through explicit provider config when discovery
is not available. Prefer provider registration when the provider app is loaded
in the runtime because it keeps the object-space contract with the provider.

### Search Scope Filters And Relevance Tuning

A search scope may declare a `filters` schema — the params a client can pass to
`object.search` for that scope (state, tags, date ranges, …). Advertise them so
the agent tool catalog and `object.schema` surface them.

A hybrid-search provider (semantic + lexical + recency) may also expose
provider-owned tuning objects. These names are a convention, not a universal
scoring engine:

- **`factor_weights`**: relative weights only. `0` disables that factor.
- **`thresholds`**: eligibility floors for normalized provider factors only.
- **`scoring`**: non-weight ranker parameters.

Unset keys keep the subsystem default, so omitting all tuning keeps current
behavior. Canonical shape as the task issue scope declares it:

| Key | Type / range | Default | Meaning |
|---|---|---|---|
| `factor_weights.lexical_weight` | number, ≥ 0 | 0.55 | keyword (bm25) ranker weight; relative |
| `factor_weights.semantic_weight` | number, ≥ 0 | 0.45 | embedding (cosine) ranker weight; relative |
| `factor_weights.recency_weight` | number, ≥ 0 | 0.25 | recency ranker weight; relative, 0 = ignore recency |
| `thresholds.semantic_score` | number, -1.0–1.0 | 0.30 | cosine-similarity floor; raise to tighten, <0 turns semantic off |
| `scoring.rrf_k` | number, ≥ 1 | 60.0 | RRF constant (larger = flatter) |
| `scoring.recency_half_life_days` | number, > 0 | 21.0 | days until recency score halves |

The three ranker weights are **relative** (only their ratio matters); set one to
`0` to disable that ranker. `thresholds.semantic_score` floors semantic hits so an
unrelated query returns few/none instead of every nearest row. Defaults are the
declaring subsystem's — each provider documents its own.

Memory declares its own `factor_weights` set for its additive weighted-sum
scorer (`semantic`, `text`, `label`, `salience`, `importance`, `confidence`,
`freshness`, `confirmation`); it is not RRF and has no `rrf_k`. Memory's
freshness decay is in `scoring.half_life_days`, and its optional normalized
relevance floor is `thresholds.relevance_score`.

Memory also declares an agent-facing `origin` filter. These values are public
search semantics; storage names such as `bundle_id` are not exposed to the
model:

| `filters.origin` | Meaning |
|---|---|
| `any` | all user-visible memory records, regardless of whether a user or agent created them |
| `this_agent` | records saved in this agent/application context |
| `any_agent` | agent-created records across agent/application contexts |
| `created_by_user` | records explicitly created by the user |
| `global` | shared records not tied to one agent/application context |

Canvas card search declares the `cnv` search scope when the canvas named-service
provider is registered. Its filters are `canvas_name`, `canvas_id`,
`all_boards`, `kinds`, `namespaces`, and `thresholds.semantic_score`. It
searches card-visible snapshots, not the full source objects behind the cards.

Canvas exposes these object families:

| `object_kind` | Refs / payloads | Notes |
|---|---|---|
| `canvas.board` | `cnv:<board-name>` and `cnv:<board-name>@<revision>` | Board document with cards, layout, and revision metadata. `object.upsert` writes/replaces the board document. |
| `canvas.card` | card body inside a board; hosted content may produce `cnv:canvas/users/.../objects/...` | `object.upsert` creates or updates a card by calling the existing canvas patch path. Cards may host canvas-owned content or pin `fi:`, `mem:`, `task:`, `so:`, or other refs. |
| `canvas.object` | `cnv:canvas/users/<user>/canvases/<board>/objects/<kind>/<card-id>/v000001.<ext>` | Versioned bytes/text hosted by a card. Mutate the owning `canvas.card`; do not upsert this hosted object directly. |

### Object Operations

| Operation | Purpose |
| --- | --- |
| `object.list` | Browse objects in a collection with pagination. |
| `object.search` | Search objects; default mode is hybrid when the provider supports it. Providers may accept the configured base namespace or provider-declared scoped namespaces. |
| `object.get` | Fetch one object by `object_ref` or owner-local id. With `response_mode: stream`, fetch the object's byte representation while still returning structured response metadata. |
| `object.schema` | Return provider-defined object schemas, search/filter contracts, and tool payload guidance for one object kind or ref. |
| `object.host_file` | Host a caller-owned runtime file/ref into provider-owned storage and return the provider-owned file object/ref. |
| `object.upsert` | Create or update one object with idempotency and revision checks. |
| `object.delete` | Delete or archive one object with revision checks. |
| `object.action` | Run a bounded UI or domain action on an object, such as `preview`, `open`, `download`, `pin`, or provider-defined actions. |
| `object.resolve` | Normalize a ref into a canonical object descriptor and optional `ret.ui_event` or `ret.extra` hints. |

### Relation Operations

| Operation | Purpose |
| --- | --- |
| `relation.list` | List known relations for one object or object family. |
| `relation.search` | Search or filter relations across owned and referenced namespaces. |

Relation operations allow one provider to connect multiple owned objects or
report relationships to objects in other namespaces without taking ownership of
those foreign refs.

### Event And Block Operations

| Operation | Purpose |
| --- | --- |
| `event.resolve` | Resolve an owner URI or event payload into lightweight routing metadata. |
| `event.action` | Run a bounded action on an event ref, such as preview, open, or explain. |
| `block.produce` | Produce model-visible blocks from provider-owned objects or events. |
| `block.render` | Optionally patch provider-owned timeline blocks during model prompt rendering, or serve explicit provider-render clients. |

`event.resolve` is the provider-owned URI resolver. It is a function, not a
host-side pattern declaration. The host may dispatch `task:...` to the task
namespace resolver, but only the provider function decides what that URI means.
The function receives the URI as `request.object_ref` and returns a bounded
resolution object, usually in `ret.extra`, for example:

```json
{
  "event_source_id": "named_services.task",
  "object_ref": "task:issue:BUG-123",
  "object_kind": "task.issue",
  "namespace": "task"
}
```

The resolver is the routing step used before block production. Object content
belongs to `object.get`; model-visible projection belongs to `block.produce`;
workspace materialization belongs to streamed `object.get` through `react.pull`.
The ReAct read pipeline calls `block.produce`. Timeline rendering then renders
the stored blocks, applies local ReAct timeline/compaction projection policies,
and runs optional provider `block.render` hooks for visible provider-owned
blocks. The named-service render adapter calls relevant providers concurrently
and merges patches only for blocks owned by the provider namespace/event source.
Explicit clients may also call `block.render` directly when they need a
rendered provider representation without producing a new timeline block
occurrence.

These operations are the provider-side shape behind event-source readers,
block-production policies, timeline projection policies, and renderer-specific
resolvers. A ReAct policy may call the provider through a local resolver, API
adapter, or service-discovery-selected bundle operation; the operation
semantics stay the same.

## Standard Request Fields

All operations receive a context from the runtime and a transport-neutral
request payload.

```python
auth = AuthContext.from_current_request_context()
ctx = NamedServiceContext.from_auth_context(auth)
```

Object-oriented operations use these common request fields:

```json
{
  "schema": "kdcube.named_service.request.v1",
  "provider": "task.issue",
  "namespace": "task",
  "object_ref": "task:issue:BUG-123",
  "object_id": "BUG-123",
  "collection": "issues",
  "cursor": null,
  "limit": 50,
  "query": "blocked auth bug",
  "search_mode": "hybrid",
  "filters": {},
  "sort": [],
  "include": [],
  "action": "open",
  "object": {},
  "base_revision": null,
  "idempotency_key": "client-op-01HX",
  "response_mode": "json",
  "context": {}
}
```

Only fields relevant to the operation are required. Providers validate the
payload and enforce ownership.

## Standard Response Fields

Responses are bounded and semantic.

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "provider": {
        "bundle_id": "task-tracker@1-0",
        "provider_id": "task.issue"
      },
      "namespace": "task",
      "object_ref": "task:issue:BUG-123",
      "next_cursor": null,
      "revision": "rev-7",
      "capabilities": {},
      "relations": [],
      "warnings": []
    },
    "object": {},
    "items": [],
    "extra": {},
    "ui_event": null
  },
  "error": null
}
```

Large bytes, long reports, and generated artifacts should be returned as refs,
hosted files, or streamed `object.get` results, not as unbounded inline
response payloads.

### Streamed Object Reads

`object.get` has two response modes:

| `response_mode` | Provider return | Used by |
| --- | --- | --- |
| `json` or omitted | `NamedServiceResponse` | normal tools, resolvers, schema-aware clients |
| `stream` | `NamedServiceStreamResult` | `react.pull`, future local artifact materializers, file/object transfers |

`NamedServiceStreamResult` carries both:

- `response`: a named-service sidecar response. Keep it compact: provider
  identity, namespace, object ref, revision, MIME, and descriptor fields are
  appropriate. The large object body belongs in the streamed bytes;
- `chunks`: an async byte iterator for the object representation.

Large files travel as stream chunks, while object metadata travels in the
named-service response sidecar. Access denial and missing-object conditions are
represented as failed `NamedServiceResponse` values in the stream result;
callers such as `react.pull` surface that exact error to the agent.

For JSON object refs, `response_mode: stream` still means "produce bytes". The
bytes can be UTF-8 JSON and should be the compact representation the model will
read after `react.pull`, for example `{ok, object_ref, object}` or a
provider-owned read shape. Returning only a plain `NamedServiceResponse` to a
stream request is tolerated by the generic materializer as a compatibility
fallback, but it is not the preferred provider contract and may lose
provider-specific read formatting. The preferred shape is:

```text
object.get(response_mode=stream)
  response: compact sidecar descriptor
  chunks:   compact JSON/file bytes to write into fi:

block.produce
  input target.meta.object_ref or target.object_ref
  output provider-owned ReAct visible blocks

block.render
  input bounded timeline snapshot plus render_context
  output patches for provider-owned block indexes
```

Model-visible owner formatting is the `block.produce` contract. After
`react.read(fi:...)`, the consumer runtime preserves `object_ref` and asks the
owner event source to run `block.produce`. Every object family that needs
formatting different from raw file text should expose `block.produce`. If
`block.produce` returns no blocks, the consumer falls back to generic text for
textual `fi:` artifacts.

`block.render` is the optional second-stage rendering contract. It has two
compatible call modes:

- timeline mode, used by `Timeline.render()`: request payload includes
  `blocks[]`; response includes patch operations for provider-owned block
  indexes;
- direct-client mode: request payload may omit `blocks[]`; response can include
  a direct rendered representation such as `{format, markdown, blocks}` for a
  client that called `block.render` explicitly.

Timeline mode runs during prompt rendering after stored blocks have already
been produced. The provider receives a bounded snapshot of visible blocks
around its objects:

```json
{
  "blocks": [
    {
      "index": 12,
      "type": "react.tool.result",
      "text": "...",
      "meta": {
        "object_ref": "mem:record:mem_123"
      }
    }
  ],
  "render_context": {
    "phase": "timeline_projection",
    "audience": "model",
    "event_source_id": "named_services.mem",
    "trigger_object_refs": ["mem:record:mem_123"],
    "limits": {
      "max_blocks": 64,
      "neighbor_radius": 4
    }
  }
}
```

The provider returns patch operations:

```json
{
  "patches": [
    {
      "op": "patch_block",
      "index": 12,
      "fields": {
        "text": "[MEMORY RECORD]\n...",
        "meta": {
          "render_policy": "memory.named_service.block_render"
        }
      }
    }
  ]
}
```

The central adapter accepts patches for indexes owned by that provider
namespace/event source. Context neighbors are available to the provider as
read-only input. Provider render calls are parallel; each provider sees the
same input snapshot and the central adapter merges the accepted patches.

### Provider Implementation Matrix

| Operation | Provider receives | Provider returns | Consumer caller | Trace markers |
| --- | --- | --- | --- | --- |
| `object.get` | `request.object_ref`, optional `object_id`, `response_mode` | `NamedServiceResponse` for JSON mode; `NamedServiceStreamResult` for stream mode | tools, resolvers, `react.pull` namespace rehoster | `Named-service artifact rehost start/complete`, provider dispatch logs |
| `event.resolve` | `request.object_ref` | `ret.extra.event_source_id`, canonical `object_ref`, object kind, cheap routing metadata | ReAct owner event-source resolver, scene/canvas resolvers | `react.read.owner_projection status=namespace_event_source/...` |
| `block.produce` | `request.object_ref`, `payload.target` with read artifact metadata | `ret.extra.blocks[]` with provider-authored model-visible blocks | `react.read` owner projection | `memory.named_service.block_produce`, provider-specific logs, `react.read.owner_projection status=produced` |
| `block.render` timeline mode | `payload.blocks[]` with stable `index` values plus `render_context` | `ret.extra.patches[]`, or `ret.extra.blocks[]` with `index` fields | `Timeline.render()` named-service render adapter | `named_services.block_render status=called/rendered/empty/not_declared/merged` |
| `block.render` direct mode | `request.object_ref`, optional custom payload | provider-defined rendered representation, commonly `{format, markdown, blocks}` | explicit SDK/client call | provider dispatch logs |

Timeline-mode `block.render` patches are block-indexed. The central adapter
accepts `patch_block`, `replace_block`, and `append_block_after` operations for
indexes owned by the same provider namespace/event source. Direct-client
responses are useful for custom clients, but timeline rendering consumes
patches or indexed blocks.

Example provider return:

```python
return NamedServiceStreamResult(
    response=NamedServiceResponse.ok_response(
        provider=self.provider_identity(),
        namespace="task",
        object_ref="task:issue:attachment:BUG-123/attachments/ta_1/v000001/evidence.md",
        object=attachment_descriptor,
    ),
    chunks=artifact_store.iter_bytes(relpath),
    filename="evidence.md",
    media_type="text/markdown",
)
```

### Provider-Owned File Hosting

`object.host_file` is the client-to-provider file-hosting operation. It is
separate from `object.upsert`: hosting creates a provider-owned file ref;
upsert cites or attaches that returned ref on a provider object when the schema
supports it.

Request shape:

```json
{
  "operation": "object.host_file",
  "namespace": "task",
  "object_ref": "task:issue:BUG-123",
  "payload": {
    "file": {
      "ref": "fi:turn_1.files/report.md",
      "filename": "report.md",
      "mime": "text/markdown",
      "description": "Investigation note"
    }
  }
}
```

The request carries a file descriptor, not base64 bytes. Same-runtime providers
may accept a runtime-local `local_path` descriptor when the transport is trusted
and request-bound. Cross-runtime and agent-facing paths should normally use
artifact refs such as `fi:` and let the provider materialize the source through
platform storage under the current auth context.

Response shape:

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "namespace": "task",
      "object_ref": "task:issue:attachment:BUG-123/attachments/ta_1/v000001/report.md"
    },
    "object": {
      "schema": "kdcube.named_service.object.v1",
      "identity": {
        "object_ref": "task:issue:attachment:BUG-123/attachments/ta_1/v000001/report.md",
        "object_id": "ta_1",
        "object_kind": "task.attachment",
        "namespace": "task"
      },
      "meta": {},
      "body": {
        "filename": "report.md",
        "mime": "text/markdown"
      }
    },
    "extra": {
      "attach_with": {
        "tool": "named_services.upsert_object",
        "namespace": "task",
        "object_ref": "task:issue:BUG-123",
        "object_json": {
          "attachment_refs": [
            {
              "ref": "task:issue:attachment:BUG-123/attachments/ta_1/v000001/report.md",
              "filename": "report.md",
              "mime": "text/markdown"
            }
          ]
        }
      }
    }
  },
  "error": null
}
```

Providers must enforce write/attach permission before hosting. If hosting
fails, return a normal failed `NamedServiceResponse` so agent tools can surface
the exact provider error.

## Object Actions And UI Routing

`object.action` is the operation family that powers canvas cards, chat context
chips, scene summons, and widget-focused opens.

`object.resolve` should also declare the click/open effect for the concrete
object handle when one exists:

```json
{
  "ret": {
    "attrs": {
      "object_ref": "task:issue:BUG-123",
      "capabilities": { "preview": true, "open": true, "download": false }
    },
    "extra": {
      "object_kind": "task.issue",
      "actions": ["preview", "open"],
      "default_open_effect_action": "open"
    }
  }
}
```

`default_open_effect_action` is provider-owned and ref/object-kind-specific.
It answers "what action should a generic UI run when the user opens/clicks this
object handle?" It is not inferred by the host surface and it is not a single
namespace-wide value. For example, the same `task` namespace can return `open`
for `task:issue:<id>` and `download` for
`task:issue:attachment:<id>/attachments/...`.

For `download`, the provider should return a cookie-authenticated
`download_url` plus file metadata. Downloaded bytes stream from the URL target.
`content_base64` is a legacy compatibility field only; new providers should use
a URL response and stream the bytes from that URL.
Build KDCube bundle-operation URLs with the SDK helper
`kdcube_ai_app.apps.chat.sdk.infra.bundle_urls.bundle_operation_url(...)`;
the SDK helper centralizes `/api/integrations/bundles/...` route construction.

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "namespace": "task",
      "object_ref": "task:issue:attachment:BUG-123/attachments/ta_1/v000001/evidence.md"
    },
    "extra": {
      "download_url": "/api/integrations/bundles/demo-tenant/demo-project/task-tracker%401-0/operations/issue_attachment_download?object_ref=task%3Aissue%3Aattachment%3ABUG-123%2Fattachments%2Fta_1%2Fv000001%2Fevidence.md",
      "filename": "evidence.md",
      "mime": "text/markdown",
      "size_bytes": 1024
    }
  }
}
```

For `open`, the provider returns the effect result, including
`ui_event.target_surface` and enough object payload for that surface. The host
scene owns the reaction: mounting/focusing an app iframe, sending a widget
command, or reporting that the target surface is unavailable. Host-specific UI
behavior lives in the scene/host, while chat/canvas keep the generic object
contract.

Example:

```json
{
  "provider": "task.issue",
  "namespace": "task",
  "object_ref": "task:issue:BUG-123",
  "action": "open",
  "context": {
    "source_surface": "sdk.canvas.pinboard"
  }
}
```

Typical response:

```json
{
  "ok": true,
  "ret": {
    "attrs": {
      "namespace": "task",
      "object_ref": "task:issue:BUG-123"
    },
    "ui_event": {
      "type": "kdcube.ui.object.open.requested",
      "subject": "ui.object.open.requested",
      "target_surface": "task_tracker.issue_editor",
      "object_ref": "task:issue:BUG-123",
      "mode": "focus",
      "params": {
        "issue_id": "BUG-123"
      }
    }
  },
  "error": null
}
```

The scene host routes `target_surface` to a mounted widget through its local
surface registry. The provider decides the target and parameters; the scene host
decides how to mount or focus the iframe.

## Provider Declaration

The SDK package lets a bundle declare a provider once and expose that provider
through enabled transports.

```yaml
named_service_providers:
  - provider_id: task.issue
    namespace: task
    refs:
      - task:issue:*
      - task:issue:attachment:*/attachments/*
    object_kinds:
      - task.issue
      - task.attachment
    operations:
      provider.about:
        transports: [local, api, mcp]
      provider.capabilities:
        transports: [local, api, mcp]
      object.list:
        transports: [local, api, mcp]
      object.search:
        transports: [local, api, mcp]
      object.get:
        transports: [local, api, mcp]
      object.schema:
        transports: [local, api, mcp]
      object.host_file:
        transports: [local, api, mcp, data_bus]
      object.upsert:
        transports: [local, api, mcp, data_bus]
      object.delete:
        transports: [local, api, mcp, data_bus]
      object.action:
        transports: [local, api, mcp, data_bus]
      object.resolve:
        transports: [local, api, mcp]
      relation.list:
        transports: [local, api, mcp]
      event.resolve:
        transports: [local, api, mcp]
      event.action:
        transports: [local, api, mcp]
      block.produce:
        transports: [local, api]
      block.render:
        transports: [local, api]
```

The stable concept name is named service provider. The current SDK package
shape is:

```text
kdcube_ai_app/apps/chat/sdk/solutions/named_services_providers/
  types.py
  provider.py
  registry.py
  discovery.py
  client.py
  canvas_resolver.py
  transports/
    api.py
    api_client.py
```

### Provider Resolver Function

Namespace URI routing is a provider function. Use
`@event_source_resolver(namespace=...)` for same-runtime discovery, and expose
the same function through provider `event.resolve` for named-service clients
that reach the provider through service discovery.

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_resolver
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceRequest,
    NamedServiceResponse,
)

@event_source_resolver(namespace="task")
async def resolve_task_event_source(ref: str, **_) -> dict:
    if not ref.startswith("task:"):
        return {"ok": False, "error": "task_ref_required"}
    return {
        "ok": True,
        "event_source_id": "named_services.task",
        "object_ref": ref,
        "object_kind": "task.attachment" if "/attachments/" in ref else "task.issue",
        "namespace": "task",
    }

class TaskIssueProvider(NamedServiceProvider):
    async def event_resolve(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        resolved = await resolve_task_event_source(request.object_ref or "")
        if not resolved.get("ok"):
            return NamedServiceResponse.error_response(
                code=str(resolved.get("error") or "event_resolve_failed"),
                message="Task event resolver failed.",
                namespace="task",
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            namespace="task",
            object_ref=resolved["object_ref"],
            extra=resolved,
        )
```

This resolver is the provider-owned route from `uri` to resolution metadata.
Heavy reads belong to `object.get`; model-visible content belongs to
`block.produce`.

## Bundle Configuration Surface

Provider registration and consumer configuration are different surfaces.

Provider bundles expose code and register provider records:

```text
Provider bundle
  @named_service_provider(...)
  named_services() -> NamedServiceRegistry
  @api(alias="named_service") optional transport facade
  on_bundle_load() -> Redis Named Service Discovery registration
```

Consumer bundles decide which of those registered provider surfaces they use:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: task_service
            kind: named_service
            alias: named_services
            namespaces:
              task:
                allowed: [provider.about, object.search, object.schema, object.upsert, object.delete]
        event_sources:
          - kind: named_service
            namespace: task
            enabled: true
            discovery:
              mode: service_discovery
            policies:
              pull:
                mode: provider
                operation: object.get
              block_production:
                mode: provider
                operation: block.produce
    ui:
      canvas:
        resolvers:
          - kind: named_service
            namespace: task
            enabled: true
            discovery:
              mode: service_discovery
            allowed: [object.resolve, object.action]
```

`surfaces.as_consumer` declares which namespaces this bundle consumes and which
agents, event-source policies, pull policies, and UI resolver surfaces may use
that namespace. Provider location is normally resolved from Named Service
Discovery.

Provider bundles register their available providers into the Redis-backed
tenant/project discovery table when the bundle is loaded and ready:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    RedisNamedServiceDiscovery,
)

discovery = RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)
await discovery.register_registry(
    self.named_services(),
    bundle_id="task-tracker@1-0",
    transport="bundle_registry",
    registry_method="named_services",
)
```

Named Service Discovery is a provider index. It can contain multiple providers
for the same namespace, including providers from different bundles. Each entry
advertises operations, object kinds, and provider ref scopes so the runtime can
choose the provider per request. Those ref scopes are not an event-source
resolver. URI interpretation still belongs to the provider function exposed by
`event.resolve`.

`surfaces.as_consumer.agents.<agent>.tools[*].namespaces.<namespace>.allowed`
controls which provider operations become model-callable tools for a specific
agent surface. A bundle can expose the same namespace to canvas/chat resolvers
while only one agent receives model-callable tools for it.

Agent ids are not ReAct-specific. The same consumer-surface pattern can
describe tool access for ReAct agents, Claude Code, Codex, MCP, widget, job,
or other client runtimes once their adapters consume the provider contract.

When a client must pin provider endpoints instead of using discovery, provider
endpoint transport is explicit in the namespace config that the consumer
surface passes to the named-service adapters. The list is plural because one
namespace may be split across providers by operation, ref, or object kind.

In that `providers` list, `operations` is the provider capability contract. In
an agent tool item, `allowed` controls the model-callable tool surface for that
agent. `surfaces.as_consumer.ui.canvas.resolvers` lets the canvas resolver call
the provider for object refs. Canvas uses `object.resolve` to discover cheap
metadata/capabilities, then uses `object.action` for explicit UI commands;
provider code decides which concrete action values are accepted.

| `transport` | Runtime path | Use when |
| --- | --- | --- |
| `bundle_registry` | same KDCube runtime loads the owner bundle object and calls `named_services()` directly | the provider bundle is deployed in the same cube and the caller wants the fastest request-bound path |
| `bundle_operation` | same KDCube runtime calls the owner bundle's `@api(alias="named_service")` operation | the owner has only exposed the API facade or the integration wants the operation envelope |
| `module` | same Python runtime imports an explicit module/factory and calls the returned registry/provider | the provider lives in another module/package already importable in the current runtime |

`bundle_registry` and `module` preserve the request/auth context; they only
change how the provider object is reached. Provider code still owns object-level
permission checks.

## Client Surface

Callers use a named service client instead of hardcoding owner bundle routes.

```python
client = get_named_service_client()

result = await client.action(
    object_ref="task:issue:BUG-123",
    action="open",
    context={"source_surface": "sdk.canvas.pinboard"},
)
```

The client resolves by `object_ref` first. When no object ref exists yet, it
resolves by provider or namespace and operation:

```python
items = await client.search(
    provider="task.issue",
    namespace="task",
    query="blocked auth bugs",
    search_mode="hybrid",
    limit=20,
)
```

The client may choose a transport from provider capabilities and caller needs:

| Need | Preferred transport |
| --- | --- |
| same process/provider loaded locally | local |
| browser widget read or bounded write | API |
| external tool/client call | MCP |
| durable async mutation or command | Data Bus |

Data Bus responses mean that a command was accepted into the durable stream.
The domain result arrives through the handler result/reply path or by later
reading the object state.

## Auth Context

Auth belongs to the transport and runtime context. It is not part of model-call
arguments.

| Caller | Auth carrier | Provider receives |
| --- | --- | --- |
| KDCube widget/main UI over API | platform headers/cookies/session | resolved tenant, project, user, roles |
| same-origin browser MCP | MCP HTTP request auth, including cookies where the platform allows them | resolved tenant, project, user, roles |
| external MCP client | bearer/id token or bundle-issued/federated token | resolved tenant, project, user, roles |
| Data Bus browser client | authenticated Socket.IO/SSE peer or federated Data Bus token | message actor and tenant/project stream scope |
| server-side bundle call | current runtime context or explicit local context | current tenant, project, user, roles when present |
| scheduled job / cron | explicit bundle-job context | tenant, project, job principal, job metadata |
| scheduled job on behalf of a user | restored saved user auth context plus job metadata | original user principal, tenant, project, executing bundle id |

MCP tool schemas expose domain parameters. The platform/adapter resolves
`cookie`, `authorization`, `user_id`, and `roles` from the request and passes a
`NamedServiceContext` to the provider.

The SDK primitive is:

```python
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceClient,
    NamedServiceContext,
)

# API/MCP/Data Bus handlers that already have a bound runtime request:
client = NamedServiceClient.from_current_request(registry)

# Data Bus handler:
client = NamedServiceClient.from_data_bus_context(registry, ctx)

# Scheduled jobs and other headless bundle work:
client = NamedServiceClient.for_bundle_job(
    registry,
    tenant=tenant,
    project=project,
    bundle_id="task-tracker@1-0",
    job_alias="nightly-index",
)
```

Provider code reads `ctx.auth_context.principal_kind` when it needs to
distinguish a user request from job/system/service work. A bundle is the
execution/provider context (`bundle_id`), not the caller principal. Headless
contexts carry a job/service principal, and delegated jobs can preserve the
saved user principal while marking the call source as `bundle_job`.

### Scoped MCP Tokens

Some MCP servers are exposed by a bundle for an external client process such
as Claude Code. In that path there may be no live browser request and no
current platform user session. The task-and-memo email integration uses this
pattern:

1. The bundle prepares a run document.
2. The bundle signs a short-lived, run-scoped MCP token.
3. The public `@mcp(...)` route validates that token.
4. The MCP tools operate only inside the scoped run.

Named service MCP adapters should follow the same shape. The generic SDK
primitive is a signed `AuthContext` token:

```python
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    AuthContext,
    sign_auth_context_token,
    verify_auth_context_token,
)

saved_user_context = AuthContext.from_mapping(saved_user_auth_doc)
auth = AuthContext.for_bundle_job(
    tenant=tenant,
    project=project,
    bundle_id="task-and-memo-app@1-0",
    job_alias="email-check",
    on_behalf_of=saved_user_context,
)

token = sign_auth_context_token(
    auth,
    secret=secret,
    audience="task-and-memo-app@1-0:mcp/named-services",
    ttl_seconds=900,
    metadata={"run_id": run_id},
)

# Inside the public MCP route after reading the configured header:
auth = verify_auth_context_token(
    token,
    secret=secret,
    audience="task-and-memo-app@1-0:mcp/named-services",
)
client = NamedServiceClient(registry, auth_context=auth, transport="mcp")
```

The token is not a replacement for platform auth. It is the bundle/provider
credential for a bounded external client run. Use a narrow audience, short TTL,
and provider-owned run metadata.

## Transport Adapter Contract

Each transport adapter maps the same semantic operation to its native protocol.

```text
API:
  POST /api/.../named-services/{provider}/{operation}

MCP:
  named_service.task.issue.object.search(...)
  named_service.task.issue.object.action(...)

Data Bus:
  subject: named_service.task.issue.object.upsert
  object_ref: task:issue:BUG-123
  payload: named service request

local:
  await provider.object_search(ctx, request)
```

Exact URLs and MCP tool names are implementation details. The operation names,
context rules, idempotency fields, and response semantics are the stable
contract.

Transport adapters are context adapters plus protocol adapters:

- API/MCP adapters hydrate `AuthContext` from the already-authenticated request
  and call the provider/client surface.
- Public MCP adapters used by external clients can verify a scoped signed
  `AuthContext` token and then call the same provider/client surface.
- Data Bus adapters hydrate `AuthContext` from message actor metadata and
  tenant/project stream scope.
- Local callers pass an explicit context or use the currently bound runtime
  context.

The provider surface remains callable without entering platform ingress when
the caller is already running inside trusted bundle/platform code.

### API Local Loop

The API adapter is the first concrete transport adapter. A bundle mounts one
normal `@api(alias="named_service")` operation, and that operation dispatches
through the local named-service registry. The helper multiplexes JSON and
streamed reads: when the request has `response_mode: stream`, the same API
method may return a stream-capable result.

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceRegistry,
    dispatch_named_service_api_request,
)

class MyEntrypoint(...):
    def _named_service_registry(self) -> NamedServiceRegistry:
        registry = NamedServiceRegistry()
        registry.register(self.task_issue_provider())
        return registry

    @api(method="POST", alias="named_service", route="operations")
    async def named_service_api(self, **payload):
        return await dispatch_named_service_api_request(
            self._named_service_registry(),
            payload,
        )
```

The platform API route authenticates the browser/widget request and binds the
request context. The helper then creates a `NamedServiceClient` with
`transport="api"` and calls the provider in-process. This path avoids a public
`/api/integrations/...` round trip, keeps auth handling in one place, and works
for scheduled/local callers.

### Request-Bound Runtime-Local Bridges

A composition bundle may need to resolve an object owned by another bundle
while handling a current browser/widget request. Same-KDCube namespace-service
clients should use the configured endpoint transport:

- `bundle_registry` loads the owner bundle object and calls `named_services()`
  directly. Singleton owner bundles are served from the loader singleton cache
  after the first load.
- `bundle_operation` calls the owner bundle's `@api(alias="named_service")`
  facade and is the compatibility/fallback path.

The lower-level operation bridge remains available for explicit bounded
operation calls:

```python
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation

raw = await call_bundle_operation(
    bundle_id="task-tracker@1-0",
    operation="named_service",
    data={
        "operation": "object.action",
        "provider": "task.issue",
        "namespace": "task",
        "object_ref": "task:issue:BUG-123",
        "action": "open",
    },
)
```

Platform runtime binds the same caller context while executing request-scoped
bundle code. Peer calls stay inside the same KDCube process and reuse the
current tenant/project/session visibility checks. Peer calls use the current
runtime context instead of replaying browser cookies, minting ad-hoc tokens, or
posting back to their own public API.

This bridge is for request-scoped bounded operations. Headless jobs should use
an explicit `AuthContext` and provider/client path instead of assuming a live
browser session exists.

### Configured Canvas/Chat Resolver

Composition bundles can configure namespace resolvers for canvas cards and chat
context chips. The chat widget already routes object actions through the
bundle's `canvas_object_action` operation, so the same resolver registry covers
both surfaces.

```yaml
surfaces:
  as_consumer:
    ui:
      canvas:
        resolvers:
          - kind: named_service
            namespace: task
            enabled: true
            discovery:
              mode: service_discovery
            allowed: [object.resolve, object.action]
```

In the bundle entrypoint:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_canvas_resolver_namespaces,
    register_configured_named_service_canvas_resolvers,
)

def _canvas_object_resolvers(self, payload, *, user_id):
    registry = build_default_canvas_resolver_registry(store)
    register_configured_named_service_canvas_resolvers(
        registry,
        namespaces=named_service_canvas_resolver_namespaces(self.bundle_props),
        tenant=tenant,
        project=project,
        logger=_log,
    )
    return registry
```

The helper registers `NamedServiceCanvasObjectResolver` instances. A `task:`
card then resolves by calling the owning bundle's `named_service` operation
through the request-bound bridge, preserving the user's current auth/session.

## Implementation Order

1. Define SDK types and provider/client interfaces.
2. Add local provider registry and local client dispatch.
3. Add API adapter for widgets and scene hosts.
4. Add MCP adapter for external tools/clients with request-level auth
   context.
5. Add Data Bus adapter for durable async commands.
6. Migrate existing resolver actions such as `canvas_object_action` to delegate
   to named service provider `object.action`.
7. Add provider declarations for task, memory, canvas, ReAct artifacts, and
   knowledge as each owner is ready.

Existing bundle-specific operations can stay as compatibility routes while
they delegate to the named service provider.

For canvas, this means a scene or widget may keep calling the existing
`canvas_search` and `canvas_patch` bundle operations. The consumer bundle should
implement those aliases by dispatching to its registered `cnv` provider and then
returning the same legacy result envelope the UI already expects. This keeps the
browser transport stable while canvas search/upsert semantics move to the
named-service contract.

## Current SDK Package

The initial SDK package is:

```text
kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers
```

It currently provides:

- async auth context, request, response, operation, and provider spec types;
- `AuthContext` for request, Data Bus, job, service, system, and local callers;
- `@named_service_provider(...)` metadata decorator;
- `NamedServiceProvider` async base class;
- in-process `NamedServiceRegistry`;
- async `NamedServiceClient` local dispatch;
- API transport helper for local-loop `@api(...)` dispatch;
- request-bound local bundle operation bridge for peer bundle API calls;
- API endpoint client for named-service calls through that bridge;
- canvas/chat object resolver adapter plus reusable config registration helper;
- client-scoped named-service tool adapter that reads
  `surfaces.as_consumer.agents.<agent>.tools`;
- client constructors for current request, Data Bus context, and bundle-job
  context.

MCP and Data Bus platform adapter routes are still separate integration work.
Their transport names are part of provider capabilities so bundle code can
declare the intended exposure before each adapter is mounted.

## Design Checklist

When introducing a named service provider:

- choose one owner;
- define provider id, labels, and purpose;
- define canonical ref grammar and object kinds when the provider owns refs;
- define `provider.about` and `provider.capabilities`;
- define `object.schema` for each object kind that agents may create, update,
  delete, render, or pull from;
- define object operations and action names;
- define `object.resolve` as lightweight URI resolution. It should parse the
  provider-owned ref and return canonical `object_ref`, `object_kind`, parent
  refs, capabilities/actions, `default_open_effect_action` when a generic UI
  can open/click the object handle, and cheap display metadata. Object bodies
  belong to `object.get`; byte streams belong to streamed `object.get`.
- implement `object.action` as `action(object_ref, action, payload)`. The
  provider must parse `object_ref` on every call, branch by object kind, enforce
  auth, and return a bounded result. Attachment refs and parent object refs are
  separate object spaces with separate mutation behavior.
- implement `download` actions as authenticated URL responses with
  `download_url`, `filename`, `mime`, and size metadata. The URL may point at a
  provider-owned operation that streams a `BundleBinaryResponse`; build it with
  `bundle_operation_url(...)`. JSON responses carry the URL and metadata; the
  bytes stream from the URL target.
- define streamed `object.get` with `response_mode: stream` for large
  attachment refs that must become `fi:` artifacts;
- define `block.produce` when ReAct should project provider-owned objects as
  model-visible blocks;
- define `block.render` timeline mode when provider-owned blocks need
  prompt-render patches; define direct mode when explicit clients need a
  rendered representation;
- define pagination, search mode, revision, and idempotency rules;
- define relation operations if the provider connects multiple objects;
- define auth and visibility policy;
- choose transport adapters per operation;
- keep Data Bus for durable async commands and stream-backed mutations;
- return refs or hosted files for large object bodies or attachments;
- document how scene `target_surface` commands are produced;
- add tests for provider validation, client dispatch, auth context, and
  transport-specific request shapes.
