---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
title: "Memory Widget Solution"
summary: "How to mount the reusable SDK memory widget in a bundle, the host postMessage commands it accepts, its drag/context payload, and how mem: pins resolve. Memory semantics (tiers, reconciliation, snapshots) live in the memory operational docs."
status: draft
tags: ["sdk", "solutions", "memory", "widget", "bundle", "iframe", "data-bus"]
updated_at: 2026-06-17
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

## Search Ranking

The widget search path is not the named-service tool path. It calls the bundle
operation `memories_widget_data`, which builds a `MemorySearchRequest` and then
uses the same backend store/scorer as named-service memory search:

```text
memory widget UI
  -> memories_widget_data
  -> UserMemoryStore.search()
  -> rank_candidate()
```

For non-empty queries, `rank_candidate()` computes one clamped weighted sum:

```text
score = clamp(
  0.30 * semantic
+ 0.22 * text
+ 0.13 * label
+ 0.11 * salience
+ 0.08 * importance
+ 0.07 * confidence
+ 0.05 * freshness
+ 0.04 * confirmation
)
```

The factors are:

| Factor | Meaning |
| --- | --- |
| `semantic` | Embedding/cosine similarity when the query embedding is available. |
| `text` | Max of PostgreSQL text rank and token-overlap score. |
| `label` | Label/keyword overlap with requested labels or keywords. |
| `salience` | Stored memory salience score. |
| `importance` | Stored memory importance score. |
| `confidence` | Stored memory confidence score. |
| `freshness` | Recency decay from `last_event_at` or `updated_at`; default half-life is 45 days. |
| `confirmation` | Stored confirmation rate. |

If semantic embedding is unavailable or denied by economics, the widget search
still runs with lexical/text recall and the remaining stored quality factors.

Semantic can also be turned **off** deliberately: a `factor_weights.semantic_weight`
of `0` (or less) drops the semantic factor from the sum **and skips the query embed
entirely** — no embedder cost — so the search ranks on text + labels + the stored
quality factors. This is the graceful "no embeddings / no budget" choice. Because
memory ranks with a weighted sum (not a floor-based hybrid index), the off switch is
the **weight = 0**, not a negative `min_relevance_score`; `min_relevance_score` is a
separate floor on the final query relevance, not on the semantic factor.

The widget applies one additional user-facing relevance guard for non-empty
queries: `memory.widget.search_min_relevance_score`, default `0.58`. This is a
floor over direct query relevance:

```text
max(semantic, text, label)
```

That means a memory can have high salience or importance but still be excluded
from widget query results when it does not match the user's search text,
embedding, labels, or keywords strongly enough. Named-service memory search
does not apply the widget's `0.58` default unless the caller passes its own
`min_relevance_score` filter.

The named-service path can expose per-call `factor_weights` for `mem` /
`mem:record`. The widget currently does not expose those tuning knobs in its UI
request; it uses the default memory weights plus the widget relevance floor.

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

## Auth And Host Config

The memory widget uses the standard widget runtime config handshake. It does not
have a memory-specific auth protocol.

```text
memory iframe -> host
  CONFIG_REQUEST { identity: "MEMORIES_WIDGET", requestedFields: [...] }

host -> memory iframe
  CONFIG_RESPONSE { identity: "MEMORIES_WIDGET", config: {...} }
```

Browser/scene hosts may supply `accessToken`, `idToken`, `idTokenHeader`, or rely
on cookies. Any host with additional request auth material gets a
server-authored `authContext.headers` template from its backend and supplies it
in the same `config` object:

```json
{
  "authContext": {
    "headers": {
      "X-KDCube-Auth-Connection-ID": "telegram.default"
    }
  }
}
```

The memory widget does not interpret those headers. It still calls the standard
`/operations/memories_widget_*` routes, always uses `credentials: 'include'` for
the cookie/session path, and blindly promotes `authContext.headers` on each
request. If the host is a Telegram Mini App, the host adds browser-owned
`initData` only because its backend template declares
`X-KDCube-Auth-Provider: telegram`; the memory widget still does not know what
those headers mean. It does not switch itself to a provider-specific public API.
It does not read `window.parent.Telegram`, validate Telegram, call Connection
Hub directly, or use any `kdcube.auth.*` message family. The gateway and
Connection Hub validate provider proofs centrally and project linked authority
into the request session before the operation sees the request.

If the host session or auth context changes, the host announces
`kdcube-auth-changed`.
The widget re-sends `CONFIG_REQUEST`, applies the latest `CONFIG_RESPONSE`, and
reloads memory data if the effective auth material changed.

See [Scene Auth State Contract](../scene/scene-auth-README.md) and
[Connection Authenticators](../connections/authenticators-README.md).

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
