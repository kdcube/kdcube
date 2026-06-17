---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/surface-layering-README.md
title: "Scene Surface Layering"
summary: "Shared z-index convention for host scenes: a small named tier scale so a surface opened on top of an active full-screen overlay (e.g. an issue wizard opened from inside an expanded chat) always lands in front instead of behind it."
status: active
tags: ["sdk", "solutions", "scene", "layering", "z-index", "overlay", "modal", "ui-convention"]
keywords:
  [
    "z-index convention",
    "surface layering",
    "overlay modal",
    "expanded chat",
    "wizard behind chat",
    "stacking context",
    "scene host layering",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
---
# Scene Surface Layering

A host scene stacks several surfaces — a chat panel, an expandable side pane, a
canvas, a wizard/editor, transient toasts. Each host historically picked its own
`z-index` magic numbers, which produces a recurring bug:

> A surface opened **from within** or **on top of** a full-screen overlay renders
> **behind** that overlay.

Concrete case: the chat is expanded to a full overlay; the user clicks a task
object inside it; the issue **wizard opens behind the expanded chat**.

The cause is always the same — the overlay sits at a high `z-index`, and the
newly-opened surface lands in its in-flow slot at a lower one. The fix is a
shared **tier scale** plus one rule.

## The rule

> A surface opened on top of the active full-screen overlay uses the
> **`overlay-modal`** tier, which is above the **`overlay`** tier.

## The tier scale

Define these as CSS custom properties in the host's `:root` and reference them
everywhere instead of bare numbers:

```css
:root {
  --z-content:        1;   /* normal in-board content, placed cards */
  --z-raised:        10;   /* hover-raised cards, in-content popovers */
  --z-rail:          20;   /* docked side rails / sticky toolbars */
  --z-overlay:       90;   /* a surface expanded to a full-screen overlay (chat, a pane) */
  --z-overlay-rail: 100;   /* controls pinned to the active overlay (its rail buttons) */
  --z-overlay-modal:120;   /* a surface/modal opened ON TOP of the active overlay */
  --z-toast:        200;   /* transient notices/toasts — always on top */
}
```

Gaps between tiers are intentional: a host can place its own sub-levels inside a
tier (e.g. two stacked in-content popovers at `--z-raised` and `--z-raised + 1`)
without colliding with the next tier.

## Applying it

- The expanded surface (chat, an expanded pane) → `--z-overlay`.
- Controls attached to that overlay (rail/close buttons) → `--z-overlay-rail`.
- **Anything opened on top of it** (a wizard/editor, a confirm dialog reached
  from inside the overlay) → `--z-overlay-modal`, plus `pointer-events: auto`
  if the surface behind it was made inert.
- Toasts/notices → `--z-toast`.

### Stacking-context caveat

`z-index` only competes **within the same stacking context**. A high
`--z-overlay-modal` on a deeply-nested element does nothing if an ancestor
created a stacking context with a lower `z-index`. Two safe patterns:

- Give the modal surface `position: fixed` (or `absolute` relative to the scene
  root) and ensure no ancestor between it and the overlay creates a stacking
  context (no `transform` / `opacity < 1` / `filter` / positioned `z-index`).
- Or hoist the modal to the scene root (portal) before applying the tier.

## Reference implementation

The task-tracker host app applies this scale: the expanded chat is `--z-overlay`,
its rail buttons `--z-overlay-rail`, and the issue wizard opened from inside the
expanded chat is floated at `--z-overlay-modal` (a centered modal, with the chat
dimmed behind). See its `ui/main/src/styles.css`.

Other hosts (e.g. the versatile scene, which today uses an ad-hoc scale —
expanded chat at `92`, panes at `70`) should adopt the same tokens so the
behavior is consistent across every scene.
