---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-long.md
title: "Architecture Long"
summary: "Detailed current KDCube architecture: deployment scope, app catalogs and surfaces, ingress, ordered conversation lanes, Data Bus and relay, identity and delegation, cross-runtime context, isolated execution, storage, scaling, sites, and economics."
status: current
tags: ["arch", "architecture", "runtime", "apps", "events", "identity", "execution", "storage"]
updated_at: 2026-07-18
keywords: ["KDCube architecture", "tenant project", "app provider consumer", "conversation event bus", "data bus", "isolated execution", "Connection Hub", "site catalog"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-we-built-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-short.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
---
# KDCube Architecture: Long Map

This document explains the stable architecture of the KDCube platform runtime.
It avoids deployment-specific port numbers, task sizes, DNS names, and IAM
details; those belong to the deployment and operations descriptors.

Use [Architecture Of What You Build](architecture-of-what-you-build-README.md)
for the product architecture an app builder composes on top of this runtime.
Use [Security And Trust Model](security-and-trust-model-README.md) for the
canonical distinction between tenant/project scope, trusted application code,
request-scoped users, and profile-dependent generated-code isolation.

## 1. Architectural Invariants

1. One running deployment is bound to one effective `tenant/project`.
2. Many users and apps may execute concurrently in that deployment.
3. Request identity and authority must survive every runtime boundary.
4. Apps state separately what they provide and what they consume.
5. Redis queue order is not conversation event order; the lane sequence is.
6. Conversation events, app-domain Data Bus work, and client relay are
   different mechanisms.
7. Model-proposed refs and paths are requests, not authority.
8. Generated code receives selected workspace material, not ambient platform
   storage or credentials.
9. Storage has subsystem owners; there is no universal KDCube data store.
10. Accounting follows the same request/work lineage as execution.
11. Operator-loaded application code is trusted; isolated generated code is a
    different boundary selected by execution profile.

## 2. Deployment Scope

One KDCube deployment serves one `tenant/project` and can host many apps and
users:

```text
KDCube deployment
  tenant = T
  project = P
  |
  +-- app A
  +-- app B
  +-- app C
  |
  +-- user 1 requests
  +-- user 2 requests
  `-- user N requests
```

PostgreSQL, Redis, object storage, and shared filesystems may be dedicated to
that deployment or shared with other tenant/project deployments. Shared
infrastructure preserves scope through schemas, keys/namespaces, prefixes, and
service-owned lookup contracts. A shared process can handle multiple users, so
user isolation cannot be inferred from process isolation.

```text
tenant/project   deployment and persistence namespace
actor/user       authenticated requester inside that deployment
authority        grants projected for the current operation
app              product/runtime owner
conversation     ordered interaction lineage
agent/turn       active execution lineage
```

See [Tenant, Project, User, Authority, And Execution Boundaries](../runtime/tenant-project-user-and-execution-boundaries-README.md).

## 3. Physical Runtime Map

```text
clients
  browser | app website | Telegram/channel | webhook | REST | MCP
                                    |
                                    v
                         public proxy and routing
                                    |
               +--------------------+--------------------+
               |                                         |
               v                                         v
       browser/static/site paths                  API and event ingress
                                                         |
                                  authenticate actor and bind request context
                                  validate payload, visibility, limits, budget
                                                         |
                    +---------------------+--------------+----------------+
                    |                     |                               |
                    v                     v                               v
            conversation lane         Data Bus                    direct app call
              + wake queue             stream                       / response
                    |                     |                               |
                    +---------------------+-------------------------------+
                                          |
                                          v
                               proc / processor workers
                                    app dispatcher
                                          |
                +-------------------------+-------------------------+
                |                         |                         |
                v                         v                         v
          app entrypoint            SDK/runtime services      execution runtime
        API/agent/job/UI             identity, storage,       in-proc or isolated
        provider/handler             hosting, economics
                |                         |                         |
                +-------------------------+-------------------------+
                                          |
                                          v
                  PostgreSQL | Redis | app files | artifacts | providers
```

The proxy routes stable platform and app surfaces. It does not own the app
registry, site registry, authority model, or per-app policy.

## 4. Descriptor, App, And Site Catalogs

`bundles.yaml` is the app/source/configuration authority. It identifies app
source, path, module, singleton behavior, non-secret configuration, surfaces,
and website declarations. `assembly.yaml` selects deployment/platform behavior;
it is not the long-term owner of app provider internals or Scene composition.

The app store parses descriptor state with a process cache keyed by file stat
identity. The file remains authoritative: a changed path modification time or
size causes a reparse. Secret values are not cached there.

Startup app preload is collaborative across workers:

```text
descriptor-resolved app generation
          |
          v
Redis claim + heartbeat ------ another worker sees claimed and skips
          |
          v
load/prebuild app UI
          |
          v
generation-specific done marker
          |
          `------ later workers skip the completed generation
```

Shared-filesystem locks remain the final guard around UI artifact writes.
Redis distributes preload ownership; filesystem locks protect the actual
write/swap. If Redis is unavailable, workers fall back to local traversal with
the shared-storage lock.

Application website declarations are compiled into a validated immutable
`ApplicationSiteCatalog` containing alias/host/default policy and the resolved
app target. Publication atomically advances a Redis generation, replaces the
snapshot, and emits an update. Each proc subscribes, loads the current snapshot,
rejects delayed generations, and performs request-time lookups only against its
local immutable catalog.

```text
bundles.yaml -> validate -> catalog revision + generation -> Redis projection
                                                               |
                                                               v
                                                        proc-local snapshot
                                                               |
                                                               v
                                                    host/alias request lookup
```

Redis distributes the site catalog; it is not the site authority and is not
queried for every website request.

## 5. Directional App Surfaces

An app is simultaneously capable of being a service provider and a service
consumer. Those roles are declared independently:

```text
surfaces.as_provider
  bundle/app visibility and default_chat intent
  API visibility and managed auth
  widget visibility and auth
  MCP endpoint auth

surfaces.as_consumer
  agents.<agent_id>.tools and skills
  mcp.services connection and authentication config
  named-service namespace/operation inventory
  event-source pull/materialization policy
  UI resolvers and Scene component wiring
```

`enabled.*` decides whether a concrete surface is served. Provider policy
decides who may see/call an exposed surface. Consumer configuration decides
what this app and each agent may call or resolve. One direction never implies
the other.

For consumed MCP, `server_id` in an agent tool entry must identify a server
under `surfaces.as_consumer.mcp.services`. Different agents can use the same
server with different tool allow-lists. Python, MCP, and named-service
connections converge into ToolSubsystem metadata and policy.

Runtime decorators and registrations supply the implementation:

| Surface | Runtime role |
| --- | --- |
| API / operations | Synchronous app calls, callbacks, and webhooks. |
| Widget / main view | App-owned browser surfaces. |
| `default_chat` | Explicit intent to serve the SDK chat under alias `chat`. |
| MCP | Tool/resource facade, optionally protected by managed credentials. |
| Named-service provider | Self-describing domain realm. |
| Data Bus handler | App-owned durable message consumption. |
| Cron / job | Scheduled and background work. |
| Reactive event entrypoint | One scheduled conversation-event app turn. |

An app may provide any subset and need not have UI, chat, ReAct, or a database.

## 6. Browser, Authentication, And App Presentation

The browser obtains the effective platform contract from
`/api/cp-frontend-config`. It follows returned auth fields such as `loginUrl`,
`profileUrl`, and `logoutUrl`; it does not inspect provider implementation or
infer login state from visible cookies. `/profile` is the browser-session truth.

Platform authority may be backed by Cognito, multi-Cognito, SimpleIDP, or an
application-hosted authority flow. Connection Hub owns the platform authority
provider registry and policy. An app may host login/session/consent UI and
operations without becoming the owner of global authority semantics.

The control plane presents an app's own main view when present, otherwise an
automatic app scene. Apps declaring `default_chat` serve the reserved SDK chat
widget. A Scene can compose cross-app widgets by consumer configuration and
deliver declared surface commands; the route that serves a widget remains its
app identity.

Complete app-hosted websites are separate from indexed `@public_content`:

```text
application-hosted website
  serves the complete built main-view tree
  alias/host resolution through ApplicationSiteCatalog
  multipage files + directory index + SPA fallback

@public_content
  serves indexed public records, catalogs, metadata, and sitemaps
```

CDN clean-path routing rewrites to the reserved `site-root` origin route while
preserving the viewer hostname. The CDN forwards and caches; it does not own or
query the site catalog.

## 7. Ingress And Request Context

Ingress normalizes transport-specific input into a bound request:

```text
transport proof and payload
        |
        v
authenticate / verify actor
        |
        v
bind tenant + project + actor + user + authority
        |
        v
resolve app + conversation + agent + turn routing
        |
        v
apply visibility, payload, backpressure, and economics admission
```

`ChatIngressSubmitter` provides a proc-local adapter for channels and backend
webhooks that cannot call browser SSE or Socket.IO ingress directly. After
normalization it calls the canonical chat-message path; it is a conversation
event submitter, not a universal event bus.

Nested in-process app calls use `call_bundle_operation()`. Internal peer-call
provenance bypasses only the generic external-entry requirement for an already
bound user; target visibility, role, delegated authority, enablement, and API
policy still run.

## 8. Ordered Conversation Eventing

One lane identity is:

```text
tenant + project + user_id + conversation_id + agent_id
```

Redis lane sequence defines accepted event order. A processor queue schedules
work but does not contain the authoritative event body or define its order.

```text
reactive ingress
  atomically append all prepared events to lane L
  atomically admit one ExternalEventLaneWakeup pointer to queue Q

non-reactive ingress
  append events to lane L only
```

The processor consumes the wake, resolves the accepted event from lane state,
reconstructs `ExternalEventPayload` from retained `task_payload`, and invokes
the routed app's conversation-event surface.

A live turn consumes later events through `ContextBrowser`. Ownership combines:

- a logical handler turn id;
- a fresh active-consumer heartbeat for stale-owner reclaim;
- a short scheduled-start reservation during app loading;
- a token-fenced Redis event-source lease for the physical reader.

The background listener and direct decision/tool-phase watcher use the same
owner-fenced acceptance path. Cancellation closes the handler, stops the
listener, and releases the owner lease.

The processed cursor is timestamp plus event id, with lane sequence when
available. It lives on the timeline and survives serialization and in-turn
compaction. A consumed event that produces zero timeline blocks advances the
cursor explicitly. The close gate succeeds only when the processed/rendered
cursor covers the latest accepted event.

Supersession can be detected at several boundaries. The invariant is stable: a
stale turn may briefly resume execution, but it cannot fold new lane events,
commit an answer, or become conversation head.

`@on_reactive_event` starts one scheduled app turn. Events arriving during that
turn fold into it when eligible. Passive versus promotable behavior is decided
by the event's retained `task_payload`, not by `reactive: false` alone.

## 9. Event Materialization

An accepted lane event is not automatically a timeline block or PostgreSQL
chat row.

```text
accepted event
      |
      v
source-owned block production
      |
      +-- zero blocks -> advance cursor, mark consumed, no generic hook
      |
      `-- blocks      -> contribute blocks, then enabled workflow hooks
```

`react.block_production.no_timeline` is a visibility decision, not proof that a
generic callback stored product state. Business durability belongs to the
producing service or explicit source-owned processing.

Steer and followup are semantic event types nested inside the uniform external
event transport envelope. Eligible followups may add bounded iteration credit;
a steer requests active-phase cancellation and bounded finalization. This does
not promise synchronous termination of every possible external process.

## 10. Data Bus And Client Relay

Conversation Event Bus and Data Bus share some transports but have different
ownership:

| Mechanism | Intended work |
| --- | --- |
| Conversation Event Bus | Context for a current or future conversation/app turn. |
| Data Bus | App-owned domain mutation independent of chat. |
| Chat relay | Transient delivery to connected browser/client streams. |

Data Bus messages are consumed by `@data_bus_handler`. A handler explicitly
submits `external_events[]` when its result belongs in a conversation. Apps do
not write processor ready queues or invent lane state.

SSE relay state uses the same namespace as its Redis channel:

```text
tenant + project + session_id -> connected clients
                                  |
                                  `-- stream_id -> one SSE connection
```

Missing tenant/project metadata may trigger session-id fallback for legacy
payloads, but tenant/project/session is the intended contract. `stream_id` is
an exact identifier; it does not normalize `stream_<uuid>` and `<uuid>`.

## 11. Authority And The Two Delegation Directions

Connection Hub is the central authority and connection plane. It owns:

- platform authority/provider registration and policy;
- connection edges and authority projection;
- connected external accounts and provider-claim consent;
- delegated KDCube credentials and managed MCP/REST guards;
- server-side grant/session records and resource policy.

```text
delegated to KDCube
  external provider -> current KDCube user -> trusted KDCube tool/provider
  examples: Gmail, Slack, custom OAuth/OIDC service

delegated by KDCube
  current KDCube user -> bounded credential -> external automation/client
  examples: script, CI job, Claude MCP client
```

Provider claims such as `gmail:send` authorize KDCube's use of a connected
provider account. KDCube resource grants such as `mail:send` authorize an
external client to enter a protected KDCube resource. They are two gates and
must not be merged.

Consent for connected-account claims is demand-driven. The attempted tool or
named-service operation raises the scoped request. Approval is not the union of
every configured tool. The managed denial envelope explains whether connection,
claim upgrade, reconnect, or account selection is needed.

Tokens remain server-side credential handles. Trusted guards resolve grant and
session records; application handlers do not infer authority by decoding token
bodies. Provider credentials live in user-scoped secrets and are resolved by
trusted SDK/tool code, not exposed to the model or generated-code executor.

## 12. Cross-Runtime Context

The same request can cross coroutine, thread, subprocess, isolated-supervisor,
Data Bus, app-call, and named-service boundaries. The portable context room
preserves the situation:

```text
request identity + authority
tenant/project/app/conversation/turn routing
accounting/run context
app-call context
named-service discovery descriptors
named-service consumer-policy ceiling
```

It does not carry live database/Redis clients, secrets, provider tokens, or
arbitrary blobs. Each target runtime restores context variables and rebuilds
services from descriptors.

Conversation-scoped capability denials travel in the context snapshot. App consumer
configuration remains the ceiling. Providers authorize through restored
request identity, never a model-supplied user id.

## 13. Named Services Across Runtime Boundaries

Named services give domains one fixed agent-facing grammar while each provider
owns object kinds, refs, search schema, actions, presentation, and guards.

Direct in-proc calls use the live app registry. From isolated execution, the
supervisor first applies the carried consumer policy. If no live registry
caller exists, the named-service relay transports the request over the Data Bus
to the provider app's worker:

```text
executor code
  -> supervisor tool bridge
  -> named-service client policy
  -> Data Bus relay request with restored actor
  -> provider app worker and registry
  -> normal provider guards/claims
  -> recorded relay result
  -> supervisor
  -> executor result
```

The executor itself remains network-isolated. The supervisor is the relay
client. Relay delivery is at least once, while the handler records results by
message id and requires idempotency so a redelivered side-effecting operation
does not run twice.

The relay is transport, not a new authorization surface. Missing identity is
refused; missing connected-account consent returns the standard consent
contract.

## 14. Requester, Resolver, Workspace, And Executor

For model-controlled execution, distinguish the requester from the authority:

```text
agent or generated code
  proposes ref/path/operation
           |
           v
trusted resolver/tool
  binds current tenant/project/user/authority
  validates visibility and grants
  returns only in-scope bytes/result
           |
           v
sparse current workspace
  only materialized inputs and produced outputs
           |
           v
isolated executor
```

KDCube ReAct operates on logical refs, not an unrestricted platform filesystem.
`react.pull` materializes visible historical or external owner refs;
`react.checkout` materializes editable project state. Other agent frameworks
can use the same pull/workspace/ISO-exec pattern through adapters.

Current conversation-owned refs use `conv:<family>:<body>`, including
`conv:fi:` for files. Bare `fi:` and other pre-migration family refs are not
valid current protocol.

Workspace paths are role-based:

```text
turn_<id>/git/projects/...   editable project state
turn_<id>/files/...          produced files
turn_<id>/git/snapshots/...  workflow/scene snapshots
turn_<id>/attachments/...    current-turn uploads
turn_<id>/external/...       rehosted external evidence
```

In split Docker execution:

- the executor has no network;
- only selected work/artifact/log/socket paths are mounted;
- platform and app storage roots are not mounted;
- descriptors and provider credentials are not sent to the executor;
- runtime globals are stripped to the executor subset;
- approved tool calls cross the supervisor socket and run in the trusted
  supervisor under carried identity and policy.

Oversized supervisor-only environment payloads are streamed over container
stdin as a JSON env map; this avoids command-line `E2BIG` without widening the
executor. Distributed Fargate execution uses a separate launch-payload
transport.

Structured harness failures return normal tool-result blocks with
`status: "error"`; they do not disappear as logs. A repeated identical launch
failure should not cause unbounded retries.

## 15. Tool Policy

Python, MCP, named-service, and built-in tools converge into the tool subsystem.
Authorization, user selection, connected-account claims, strategy traits, and
execution traits remain distinct policy layers.

`strategy` describes ordered multi-action causality (`exploration`,
`exploitation`, `neutral`, or `unknown`). `execution` describes completed-call
scheduling and replay. Early detached execution currently requires a fully
validated, exactly neutral call and the supported execution profile; it is not
a Boolean "run early" hint and not process-global exactly-once execution.

## 16. User Settings And Conversation Choices

Agent model/capability choices are durable per conversation:

```text
conversation:<conversation_id>:agent_selection:<agent_id>
```

The optional `agent_selection:<agent_id>` row is a baseline for future
conversations, not the app-configured default. A new conversation materializes
the current baseline once using insert-if-absent; otherwise it starts from app
configuration. Existing conversation rows do not change when the baseline
changes.

The picker edits a local draft. Only **Save changes** persists it. Switching
conversations discards unsaved edits, and a chat-originated capabilities command
carries `conversation_id` to preserve scope.

## 17. Storage Ownership

KDCube intentionally separates storage responsibilities:

| Storage surface | Owner / purpose |
| --- | --- |
| `bundles.yaml` and deployment descriptors | App source, non-secret configuration, declared surfaces. |
| Secret lifecycle | Deployment and app secret values behind references. |
| PostgreSQL | Durable subsystem/product records in tenant/project schema. |
| Redis | Queues, lanes, streams, relay, locks, catalogs, and selected fail-closed grant/session records. |
| App filesystem storage | App-owned mutable files; local/mounted locally, normally shared filesystem in cloud. |
| Artifact storage | Separate local/object-backed artifact API. |
| User-scoped secrets | Connected external-provider credentials. |
| Conversation workspace/artifacts | Turn/conversation materialization and deliverables. |
| User settings | Durable user choices, including conversation selection records. |

`bundle_storage_root()` is filesystem storage, not S3. Connected-account
metadata may live in app filesystem storage, while raw provider tokens live in
user-scoped secrets. Delegated access authority comes from server-side records,
not token-body decoding.

Temporary staging is not a distributed object store. Producers and consumers
must share the intended storage root or use the hosting/artifact contract.

## 18. Economics, Observability, And Failure

Economics-aware work follows:

```text
verify policy and balance
        -> reserve expected spend
        -> execute tracked calls
        -> settle actual spend
```

Usage records inherit request lineage such as tenant/project, user, app,
conversation, turn, flow, provider, and model. A turn has no fixed "cost per
turn"; its cost is the sum of spendings inside it. Helper-agent spend can be
attributed under the child conversation and rolled up to the parent work.

Logs, structured events, tool results, lane records, authority decisions, and
accounting records provide reviewable evidence. Compliance depends on
deployment retention, integrity, access, and export policy plus reviewer
judgment; not every log line is automatically compliant evidence.

Failures cross tool boundaries as structured results where applicable. Runtime
cleanup releases handlers, leases, and execution resources during cancellation.
No architecture should rely on a missing result being interpreted as success.

## 19. Scaling And Deployment Profiles

Web/proc/processor workers may scale horizontally around shared configured
stores. Correctness depends on explicit coordination:

- turn-scoped hosted-agent graphs rebuilt from configuration plus
  shared/checkpointed state, never retained as conversation state in one worker;
- lane ownership, active-consumer heartbeats, and owner leases for live turns;
- Redis claims plus filesystem locks for collaborative app preload;
- consumer groups and idempotency for Data Bus handling;
- generation ordering for distributed catalogs;
- scoped relay refcounts for connected streams;
- per-user/app/work concurrency and economics admission.

The same application contracts support local processes, Docker Compose,
container clusters, and ECS/Fargate deployment. Production topology may use
managed PostgreSQL/Redis/object storage and a shared filesystem such as EFS.
Operators choose whether stores are dedicated or shared and configure the
matching namespace, durability, IAM, TLS, DNS, backup, and recovery policy.

## 20. Source Map

- [Control Plane Web App](control-plane-web-app-README.md)
- [Architecture Of What We Built](architecture-of-what-we-built-README.md)
- [Architecture Of What You Build](architecture-of-what-you-build-README.md)
- [Bundles Descriptor](../configuration/bundles-descriptor-README.md)
- [Cross-Runtime Context](../runtime/cross-runtime-context-README.md)
- [Fenced Runtime Bootstrap And Reduce](../runtime/fenced-runtime-bootstrap-and-reduce-README.md)
- [ISO Runtime](../exec/README-iso-runtime.md)
- [Conversation Event Journey](../sdk/events/external-events-journey-and-handling-README.md)
- [Conversation Event Lane State](../sdk/events/conversation-event-lane-state-README.md)
- [Conversation Event Bus And Data Bus](../service/comm/conversation-event-bus-and-data-bus-README.md)
- [Connection Hub](../sdk/solutions/connections/connection-hub-solution-README.md)
- [Named-Service Providers](../sdk/namespace-services/providers-README.md)
- [Application-Hosted Sites](../sdk/solutions/sites/application-sites-README.md)
- [User Settings](../sdk/solutions/user-settings/user-settings-solution-README.md)
