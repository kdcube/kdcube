---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
title: "Memory Widget Solution"
summary: "How to mount the reusable SDK memory widget in a bundle, the host postMessage commands it accepts, its drag/context payload, and how mem: pins resolve. Memory semantics (tiers, reconciliation, snapshots) live in the memory operational docs."
status: draft
tags: ["sdk", "solutions", "memory", "widget", "bundle", "iframe", "data-bus"]
updated_at: 2026-06-11
keywords:
  [
    "sdk memory widget",
    "sdk://context/memory/ui/widget/memories",
    "memory widget config",
    "kdcube-memory-widget-command",
    "kdcube-set-view",
    "memory context attach",
    "mem: resolver",
    "memory widget mount",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-operational-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-react-integration-README.md
---
# Memory Widget Solution

The SDK memory solution provides a reusable React widget source for browsing,
editing, and managing a user's durable memories. A bundle mounts it without
copying UI code, the same way the chat widget is mounted.

The widget source is:

```text
sdk://context/memory/ui/widget/memories
```

This doc is widget-integration only. The memory model itself — tiers, salience,
reconciliation, snapshots, ReAct announce hotsets — is owned by the memory
subsystem and documented separately:
[User Memories Overview](../../memory/user-memories-overview-README.md),
[Operational](../../memory/user-memories-operational-README.md),
[ReAct Integration](../../memory/user-memories-react-integration-README.md).

## What This Component Owns

| Layer | Owned by the memory solution |
| --- | --- |
| Widget UI | Memory list, detail, editor, filters (status/tags/ids/search), reconciliation panel, compact and expanded layouts. |
| Note management | Create, edit, confirm, pin, delete, reconcile — gated by `allow_write`, not by the "use my memory" runtime toggle. |
| Host iframe contract | Runtime config handshake; host view changes, focus, and status through `postMessage`. |
| Context payload | Emits a memory as a draggable `mem:` context for chat or canvas. |

The bundle still owns write permission and whether the agent uses memories at
runtime.

Scope is no longer a user-facing choice. The widget browses **all of the user's
memories** (`all_user_memories`) and renders no scope selector. A bundle that
disallows cross-bundle reads (`allow_all_user_memories=false`) makes the widget
fall back to `current_bundle` automatically. The `default_scope_filter` mount
key is inert and retained only for back-compat.

## Mounting

The memory mixin already declares `@ui_widget(alias="memories")`. A bundle that
derives from it only selects and configures the built UI:

```yaml
config:
  memory:
    enabled: true
    widget: {enabled: true, allow_write: true, default_scope_filter: current_bundle}
  ui:
    widgets:
      memories:
        enabled: true
        src_folder: sdk://context/memory/ui/widget/memories
```

If a bundle does not derive from the memory mixin, declare the alias itself with
`@ui_widget(alias="memories")` and an `@api` mount point, then point
`ui.widgets.memories.src_folder` at the SDK source. See
[Bundle Subsystem Integration](../../bundle/bundle-subsystem-integration-README.md)
for the full memory wiring (entrypoint, config, visibility, resolver, storage).

## Host Commands

When a scene host embeds the widget, it drives layout and focus through
`postMessage`. The widget answers with status.

| Message | Direction | Purpose |
| --- | --- | --- |
| `CONFIG_REQUEST` / `CONFIG_RESPONSE` | widget ⇄ host | Runtime config handshake (base URL, tenant/project/bundle, auth). |
| `kdcube-set-view` | host → widget | Switch between compact and expanded layout. |
| `kdcube-memory-resize` `{height, compact}` | widget → host | Report rendered content height so a floating host can fit the panel to content (no empty space below a short compact list). Measured from the lowest child bottom, so it is correct even when the shell is stretched to fill the iframe. |
| `kdcube-memory-widget-command` `{action: "create"}` | host → widget | Open the new-note editor. |
| `kdcube-memory-widget-command` `{action: "open", memory_id\|object_ref}` | host → widget | Focus a specific memory in the expanded layout. |
| `kdcube-widget-view` | widget → host | Request the host expand the pane. |
| `kdcube-widget-focus` | widget → host | Promote the pane to front. |
| `kdcube-memory-widget-status` | widget → host | Report `{count, compact, memoryUseEnabled}`. |

The "use my memory" toggle governs only runtime use of memories by the agent.
Note management stays available while it is off — the widget banner says notes
remain visible for review, export, and deletion.

## Drag And Context Payload

A memory row is draggable. The widget writes a context payload that chat or
canvas can accept:

```text
type:    kdcube.context.attach
mime:    application/vnd.kdcube.context+json  (+ text/uri-list = the mem: ref)
context: { id: "mem:<id>", kind: "memory", label, summary, ref, data: {...} }
```

Dropping that onto the chat composer attaches the memory as a context chip;
dropping it onto the canvas pins a `memory` card. Dropping a memory **back onto
the memory widget** is a focus/filter gesture — it filters the list to the
dropped id and preserves the current view.

## How `mem:` Pins Resolve

A memory pinned on the canvas is a `mem:` ref. Opening it does not go through the
widget directly — the canvas calls the memory object resolver
(`resolve_memory_ref_action`, capabilities from `memory_ref_capabilities()`),
which returns a `ui_event` targeting the memory surface. The scene then opens
the widget and sends `kdcube-memory-widget-command {action: "open"}`.

The bundle registers that resolver for the `mem:` namespace; the routing of the
returned `ui_event` to the widget is the
[Scene Surface Registry](../scene/scene-surface-registry-README.md). For how the
memory widget sits alongside chat and canvas in one host page, see
[Scene Composition](../scene/scene-composition-README.md).
