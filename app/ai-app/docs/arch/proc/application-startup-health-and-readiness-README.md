---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/proc/application-startup-health-and-readiness-README.md
title: "Application Startup, Health, And Readiness"
summary: "Canonical proc architecture for supervised application initialization, process-local and shared lifecycle hooks, aggregate service readiness, per-application admission, retries, and deployment probe mapping."
tags: [architecture, proc, applications, lifecycle, health, readiness, admission]
keywords: [application lifecycle supervisor, application preparation, service.readiness, application_not_ready, proc liveness, proc readiness, on_bundle_load, on_app_deploy, queue deferral, app admission]
updated_at: 2026-09-24
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/app-deployment-and-static-widget-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/ops/health-README.md
---

# Application Startup, Health, And Readiness

This document owns the proc-level contract for starting the application host,
preparing each configured application, reporting health, and admitting work.
The contract is the same when proc runs through Docker Compose, Kubernetes,
ECS, or a direct service process. Deployment adapters consume the HTTP health
surfaces; they do not redefine runtime readiness.

Application-specific lifecycle hooks are documented in
[Bundle Lifecycle](../../sdk/bundle/bundle-lifecycle-README.md). Static UI build
mechanics are documented in
[UI Components Lifecycle](../../sdk/bundle/ui-components-lifecycle-README.md).

## Four Separate Facts

KDCube represents four availability facts independently:

| Fact | Owner | Meaning |
| --- | --- | --- |
| Proc liveness | Proc process | The event loop and HTTP service are running and the process is not draining. |
| Aggregate proc readiness | Proc readiness registry | Every application declared `service.readiness: required` is ready for its desired generation. |
| Per-application readiness | Application lifecycle supervisor | This proc process completed local initialization and the shared resource barrier for the application's desired generation. |
| Admission | Each application door | A resolved application may begin this invocation because its current desired generation is ready in this proc process. |

An application may be `independent` and still be unavailable through its own
doors while it prepares. Independence controls aggregate proc readiness only;
it never bypasses application admission.

## Proc Startup Sequence

Proc starts core runtime facilities before application preparation finishes:

```text
load service configuration and core dependencies
  |
  +-- establish Redis/Postgres/service communication
  +-- load the authoritative application registry
  +-- create the process-local application lifecycle supervisor
  +-- publish desired application generations
  +-- start one owned preparation task per application
  +-- start turn, event, job, and scheduler processing
  +-- expose HTTP service

owned application tasks, bounded by configured concurrency
  |
  +-- app A: pending -> preparing -> ready
  +-- app B: pending -> preparing -> retrying -> ready
  +-- app C: pending -> preparing -> ready
```

The initial registry reconciliation schedules application work. It does not
wait for every application task to complete. A slow source checkout, npm build,
index preparation, or expired shared lock therefore affects that application,
not the core process lifespan.

## Desired Application State

The supervisor tracks one desired generation per `(tenant, project,
application_id)`. The generation is an internal fingerprint of inputs that can
change prepared behavior, including:

- application id, path, module, and singleton declaration;
- Git repo, ref, subdirectory, and resolved commit;
- source fingerprint;
- descriptor property fingerprint;
- runtime/resource schema generation.

The generation is an internal reconciliation identity, not an application
version shown to users. It exists so completion for an older source or config
cannot mark a newer desired state ready.

Lifecycle states are:

| State | Meaning |
| --- | --- |
| `pending` | Desired state is registered and preparation is scheduled. |
| `preparing` | The owned task is actively loading or reconciling resources. |
| `retrying` | The previous attempt failed and the owned task is waiting for its bounded backoff. |
| `ready` | Local initialization and shared resource reconciliation completed for the desired generation. |
| `failed` | A terminal failure state reserved for preparation policies that stop retrying. |

Current automatic preparation retries with exponential backoff bounded by the
assembly descriptor. Operator diagnostics retain a bounded error and retry
time; public callers receive only the actionable readiness fact.

## Descriptor Readiness Policy

