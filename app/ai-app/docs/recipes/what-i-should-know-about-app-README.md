---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/what-i-should-know-about-app-README.md
title: "What I Should Know Before Writing a KDCube App"
summary: "A builder's mind map for KDCube apps: async runtime rules, provider and consumer surfaces, identity and delegation, configuration, storage, concurrency, eventing, conversations, economics, UI, telemetry, isolated execution, and operational checks."
status: current
tags: ["recipe", "app", "bundle", "builder", "runtime", "async", "surfaces", "storage", "eventing", "economics"]
updated_at: 2026-07-16
keywords:
  [
    "KDCube app ingredients",
    "what to know about a KDCube app",
    "bundle builder mind map",
    "async app runtime",
    "as_provider",
    "as_consumer",
    "cross runtime context",
    "app storage",
    "conversation event bus",
    "data bus",
    "background jobs",
    "EconomicsGuard",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-developer-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
---

# What I Should Know Before Writing a KDCube App

Use this recipe as the **architecture mind map** for a KDCube app. It does not
require every app to expose every surface. It tells you which boundary to use
when your app needs a capability, which state belongs where, and which shortcuts
are unsafe in the shared runtime.

Current source and descriptors still use **bundle** in identifiers such as
`bundle_id`, `bundles.yaml`, and `@bundle_entrypoint`. Product documentation uses
**app**. In this guide, **app = bundle**: one deployable, independently configured
runtime unit.

## The Mind Map

```text
KDCube app (bundle)
|
+-- PROVIDES capabilities
|   +-- @api                 authenticated operation or verified public callback
|   +-- @mcp                 MCP tools/resources/prompts
|   +-- @ui_widget           embeddable UI surface
|   +-- @ui_main             main view / site UI
|   +-- agent identities     one reactive door, internally dispatched by agent_id
|   `-- @cron                detects due work and produces ready jobs
|
+-- CONSUMES work and capabilities
|   +-- @on_reactive_event   consumes an ordered conversation-event lane
|   +-- @on_job              consumes retryable ready-work delivery
|   +-- @data_bus_handler    consumes durable app-domain messages
|   +-- Python/SDK tools     built-in or app-local tools allowed per agent
|   +-- MCP servers          configured per consuming agent
|   `-- named services       provider-owned namespaces and object operations
|
+-- RUNS inside a shared concurrent proc
|   +-- async end to end; never block the event loop
|   +-- stateless per invocation, even when singleton=true
|   +-- request identity/context is rebound for every invocation
|   `-- explicit fenced/venv runtime only when a boundary is needed
|
+-- OWNS declared state
|   +-- app props / app secrets
|   +-- user props / user secrets
|   +-- Redis / Postgres / durable artifacts / shared filesystem
|   `-- no request state in globals, env vars, or singleton fields
|
+-- CROSSES guarded boundaries
|   +-- roles and grants are checked on the exposed surface
|   +-- delegation edges project an incoming actor only when authorized
|   +-- fenced tools resolve authority again at the protected operation
|   `-- paid work is tracked, admitted, reserved, and settled
|
`-- COMMUNICATES deliberately
    +-- conversation event bus   agent-visible ordered context
    +-- Data Bus                durable domain messages/mutations
    +-- background jobs         ready work, retryable delivery
    +-- communicator            peer/session/project progress events
    +-- recording + sinks       approved emitted-event capture
    `-- telemetry               facts that already happened
```

## Eight Non-Negotiable Rules

1. **Every platform-invoked app path is async end to end.** A synchronous call
   inside `async def` still blocks the shared proc event loop.
2. **Runtime identity comes from the bound request context.** A request body,
   model argument, object ref, or tool parameter cannot select the effective
   tenant, project, user, authority, or economics subject.
3. **Environment variables and module globals are not app state or app
   configuration.** Use descriptor-backed properties, secret helpers, portable
   invocation context, and supported stores.
4. **Treat every invocation as stateless.** `singleton: true` is reuse inside one
   worker, not durable state, request affinity, or automatic serialization.
5. **Choose the transport by semantics.** Conversation events, Data Bus messages,
   jobs, communicator events, and telemetry are different mechanisms.
6. **Make mutations idempotent and guard shared initialization.** Delivery can be
   retried and several proc workers can initialize the same app concurrently.
7. **Resolve authority and economics at the protected boundary.** Carrying
   identity provenance does not itself grant access or funding.
8. **Declare both sides of the app.** Decorators declare what exists;
   `surfaces.as_provider` governs exposed policy; `surfaces.as_consumer` limits
   what the app and each agent may consume.

## 1. Start With the Surface, Not With a Framework

An app can provide one API and nothing else. It can host an existing LangGraph
agent, expose only MCP, render a widget, run scheduled work, or combine several
surfaces. Chat, ReAct, UI, and a database are optional.

### Provider-side surface map

| Product capability | App declaration | Important policy |
| --- | --- | --- |
| Request/response operation | `@api(route="operations", ...)` | user type, raw/custom roles, optional authority and grants |
| Public callback/webhook | `@api(route="public", ...)` | app verifies the caller's proof or uses a configured managed guard |
| MCP endpoint | `@mcp(...)` | public, app-owned, or platform-managed auth; tool grants when managed |
| Embeddable UI | `@ui_widget(...)` plus `ui.widgets.<alias>` when built from source | visibility/auth and runtime-config handshake |
| Main UI/site | `@ui_main` plus `ui.main_view` configuration | build/serve policy; a scene is optional |
| Conversation agent | one `@on_reactive_event` method, normally inherited from `BaseEntrypoint` | stable `agent_id`; internal dispatch when the app has several agents |
| Scheduled producer | `@cron(...)` | detect due work quickly and enqueue/return ready work; do not perform the long job in the scan |

`user_types` are ordered platform levels (`anonymous`, `registered`, `paid`,
`privileged`). `roles` and app-level `allowed_roles` compare raw roles such as
`kdcube:role:finance-team`; custom roles are valid. When a surface declares both
user-type and role restrictions, both checks must pass. Authority/grant policy
is a separate check and must not be encoded as a role guess.

### Consumer-side surface map

| What enters or is consumed | Runtime contract | Builder responsibility |
| --- | --- | --- |
| Conversation events | one async `@on_reactive_event` | route by `agent_id`; consume the bound batch/lane, not an invented queue |
| Ready background work | one async `@on_job` | dispatch by `work_kind`, persist domain state, remain idempotent |
| Durable domain messages | `@data_bus_handler(subject=...)` | validate, dedupe writes, use revision checks and object partitioning where needed |
| Built-in/app-local tools | `surfaces.as_consumer.agents.<id>.tools` | expose only the tool IDs and traits that agent needs |
| Remote/app MCP services | `surfaces.as_consumer.mcp.services` plus the agent allow-list | resolve credentials on the trusted side and keep the agent-specific allow-list narrow |
| Named services | named-service discovery plus allowed namespace operations | preserve provider refs and let the provider own schema, actions, auth, and materialization |

An agent crosses both columns. The app **provides** a reactive agent entrypoint
to callers; the agent **consumes** tools, MCP servers, skills, named services,
and event sources. Do not invent `surfaces.as_provider.agents`. Agent capability
policy belongs under `surfaces.as_consumer.agents.<agent_id>`.

Read: [Bundle Platform Integration](../sdk/bundle/bundle-platform-integration-README.md),
[Bundle Descriptor](../configuration/bundles-descriptor-README.md), and
[Bundle Entrypoint Classes](../sdk/bundle/bundle-entrypoint-classes-README.md).

## 2. The Proc Event Loop Is Shared

Platform-invoked app callbacks execute in a concurrent proc service unless the
app explicitly crosses a supported runtime boundary. Blocking one callback can
delay unrelated users, conversations, apps, health work, and event consumers in
the same process.

Use `async def` for:

- lifecycle hooks, including `on_bundle_load` and `on_props_changed`;
- `@api`, `@mcp`, `@ui_widget`, and `@ui_main` methods;
- `@on_reactive_event`, `@data_bus_handler`, `@cron`, and `@on_job`;
- database, Redis, storage, HTTP, secret, subprocess, and SDK call chains.

Use asynchronous clients and `await` every I/O boundary. Never use synchronous
HTTP clients, filesystem reads/writes, database drivers, Redis clients,
`subprocess.run`, `time.sleep`, or a blocking lock in a proc path. A small pure
in-memory helper may be synchronous. If a bounded library has no async API, use
`await asyncio.to_thread(...)`; move long CPU-bound or operational work to an
appropriate worker/fenced execution path.

```python
@api(method="GET", alias="status", route="operations")
async def status(self, **_: object) -> dict[str, object]:
    record = await self.service.read_status()
    return {"ok": True, "record": record}
```

## 3. Identity, Roles, and Delegation Are Boundaries

One proc can serve many users. Keep these identities distinct:

```text
actor             who arrived at this surface
storage subject   whose user-scoped state is addressed
platform subject  whose KDCube roles/grants are evaluated
economics subject whose quota/funding pays for accountable work
```

They are often the same user, but external channels and delegated clients can
make them different. For example, a verified Telegram actor can have an
explicit Connection Hub edge to a KDCube platform user. The edge may project
platform roles or economics authority while audit/storage still records the
Telegram actor.

The safe journey is:

```text
incoming caller proof
  -> ingress/app verifies actor
  -> required authority boundary resolves an explicit delegation edge
  -> runtime carries actor + projection + edge provenance
  -> protected API/tool/provider/fenced boundary checks its own grants
  -> operation runs or fails closed / returns a managed connect action
```

Do not hand-build a privileged user from request fields. Do not treat an
`object_ref` as authorization. Do not assume that identity carried into a
subprocess grants a protected tool call. The trusted resolver combines the
untrusted locator with the bound runtime context and rechecks the provider or
managed boundary.

Read: [Tenant, Project, User, and Execution Boundaries](../runtime/tenant-project-user-and-execution-boundaries-README.md)
and [Fenced Runtime Bootstrap and Reduce](../runtime/fenced-runtime-bootstrap-and-reduce-README.md).

## 4. Context and Configuration: No Env Vars, No Globals

### Durable configuration and secrets

| Need | Correct API/home |
| --- | --- |
| Deployment-scoped non-secret app config | `self.bundle_prop("path", default)`; authority is `bundles.yaml` / configured descriptor provider |
| Write deployment-scoped app config | `await set_bundle_prop(...)` |
| Deployment-scoped app secret | `await get_secret("b:path")`; local secrets-file representation is `bundles.secrets.yaml` |
| Write deployment-scoped app secret | `await set_bundle_secret(...)` |
| Per-user non-secret app state | `await get_user_prop(...)`, `await get_user_props()`, `await set_user_prop(...)`, `await delete_user_prop(...)`; use an async typed `UserSettingsStore` for a structured settings subsystem |
| Per-user secret | `await get_secret("u:path")`, `await set_user_secret(...)`, `await delete_user_secret(...)` |

Properties are the app's only operator-visible configuration channel. Do not
create app behavior that can only be changed with `os.getenv(...)`. Do not put
secrets in properties. Do not read raw descriptor files from runtime paths or
mutate global `os.environ`.

Every durable user-prop helper is asynchronous and uses the shared async
Postgres pool. Call it with `await`; do not wrap it in `asyncio.to_thread(...)`
and do not create a second synchronous database client. Typed user-settings
stores use the same async storage boundary and add subsystem-specific record
shape and merge rules.

### Portable invocation context

Use `bundle_call_context` for **small JSON-safe metadata that belongs to one
invocation** and must follow nested agents, tools, or supported isolated
runtimes. Good examples are correlation IDs, a mode, an object ID, or a bounded
policy/model-role override.

Do not put secrets, file bytes, large documents, database clients, callbacks,
or durable state in it. The platform snapshots authenticated request context,
app identity, named-service discovery descriptors, and accounting context and
reconstructs trusted SDK surfaces at the target runtime. App code should use
the accessors, not parse or create the platform's transport representation.

Context variables follow the same async task. New threads require explicit
context copying; subprocesses and isolated runtimes require the supported
portable bootstrap. A module global loses this contract entirely.

Read: [App Properties and Secrets Lifecycle](../sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md)
and [Cross-Runtime Context](../runtime/cross-runtime-context-README.md).

## 5. Lifecycle and Singleton: Reuse Is Not State

The default mental model is one fresh app invocation per request. A descriptor
can enable `singleton: true`, but the cached instance is local to one proc
worker and loaded app spec. It can receive concurrent invocations and disappear
on reload, restart, scale-out, or failure.

Therefore:

- never retain current actor, user, communicator, conversation, turn, request,
  or delegation data on shared instance fields;
- never use singleton memory as the authoritative cache or lock;
- expect another invocation to run in another process;
- let `BaseEntrypoint` rebind request-local context for each invocation;
- protect any shared in-memory optimization with an async lock, while still
  keeping durable truth outside the process.

| Hook | Use it for | Do not use it for |
| --- | --- | --- |
| `on_bundle_load` | idempotent per-process preparation, schema/index readiness, SDK base setup | one-time global truth, user/request state, long unguarded builds |
| `on_props_changed` | reconcile long-lived effects after effective config changes | request logic |
| request/turn handler | current invocation behavior | durable state in `self` |
| `on_turn_completed` | fast, timeout-bounded, idempotent cleanup | expensive reporting or user-visible delivery |
| `@cron` | detect due work | run the long per-user operation inline |
| `@on_job` | execute ready work | assume exactly-once delivery |

If a base entrypoint owns lifecycle behavior, call the appropriate
`await super()` from overrides. Read [Bundle Lifecycle](../sdk/bundle/bundle-lifecycle-README.md).

## 6. Put State in the Correct Store

| State shape | Store |
| --- | --- |
| Small distributed cache, lease, dedupe key, or coordination value | runtime Redis/KV helper, namespaced by tenant/project/app |
| Durable backend-neutral app artifact/blob | `BundleArtifactStorage` (deployment may back it with object storage or filesystem); use `write_a(...)`, `read_a(...)`, and `iter_bytes(...)` in proc |
| Mutable shared filesystem tree, checkout, or generated index | `self.bundle_storage_root()` below the runtime-provided root; EFS/local filesystem semantics |
| Relational/queryable domain state | Postgres in the one tenant/project schema, with app-prefixed tables and scoped columns |
| Conversation transcript, turn artifacts, and user-visible files | KDCube conversation and file contracts, not app storage shortcuts |
| Remote provider-owned data | provider API/named service unless the product explicitly owns a copy |

Never put physical host paths, mount paths, S3 URIs, or storage credentials in
app properties. Ask the runtime for a storage abstraction/root. Filesystem
storage and backend-neutral artifact storage are not interchangeable.

`self.bundle_storage_root()` is a cheap path accessor; filesystem operations on
that path are still blocking and must use an async filesystem API or
`asyncio.to_thread(...)`. Likewise, any storage method that has only a
synchronous compatibility form (`list`, `exists`, or recursive delete in the
current artifact wrapper) must be offloaded rather than called directly in a
platform callback.

### Postgres rules

- Use the shared schema returned for the current tenant/project.
- Namespace by app-prefixed table names, not one schema per app/version/agent.
- Scope rows and every query by the columns the domain requires: tenant,
  project, app/bundle ID, and user/agent/conversation where applicable.
- Provision with `CREATE ... IF NOT EXISTS` inside an async transaction and a
  tenant/project/app-scoped advisory critical section.
- Run provisioning idempotently from `on_bundle_load` or first safe use.
- Do not run `CREATE EXTENSION`; platform database setup owns extensions.

**Current lifecycle gap:** KDCube does not yet expose a complete app
deprovision/delete hook that an app can use to drop its owned tables. Provision
for now, document table ownership and a manual/operator cleanup procedure, and
do not claim that deleting an app automatically drops its data. A future
deprovision phase must add an explicit, guarded cleanup contract.

Read the [Storage SDK](../sdk/storage/),
[Bundle Storage and Cache](../sdk/bundle/bundle-storage-and-cache-README.md), and
[How to Write a Bundle: Postgres rule](../sdk/bundle/build/how-to-write-bundle-README.md).

## 7. Guard Shared Work With the Right Critical Section

Several proc workers can execute `on_bundle_load`, rebuild an index, or mutate
the same object. Choose a lock whose scope matches the shared resource:

- Postgres advisory transaction lock for schema/bootstrap work;
- Redis/distributed lock for coordination across workers when Redis is the
  shared authority;
- `observed_file_lock_async(...)` for a shared mounted-filesystem resource;
- object partition + revision/idempotency in Data Bus domain handlers.

`asyncio.Lock` protects only one event loop in one process. A synchronous file
lock blocks that event loop. Neither is a cross-worker solution by itself.

Use the double-check pattern:

```text
compute expected signature
  -> current signature + ready output? return
  -> await cross-process lock
  -> recheck signature + readiness
  -> build into temporary/safe location
  -> verify complete output
  -> atomically publish/swap
  -> write signature last
```

Keep lock waits bounded. Do not hold a schema lock during a long backfill; create
the schema quickly, persist work state, and schedule the backfill. Read
[Synchronization Mechanisms](../service/synch-mechanisms/critical-section-README.md).

## 8. Choose the Correct Delivery Mechanism

| Mechanism | Use it when | Do not treat it as |
| --- | --- | --- |
| API/operation | caller needs a direct request/response command | a durable queue |
| Conversation event bus | data should become ordered context for one user/conversation/agent lane | app-domain mutation transport |
| Data Bus | app owns a durable domain message/mutation with retry and optional object serialization | conversation timeline or strict global FIFO |
| Background job stream | durable domain state says work is ready and proc should execute it | scheduler/database/source of business truth |
| Communicator event | current peer/session/project needs transient progress or UI notification | durable domain state |
| Telemetry/recording | record an observation about work that already happened | work execution trigger |

### Conversation event bus

A submitted `external_events[]` batch is stored in a lane keyed by
tenant/project/user/conversation/agent. The proc queue contains a **wake**, not
the event body. The active lane owner reads accepted events and the app's one
`@on_reactive_event` method is invoked for the scheduled turn.

- KDCube ReAct may keep the lane open and fold eligible later events into its
  active turn.
- A run-to-completion framework adapter gets a fixed start batch. Later events
  create a later turn.

Do not assume every agent has ReAct's live-fold behavior. Read
[External Events Journey and Handling](../sdk/events/external-events-journey-and-handling-README.md).

### Data Bus

Data Bus routes by app + `subject`; `object_ref` can partition object work. A
message is retryable. Persist a stable `message_id`/`idempotency_key`, validate
the expected revision, and make repeat handling safe. `serial_per_partition`
prevents concurrent handler execution for that partition; it is not a promise
of strict global FIFO. The durable object store remains authoritative.

Bridge a Data Bus handler to a conversation event only when an agent should
react. Read [Bus Routing and Partitioning](../service/comm/bus-routing-and-partitioning-README.md)
and [Data Bus](../service/comm/data-bus-README.md).

### Background jobs and webhooks

The producer first records durable domain work, then enqueues a ready-work
reference. Proc acknowledges the stream item only after `@on_job` returns, so
cancellation or failure can cause redelivery. The handler must be async and
idempotent; the job stream is transport, not the source of truth.

For a webhook/API that starts a normal conversation turn, verify the webhook,
use `ChatIngressSubmitter.submit(...)`, and return early. Do not keep the public
request open while the agent finishes and do not route a chat turn through the
background-job stream.

The mailbox/wake/lease conversation scheduler described in
[Conversation Scheduler](../service/streams/conversation-scheduler-README.md) is
a **proposal**. The shipped processor currently uses its existing scheduler
backend. Do not design app code against proposal-only mailbox APIs.

Read [Background Jobs](../service/streams/background-jobs-README.md).

## 9. Use KDCube's Conversation Around Any Agent

An agent has two distinct memories:

1. its framework/checkpointer working memory, which determines what the model
   sees on the next turn;
2. the KDCube conversation record, which powers ordered delivery, chat listing,
   reload, search, titles, files, artifacts, cost/time restoration, and clients.

Wrap an existing agent at the one-turn boundary and keep both stores aligned.
Key the framework thread by the KDCube `conversation_id`, not by a transient
browser session. Rebuild per-turn graphs from durable state rather than keeping
a stateful graph in a singleton. Map framework progress to communicator events,
return the final answer/files, and use the framework-neutral recorder/fallbacks
so the conversation is automatically durable.

There is one reactive entrypoint per app. If the app hosts several agents,
dispatch internally by stable `agent_id`; do not add several competing reactive
decorators.

Read [The Conversation for Any Agent](../sdk/solutions/conversation/hosted-agent-conversation-README.md)
and [Connect an Agentic Loop to Ordered Delivery](./dataflow/connect-agentic-loop-to-ordered-delivery-README.md).

## 10. Consume Tools, MCP, and Named Services Deliberately

### Built-in tools

Use KDCube's built-in SDK tools directly instead of wrapping/reimplementing
web search, web fetch, isolated execution, rendering, and file hosting. Their
native contracts already provide accounting, citations/source pools,
supervisor isolation, artifact hosting, and ReAct result policy. Configure the
allowed IDs and traits per agent under `surfaces.as_consumer`.

Read [SDK Tools](../sdk/tools/sdk-tools-README.md) and
[Tool Subsystem](../sdk/tools/tool-subsystem-README.md).

### MCP

Connecting MCP does not require a named service. Configure the MCP server once,
resolve its secret server-side, and allow only selected tools to each agent.
In isolated execution, the restricted executor does not receive credentials;
the trusted supervisor performs the MCP call.

Exposing MCP also does not require a named service. Implement ordinary async MCP
methods, choose public/app-owned/platform-managed auth, declare tool grants when
managed, and make write tools idempotent. Named services are optional when the
same domain also needs provider-owned object refs, schemas, actions, search, and
generic UI/agent treatment.

Read [Connect an MCP Service](./kdcube_for_agents/consume-mcp-service-README.md)
and [Expose an MCP Service](./kdcube_for_agents/expose-mcp-service-README.md).

### Named services

The provider owns the namespace grammar, schemas, capabilities, authorization,
actions, materialization, and presentation. The consumer owns which namespaces
and operation families its agent/UI may call. Preserve canonical refs and ask
the provider; do not hard-code foreign namespace semantics in generic chat,
canvas, or scene code.

Read [Providers](../sdk/namespace-services/providers-README.md),
[Clients](../sdk/namespace-services/clients-README.md), and
[Ontologic Tools](../sdk/namespace-services/ontologic-tools-README.md).

## 11. Guard and Account for Paid Work

Accounting answers **what was used and what did it cost**. Economics enforcement
answers **may this run, who pays, what is reserved, and how is actual usage
settled**.

Use an economics-enabled entrypoint for guarded agent turns. For paid API,
search, MCP, tool, cron, or job work outside a parent turn:

1. project the economics subject from the bound authority context;
2. choose a stable unique `scope_id` for this accountable request;
3. enter `EconomicsGuard` with an estimate;
4. run tracked services inside the guard;
5. let the guard settle actual recorded usage on exit;
6. handle denial or an intentional degraded path.

Do not charge the queue label, external actor, or fallback user merely because
it is convenient. Use the projected economics subject authorized by the carried
delegation edge. Nested paid calls should join the existing parent accounting
scope instead of creating unrelated charges.

For an app-owned paid provider, emit a tracked `ServiceUsage`; a call that is
not tracked cannot be settled accurately. Read
[Guard a Paid Surface](./economics/guard-paid-surface-and-enforce-economics-README.md),
[Implement a Self-Tracked Service](./economics/tracked-service-README.md), and
[Bundle Economics Integration](../sdk/bundle/bundle-economics-integration-README.md).

## 12. Communicate, Firewall, and Record Intentionally

Use the current invocation's communicator:

- `comm.service_event(..., broadcast=False)` for the current peer/session;
- session broadcast only when every connected peer should see it;
- `comm.project_event(...)` for a bounded tenant/project event intended for
  project subscribers;
- Data Bus for durable app-domain messages, not communicator events.

Define the outbound event firewall for what is allowed to leave the app. It is
an outbound client-delivery filter, not inbound authentication. Keep payloads
small, omit secrets and raw sensitive content, and use stable event types.

Recording captures approved **post-firewall** communicator envelopes. Open a
bounded recording scope at the outer invocation boundary, select the events you
need, use an async sink, and flush explicitly. Recording is not another bus and
does not make a transient event durable business state. Supported isolated
runtimes reduce child-side recorded events back to the host; callbacks and live
objects do not cross the fence.

Read [Bundle Outbound Firewall](../sdk/bundle/bundle-firewall-README.md),
[Comm Recording and Sinks](../service/comm/comm-recording-event-sinks-README.md),
and [Bundle Event Recording](../sdk/bundle/bundle-event-recording-and-sinks-README.md).

## 13. Add UI Without Inventing a Second Protocol

### Widget

Declare `@ui_widget(alias=...)`. If it is a source-built widget, align the alias
with `ui.widgets.<alias>`. The widget requests runtime config from its host and
derives operation/Data Bus URLs, tenant, project, app ID, and auth from that
handshake. Do not bake deployment URLs or credentials into the bundle.

Use an operation for direct request/response. Use Data Bus for durable domain
mutations. Use the provider's object resolver/action contract for namespaced
objects.

### Chat

The reusable chat solution already owns event packaging, streaming transport,
conversation lifecycle, files, context chips, reconnect, and host messages. An
app can mount the standard widget, skin the headless engine, or use the backend
from another client. The app still owns agent behavior, tools, resolvers,
policies, and visibility.

### Scene

A scene is optional composition for several cooperating UI surfaces. Use its
surface registry, config handshake, context drag/drop, commands, and event
relay. Do not add ad hoc cross-iframe protocols or infer namespace behavior in
the scene host.

Read [Widget Integration](../sdk/bundle/bundle-widget-integration-README.md),
[Chat Widget Solution](../sdk/solutions/chat/chat-widget-solution-README.md),
[Chat Stream Events](../sdk/solutions/chat/chat-stream-events-README.md), and
[Scene](../sdk/solutions/scene/).

## 14. Use Built-In Observability, but Know What Is Current

Use the current communicator recording/sink/collector path for app telemetry
and self-describing reported metrics. A reported metric carries its value,
aggregation intent, label, format, and low-cardinality labels; the collector can
store/query it without a metric-specific schema change. Keep telemetry separate
from accounting and from work delivery.

The broader platform-level telemetry stream and a generic bundle `@on_event`
listener described in [Telemetry Streams](../service/streams/telemetry-README.md)
remain a **proposal**; there is no generic `@on_event` app listener today. Do not
build against it. Emit through the implemented communicator/recording/collector
surface and read [Reported Metrics Conventions](../sdk/solutions/telemetry/reported-events-conventions-README.md)
plus [Formatting Reported Metrics](../sdk/solutions/telemetry/formatting-reported-events-README.md).

## 15. Cross a Runtime Boundary Only for a Reason

### `@venv`

Use `@venv(...)` only for app-specific Python dependencies that are absent from
the processor runtime. Keep orchestration in proc. Pass serializable inputs and
outputs, return refs/metadata for produced files, and remember that source reload
and cached-venv rebuild are separate. Do not try to carry pools,
communicators, provider clients, or live context objects into it.

### Fenced execution

For untrusted/generated code, use the KDCube isolated executor and trusted
supervisor boundary. The restricted executor receives a sparse workspace and no
platform credentials/network authority. Trusted tools resolve refs, secrets,
and provider calls under the restored context; host reducers merge declared
files, communicator records, and accounting output idempotently.

### Claude Code

Use the SDK Claude Code integration rather than app-local subprocess glue. It
binds user/conversation/agent continuity, streams through the communicator, and
emits accountable usage. In a scaled deployment, use the Git-backed session
store when Claude's native session/transcript must survive another worker or
restart. Keep the workspace/session identity deterministic and separate service
jobs from interactive user conversations.

Read [Bundle Venv](../sdk/bundle/bundle-venv-README.md),
[Fenced Runtime](../runtime/fenced-runtime-bootstrap-and-reduce-README.md),
[Claude Code Agent](../sdk/agents/claude/claude-code-README.md),
[Claude Code Accounting](../sdk/agents/claude/claude-code-accounting-README.md),
and [Claude Code Workspace](../sdk/agents/claude/claude-code-workspace-bootstrap-README.md).

## 16. A Maintainable App Package

Keep the root small and use named, documented modules:

```text
my-app@1-0/
  entrypoint.py                 thin composition root
  README.md                     product/surface overview
  AGENTS.md                     implementation contract for coding agents
  release.yaml                  release metadata
  requirements.txt             only when app-specific dependencies are needed

  agents/                       agent construction and prompts
  services/                     domain services
  surfaces/                     API/MCP/widget adapters when useful
  events/                       event normalization/dispatch
  tools/                        app-local tools
  ui/                           main view and widgets
  skills/                       app-local skills
  config/                       descriptor templates
  interface/                    OpenAPI and non-HTTP surface declaration
  docs/storage/README.md        owned stores/tables/retention/deprovision note
  docs/journal/                 meaningful implementation decisions
  tests/                        focused contract and transport tests
```

Only add folders the app owns. `entrypoint.py` composes; it should not become the
entire implementation. Keep decorators, interface declaration, descriptor keys,
README, tests, and journal synchronized in one change.

The maintenance contract is part of the app, not cleanup after implementation:

- `README.md` says what the app is, what it provides and consumes, and how to
  run its focused verification.
- `AGENTS.md` tells coding agents which modules own which behavior, the hard
  invariants, verification commands, and files that must change together.
- `interface/` documents the actual callable contract: OpenAPI for HTTP and an
  explicit inventory for MCP, widgets, main view, agents, Data Bus subjects,
  jobs, named services, and other non-HTTP surfaces.
- `config/` declares every property, consumer/provider policy, UI build, and
  secret placeholder. Document meanings and safe defaults; never commit secret
  values.
- `docs/storage/README.md` names every owned store/table/key prefix, scope,
  retention, migration/provisioning behavior, backup expectations, and the
  current manual deprovision procedure.
- `docs/journal/` records meaningful architecture and behavior changes as they
  happen. It is the fast onboarding trail for the next developer or coding
  agent, not a dump of routine edits.
- `release.yaml` and release notes identify the app version and user/operator
  impact. They do not replace runtime configuration.
- `tests/` verifies declarations and the real boundaries: transport, auth,
  retries, duplicate delivery, concurrency, config reload, and restart.

Follow the builder path in order:

1. [Assemble With SDK Building Blocks](../sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
   before creating app-local substitutes.
2. [Write a KDCube App](../sdk/bundle/build/how-to-write-bundle-README.md)
   for package shape, interfaces, storage, tests, and documentation.
3. [Configure and Run the App](../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)
   for descriptor wiring and the local runtime loop.
4. [Avoid Common Integration Failures](../sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md)
   before declaring the implementation complete.
5. [Release App Content](../sdk/bundle/build/how-to-release-bundle-content-README.md)
   for release metadata, validation, tag/build, and publication procedure.

Each functional change keeps code, declarations, configuration templates,
interfaces, docs/journal, focused tests, and release notes semantically aligned.

## Common Misunderstandings

| Misunderstanding | Correct model |
| --- | --- |
| "It is `async def`, so blocking work is safe." | Blocking work inside it still blocks proc; use async clients or an explicit boundary. |
| "Singleton means one app instance globally." | It means worker-local reuse and can be concurrent; it is not durable or serialized. |
| "The env var is convenient app config." | App config is descriptor-backed properties; secrets use secret helpers. |
| "The queue contains the user event." | A conversation wake points proc to the durable event lane; the owner reads the lane. |
| "A job is my domain record." | The durable domain record is authoritative; the job stream is retryable ready-work transport. |
| "Data Bus is the agent event bus." | Data Bus carries app-domain messages; bridge explicitly when an agent should react. |
| "The caller supplied a user ID, so I can use it." | Effective identity is host-bound; caller/model fields are untrusted locators. |
| "A delegation projection gives every downstream operation access." | Each protected boundary checks the authority/grants it requires. |
| "Connecting MCP requires named services." | Ordinary MCP is first-class; named services are optional semantic object contracts. |
| "I need to wrap web search or exec." | Built-in SDK tools already carry isolation, accounting, provenance, and artifact behavior. |
| "Conversation history is my agent checkpointer." | KDCube's record and the agent's working memory are separate and both need correct wiring. |
| "Deleting the app drops its tables." | There is no complete deprovision hook today; document owned tables and cleanup explicitly. |
| "Telemetry can trigger app work." | Telemetry records observations; use events, Data Bus, or jobs to execute work. |

## Ship Checklist

- [ ] Every platform callback and every I/O chain is async and non-blocking.
- [ ] Provider surfaces and consumer capabilities are both explicit.
- [ ] API/widget role, user-type, authority, and grant policy is tested.
- [ ] External callers use verified proof and explicit delegation edges.
- [ ] No app config/state relies on env vars, module globals, or singleton fields.
- [ ] App and user props/secrets use the supported helpers.
- [ ] Store choice, tenant/project/user scoping, retention, and table ownership are documented.
- [ ] Shared initialization and mutations are idempotent and guarded across workers.
- [ ] Conversation Event Bus, Data Bus, jobs, communicator events, and telemetry are not conflated.
- [ ] Webhooks reply early; long work is submitted through the correct mechanism.
- [ ] Paid calls are guarded and emit accountable usage under the correct scope/subject.
- [ ] Outbound events pass the app firewall; recording is bounded and uses an async sink.
- [ ] Agent working memory and KDCube conversation persistence both survive restart/scale-out.
- [ ] Built-in tools are reused; MCP/named-service allow-lists are least-privilege.
- [ ] Widgets use runtime config; scenes use the standard surface/event contracts.
- [ ] Venv/fenced/Claude Code boundaries receive only portable data and preserve accounting/recording.
- [ ] Tests cover the real transport, retry, duplicate, denial, restart, and concurrent-worker paths.

Start with the smallest useful surface. Add a capability only when the same app
should own its behavior, authority, state, and release lifecycle.
