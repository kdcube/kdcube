# AI Bundles in KDCube (Registry + Runtime)

This document describes how AI bundles (dynamic plugins) are configured, stored, loaded, and executed in this service.

It is written for the chat service located at:
- [kdcube_ai_app/apps/chat](../..)

## What is a bundle

A bundle is a Python module or package that exposes a decorated workflow class or factory. The runtime loads it
from a path + module spec, instantiates it, and calls `run(...)` (or another named operation) while streaming
events back to the client.

Bundles are multi-tenant capable. The registry is stored per (tenant, project) in Redis and mirrored into
process memory for fast resolution.

## Configuration sources

### 1) Environment variable: `AGENTIC_BUNDLES_JSON`

Accepted shape:
```json
{
  "default_bundle_id": "with.codegen",
  "bundles": {
    "with.codegen": {
      "id": "with.codegen",
      "name": "Codegen Agentic App",
      "path": "/bundles/codegen",
      "module": "codegen.entrypoint",
      "singleton": false,
      "description": "Codegen Agentic App"
    }
  }
}
```

Fields:
- `id` (string, required): bundle id.
- `name` (string, optional): friendly name.
- `path` (string, required): container-visible path to bundle root (dir, .py, .zip, .whl).
- `module` (string, optional): dotted module name inside the path (required for zip/whl).
- `singleton` (bool, optional): if true, the workflow instance is cached and reused.
- `description` (string, optional).
- `version` (string, optional): bundle version (computed content hash prefix).

### 2) Redis registry (source of truth at runtime)

The registry is stored in Redis under:
- key: `kdcube:config:bundles:mapping:{tenant}:{project}`
- channel: `kdcube:config:bundles:update`

The registry is loaded at chat service startup and kept in memory. Updates are published to the channel
and picked up by all processors.

### 3) Admin APIs (update registry + broadcast)

- `GET /admin/integrations/bundles`
  - Returns the current registry (reads Redis, falls back to in-memory registry).

- `POST /admin/integrations/bundles`
  - Body: `{ op: "replace"|"merge", bundles: {...}, default_bundle_id?: "..." }`
  - Updates the in-memory registry, mirrors to env, clears bundle caches, broadcasts a config update.

- `POST /admin/integrations/bundles/reset-env`
  - Reloads from `AGENTIC_BUNDLES_JSON` and overwrites Redis + memory.

### Cache cleanup (retiring old bundle versions)

- `POST /admin/integrations/bundles/cleanup`
  - Body: `{ drop_sys_modules?: true }`
  - Evicts cached bundle modules/singletons not present in the current registry.
  - Use this after rolling out a new bundle path/version to release memory held by old versions.

## Bundle props (runtime overrides)

Bundles can expose runtime props (config overrides) that are stored per tenant/project/bundle in Redis.
The bundle reads them through the KV cache layer and merges them with code defaults.

Redis keys and channel:
- key: `kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}`
- channel: `kdcube:config:bundles:props:update`

### How bundles read props

Base entrypoint behavior:
- `bundle_props_defaults` provides code defaults (override in the bundle entrypoint).
- `refresh_bundle_props(...)` loads overrides from Redis (via KV cache) and merges with defaults.
- `self.bundle_props` is available to the workflow at runtime.
- `bundle_version` is injected into defaults from the live bundle configuration and is always returned
  in props (read-only; not persisted in Redis).

Implementation: [BaseEntrypoint](../../sdk/solutions/chatbot/entrypoint.py)

### Admin APIs for props

- `GET /admin/integrations/bundles/{bundle_id}/props?tenant=...&project=...`
  - Returns `{ props, defaults }` for the bundle (includes `bundle_version` from live bundle config).

- `POST /admin/integrations/bundles/{bundle_id}/props`
  - Body: `{ tenant?, project?, op: "replace"|"merge", props: {...} }`
  - Stores props in Redis (source of truth for overrides).
  - `bundle_version` is ignored on write; it is controlled by code.

- `POST /admin/integrations/bundles/{bundle_id}/props/reset-code`
  - Body: `{ tenant?, project? }`
  - Rewrites Redis props with the bundle's code defaults.

### KV cache abstraction

Bundles read props via the KV cache wrapper (Redis-backed). See:
- [KV Cache](../../../../infra/service_hub/cache-README.md)

## Registry loading and sync

On chat service startup, the registry is loaded from Redis and mirrored to the in-process registry:
- [web_app.py](../web_app.py)
  - `bundle_store.load_registry(...)` -> `bundle_registry.set_registry(...)`