Readiness policy belongs to the application item in `bundles.yaml`:

```yaml
- id: news@2026-05-20-12-05
  path: /bundles/kdcube/applications/src/demo/news@2026-05-20-12-05
  module: entrypoint
  singleton: true
  service:
    readiness: required
```

Values:

| Value | Aggregate `/health` | Application admission |
| --- | --- | --- |
| `independent` | Does not block proc readiness. This is the default. | Denied until this app is ready. |
| `required` | Blocks proc readiness until this app is ready. | Denied until this app is ready. |

Use `required` only when the proc instance should receive no normal service
traffic without that application. A user-facing or operationally important app
does not automatically need to be required; its own routes remain protected by
per-app admission either way.

`service.readiness` is platform lifecycle metadata. It is not an app property,
feature flag, or implication of `singleton`.

## Application Preparation Sequence

The complete preparation and publication path is:

```text
proc lifespan
apps/chat/proc/web_app.py
  |
  +-- load authoritative BundlesRegistry
  |     infra/plugin/bundle_store.py
  |     infra/plugin/bundle_registry.py
  |
  +-- ProcApplicationLifecycle.reconcile(registry)
  |     apps/chat/proc/app_lifecycle/runtime.py
  |       |
  |       +-- read descriptor props and source fingerprints
  |       +-- compute one desired generation per application
  |       |
  |       `-- ApplicationLifecycleSupervisor.reconcile(...)
  |             apps/chat/proc/app_lifecycle/supervisor.py
  |               |
  |               +-- publish desired state
  |               |     infra/plugin/app_readiness.py
  |               +-- own one task per application/generation
  |               +-- bound concurrent preparation with a semaphore
  |               `-- cancel and reap superseded generation tasks
  |
  +-- start processor work without waiting for app tasks
  |     apps/chat/processor.py
  |
  `-- owned preparation tasks run independently
        |
        +-- [1] resolve and materialize Git-backed source
        |         infra/plugin/bundle_registry.py
        |         resolve_git_bundle_entry_async()
        |
        +-- [2] import app and await process-local on_bundle_load()
        |         infra/plugin/bundle_loader.py
        |         preload_bundle_async()
        |
        +-- [3] validate configured UI aliases against @ui_widget
        |         apps/chat/proc/app_lifecycle/runtime.py
        |         validate_prepared_application_manifest()
        |
        +-- [4] reconcile the generation-fenced shared deployment
        |         apps/chat/proc/app_deployment/coordinator.py
        |         deploy_loaded_bundle_app_resources()
        |           +-- await on_app_deploy()
        |           +-- publish app-resource signature under shared lock
        |           `-- build/publish static UI and policy manifest
        |
        +-- [5] publish READY for this exact generation
        |         apps/chat/proc/app_lifecycle/supervisor.py
        |         infra/plugin/app_readiness.py
        |           `-- ready callback reconciles scheduler + Data Bus
        |                 apps/chat/processor.py
        |
        `-- [6] on failure or a newer desired generation
                  apps/chat/proc/app_lifecycle/supervisor.py
                    +-- failure: RETRYING + bounded exponential backoff
                    `-- supersession: cancel/reap old task; run new task
```

Readiness then feeds health and admission without starting lifecycle work:

```text
ApplicationReadinessRegistry
infra/plugin/app_readiness.py
  |
  +-- process liveness ------> GET /health/live
  |                              apps/chat/proc/web_app.py
  |
  +-- required-app aggregate -> GET /health
  |                              apps/chat/proc/web_app.py
  |
  +-- HTTP / MCP / UI --------> 503 application_not_ready
  |                              apps/chat/proc/rest/integrations/integrations.py
  |                              infra/plugin/bundle_loader.py
  |
  +-- chat / reactive turns --> release unstarted queue claim for redelivery
  |                              apps/chat/processor.py
  |
  +-- scheduler -------------> omit jobs until ready; reconcile on READY
  |                              apps/chat/sdk/runtime/bundle_scheduler.py
  |
  `-- Data Bus -------------> keep handlers inactive until ready
                                 apps/chat/sdk/runtime/data_bus/worker.py
```

