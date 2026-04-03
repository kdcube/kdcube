---
id: ks:docs/sdk/bundle/bundle-lifecycle-README.md
title: "Bundle Lifecycle"
summary: "How a bundle is discovered, loaded, initialized, invoked, and what storage/config surfaces are available across those phases."
tags: ["sdk", "bundle", "lifecycle", "storage", "configuration", "entrypoint"]
keywords: ["agentic_workflow", "on_bundle_load", "execute_core", "pre_run_hook", "post_run_hook", "singleton", "bundle_props", "bundle_storage_root", "ui", "main_view", "_ensure_ui_build"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-config-README.md
  - ks:docs/sdk/bundle/bundle-storage-cache-README.md
  - ks:docs/sdk/bundle/bundle-knowledge-space-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/clients/client-communication-README.md
---
# Bundle Lifecycle

This doc explains the **runtime lifecycle** of a bundle and the **storage/config surfaces** available to it.

Read it together with:

- [Bundle Runtime](bundle-runtime-README.md) for request/tool runtime surfaces
- [Bundle Interfaces](bundle-interfaces-README.md) for streaming/widgets/operations
- [Client Communication](../../clients/client-communication-README.md) when the bundle also ships client code

## Mental model

Write bundle code as **stateless per invocation**.

Even if a deployment enables singleton reuse, durable bundle state should live in:
- bundle props
- secrets
- Redis KV cache
- bundle storage backend
- shared local bundle storage

Do **not** rely on Python instance fields as durable cross-request state.

## Lifecycle at a glance

```mermaid
flowchart TD
    R[Bundle registry entry] --> D[@agentic_workflow discovery]
    D --> I[Instantiate entrypoint]
    I --> L[on_bundle_load once per process per tenant/project]
    L --> Q[Incoming turn or operation request]
    Q --> B[Refresh request context + bundle props]
    B --> P[pre_run_hook]
    P --> E[execute_core]
    E --> O[post_run_hook]
```

## Main phases

| Phase | When | What happens |
|---|---|---|
| Discovery | Proc startup / bundle load | Loader imports the bundle module and finds the class decorated with `@agentic_workflow` |
| Instantiation | Per request by default | Entrypoint instance is created with `config`, `comm_context`, `pg_pool`, `redis` |
| One-time init | Once per process per tenant/project | `on_bundle_load(...)` may prepare indexes, local caches, repos, or other bundle-local assets |
| Request prep | Every invocation | Request-bound context is refreshed, bundle props are loaded/merged, hooks can run |
| Execution | Every invocation | `execute_core(...)` handles the turn or operation |
| Completion | Every invocation | `post_run_hook(...)` can finalize bookkeeping |

## Instance lifetime

### Default mental model

Treat each incoming chat turn or operation call as a fresh invocation:
- one request
- one execution path
- one result

### Singleton reuse

The registry may enable `singleton=true`.

If that happens:
- the entrypoint instance may be reused
- `rebind_request_context(...)` refreshes request-bound objects such as `comm_context`
- `on_bundle_load(...)` still runs only once per process per tenant/project

Even with singleton reuse, you should still treat runtime memory as ephemeral and non-authoritative.

## Entrypoint methods and timing

| Method | Frequency | Purpose |
|---|---|---|
| `on_bundle_load(**kwargs)` | once per process per tenant/project | build indexes, warm caches, clone repos, prepare local read-only assets |
| `pre_run_hook(state=...)` | every invocation | last-minute validation or reconciliation |
| `execute_core(state=..., thread_id=..., params=...)` | every invocation | main bundle logic |
| `post_run_hook(state=..., result=...)` | every invocation | final bookkeeping |
| `rebind_request_context(...)` | singleton reuse only | refresh request-local handles on cached instance |

Important:
- `on_bundle_load(...)` is intended to be deterministic and idempotent
- do not store request-local state there
- use storage for durable state, not instance fields

## Storage and isolation surfaces

| Surface | Access | Isolation | Use it for |
|---|---|---|---|
| `bundle_props` | read | tenant + project + bundle | effective non-secret configuration |
| `get_secret(...)` | read | secret key namespace | API keys, tokens, credentials |
| Redis KV cache | read/write | whatever keys you choose | lightweight distributed state, flags, small caches |
| Bundle storage backend (`CB_BUNDLE_STORAGE_URL`) | read/write | tenant + project + bundle | persistent bundle data on file/S3 storage |
| Shared local bundle storage (`BUNDLE_STORAGE_ROOT`) | read/write by bundle code | tenant + project + bundle | large local/EFS caches, cloned repos, indexes, read-only assets |
| Current turn `OUT_DIR` / `workdir` | read/write during execution | current invocation | transient turn files, generated artifacts, isolated exec inputs/outputs |

## Storage diagram

```text
Bundle developer surfaces

  Config / identity
    bundle_props
    get_secret(...)
    comm_context.actor.{tenant_id, project_id, user_id, ...}

  Distributed state
    Redis KV cache
    CB_BUNDLE_STORAGE_URL-backed storage

  Local shared state
    BUNDLE_STORAGE_ROOT/<tenant>/<project>/<bundle_id>/
      indexes/
      repos/
      caches/
      assets/

  Per-invocation execution state
    OUT_DIR / WORKDIR
```

## User, tenant, and request context

Bundle code can access request identity through the request context:
- tenant
- project
- user / actor metadata
- conversation and turn ids

This context is request-bound. Do not cache it as durable bundle state.

## Custom bundle UI

A bundle can ship a custom frontend (a Vite/React SPA) that is built once at load time
and served to the browser as a standalone panel.

### How it works

1. Configure `ui.main_view` in `bundle_props` (code defaults or `bundles.yaml`):

```python
@property
def configuration(self):
    return {
        "ui": {
            "main_view": {
                "src_folder": "ui-src",          # relative to bundle root
                "build_command": "npm install && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
            }
        }
    }
```

2. `on_bundle_load(...)` calls `_ensure_ui_build()` which:
   - resolves `src_folder` relative to the bundle root
   - runs `build_command` (with `<VI_BUILD_DEST_ABSOLUTE_PATH>` substituted)
   - stores the build output under `<bundle_storage_root>/ui/`
   - writes a `.ui.signature` file so the build is skipped on subsequent loads if nothing changed

3. The built SPA is served by the processor's static endpoint:

```
GET /api/integrations/static/{tenant}/{project}/{bundle_id}/{path}
```

The endpoint computes the content hash of the bundle directory (same algorithm as
`_apply_configuration_overrides`) to locate the correct `bundle_storage_root`, then
returns files from its `ui/` subdirectory. Missing paths fall back to `index.html`
for client-side routing.

### Notes

- The UI is built per process per tenant/project (same cadence as `on_bundle_load`).
- `node_modules/` and `package-lock.json` are excluded from the content hash so that
  `npm install` during the build does not change the hash.
- The built UI typically communicates back to the backend through the bundle operations
  endpoint (`POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}`)
  and receives runtime config (base URL, auth tokens, tenant/project) via `postMessage`
  from the host frame.
- Legacy callers may still use
  `POST /api/integrations/bundles/{tenant}/{project}/operations/{operation}`.
  When `bundle_id` is omitted there, proc resolves the current default bundle id.
- That UI is a normal platform client. If it needs bundle-originated progress or
  step events to target one exact connected peer, it must follow the client
  communication contract and propagate the connected peer id on REST requests.
- Reference implementation:
  - `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
  - `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx`
  That example shows a lightweight chat main view with bundle-scoped conversation browsing on top of the standard iframe handshake plus chat REST/SSE endpoints.
- See:
  [docs/clients/client-communication-README.md](../../clients/client-communication-README.md)
  and [docs/sdk/bundle/bundle-runtime-README.md](bundle-runtime-README.md)

## React integration

If a bundle uses the React agent:
- teach the agent bundle-specific behavior with skills
- expose bundle tools via `tools_descriptor.py`
- optionally expose `ks:` as a read-only logical namespace
- optionally back `ks:` from shared local bundle storage

See:
- [bundle-knowledge-space-README.md](bundle-knowledge-space-README.md)
- [../agents/react/react-turn-workspace-README.md](../agents/react/react-turn-workspace-README.md)

## Practical rules

- Use `on_bundle_load(...)` for heavy preparation that should happen before requests rely on it.
- Persist durable state in storage, not on `self`.
- Use shared local bundle storage for large local reusable assets.
- Use the bundle storage backend or Redis for distributed state that must survive host changes.
- Treat `OUT_DIR` and `workdir` as per-invocation execution state, not bundle state.
