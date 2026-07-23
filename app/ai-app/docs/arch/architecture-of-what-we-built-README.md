---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-we-built-README.md
title: "Architecture Of What We Built"
summary: "Current platform-runtime map of KDCube: one tenant/project deployment, browser and external ingress, app loading, ordered conversation eventing, Data Bus, tenant/project/session relay, identity and authority, storage ownership, isolated execution, economics, and deployment profiles."
status: current
tags: ["arch", "architecture", "runtime", "services", "ingress", "events", "authority", "execution", "deployment"]
updated_at: 2026-07-18
keywords: ["platform architecture", "runtime architecture", "tenant project deployment", "conversation event lane", "data bus", "SSE relay", "cross runtime context", "isolated execution", "application site catalog"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-short.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-long.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
---
# Architecture Of What We Built

This page is the current physical/runtime map of KDCube. It explains the
platform that loads apps, serves browser surfaces, authenticates actors,
schedules work, preserves event order, runs tools and agents, stores state, and
delivers results.

For the architecture an app builder composes on top of this runtime, read
[Architecture Of What You Build](architecture-of-what-you-build-README.md).
For the exact trust boundaries behind this map, read
[Security And Trust Model](security-and-trust-model-README.md).

## Scope First

One running KDCube deployment is bound to one effective `tenant/project`. It may
serve many users and apps concurrently.

Backing PostgreSQL, Redis, object storage, or filesystem infrastructure may be
dedicated or shared with other deployments. Shared topologies preserve
tenant/project boundaries through schemas, namespaces, and prefixes. Users
inside one deployment may share processes, queues, pools, and filesystem
infrastructure; per-request identity and scoped service contracts are therefore
part of the runtime boundary.

Applications loaded into processors are operator-approved, trusted backend
code. They are not isolated from one another by the generated-code sandbox.
Use separate deployments for mutually untrusted application backends.

```text
tenant + project
  selects one deployment and its storage/runtime namespace

actor + user + authority
  scope one request inside that deployment

app + conversation + agent + turn
  route and attribute one unit of product work
```

## Runtime Map

```text
browser / webhook / REST client / MCP client / channel
                              |
                              v
                    proxy and public routing
                              |
             +----------------+----------------+
             |                                 |
             v                                 v
    browser UI + app sites             API / chat / app ingress
                                               |
                         authenticate, bind request context,
                         validate, admit, upload, enqueue
                                               |
                                               v
                              Redis-backed runtime fabric
                         +---------------------+-------------------+
                         |                     |                   |
                 conversation lanes      Data Bus streams    queues / relay
                 + lane state             + results           + locks/catalogs
                         |                     |                   |
                         +---------------------+-------------------+
                                               |
                                               v
                                      proc / processor workers
                              load app and invoke declared surface
                                               |
                +------------------------------+---------------------------+
                |                              |                           |
                v                              v                           v
          app / agent logic            trusted SDK services        isolated execution
          tools / jobs / APIs          identity / storage /        supervisor + executor
          named-service providers      economics / hosting
                |                              |                           |
                +------------------------------+---------------------------+
                                               |
                                               v
                              configured stores and external providers
```

## Request And Browser Boundary

The browser first consumes `/api/cp-frontend-config`. That response is the
effective browser contract for app discovery, UI routing, and authentication.
The browser follows its provider-neutral auth fields instead of hardcoding a
Cognito, SimpleIDP, or application-hosted login flow.

`/profile` is the source of truth for logged-in state. Local OIDC cache, visible
email, or readable cookies are not sufficient.

Apps may serve widgets, a main view, a default chat, or a complete website. Site
declarations compile into a validated, versioned `ApplicationSiteCatalog`.
Redis distributes generations; each proc routes requests from an immutable
in-memory snapshot. Request-time site selection does not parse descriptors or
query Redis.

## App Loading And Surface Dispatch

`bundles.yaml` remains the descriptor and source registry. It selects app
source, module, singleton behavior, non-secret config, and provided/consumed
surfaces. Technical identifiers still use `bundle`; public prose uses **app**.

The surface model is directional:

```text
                         one app
                            |
          +-----------------+-----------------+
          |                                   |
          v                                   v
surfaces.as_provider                 surfaces.as_consumer
what the app exposes                what the app may call/resolve
          |                                   |
API, widget, MCP, bundle            per-agent Python tools
visibility and managed auth         MCP server connections + allow-lists
default chat intent                 named-service namespaces/operations
                                    UI resolvers and materialization policy
```

`surfaces.as_provider` is provider-side policy for exposed surfaces. `enabled`
still decides whether a concrete surface is served. `surfaces.as_consumer` is
app-scoped wiring for dependencies and agent inventories; it does not expose
those dependencies as this app's own surfaces. One app may fill either side or
both.

For consumed MCP, connection details live under
`surfaces.as_consumer.mcp.services`; an agent's MCP entry under
`surfaces.as_consumer.agents.<agent_id>.tools` selects a matching `server_id`
and narrows the visible tools. Python and named-service connections converge
through the same tool-subsystem policy plane.

The runtime loads app entrypoints and dispatches only declared surfaces:

```text
@api / operations       synchronous app calls and public callbacks
@ui_widget / main view  app-owned browser surfaces
default_chat            reserved SDK chat widget when explicitly declared
@mcp                    MCP facade or protected resource surface
@data_bus_handler       durable app-domain message handling
@cron / @on_job         scheduled and background work
@on_reactive_event      one scheduled conversation-event app turn
named-service provider  realm discovery and fixed-grammar operations
```

An app does not need chat, ReAct, UI, a database, or every surface family.

## Conversation Eventing And Turn Ownership

One conversation lane is keyed by:

```text
tenant + project + user + conversation + agent
```

Reactive ingress atomically publishes the prepared event batch to the lane and
admits one bodyless wake to a processor queue. Non-reactive events enter the
lane without a wake. Redis lane sequence defines order; the queue schedules
work but does not define event order.

The processor reconstructs the accepted request from retained lane state. A
live `ContextBrowser` owns consumption through a handler claim, active-consumer
heartbeat, and token-fenced event-source lease. A processed timestamp-plus-event
id cursor drives the close gate, including explicit advancement for events that
produce no timeline blocks. Superseded turns cannot fold new events or become
conversation head.

One `@on_reactive_event` call starts the scheduled app turn. Later events fold
inside that live turn or remain for a future turn; they do not repeatedly call
the app entrypoint.

## Conversation Event Bus, Data Bus, And Relay

These mechanisms solve different problems:

| Mechanism | Owns |
| --- | --- |
| Conversation Event Bus | Conversation/agent context: prompts, attachments, followups, steers, approvals, callbacks, and future-turn context. |
| Data Bus | App-owned domain mutation independent of chat, consumed by `@data_bus_handler`. |
| Chat relay | Transient delivery of chat/service events to connected SSE or Socket.IO clients. |

Chat relay subscriptions and connected-client registries are scoped by
`tenant + project + session_id`. Within that selected set, `stream_id` targets
one concrete connection. A session id alone is not a complete routing key.

A Data Bus handler must explicitly submit `external_events[]` if its outcome
should enter or wake a conversation. Apps do not push directly into processor
ready queues or invent lane state.

## Identity, Authority, And Delegation

Ingress authenticates the actor and binds runtime context. Connection Hub owns
the authority/provider registry, connection edges, authority projection,
connected external accounts, and delegated credentials.

```text
request proof -> verified external or platform actor
                      |
                      +-- same authority: resolve grants
                      |
                      `-- different authority: require an explicit edge,
                          project only allowed authority and provenance
```

Raw channel proof is not platform login. A Telegram actor becomes
platform-authorized only through a configured platform flow or explicit
connection edge.

Connected accounts and delegated access point in opposite directions:

- **delegated to KDCube:** trusted tools use a user's external provider account;
- **delegated by KDCube:** a delegate — an external automation or an in-app
  agent — receives bounded KDCube access through a server-side grant keyed to its
  client identity (`kdcube-agent:<app>:<agent>` for an agent) plus resources and
  claims, scoped per connected account. Guards accept only that client id, and an
  agent does not inherit the accounts the user connected.

Bearer tokens are handles. Managed guards load server-side grant/session
records; product code does not derive authority by decoding token bodies.

## Cross-Runtime Context And Isolated Execution

Request identity must survive async, thread, subprocess, isolated-supervisor,
app-call, and Data Bus transitions. The portable context carries JSON-safe
identity, authority, routing, app-call, accounting, discovery, and named-service
client-policy facts. It carries no live handles, secrets, or blobs; services are
rebuilt on the far side.

For generated code, the agent is an untrusted requester:

```text
agent proposes locator
        |
trusted resolver applies bound tenant/project/user/authority
        |
        +-- outside scope -> no bytes
        `-- inside scope  -> sparse execution workspace
                                      |
                                      v
                           restricted code executor
                                      |
                         authenticated tool socket
                                      |
                                      v
                        trusted supervisor/tool runtime
```

In split Docker, the executor has no network and does not receive platform
storage roots, app storage, descriptors, or provider credentials. Approved
tools execute in the trusted supervisor under carried identity and grants.
Other profiles provide different isolation strengths. Placement is per tool and
the profile is per agent: a tool declares whether it runs in the trusted
supervisor, a subprocess, or the isolated executor, layered under the
operator-selected ceiling.

## Storage Ownership

KDCube intentionally uses several storage surfaces:

| Surface | Typical role |
| --- | --- |
| Descriptors and secret lifecycle | Deployment/app configuration and secret references. |
| PostgreSQL | Durable subsystem metadata and product records. |
| Redis | Queues, lanes, relay, coordination, catalogs, and selected fail-closed auth/session/grant state. |
| App filesystem storage | App-owned mutable files; local/mounted locally, normally shared EFS in cloud. |
| Artifact storage | Separate local/object-backed artifact API. |
| User-scoped secrets | Connected external-provider credentials. |

`bundle_storage_root()` is filesystem storage, not S3. Services own their
durability contracts; Redis is not merely a cache, and app filesystem storage
is not a distributed object store by itself.

## Economics And Evidence

Economics-aware work follows `verify -> reserve -> run -> settle`. Accounting
attributes tracked usage to request lineage such as user, app, conversation,
turn, flow, provider, and model.

Structured authority, event, tool, execution, and accounting records support
review. Deployment-owned retention, integrity, access, and export policy decide
what becomes audit evidence; a reviewer decides compliance.

## Deployment Profiles

The same app/runtime contracts run locally and in cloud deployments:

- local or Docker Compose for development and single-host operation;
- process and container workers sharing configured PostgreSQL, Redis, and
  filesystem/object storage;
- ECS/Fargate profiles with managed stores, task roles, and shared filesystem;
- external execution tasks for distributed generated-code workloads.

Hosted agent frameworks follow the same horizontal-scaling rule: a graph
instance is built inside one bound turn and discarded afterward. Shared
checkpointer/domain storage carries continuity; long-lived workers retain
connections, not conversation graph state.

Deployment-specific ports, task sizes, DNS, IAM, and proxy settings belong to
their operational descriptors and deployment docs, not this stable map.

## Read Next

- [Control Plane Web App](control-plane-web-app-README.md)
- [Architecture Short](architecture-short.md)
- [Architecture Long](architecture-long.md)
- [Architecture Of What You Build](architecture-of-what-you-build-README.md)
- [Tenant, Project, User, Authority, And Execution Boundaries](../runtime/tenant-project-user-and-execution-boundaries-README.md)
- [Conversation Event Bus And Data Bus](../service/comm/conversation-event-bus-and-data-bus-README.md)
- [Application-Hosted Sites](../sdk/solutions/sites/application-sites-README.md)