The supervisor owns every task reference and observes every completion. One
slow or retrying application therefore leaves other application tasks and
processor work running. The readiness mode changes only aggregate `/health`:
every application remains admission-gated until its desired generation is
ready.

Preparation concurrency and retry timing are descriptor-owned:

```yaml
platform:
  services:
    proc:
      bundles:
        application_preparation_concurrency: 4
        application_preparation_retry_initial_seconds: 2
        application_preparation_retry_max_seconds: 60
```

## Hook Ownership

`on_bundle_load` and `on_app_deploy` are both part of preparation, with distinct
ownership:

| Hook | Scope | Contract |
| --- | --- | --- |
| `on_bundle_load` | Once per loaded app spec in each proc process | Prepare process-local handles, caches, local indexes, and app instance resources. It is async and idempotent. |
| `on_app_deploy` | Once per shared app-resource generation | Publish idempotent shared resources such as catalogs, schemas, indexes, projections, and generated assets. Shared signatures and locks coordinate workers. |

A shared completion signature proves shared publication only. Every proc
process still runs its own `on_bundle_load`; a Redis or filesystem done marker
cannot establish another process's local initialization.

UI compilation is one participant in `on_app_deploy`, not the definition of
application readiness. An application without UI may prepare other resources.

## Health Surfaces

### `GET /health/live`

This endpoint reports process liveness:

- `200` while the proc process is running;
- `503` while the process is draining;
- no application preparation state participates.

A long app build, checkout, or retry must not fail liveness.

### `GET /health`

This endpoint reports aggregate readiness:

- `200` when the process is not draining and all `required` applications are
  ready;
- `503` while draining or while any required application is not ready;
- independent applications appear in the response but do not change the
  status code.

The payload contains the required and blocking application ids plus bounded
per-app readiness mode, state, and ready flag.

### Monitoring and admin diagnostics

`GET /monitoring/applications` exposes bounded state suitable for monitoring.
It omits reconciliation fingerprints and errors.

`GET /admin/integrations/applications/readiness` is the authenticated operator
view. It includes desired and ready generations, attempts, retry timing,
timestamps, and the bounded preparation error.

