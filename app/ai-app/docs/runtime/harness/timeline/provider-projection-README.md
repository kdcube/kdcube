---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/provider-projection-README.md
title: "Provider-Owned Timeline Projection"
summary: "How namespace providers safely prepare and patch only the timeline blocks they own."
tags: ["runtime", "harness", "timeline", "projection", "named-services", "rendering"]
updated_at: 2026-07-18
keywords:
  [
    "provider render patch",
    "timeline projection",
    "namespace ownership",
    "render_window_blocks",
    "block.produce",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
---
# Provider-Owned Timeline Projection

Namespace providers can enrich the presentation of blocks they own without
being allowed to rewrite unrelated timeline history.

```text
timeline window
      |
      v
select block carrying provider object_ref
      |
      v
provider render hook
      |
      v
normalize and ownership-check patches
      |
      +-- replace owned block
      +-- append after owned block
      +-- reject unrelated/invalid patch
```

## Ownership Rule

Ownership is explicit. A block is provider-owned when either:

- its resolved `event_source_id` exactly matches the provider's registered
  source ID; or
- its canonical object ref belongs to the provider namespace.

A provider for `task:` can patch a block owned by `task:`; it cannot patch
`mem:`, `conv:`, or an arbitrary timeline block.

The common projection layer:

- extracts the canonical object ref;
- derives its namespace;
- prepares a bounded render window;
- normalizes provider patch responses;
- allows only replace, field-patch, or append-after operations on stable block
  indexes;
- checks each target block against the provider namespace;
- applies replacements/appends deterministically.

This is an integrity boundary, not merely a UI helper.

## What Remains Adapter-Owned

The shared layer does not decide:

- when rendering is requested;
- which timeline segment is visible to the model or client;
- whether the result is shown in chat, canvas, or another client;
- how a framework represents thinking, tools, or completion blocks.

ReAct's event-source rendering policies call these primitives as one adapter.
Future harness adapters can call the same projection contract without importing
ReAct internals.
