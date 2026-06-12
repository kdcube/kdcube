---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/node/node-backend-sidecar-README.md
title: "Node Backend Sidecar"
summary: "How a bundle starts and calls a local Node or TypeScript backend sidecar from the KDCube runtime, including local machine and Docker chat-proc behavior."
tags: ["sdk", "node", "typescript", "sidecar", "bundle", "runtime"]
keywords: ["node backend sidecar", "typescript bundle backend", "python to node bridge", "chat-proc node runtime", "docker node sidecar", "bundle local backend"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24/entrypoint.py
---
# Node Backend Sidecar

Use this pattern when:
- the KDCube-facing app is still a Python bundle
- some backend logic already exists in Node or TypeScript
- you want a real runnable backend process, not a one-shot shell bridge

## What KDCube owns

KDCube still owns:
- bundle lifecycle
- bundle props and bundle secrets
- API, widget, MCP, and cron registration
- request auth and role gating
- per-tenant/project runtime scope

Node stays behind a bundle-local sidecar boundary.

## Runtime model

The supported shape is:

```text
Python bundle entrypoint
  -> ensure_local_sidecar(...)
  -> bundle-local Node process
  -> narrow HTTP calls into the Node backend
```

This is a **process-local sidecar**, not a direct FastAPI replacement.

## Reusable SDK helper

Public runtime helper:

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/node/runtime_bridge.py`

Public sidecar launcher files:

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/node/sidecar/cli.mjs`
- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/node/sidecar/ts_loader.mjs`

The helper:
- resolves the current bundle root
- resolves the bundle-local Node source dir
- starts a per-bundle/per-tenant/per-project Node sidecar
- calls it through HTTP
- keeps the Python bundle as the public app surface

## Minimal public example

Runnable example bundle:

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24/entrypoint.py`

Its Node backend source lives inside the bundle:

- `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24/backend_src/src/bridge_app.ts`

What it demonstrates:
- Python `@api(...)` methods calling the Node sidecar
- Python `@mcp(...)` endpoint wrapping the same Node backend
- a `.ts` backend file loaded through the Node bridge loader

## How to wrap an existing Node backend

Use this conversion rule:

- keep the existing Node or TS business logic
- add a thin Python bundle shell in `entrypoint.py`
- move only the KDCube-facing contract into Python
- run the existing Node code as the bundle-local sidecar

That means:

- Python owns:
  - `@api(...)`
  - `@mcp(...)`
  - `@ui_widget(...)`
  - `@cron(...)`
  - bundle props and secrets
  - auth and role gating
- Node owns:
  - internal domain logic
  - backend routes registered through the bridge
  - optional live reconfigure handling

Practical wrapping steps:

1. Put the existing Node backend under `backend_src/` inside the bundle folder.
2. Keep the Python bundle entrypoint as the public app shell.
3. Start the sidecar through the runtime bridge helper.
4. Call the Node routes from Python bundle methods.
5. Keep startup config and live config separate.

Do not expose the Node process directly as the public KDCube app surface.
The KDCube app surface still belongs to the Python bundle.

## Bundle layout

```text
my.bundle@1-0/
  entrypoint.py
  backend_src/
    package.json
    src/
      bridge_app.ts
```

The bundle itself can come from:
- a local filesystem folder
- or a Git bundle spec with `repo`, `ref`, and `subdir`

The Node helper resolves the loaded bundle root first, so the same bridge
pattern works for both local and Git-backed bundles.

## How the Node module is invoked

The Python side starts Node through:
- `BaseEntrypoint.ensure_local_sidecar(...)`

That injects:
- `KDCUBE_BUNDLE_ID`
- `KDCUBE_TENANT`
- `KDCUBE_PROJECT`
- `KDCUBE_BUNDLE_ROOT`
- `KDCUBE_BUNDLE_STORAGE_ROOT`

The Node bridge adds:
- `KDCUBE_NODE_BRIDGE_ENTRY`
- `KDCUBE_NODE_BRIDGE_SOURCE_ROOT`
- `KDCUBE_NODE_BRIDGE_ALLOWED_PREFIXES`

The sidecar launcher then:
1. loads the configured entry module
2. asks it to register explicit routes
3. exposes `/healthz`
4. serves only the registered route prefixes

## Startup config vs live config

Treat Node bridge config as two classes:

- **startup config**
  - entry module
  - source dir
  - allowed prefixes
  - bootstrap env
- **live config**
  - feature flags
  - search limits
  - labels
  - runtime behavior knobs

The SDK helper now supports both:

- startup config is hashed into a startup fingerprint
- if that fingerprint changes, the running sidecar is stopped and restarted
- live config can be pushed into the running sidecar through:
  - `POST /__kdcube/reconfigure`

This keeps restart-only settings and live-applied settings separate.

Recommended bundle prop split:

```yaml
node_backend:
  source_dir: backend_src
  entry: src/bridge_app.ts
  allowed_prefixes:
    - /api/projects
    - /mcp/projects
  runtime:
    status_label: "ready"
    search_prefix: "demo:"
```

Interpretation:

- `source_dir`, `entry`, and `allowed_prefixes` are startup config
- `runtime` is live config

## Contract for the Node entry module

Export:

```ts
export async function registerBridgeRoutes(registry, context) {
  registry.get('/api/projects/status', async () => {
    return {
      status: 200,
      data: { ok: true }
    }
  })
}
```

Available registry methods:
- `get(path, handler)`
- `post(path, handler)`
- `put(path, handler)`
- `patch(path, handler)`
- `delete(path, handler)`

Handler input:
- `method`
- `path`
- `headers`
- `query`
- `body`
- `context`

Handler output:

```json
{
  "status": 200,
  "data": {
    "ok": true
  }
}
```

Optional live reconfigure hook:

```ts
export async function reconfigureBridge({ config, fingerprint, context }) {
  return {
    status: 200,
    data: { applied: true, fingerprint }
  }
}
```

If the Node module does not export `reconfigureBridge(...)`, the sidecar still
accepts `POST /__kdcube/reconfigure` and updates `context.liveConfig` and
`context.liveConfigFingerprint` for later handlers.

## Local machine behavior

On a local KDCube runtime:
- the bundle runs in the proc Python process
- the Node backend starts as another local child process
- the sidecar is scoped by `bundle_id + tenant + project`
- repeated calls reuse the same sidecar inside that proc worker

## Sidecar lifecycle

Current lifecycle contract:

- `ensure_local_sidecar(...)` reuses the running sidecar for the same:
  - loaded bundle spec in the current worker
  - `tenant`
  - `project`
  - sidecar `name`
- proc shutdown stops all running local sidecars
- bundle registry updates for a bundle stop that bundle's local sidecars
- the next API or MCP call recreates the sidecar lazily

That means:

- **bundle delivery/code reload**
  - the sidecar is stopped
  - code/module caches are evicted
  - the next request starts a fresh Node sidecar
- **props-only live update**
  - the sidecar is **not** restarted automatically by proc
  - the bundle instance receives `on_props_changed(...)` when effective props changed
  - startup fingerprint mismatch is applied lazily on the next sidecar use
  - optional live config is pushed lazily on the next sidecar use through
    `POST /__kdcube/reconfigure`

So if sidecar startup behavior depends on bundle props such as:
- entry module
- source dir
- allowed prefixes
- Node-side env contract

then a props-only update does not proactively restart the sidecar at publish time.

What happens instead:

- if the changed props affect startup config:
  - the next bridge call detects the fingerprint mismatch
  - the sidecar is restarted before serving that call
- if the changed props affect only live config:
  - the next bridge call posts the updated config to `/__kdcube/reconfigure`
  - the sidecar keeps running

If the Python bundle also keeps its own long-lived side effects, override:

- `on_props_changed(...)`

Use that hook to invalidate Python-side caches or reconcile other long-lived
state. The Node bridge itself already handles sidecar startup/live config split,
so most wrappers do not need extra sidecar code in that hook.

## Docker behavior

In Docker-based KDCube runtime:
- the Python bundle still runs in `chat-proc`
- the Node sidecar is started **inside the same `chat-proc` container**
- it is still a separate sidecar process from the bundle point of view
- it is not a separate external service by default

Current Docker images already install Node for:
- bundle UI builds
- Claude Code support

So when KDCube runs in Docker:
- the Python bundle and the Node sidecar share the same `chat-proc` container
- the sidecar is still a separate process with its own lifecycle
- no separate sidecar service definition is required for this pattern

That is why the same Node sidecar helper works in local Docker runtime without
adding a second app-specific container for this example.

## Trust boundary

Keep this boundary explicit:
- Python reads props and secrets
- Python owns auth and role checks
- Python decides which Node routes are callable
- Node gets only the concrete values it needs

Do not move KDCube-native secret lookup or bundle auth decisions into the Node side.

## When to use this pattern

Use it for:
- existing Node or TypeScript domain backends
- backend code you want to keep in its current language
- MCP or API surfaces that still need KDCube bundle ownership on the Python side

Do not use it when:
- Node is trying to replace the Python bundle entrypoint itself
- the backend needs unrestricted host access
- the boundary is so wide that the bundle no longer owns its own public surface