The localhost-internal bundle status operation includes the same preparation
diagnostic plus the committed loaded-source and widget-publication receipts
for one app. Reading status is side-effect free and leaves application code
and process state unchanged; the CLI contract and field-by-field comparison live in
[Current KDCube CLI](../../service/cicd/cli-README.md#status).

## Admission Contract

Every application door resolves the application id, checks readiness, and only
then loads or invokes app code. The check reads committed process-local state;
it does not start source resolution, lifecycle hooks, builds, or deployment
repair.

Covered surfaces include:

- authenticated and public REST operations;
- MCP provider endpoints;
- widgets, main views, application sites, and public content;
- chat and reactive turns;
- peer-application calls through the shared async loader;
- cron and background jobs;
- Data Bus and external-event delivery.

An HTTP door returns `503` with `Retry-After` and:

```json
{
  "type": "application_not_ready",
  "application_id": "news@2026-05-20-12-05",
  "state": "preparing",
  "retryable": true
}
```

`pending` is normalized to `preparing` for callers. The public diagnosis does
not expose internal generations, source paths, errors, or credentials.

MCP returns the same diagnosis in JSON-RPC error `data`, with code `-32001`
and message `Application is not ready`.

Administrative status, configuration, disable, reload, and retry surfaces
remain callable so an operator can repair an unavailable application.

## Queue-Backed Work

Queued work is checked before app-visible side effects:

- chat/reactive work is released before `chat.start`, conversation mutation,
  handler invocation, or usage accounting;
- Data Bus work stays unacknowledged and is eligible for later claim;
- scheduler reconciliation omits unready apps and reruns when an app becomes
  ready;
- background work is not acknowledged as executed when admission did not pass.

The queue owns later redelivery. The readiness gate does not manufacture a
successful result or hide the application from inventory.

## Updates, Supersession, And Retry

Registry, source, and props changes create a new desired state for the affected
application. Proc invalidates only that app's local code, singleton, manifest,
static-load, and deployed-manifest state, then schedules its preparation.
Unchanged applications continue serving.

Admin Save and Reload requests publish desired state and return without waiting
for preparation. The lifecycle supervisor, rather than the HTTP request, owns
the resulting task.

An operator can explicitly retry one app with:

```text
POST /admin/integrations/bundles/{application_id}/preparation/retry
```

The retry publishes `pending` immediately and replaces the current task even
when the generation fingerprint is unchanged. A superseded task remains owned
until cancellation cleanup completes. Late completion cannot mark the new
desired generation ready.

## Failure And Recovery

Preparation work is strongly owned. The supervisor retains task references,
observes failures, bounds concurrency, and cancels/reaps tasks during
supersession or shutdown.

Resource-specific recovery remains inside each resource contract. For UI:

- npm/Vite run in a dedicated process group;
- source and `node_modules` are worker-local;
- output, signatures, and locks are on shared storage;
- lock ownership has a heartbeat and TTL;
- publication atomically replaces final output and writes its signature;
- lock release precedes potentially expensive local cleanup;
- timeout or cancellation terminates and reaps the process group;
- a hard process death stops the heartbeat so another worker can retry after
  lock expiry.

Request disconnects have no ownership relationship to preparation.

## Deployment Adapter Mapping

The runtime endpoint contract is deployment-neutral. Recommended mapping:

| Deployment concern | Proc endpoint | Reason |
| --- | --- | --- |
| Process/container restart decision | `/health/live` | App preparation must not make a live process look dead. |
| Traffic readiness | `/health` | Required-app policy determines whether this proc should receive normal traffic. |
| App-specific UI or API behavior | Application door | The runtime returns the app-scoped `503` without taking the whole proc out of service. |
| Operator diagnosis | `/monitoring/applications` and authenticated admin readiness | Diagnostics are separate from restart and traffic-routing decisions. |

The maintained Kubernetes chart maps startup and liveness probes to
`/health/live` and its readiness probe to `/health`. The maintained Docker
Compose profiles use `/health/live` for container health while callers rely on
per-app admission. ECS task definitions use `/health/live` for task replacement;
the deployment workflow may inspect `/health` separately when aggregate
required-app readiness is part of rollout policy.

## Implementation Map

- readiness state and admission error:
  `kdcube_ai_app/infra/plugin/app_readiness.py`
- process-local task ownership:
  `kdcube_ai_app/apps/chat/proc/app_lifecycle/supervisor.py`
- proc preparation sequence:
  `kdcube_ai_app/apps/chat/proc/app_lifecycle/runtime.py`
- shared resource barrier:
  `kdcube_ai_app/apps/chat/proc/app_deployment/coordinator.py`
- startup and health endpoints:
  `kdcube_ai_app/apps/chat/proc/web_app.py`
- HTTP/MCP/admin/static admission:
  `kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`
- central async app loader admission:
  `kdcube_ai_app/infra/plugin/bundle_loader.py`
- queue, scheduler, and Data Bus deferral:
  `kdcube_ai_app/apps/chat/processor.py`,
  `kdcube_ai_app/apps/chat/sdk/runtime/bundle_scheduler.py`, and
  `kdcube_ai_app/apps/chat/sdk/runtime/data_bus/worker.py`

## Operational Rules

- Keep ordinary apps `independent`; mark an app `required` only when proc-wide
  traffic truly depends on it.
- Make both lifecycle hooks idempotent and safe to retry.
- Keep process-local work in `on_bundle_load` and shared publication in
  `on_app_deploy`.
- Use signatures and atomic publication for shared generated resources.
- Read admin diagnostics before retrying; retry the affected app rather than
  restarting or invalidating the entire registry.
- Treat a ready app with a missing published artifact as a resource invariant
  failure. Requests serve prepared state and do not repair deployment.
