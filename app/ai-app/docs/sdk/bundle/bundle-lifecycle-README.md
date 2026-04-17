---
id: ks:docs/sdk/bundle/bundle-lifecycle-README.md
title: "Bundle Lifecycle"
summary: "How a bundle is discovered, loaded, initialized, invoked, and what storage/config surfaces are available across those phases."
tags: ["sdk", "bundle", "lifecycle", "storage", "configuration", "entrypoint"]
keywords: ["agentic_workflow", "on_bundle_load", "execute_core", "pre_run_hook", "post_run_hook", "singleton", "bundle_props", "bundle_storage_root", "ui", "main_view", "_ensure_ui_build"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/sdk/bundle/bundle-storage-cache-README.md
  - ks:docs/sdk/bundle/bundle-knowledge-space-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-venv-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
---
# Bundle Lifecycle

This doc explains the **runtime lifecycle** of a bundle and the **storage/config surfaces** available to it.

Read it together with:

- [Bundle Runtime](bundle-runtime-README.md) for request/tool runtime surfaces
- [Bundle Interfaces](bundle-interfaces-README.md) for streaming/widgets/operations
- [Client Communication](bundle-client-communication-README.md) when the bundle also ships client code

## Mental model

Write bundle code as **stateless per invocation**.

Even if a deployment enables singleton reuse, durable bundle state should live in:
- bundle props
- secrets
- Redis KV cache
- bundle storage backend
- shared local bundle storage
- cached per-bundle venvs for decorated external Python callables

Do **not** rely on Python instance fields as durable cross-request state.

## Lifecycle at a glance

```mermaid
flowchart TD
    R[Bundle registry entry] --> D["@agentic_workflow discovery"]
    D --> I[Instantiate entrypoint]
    I --> L[on_bundle_load once per process per tenant/project]
    L --> Q[Incoming turn or REST operation request]
    Q --> B[Build request routing/comm_context + refresh bundle props]
    B --> P[pre_run_hook]
    P --> E[execute_core]
    E --> V[@venv boundary optional]
    V --> O[post_run_hook]
```

## Main phases

| Phase | When | What happens |
|---|---|---|
| Discovery | Proc startup / bundle load | Loader imports the bundle module and finds the class decorated with `@agentic_workflow` |
| Instantiation | Per request by default | Entrypoint instance is created with `config`, `comm_context`, `pg_pool`, `redis` |
| One-time init | Once per process per tenant/project | `on_bundle_load(...)` may prepare indexes, local caches, repos, or other bundle-local assets |
| Request prep | Every invocation | Request-bound routing/identity is rebuilt, singleton instances are rebound, bundle props are loaded/merged, hooks can run |
| Execution | Every invocation | `execute_core(...)` handles the chat turn or bundle operation |
| Decorated external execution | On demand inside an invocation | `@venv(...)` functions run in a cached per-bundle subprocess venv; the venv is rebuilt only when its `requirements.txt` hash changes |
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
- the singleton cache is by loaded bundle spec in the current proc, not by request
- the same singleton instance may therefore serve multiple turns and REST operations over time
- `rebind_request_context(...)` refreshes request-bound objects such as `comm_context`
- `on_bundle_load(...)` still runs only once per process per tenant/project

Even with singleton reuse, you should still treat runtime memory as ephemeral and non-authoritative.

Important:
- singleton reuse does **not** make `self.comm`, current actor, current user, current conversation, or current turn durable
- those are per-invocation surfaces and must be treated as request-local
- do not cache request-bound values from one call and reuse them in another
- platform does not serialize singleton invocations for you; bundle code may still be used concurrently
- what the platform guarantees is that request execution context is rebound per invocation and must not live as shared singleton state

## Entrypoint methods and timing

| Method | Frequency | Purpose |
|---|---|---|
| `on_bundle_load(**kwargs)` | once per process per tenant/project | build indexes, warm caches, clone repos, prepare local read-only assets |
| `pre_run_hook(state=...)` | every invocation | last-minute validation or reconciliation |
| `execute_core(state=..., thread_id=..., params=...)` | every invocation | main bundle logic |
| `post_run_hook(state=..., result=...)` | every invocation | final bookkeeping |
| `rebind_request_context(...)` | singleton reuse only | refresh request-local handles on cached instance before the current call runs |

Important:
- `on_bundle_load(...)` is intended to be deterministic and idempotent
- do not store request-local state there
- use storage for durable state, not instance fields

`@venv(...)` is separate from `on_bundle_load(...)`:
- it is not a one-time init hook
- it is evaluated lazily when the decorated callable is invoked
- it uses bundle-managed local storage for its cached venv
- it should be treated as an execution boundary, not as part of the shared proc instance lifecycle
- its cache key is effectively one venv per bundle id
- code reload and venv rebuild are separate:
  - Python source changes still require normal bundle reload in proc
  - `requirements.txt` changes rebuild the cached venv on the next decorated call

## Request-bound communicator and REST operations

Bundle operations use the same request-bound communicator model as normal chat turns.

For each REST call to:
- `/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}`
- `/bundles/{tenant}/{project}/{bundle_id}/public/{operation}`

proc builds a fresh `ChatTaskPayload` from the current request/session:
- tenant and project
- user/session identity
- request id
- bundle id
- stream/socket id when the client propagated it

That `comm_context` is passed into `get_workflow_instance(...)`.

What happens next depends on instance lifetime:

- non-singleton bundle:
  - a fresh entrypoint instance is created for the request
  - `self.comm_context` is request-local from the start
  - `self.comm` is built from that request context
- singleton bundle:
  - the cached instance is reused
  - before the operation runs, `rebind_request_context(...)` refreshes request-local state
  - for `BaseEntrypoint`, rebinding updates task-local request context so `self.comm_context` / `self.comm` resolve against the current invocation instead of shared instance state
  - the shared singleton object may still hold bundle-owned shared state, but request execution context must be treated as per-invocation only

So yes: the bundle does receive a request-specific communicator for REST calls, but it is passed indirectly through `comm_context`, not as a separate operation argument.

Practical rule:
- inside bundle code, use `self.comm` / `self.comm_context` as request-bound surfaces
- if the bundle is singleton, never retain an old communicator reference across requests

For custom singleton bundles that do not use `BaseEntrypoint`, the platform now
also exposes request-local helpers via `kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx`:
- `get_current_request_context()`
- `get_current_comm()`

Those helpers are bound by the platform for queued `run(...)` execution and for
REST/widget invocation paths. Use them instead of storing request execution
context on the shared singleton object.

## What changes apply to new requests

Three classes of changes matter during bundle development:

1. **Runtime/admin prop overrides**
   - stored in Redis
   - picked up by `refresh_bundle_props(...)` at invocation start
   - affect new requests immediately

2. **Descriptor-backed bundle config**
   - comes from `bundles.yaml`
   - affects new requests after proc reapplies the descriptor (`reset-env` / CLI `--bundle-reload`)

3. **Bundle code changes**
   - require proc cache eviction before the next request should load the updated module
   - for local development, the intended path is:
     - `kdcube --workdir <runtime-workdir> --bundle-reload <bundle_id>`

4. **`@venv` requirements changes**
   - if only `requirements.txt` changed, the next call to the decorated function will rebuild the cached venv automatically
   - if Python source code changed, proc still needs the normal bundle reload so the updated module is imported before the next request
   - if both code and requirements changed, do the normal reload; the venv rebuild will then happen lazily on the next decorated call

Practical rule:

- current in-flight requests continue with the code/config they already loaded
- new requests pick up:
  - Redis prop edits immediately
  - descriptor/code changes after proc cache clear + descriptor replay
  - `requirements.txt` changes for `@venv` callables when that callable is next invoked

## `@venv(...)` in the lifecycle

`@venv(...)` allows selected bundle callables to run in a cached per-bundle subprocess venv while the rest of the bundle remains in the shared proc interpreter.

Current runtime behavior:
- the decorated callable is the boundary
- proc serializes the call arguments and return value across the subprocess boundary
- the runtime resolves the bundle id and bundle root
- the runtime creates or reuses a cached venv under bundle-managed local storage at `_bundle_venvs/<bundle-id>`
- the venv is created from the selected base Python (`python=` override when set, otherwise the current runtime base interpreter)
- the venv reuses platform/runtime packages by writing a runtime overlay `.pth`
- bundle-specific requirements are then installed on top of that runtime layer from the bundle's `requirements.txt`
- the callable is executed in a subprocess using that venv's Python
- the result is deserialized back into proc

Current cache rule:
- one cached venv per bundle id
- rebuild only when the referenced `requirements.txt` content hash changes

Practical implications:
- use `@venv(...)` for dependency-heavy leaf jobs
- keep communicator use, DB/Redis pooled access, and request-bound runtime objects in proc
- pass serializable data into the decorated callable and return serializable data out
- bundle-local dataclasses and similar bundle-defined types are acceptable only when they live in normal importable bundle modules
- do not treat `@venv(...)` as a generic transport for framework request objects, DB pools, Redis clients, or live SDK runtime handles
- do not assume proc-bound runtime helpers exist in the child; `self.comm`, `self.comm_context`, `get_current_comm()`, `get_current_request_context()`, `TOOL_SUBSYSTEM`, `COMMUNICATOR`, `KV_CACHE`, and `CTX_CLIENT` are proc-side surfaces, not venv-child surfaces

See:
- [Bundle Dev](bundle-dev-README.md)
- [Bundle Interfaces](bundle-interfaces-README.md)
- [Bundle Venv](bundle-venv-README.md)

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
  [bundle-client-communication-README.md](bundle-client-communication-README.md)
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
