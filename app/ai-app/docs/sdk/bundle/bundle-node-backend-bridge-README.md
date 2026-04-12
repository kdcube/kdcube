---
id: ks:docs/sdk/bundle/bundle-node-backend-bridge-README.md
title: "Bundle Node Backend Bridge"
summary: "Pattern for keeping the KDCube bundle surface in Python while delegating selected backend work to a local Node or TypeScript service through an explicit bridge."
tags: ["sdk", "bundle", "node", "typescript", "bridge", "backend", "rpc"]
keywords: ["node backend bridge", "typescript backend", "python bridge", "bundle backend", "express", "local rpc", "subprocess"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/sdk/agents/claude/claude-code-README.md
---
# Bundle Node Backend Bridge

Use this pattern when:
- the KDCube bundle remains the public app surface
- the bundle backend is still Python-hosted
- but some domain logic already exists in Node or TypeScript and should be reused

This is the supported mental model:

- KDCube owns the bundle lifecycle, props, secrets, runtime context, and public endpoints
- Python stays the entrypoint and integration layer
- Node or TypeScript stays behind an explicit bridge

Do **not** treat the Node app as a direct KDCube entrypoint replacement.

## Recommended structure

```text
my.bundle@1-0/
  entrypoint.py
  service.py
  backend_bridge/
    cli.mjs
    ts_loader.mjs
    sample_routes.ts
```

Typical responsibility split:

- `entrypoint.py`
  - declares `@api(...)`, `@ui_widget(...)`, `@on_message`, `@cron`
  - reads bundle props and secrets
  - performs request validation and authorization
- `service.py`
  - Python orchestration
  - prepares the payload for the Node bridge
  - interprets the bridge result and maps failures to bundle-facing errors
- `backend_bridge/cli.mjs`
  - local bridge executable
  - starts a transient or persistent local Node app
  - forwards the requested operation
  - returns a narrow JSON envelope

## Why the bridge exists

The Python bundle has KDCube-native access to:

- `self.bundle_prop(...)`
- `self.bundle_props`
- `get_secret("b:...")`
- `get_user_prop(...)`
- `get_user_secret(...)`
- communicator and request context
- bundle storage
- proc integrations and DB/Redis surfaces

The Node side does not get those implicitly.

That is intentional. It keeps:

- KDCube-specific logic in Python
- domain/backend reuse in Node or TS
- the boundary explicit and auditable

## Boundary rules

Keep the bridge contract narrow:

- explicit allowed HTTP methods
- explicit allowed path prefixes
- explicit repo/workdir root
- explicit JSON payload
- explicit JSON result envelope

Prefer a result shape like:

```json
{
  "ok": true,
  "status": 200,
  "data": {
    "items": []
  }
}
```

Avoid:

- exposing an unrestricted local Express server
- passing raw KDCube secrets wholesale into Node
- letting Node discover arbitrary host paths
- treating the bridge as a generic shell executor

## Props and secrets

Resolve KDCube config on the Python side first.

Normal rule:

- Python reads bundle props and secrets
- Python passes only the exact values the Node side needs

Example:

```python
repo_path = self.bundle_prop("backend.repo_path")
api_base = self.bundle_prop("backend.api_base", "/api/projects")
token = get_secret("b:backend.service_token")
```

Then pass those into the bridge explicitly through:

- stdin JSON
- command args
- a tightly scoped env block

Do not make the Node side depend on direct KDCube secret lookup.

## Public example

Public reference files live under:

- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/cli.mjs`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/ts_loader.mjs`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/sample_routes.ts`

What they demonstrate:

- transient local Express app startup
- strict route and method allowlisting
- request forwarding through `fetch(...)`
- a TypeScript loader trick so `.js` imports can resolve to `.ts`

Treat those files as a bridge template, not as a drop-in production server.

## How this fits bundle authoring

This pattern is useful when a bundle also needs:

- React v2 for planning or tool-driven work
- Claude Code for workspace-scoped code tasks
- bundle widgets or a custom main view
- scheduled jobs with `@cron(...)`
- dependency-heavy helpers behind `@venv(...)`

In that shape:

- Python bundle = app shell
- React / Claude / custom agents = optional worker brains
- Node bridge = optional domain backend reuse

One bundle can combine all of them.
