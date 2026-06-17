---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
title: "Namespace Services: ReAct Object Materialization"
summary: "Runtime-boundary diagram for how ReAct pulls, reads, owner-projects, and renders named-service objects."
status: design
tags: ["sdk", "namespace-services", "react", "pull", "read", "block-production", "events"]
updated_at: 2026-06-17
keywords:
  [
    "react.pull",
    "react.read",
    "named service object get",
    "block.produce",
    "block.render",
    "owner projection",
    "source_namespace",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
---
# Namespace Services: ReAct Object Materialization

This page is the canonical runtime-boundary diagram for named-service objects in
ReAct. It covers the automatic `react.pull -> react.read -> Timeline.render`
path.

Current implementation state:

- `react.pull` calls the provider through `object.get(response_mode=stream)`.
- `react.read` calls owner block production through `block.produce` when the
  pulled `fi:` artifact preserves the owner identity as `object_ref`.
- `Timeline.render()` renders stored timeline blocks, applies local ReAct
  projection policies, then runs the optional named-service `block.render`
  projection for providers whose objects are present in the visible timeline.
- `block.render` is called concurrently per relevant provider event source.
  The provider receives a bounded timeline snapshot and returns patches. The
  central merge accepts patches only for blocks owned by that provider
  namespace.
- A provider response with `named_service_operation_not_supported` is cached as
  an unsupported render hook for the process lifetime, so later renders skip
  that provider until the runtime restarts.

## Runtime Boundary Diagram

```text
1. Object ref is visible
   executor: model, chat context, canvas pin, search-result widget, or timeline
   runtime: consumer/client runtime
   data:
     object_ref = mem:record:mem_123

        |
        v

2. Agent requests materialization
   executor: ReAct tool runner
   runtime: consumer ReAct runtime
   call:
     react.pull(paths=["mem:record:mem_123"])
   boundary:
     local model/tool runtime -> namespace rehoster
   latency:
     local dispatch plus one provider lookup/call when the ref is not already
     a local fi: artifact

        |
        v

3. Namespace rehoster calls owner provider
   executor: named-service artifact rehoster
   runtime: consumer backend, using Named Service Discovery
   provider call:
     object.get(
       namespace="mem",
       object_ref="mem:record:mem_123",
       response_mode="stream"
     )
   boundary:
     consumer backend -> provider bundle/backend
     transport is bundle_registry, bundle_operation, API, or another configured
     named-service transport
   provider responsibility:
     auth check, object lookup, byte stream production, compact sidecar
     response
   latency:
     provider discovery + provider object lookup + streamed byte transfer

        |
        v

4. ReAct workspace receives bytes
   executor: named-service artifact rehoster / ReAct artifact layer
   runtime: consumer ReAct runtime
   work:
     write streamed bytes under OUTPUT_DIR
     mint a local fi: artifact path for the current turn
     preserve owner identity in pull state and artifact metadata
   result:
     logical_path  = fi:turn_1.files/mem_123.json
     physical_path = turn_1/files/mem_123.json
     object_ref    = mem:record:mem_123
     source_namespace = mem
   latency:
     local filesystem write

        |
        v

5. Agent reads the materialized artifact
   executor: ReAct tool runner
   runtime: consumer ReAct runtime
   call:
     react.read(items=[{"path": "fi:turn_1.files/mem_123.json"}])
   work:
     read local bytes
     construct a read target whose metadata includes:
       meta.object_ref       = mem:record:mem_123
       meta.source_namespace = mem
       meta.materialized_path = fi:turn_1.files/mem_123.json
   latency:
     local filesystem read before owner projection

        |
        v

6. ReAct resolves the owner event source
   executor: EventSourceSubsystem
   runtime: consumer ReAct runtime
   work:
     resolve object_ref to an event source id
     ordinary path uses registered named_services.<source_namespace>
     richer path may call provider event.resolve for URI-specific routing
   traces:
     react.read.owner_projection status=...
   boundary:
     local registry lookup, plus optional consumer -> provider event.resolve
     call
   latency:
     local lookup on the ordinary path
     one lightweight provider call when event.resolve is used

        |
        v

7. ReAct asks the owner to produce model-visible blocks
   executor: named-service block-production adapter
   runtime: consumer ReAct runtime
   provider call:
     block.produce(
       namespace="mem",
       object_ref="mem:record:mem_123",
       target=<read target with materialized fi path and owner metadata>
     )
   boundary:
     consumer ReAct runtime -> provider bundle/backend
   provider responsibility:
     convert the owner object into bounded ReAct blocks for model context
     according to the namespace's object semantics and read/render policy
   latency:
     one provider call, plus provider-side object read if the provider needs it

        |
        v

8. ReAct stores the read result blocks
   executor: react.read
   runtime: consumer ReAct runtime
   result:
     if provider returned blocks:
       timeline block path may remain owner-oriented or include owner metadata
       block.meta.owner_projected = true
       block.meta.object_ref = mem:record:mem_123
       block.meta.materialized_path = fi:turn_1.files/mem_123.json
     fallback:
       generic text block for the local fi: artifact
   latency:
     local timeline append

        |
        v

9. ReAct renders prompt context
   executor: Timeline.render()
   runtime: consumer ReAct runtime
   work:
     render stored blocks
     apply local ReAct timeline, announce, and compaction projection policies
     scan visible blocks for provider-owned object_ref values
     call relevant provider block.render hooks concurrently
     apply valid provider patches to provider-owned blocks
     expose the resulting message stream to the model
   boundary:
     local timeline projection plus optional consumer -> provider block.render
     calls for provider namespaces present in the visible blocks
   latency:
     local timeline rendering plus the slowest provider render call when
     at least one provider implements block.render
```

## Operation Map

| Stage | Operation | Caller | Callee | Automatic in ReAct path |
| --- | --- | --- | --- | --- |
| Pull bytes | `object.get(response_mode=stream)` | ReAct namespace rehoster | named-service provider | Yes |
| Resolve owner source | `event.resolve` | EventSourceSubsystem / resolver bridge | named-service provider | Optional |
| Produce read blocks | `block.produce` | ReAct owner-projection adapter | named-service provider | Yes, when owner projection is registered and `object_ref` is present |
| Render provider representation | `block.render` | ReAct timeline projection or explicit custom client | named-service provider | Optional, only when provider-owned blocks are present and the provider implements it |
| Render model prompt | `Timeline.render()` | ReAct runtime | local timeline and local policies | Yes |

## Owner Identity Field

`object_ref` is the canonical owner identity field throughout this path. It is
the URI whose bytes were copied into the local `fi:` artifact, and it is the URI
used by `react.read`, owner event-source routing, and `block.produce`.

## Provider Contract

For an object namespace that supports ReAct materialization:

- `object.get(response_mode=stream)` provides the exact bytes that become the
  local `fi:` artifact.
- The stream response sidecar carries compact identity and diagnostics. The
  large object body is streamed as bytes, not embedded in the JSON response.
- `event.resolve` provides lightweight URI-to-event-source routing when the
  namespace needs more than the default `named_services.<namespace>` event
  source.
- `block.produce` provides the model-visible blocks used by `react.read`.
- `block.render` optionally patches provider-owned blocks during model prompt
  rendering. It receives a bounded block snapshot and a render context with
  `phase`, `audience`, event-source id, trigger refs, and limits.

## Provider Render Merge Contract

The central ReAct render adapter discovers provider renderers from the timeline
it is about to expose to the model.

```text
Timeline.render visible blocks
  |
  | scan block.meta.object_ref / block.object_ref
  v
group by owning event source, for example named_services.mem
  |
  | asyncio.gather(...)
  v
provider block.render calls run concurrently against the same input snapshot
  |
  | validate returned patches
  v
merge accepted patches into the visible block list
```

The provider request payload has this shape:

```json
{
  "blocks": [
    {
      "index": 42,
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

`blocks` is a bounded snapshot. It includes provider-owned blocks and nearby
context blocks. Nearby blocks are available as context for rendering decisions.
The merge contract is provider-owned-block mutation: returned patches are
accepted for indexes whose block belongs to the provider namespace/event source.

Supported patch forms:

```json
{ "op": "replace_block", "index": 42, "block": { "type": "react.tool.result", "text": "..." } }
{ "op": "patch_block", "index": 42, "fields": { "text": "...", "meta": { "render_policy": "..." } } }
{ "op": "append_block_after", "index": 42, "block": { "type": "text", "text": "..." } }
```

Provider calls are independent. Every provider sees the same input snapshot.
Merge order does not create data dependencies between providers because a
provider patch can change only blocks owned by that provider namespace.

## Consumer Runtime Contract

For a consumer ReAct runtime:

- Pull state preserves the owner URI:
  `logical_path -> object_ref/source_namespace`.
- Read targets carry that owner metadata into block production.
- Owner projection uses owner metadata, not the local `fi:` prefix.
- Read traces are emitted under `react.read.owner_projection`.
- The prompt renderer consumes stored blocks and may call provider
  `block.render` hooks for visible provider-owned blocks.
- Provider render traces are emitted under `named_services.block_render`.
