---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/widget-integration-README.md
title: "SDK Chat Widget — Package Engine Integration & Deployment"
summary: "How the in-tree SDK chat widget consumes the @kdcube/components-* packages: the one-knob local/package engine switch, the npm:// materialize-and-alias build path, the build-time engine-root swap, and how the package source ships into the runtime image. Default is the in-tree engine; the package engine is opt-in."
status: implementation
tags: ["sdk", "npm", "components", "chat-widget", "vite", "bundles", "deployment", "npm-scheme", "engine-switch"]
updated_at: 2026-06-16
keywords:
  [
    "VITE_CHAT_ENGINE",
    "chat_widget_ui_config engine",
    "npm:// shared source",
    "engine-root swap",
    "components packages in image",
    "local vs package engine",
  ]
---

# SDK Chat Widget — Package Engine Integration & Deployment

The in-tree chat widget (`sdk://solutions/chat/ui/widget`) can run on **either**
engine, chosen by a single build-time knob:

- **`local`** (default) — the in-tree engine (`src/app/useChatEngine.tsx`). No
  dependency on the `@kdcube/components-*` packages at all.
- **`package`** — the framework-agnostic engine from
  [`@kdcube/components-react/chat`](./components-react/README.md) + an iframe
  host-bridge (`src/app/packageEngine.tsx`).

The widget owns its own dependency on the packages; a bundle still mounts it with
just `src_folder` + `build_command`. Engine internals live in the package docs (see
the [library index](./README.md)); this doc is only the **consumption, switch, and
deployment** mechanics.

## The one knob

`engine` on the mount helper flips both halves together — the runtime engine **and**
the package materialization:

```python
# app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chat/backend.py
chat_widget_ui_config(engine="package")   # package engine
chat_widget_ui_config()                    # local engine (default)
```

| `engine`  | `build_command` gets | `shared_sources` |
| --- | --- | --- |
| `local` (default) | (nothing added) | (none) |
| `package` | `VITE_CHAT_ENGINE=package` prefix | `components_core`/`components_react` → `npm://…/src` |

Because `local` adds **no** `npm://` reference, a default mount builds on any image —
including one whose `/app/npm` is absent. The `npm://` path is exercised **only** when
you opt in.

**Inline bundle config** (raw YAML, not via the helper) does the equivalent by hand:

```yaml
versatile_chat:
  enabled: true
  src_folder: sdk://solutions/chat/ui/widget
  build_command: VITE_CHAT_ENGINE=package npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
  shared_sources:
    components_core: npm://components-core/src
    components_react: npm://components-react/src
```

Drop the `VITE_CHAT_ENGINE=` prefix + the `shared_sources` block to return to local.

## How resolution works (package mode)

1. **Materialize** — the bundle build resolves each `npm://<pkg>/src` to the package
   source and copies it next to the widget under `_shared/<name>` (resolver:
   `_npm_packages_root` / `_resolve_ui_shared_source_path` in
   `…/apps/chat/sdk/solutions/chatbot/entrypoint.py`).
2. **Alias** — the widget's `vite.config.ts` aliases `@kdcube/components-core[/chat]`
   and `@kdcube/components-react[/chat]` onto the materialized `_shared/…` (with an
   upward-search fallback to the workspace for a plain `npm run build`).
3. **Engine-root swap** — `vite.config.ts` resolves `@chat/engine-root` to
   `packageEngine.tsx` (package) or `localEngineRoot.tsx` (local) keyed on
   `VITE_CHAT_ENGINE`. `ChatStoreProvider` renders whichever the alias yields, so the
   **default build never pulls `@kdcube/*` into its module graph** — a no-`shared_sources`
   bundle cannot fail on an unresolved package.

## How the packages ship

The workspace lives **inside the installed app tree** at
`app/ai-app/src/kdcube-ai-app/npm/`, so the same `COPY src/kdcube-ai-app/ .` that
ships the Python package also ships the package source — it lands at `/app/npm` in
the container, where `npm://` resolves identically to the repo. `app/ai-app/.dockerignore`
keeps `node_modules` out of the image (the widget runs its own `npm install` at build).

> **Deployment gate:** the package engine needs `/app/npm` present, i.e. an image
> built from the commit that relocated the workspace (or later). Older images must use
> the local engine. The default-local path is unaffected.

## Switching, in practice

- **Helper-based bundle:** `chat_widget_ui_config(engine="package")` ↔ `engine="local"`.
- **Inline bundle / runtime `bundles.yaml`:** keep both blocks, one commented, and
  toggle which is active; then `kdcube refresh`.

No code changes are needed to switch — only the knob.

## Status

- [x] Widget consumes `@kdcube/components-react/chat` via the package engine (opt-in).
- [x] One-knob switch; default stays local; regression-safe default build.
- [x] Package source ships in the image via the app tree (`npm://` + `/app/npm`).
- [x] Default widget, `VITE_CHAT_ENGINE=package`, and `VITE_CHAT_UI=package`
  build successfully from the repo workspace.
- [ ] Make `package` the default once validated across environments.
- [ ] Publish / registry path for external consumers (still deferred).

## Readiness To Flip The Default

Build readiness is green: all three widget modes compile. The remaining gate is
runtime validation, not source availability:

- Login/config handshake in iframe host.
- Conversation list/create/switch/delete.
- Send/follow-up streaming.
- Context attach/remove, including `conv:`, `mem:record:*`, `task:*`, and `fi:*`.
- Host events: auth-required, object-open, pin-conversation, canvas-patch,
  view-change.
- Namespace style propagation into chat chips and search results.

Until those pass in local + staging, keep the default on the in-tree engine and
opt into package mode per bundle/profile.
