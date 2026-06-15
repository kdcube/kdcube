---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/context-pin-contract-README.md
title: "Context-Pin Contract"
summary: "The single cross-component contract for 'a draggable reference to an object', shared by every producer and consumer (chat, canvas, memory, tasks): one envelope ({type, contexts:[…]}), one uri field (ContextItem.ref), and the buildContextDrag / parseContextDrop helpers that enforce it. Defined in @kdcube/components-core (shared/contextPin.ts)."
status: design
tags: ["sdk", "npm", "components-core", "context-pin", "drag-and-drop", "object-ref", "contract"]
updated_at: 2026-06-16
keywords:
  [
    "context pin contract",
    "ContextItem",
    "buildContextDrag",
    "parseContextDrop",
    "kdcube.context.attach",
    "object ref drag to canvas chat",
    "ref field",
  ]
---

# Context-Pin Contract

One shape for **"a draggable reference to an object"**, shared by every producer and
consumer across systems and repos (chat, canvas, memory, tasks, future bundles).
Defined and enforced in code at `@kdcube/components-core` (`src/shared/contextPin.ts`);
this doc is the human summary — the types + helpers are the source of truth.

## Why it exists

Each system historically rolled its own `dataTransfer.setData(...)` with its own
**envelope** (`{context}` / `{contexts}` / bare / `{type,contexts}`) and its own
**uri field** (`ref` / `object_ref` / `logical_path` / `hosted_uri`). Every
producer×consumer pair whose shapes/fields didn't line up silently fell back to plain
text and minted a **generic** object instead of the native ref (a task search result
dragged onto canvas became a `cnv:` card instead of `task:issue:…`). This contract
replaces that zoo.

## The contract

**Envelope** — written to `dataTransfer` as both `application/json` and
`application/vnd.kdcube.context+json`. Always the **plural** `contexts` array, even
for one item:

```jsonc
{ "type": "kdcube.context.attach", "source": "<producer>", "contexts": [ ContextItem, … ] }
```

**`ContextItem`:**

```ts
{
  id: string,
  kind: string,        // 'object.ref' | 'memory' | 'file' | 'conversation' | 'task.issue' | …
  label: string,
  ref: string,         // THE uri — the only object-reference field. Required.
  summary?, mime?, event_source_id?, surface?, data?
}
```

**`ref` is the single uri field** — `task:issue:…` · `mem:…` · `fi:conv_…` · `cnv:…`
· `conv:…`. No `object_ref` / `logical_path` / `hosted_uri` as parallel carriers; the
backend wire may still call it `object_ref`, but the producer maps it to `ref` **once**,
at the producer boundary.

## Rules

```ts
import { buildContextDrag, parseContextDrop } from '@kdcube/components-core'

// producer (onDragStart) — the ContextItem type won't compile without `ref`:
buildContextDrag(event.dataTransfer, { id, kind: 'task.issue', label, ref: objectRef }, { source: 'chat' })

// consumer (onDrop) — one validator per boundary:
const items = parseContextDrop(event.dataTransfer.getData('application/json'))
for (const item of items) openOrPin(item.ref)
```

- **Producers** call `buildContextDrag`. `ref` is required, so the uri cannot land in
  the wrong attribute, and the helper always writes the canonical plural envelope.
  Never hand-roll `setData(...)` for context pins.
- **Consumers** call `parseContextDrop` at each drop boundary (canvas drop, chat
  composer). It returns normalized `ContextItem[]` with `.ref` populated.

## Migration

`parseContextDrop` is deliberately lenient **only to absorb not-yet-migrated
producers**: it accepts the four legacy envelopes (`{contexts}`, `{items}`,
`{context}` singular, bare item) and maps legacy uri aliases (`object_ref` /
`logical_path` / `hosted_uri` / `event_ref` / …) → `.ref`. Tighten it once every
producer uses `buildContextDrag`.

Every live drag producer already emits the canonical plural envelope (chat chips &
named-service search results, memory pins, canvas card & attachment drags,
task-tracker task cards) — still hand-rolled. The remaining step is to replace each
`setData(...)` with `buildContextDrag()` as each consumer adopts the package; then the
per-system recognizers and the `canonicalObjectRef` / `contextPinObjectRef` alias
probes can be deleted.

## Related

- Canvas drop normalizers / pin mechanics: `docs/sdk/solutions/canvas/pin-integration-README.md`.
- Named-service search results that produce these pins: `docs/sdk/namespace-services/integration-README.md`.
- Chat widget that produces/consumes them: `docs/sdk/solutions/chat/chat-widget-solution-README.md`.