Processors also subscribe to config updates:
- [processor.py](../../processor.py)
  - subscribes to `kdcube:config:bundles:update`
  - applies updates and clears agentic loader caches

## Bundle runtime flow

1) Ingress (Socket.IO or SSE) receives a message and enqueues a chat task.
2) `EnhancedChatRequestProcessor` dequeues tasks and invokes the chat handler.
3) The handler resolves the bundle id and loads the workflow via [agentic_loader.py](../../../../infra/plugin/agentic_loader.py).
4) The workflow emits events (steps + deltas) to the client via ChatCommunicator.
5) The workflow returns a JSON result (final_answer, followups, etc.).

Key call site:
- [web_app.py](../web_app.py) -> `agentic_app_func` -> `resolve_bundle(...)` -> `get_workflow_instance(...)` -> `workflow.run(...)`

## Bundle entrypoint API

### Required: workflow class or factory

A bundle must provide a decorated workflow class or factory:

```python
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, agentic_workflow_factory

@agentic_workflow(name="my.bundle", version="1.0.0", priority=100)
class MyBundleWorkflow:
    def __init__(self, config, comm_context=None, pg_pool=None, redis=None):
        self.config = config
        self.comm_context = comm_context
        self.pg_pool = pg_pool
        self.redis = redis

    async def run(self, **params):
        ...
        return {"final_answer": "...", "followups": []}
```

Notes:
- Only decorated classes or factories are recognized.
- `priority` controls selection when multiple are present.
- The loader passes only supported kwargs from: `comm_context`, `pg_pool`, `redis`.
- If `singleton` is true (spec or decorator meta), the instance is cached.
- Bundle version is computed from bundle content (hash prefix) on load.

### Optional: operations beyond `run`

The integrations API can call named operations on the workflow instance:
- `POST /integrations/bundles/{tenant}/{project}/operations/{operation}`
- If the workflow defines a method named `{operation}`, it will be invoked.
- Example operation: `suggestions` or `news`.

## Bundle registry + runtime diagram

```text
                             +----------------------------+
                             | AGENTIC_BUNDLES_JSON (env)  |
                             +--------------+-------------+
                                            |
                                            v
+----------------------+        +----------------------------+        +--------------------------+
| Admin Integrations   |<------>| Redis bundle registry      |<------>| Processor config listener|
| API (CRUD + reset)   | publish| kdcube:config:bundles:...  |  subscribe | apply + clear caches   |
+----------+-----------+        +--------------+-------------+        +------------+-------------+
           |                                   |                                      |
           | GET /admin/integrations/bundles   | load on startup                      |
           v                                   v                                      v
+----------------------+        +----------------------------+        +--------------------------+
| UI / client config   |        | In-process registry        |        | Agentic loader caches    |
+----------+-----------+        | bundle_registry.py         |        | (module + singleton)     |
           |                    +--------------+-------------+        +------------+-------------+
           |                                   |                                      |
           | bundle_id in request              | resolve_bundle(...)                  |
           v                                   v                                      v
+----------------------+        +----------------------------+        +--------------------------+
| Ingress (SSE/WS)     | -----> | agentic_app_func           | -----> | Workflow instance.run()  |
| enqueue task         |        | resolve -> load -> execute |        | emit steps + deltas      |
+----------------------+        +----------------------------+        +--------------------------+
```

## Practical notes

- The registry always injects a built-in bundle `kdcube.admin` (admin-only). If the configured default bundle cannot be loaded, the system falls back to this bundle so you can recover the UI and fix configuration without restarting services.
- Live updates (no restart): publish new bundle code to a new path/module, update the registry to point to it, then optionally call `POST /admin/integrations/bundles/cleanup` to evict old cached versions across all instances.
- The `path` must be valid inside the container/runtime. In docker-compose deployments, host paths are
  usually mounted into `/bundles` and referenced in `AGENTIC_BUNDLES_JSON`.
- The registry is per-tenant/project (via Redis key). If no registry is present, the service seeds
  it from `AGENTIC_BUNDLES_JSON` on first load.
- `clear_agentic_caches()` is called on updates so new bundles or code changes are picked up.

## Relevant code

- Registry persistence: [bundle_store.py](../../../../infra/plugin/bundle_store.py)
- In-memory registry: [bundle_registry.py](../../../../infra/plugin/bundle_registry.py)
- Loader + decorators: [agentic_loader.py](../../../../infra/plugin/agentic_loader.py)
- Startup registry load: [web_app.py](../web_app.py)
- Processor update listener: [processor.py](../../processor.py)
- Admin APIs: [integrations.py](./integrations.py)
