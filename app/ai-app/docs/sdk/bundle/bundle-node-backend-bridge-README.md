---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md
title: "Bundle Node Backend Bridge"
summary: "Bundle pattern for keeping the public KDCube app surface in Python while delegating selected backend logic to a bundle-local Node or TypeScript sidecar."
tags: ["sdk", "bundle", "node", "typescript", "bridge", "backend", "sidecar"]
keywords: ["bundle node backend", "typescript bundle backend", "python owned app surface", "node sidecar pattern", "bundle local backend process", "node integration example"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/node/node-backend-sidecar-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24/entrypoint.py
---
# Bundle Node Backend Bridge

Use this when:
- the application is still a KDCube bundle
- the public endpoints still belong to the Python bundle
- some backend logic should stay in Node or TypeScript

The mental model is:

- **application** = the bundle as exposed by KDCube
- **Node backend** = one internal implementation part of that bundle

Do **not** treat the Node backend as a direct replacement for the bundle entrypoint.

## Recommended shape

```text
my.bundle@1-0/
  entrypoint.py
  backend_src/
    package.json
    src/
      bridge_app.ts
```

Responsibility split:

- `entrypoint.py`
  - declares `@api(...)`, `@mcp(...)`, `@ui_widget(...)`, `@cron(...)`
  - reads bundle props and secrets
  - owns auth, roles, and public contract
- bundle-local Node backend
  - keeps Node or TS domain logic
  - runs as a bundle-local sidecar
  - stays behind a narrow route boundary

If you are wrapping an existing Node backend, this is the intended migration:

- keep the backend logic in Node or TS
- add a Python KDCube shell around it
- keep public KDCube surfaces in Python
- keep the Node backend internal to the bundle

## Public runtime support

Detailed runtime doc:
- [node-backend-sidecar-README.md](../node/node-backend-sidecar-README.md)

Reusable SDK runtime helper:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/node/runtime_bridge.py`

Runnable public example:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24/entrypoint.py`

## Public example behavior

The example demonstrates:
- Python bundle APIs calling Node
- Python MCP tools wrapping the same Node backend
- bundle-local Node source loaded from `backend_src/`
- no external cloud dependency
- startup config vs live config split for props
- lazy sidecar restart or lazy live reconfigure after props changes

## Boundary rules

Keep the contract narrow:
- explicit route prefixes
- explicit methods
- Python-side props and secret resolution
- Python-side auth and role gating
- Node-side domain logic only

That keeps the integration auditable and lets the same bundle work from:
- local filesystem bundles
- Git-backed bundles (`repo` + `ref` + `subdir`)

Practical rule:

- public contract, auth, roles, props, and secrets stay in Python
- Node is internal implementation code inside the bundle folder
- use bundle props to describe the bridge
- use the runtime bridge helper instead of custom subprocess management

## Reload behavior

Current reload behavior is:

- bundle update / bundle reload:
  - stops the bundle-local Node sidecar
  - evicts the Python bundle scope
  - recreates the sidecar lazily on the next call
- props-only live update:
  - does **not** proactively restart the already-running Node sidecar
  - startup-config drift is applied lazily on the next bridge call
  - live config can be pushed lazily on the next bridge call through
    `POST /__kdcube/reconfigure`

Practical rule:

- startup settings:
  - treat as restart-scoped
- live behavior settings:
  - treat as reconfigure-scoped

If your Python wrapper also keeps long-lived state, use:

- `on_props_changed(...)`

That hook now exists on `BaseEntrypoint` and runs when effective bundle props
changed for the active bundle instance.
