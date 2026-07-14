---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
title: "Tenant, User, Authority, And Execution Boundaries"
summary: "Canonical boundary map separating tenant/project deployment scope, shared-user runtime execution, cross-runtime identity continuity, Connection Hub authority enforcement, and reusable isolated-workspace execution for agent-generated code."
status: active
tags: ["runtime", "multi-tenancy", "identity", "authority", "connection-hub", "react", "langgraph", "isolation", "storage"]
updated_at: 2026-07-14
keywords:
  [
    "tenant project deployment",
    "tenant project namespace",
    "shared worker user isolation",
    "cross runtime identity",
    "request context",
    "authority projection",
    "Connection Hub boundary",
    "ReAct isolation",
    "reusable isolated workspace",
    "LangGraph code execution",
    "generated code isolation",
    "structural isolation",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/fenced-runtime-bootstrap-and-reduce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-realm-refs-and-workspace-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
---
# Tenant, User, Authority, And Execution Boundaries

KDCube uses several boundaries that cooperate but have different jobs. This
document is the canonical map of those boundaries.

The core model is:

```text
tenant + project
  selects the deployment and storage namespace

request context
  identifies the current actor, user, route, and execution

cross-runtime context
  preserves those facts across supported runtime transitions

guarded authority boundary
  verifies the authority and grants required by a protected surface

agent workspace + isolated executor
  receives only bytes resolved under the runtime-bound user/authority
  limits ambient capabilities available to model-generated code
```

These concepts describe distinct scopes. Tenant/project identifies the
deployment. User and actor identity scope each request. An app is trusted
deployable code inside that deployment. Agent harnesses such as KDCube ReAct or
an integrated LangGraph graph can use the shared isolated-workspace runtime.
Cross-runtime context preserves identity facts, and a surface guard authorizes
the action.

## Boundary Vocabulary

| Term | Meaning |
| --- | --- |
| tenant | Top-level organizational owner of a KDCube deployment scope. |
| project | Runtime and data scope under a tenant. A running KDCube deployment is bound to one effective tenant/project pair. |
| deployment | The ingress, proc/processor workers, supporting services, configuration, and storage bindings running for one tenant/project. A deployment may contain several replicas or machines. |
| backing infrastructure | PostgreSQL, Redis, object storage, shared filesystems, secret providers, and other resources used by a deployment. Resources may be dedicated or shared through tenant/project namespaces. |
| app | Trusted deployable application code, configuration, APIs, tools, jobs, UI surfaces, and optional agents inside the tenant/project deployment. Technical identifiers still use `bundle_id` in current source and descriptors. |
| actor | Identity that caused the request or work, such as a platform user, Telegram identity, or delegated external client. |
| platform subject | Identity established under an authority marked `platform: true`, used where a platform boundary requires it. |
| user scope | The user identity selected for a specific storage or product operation. It may remain the external actor or use an explicitly projected platform/grantor identity. |
| cross-runtime context | Platform-owned, JSON-safe context that carries request, authority, routing, app-call, discovery, and accounting facts across supported runtime boundaries. |
| guarded boundary | API, tool, named service, economics check, storage facade, or other surface that declares and enforces its authority and grant requirements. |
| ReAct | KDCube's reactive agentic harness. It works with logical refs and runtime tools; generated code receives only materialized physical workspace data. |
| agent adapter | Integration that binds another agent framework to KDCube runtime services. The LangGraph reference app binds its `run_python` tool to the same isolated execution, workspace, trusted tool, and file-hosting primitives. |
| isolated workspace | Per-turn or per-execution physical work/output tree populated with inputs successfully resolved under the bound user/authority and used as the filesystem view for generated code. |
| executor | Runtime that executes generated code. The split Docker executor has the narrowest mount, environment, credential, and network surface. |

## Complete Boundary Map

```text
Optional shared infrastructure
  PostgreSQL instance
    schema: kdcube_<tenant>_<project>
  Redis
    keys/channels/streams scoped by tenant + project
  object storage
    dedicated bucket OR tenant/project key prefix
  filesystem/EFS
    tenant/project/app and user/conversation paths
             |
             v
+-------------------------------------------------------------+
| One KDCube deployment: tenant + project                     |
|                                                             |
| ingress -> proc workers -> processor -> apps/agent harnesses |
|                                                             |
| Many users may execute concurrently in the same processes,  |
| worker pool, processor, and filesystem infrastructure.      |
+----------------------------+--------------------------------+
                             |
                             | authenticated request binding
                             v
                  REQUEST_CONTEXT / UserSession
                  actor + tenant/project + user
                  routing + identity_authority
                             |
              +--------------+----------------+
              |                               |
              v                               v
      trusted app/tool code          agent harness
      scoped SDK/store access        ReAct or framework adapter
      surface guards                           |
              |                               | propose locator (untrusted)
              |                               v
              |                     trusted user-bound resolver
              |                               |
              |                               | materialize in-scope bytes
              |                               v
              |                     generated-code executor
              |                     narrow work/out surfaces
              |                               |
              +---------------+---------------+
                              |
                              | protected operation
                              v
                   guarded authority boundary
                   required authority + grants
                              |
                 same authority: resolve grants
                 another authority: resolve edge,
                   project authority, resolve grants
                              |
                              v
                  allow bounded operation or reject
```

## 1. Tenant And Project Are The Deployment Scope

A running KDCube deployment has one effective `tenant` and `project`. The pair
is the deployment identity used by service configuration, queue routing,
storage layout, discovery, and accounting.

The physical backing services can be dedicated or shared:

| Surface | Tenant/project boundary | Deployment choice |
| --- | --- | --- |
| PostgreSQL | Project tables live in a tenant/project schema such as `kdcube_<tenant>_<project>`. | One database per deployment or a shared PostgreSQL instance with separate schemas. |
| Redis | Platform keys, channels, streams, queues, sessions, locks, and caches include the tenant/project namespace in their contract. | One Redis deployment per tenant/project or a shared Redis keyspace. |
| Object storage | Conversation, attachment, artifact, and execution keys include `cb/tenants/<tenant>/projects/<project>/...`. | A dedicated bucket or a shared bucket with a tenant/project prefix. |
| App filesystem storage | `bundle_storage_root()` resolves under `<root>/<tenant>/<project>/<app>`. | Local mounted storage or shared deployment storage such as EFS. |
| ReAct lineage and artifacts | Paths and refs include tenant/project, then user/conversation/turn scope where applicable. | Local/shared filesystem, git-backed lineage, or configured artifact storage. |
| Configuration and secrets | Platform values are deployment-scoped; app and user values add app/user scope. | File, secrets service, or another configured provider. |

Namespace separation and physical resource separation are independent choices:

```text
shared PostgreSQL + separate schemas
shared Redis + separate key/channel/stream namespaces
shared bucket + separate key prefixes

or

dedicated PostgreSQL / Redis / bucket per tenant/project
```

The deployment contract stays tenant/project-scoped in both topologies. A
shared backing service can physically contain data for several deployments,
while each deployment addresses its own namespace through platform-owned
storage and routing contracts.

## 2. Users Share Runtime Machinery

Users inside one tenant/project deployment normally share runtime machinery:

- a proc process can execute several user requests concurrently;
- processor workers schedule turns for many users;
- in-process tools share the app process;
- workspaces can live under one mounted filesystem or EFS root;
- Redis, PostgreSQL pools, and object-storage clients can be shared by workers.

The per-user boundary therefore comes from request binding, scoped addresses,
guarded operations, and the narrower isolated execution surface. Users can share
an operating-system process, database, Redis instance, and filesystem while
those contracts preserve the active user scope.

The important hierarchy is:

```text
tenant/project deployment
  app
    actor / user scope
      session
        conversation
          turn
            execution
```

Platform-owned stores use the scope required by the object. Examples include:

```text
user settings:
  tenant + project + app + user + typed key

conversation event lane:
  tenant + project + user + conversation + agent

conversation artifacts:
  tenant + project + user + conversation + turn

app storage:
  tenant + project + app

connected provider credential:
  tenant + project + user-scoped secret key
```

The runtime derives this scope from the authenticated request and
platform-owned context. Model output supplies operation inputs inside that
already-bound scope.

## 3. Cross-Runtime Context Preserves Identity

The same user request can move through an async task, worker thread, local
subprocess, Docker/Fargate supervisor, peer app operation, Data Bus worker, or
later scheduled execution. KDCube's cross-runtime context preserves the small
set of identity and routing facts needed to reconstruct trusted services on the
other side.

The portable room contains facts such as:

```text
REQUEST_CONTEXT
  actor.tenant_id / actor.project_id
  user.user_id / roles / permissions / identity_authority
  routing.app / session / conversation / turn
  request metadata

BUNDLE_CALL_CONTEXT
  app-owned JSON-safe invocation metadata
  resolved identity_authority and connection-edge provenance when present

NAMED_SERVICE_DISCOVERY
  tenant / project discovery scope

accounting context
  tenant / project / actor or user / conversation / turn / app / agent
```

Its request and authority portion carries ids and JSON-safe descriptors.
Database pools, Redis clients, live provider objects, callbacks, and file bytes
remain outside that context.

The full portable spec used by a local child or trusted supervisor also carries
model-service configuration; the current `ModelConfigSpec` can include model
provider keys required to rebuild `ModelService`. Docker split execution then
removes `PORTABLE_SPEC_JSON`, descriptor payloads, app storage paths, and other
privileged runtime globals from the restricted executor payload. Provider
account credentials and user secrets continue to resolve through trusted
supervisor-side services.

Boundary behavior is explicit:

| Runtime transition | Identity continuity contract |
| --- | --- |
| Await in the same async task | Current `ContextVar` bindings remain visible. |
| New async task | Create the task while the intended context is bound. |
| Worker thread | Run under copied context or pass the portable spec explicitly. |
| Local subprocess | Restore `PORTABLE_SPEC_JSON` and rebuild trusted services. |
| Docker/Fargate supervisor | Restore portable context, communicator, descriptors, and trusted tool services. |
| Split Docker executor | Receive the minimal executor environment and supervisor socket; authority-bearing services remain on the trusted supervisor side. |
| Peer app operation | Build and bind the target app's request context around the local call. |
| Data Bus handler | Bind actor/auth metadata carried by the message. |
| Cron or detached job | Persist and re-bind explicit auth metadata when the work acts for a user; otherwise run headless. |

The contract can be summarized as:

```text
cross-runtime context preserves identity and authority facts;
the receiving boundary decides whether those facts authorize an operation.
```

This distinction matters. Context propagation keeps the authenticated identity
stable across supported transitions. Authorization remains the responsibility
of the protected storage, tool, API, named-service, or economics boundary.

## 4. Connection Hub Resolves Authority At Guarded Boundaries

Authentication identifies an actor. Authorization asks whether that actor may
cross the current boundary with the required authority and grants.

The actor, storage subject, platform subject, and economics subject can differ:

```text
actor:
  telegram_100200300

storage identity:
  telegram_100200300

linked platform subject:
  platform:a1b2c3d4-...

economics subject:
  a1b2c3d4-...
```

Connection Hub keeps those facts explicit:

```text
request proof
  -> registered authenticator verifies one proof shape
  -> verified actor + authority_id
  -> surface guard declares required authority + grants
  -> same authority: resolve grants
  -> different authority: resolve an explicit connection edge
  -> project only the authority allowed by that edge
  -> carry identity_authority + edge provenance in runtime context
  -> protected surface allows or rejects the operation
```

Selector headers, provider ids, and token envelopes route verification. The
authenticator's successful verification establishes identity. Server-side
connection edges and grant records establish the meaning and limits of a
delegation.

### Platform authority and channel identity

An authority marked `platform: true` establishes platform subjects. The runtime
normalizes an empty role set for a successfully authenticated platform subject
to the baseline platform role `kdcube:role:registered`.

Telegram `initData`, webhook signatures, and similar channel proofs establish
external actors under their own authorities. A channel actor reaches a
platform-authority boundary through an explicit Connection Hub edge and the
grants selected on that edge.

Channel-first and platform-first linking produce the same edge shape:

```text
channel first:
  verify channel subject
  -> create challenge
  -> prove platform subject
  -> approve grants
  -> write edge

platform first:
  prove platform subject
  -> create challenge
  -> verify provider subject
  -> approve grants
  -> write edge
```

The external actor remains the actor. Platform roles and economics authority
come from the linked platform authority and are intersected with the edge's
delegated grants.

### Delegated in both directions

Connection Hub protects two opposite credential directions:

| Direction | Meaning | Runtime boundary |
| --- | --- | --- |
| Delegated to KDCube | A user connects Gmail, Slack, iCloud, or another provider so a trusted KDCube tool can use that provider account. | Tool declares provider claims; Connection Hub resolves the current user's account and credential on the trusted side. |
| Delegated by KDCube | A user gives an external automation a bounded KDCube credential. | Managed guard loads the server-side grant record and enforces resource, operation/tool, grant, expiry, and identity scope. |

Provider credentials remain in user-scoped secrets. Generated code and models
receive tool contracts, refs, consent actions, and bounded results. Delegated
KDCube bearer tokens are handles; server-side grant/session records remain the
authority source.

For a delegated external client, the delegate remains the actor and the user
remains the grantor. Product reads or economics may use the grantor only when
the stored delegation edge and identity scope allow that projection.

### Failure at the restricted boundary

A call can travel through shared runtime machinery under a valid actor context
and still be refused at the next protected surface. Typical refusal points are:

- authenticator rejects the proof;
- the required platform-authority connection edge is absent;
- the edge omits the required grant;
- delegated credential grant/session state is missing or expired;
- the resource or operation is outside `resource_grants` or selected tools;
- a connected provider account is absent, unhealthy, or lacks the requested
  provider claim;
- economics denies the projected subject's spend.

Connection Hub grant/session records fail closed when required state is absent.
Connected-account failures return managed connect, claim-upgrade, account-pick,
or reconnect actions rather than exposing provider credentials to app callers.

## 5. Apps And Tools Are Trusted Runtime Code

An app can provide APIs, tools, named services, jobs, UI surfaces, Data Bus
handlers, or ReAct agents. These surfaces are trusted server code. Depending on
configuration, a tool can run:

- in the app process;
- in a local subprocess;
- in the trusted supervisor of an isolated execution.

Tool execution follows its configured runtime and the surface's declared
policy. In-process tools, local subprocess tools, and supervisor-hosted tools
therefore have different physical boundaries.

Trusted app and tool code should:

1. enter through platform request/session binding;
2. read actor and user scope from runtime context;
3. declare surface visibility, authority, grants, and provider claims;
4. use tenant/project/app/user-scoped SDK storage contracts;
5. resolve provider credentials through Connection Hub helpers;
6. pass refs or bounded results to model-facing code;
7. preserve authority metadata when scheduling detached work.

Custom code that opens a shared database, bucket, Redis keyspace, or filesystem
directly owns the same authorization responsibility as any trusted server
application. Platform storage facades and guards are the enforcement seams;
cross-runtime context supplies their current identity.

## 6. Isolated Workspaces And Executors Are Reusable Agent Primitives

The isolated workspace and execution runtime belong to the KDCube SDK/runtime.
An agent harness integrates them as a model-callable execution tool. The common
lifecycle is:

```text
AGENT HARNESS
  KDCube ReAct | LangGraph | another integrated agent
       |
       | propose an object/conversation/turn locator
       v
TRUSTED RESOLVER / PULL
  scope comes from RuntimeCtx, never from the locator
  enforce tenant + project + actor + user authority
       |
       | materialize only an in-scope result
       v
SPARSE ISOLATED WORKSPACE
  current user / conversation / turn
  authorized input files + bounded output paths
       |
       | mount current work/output surfaces
       v
ISO EXECUTOR
  generated code + configured resource envelope
  split mode: read-only root + network none
       |
       | authenticated tool socket
       v
TRUSTED SUPERVISOR TOOLS
  request identity + grants + credentials + network

ISO EXECUTOR -> produced files -> hosting service -> conversation refs -> AGENT
```

The agent is an untrusted requester. It can propose a ref or locator, including
one invented by compromised model behavior. The ref carries object identity;
it carries no user authority. Trusted runtime code decides whether that locator
resolves inside the already-bound request scope. The sparse workspace contains
only bytes returned by that trusted resolution. The ISO executor runs generated
code against that physical view. Trusted tools execute in the supervisor and
return bounded results through the authenticated bridge.

### Requester versus authority

The responsibility split is explicit:

| Participant | Responsibility |
| --- | --- |
| Agent/model | Proposes a ref, conversation id, turn id, object id, or tool arguments. Treat every value as untrusted input. |
| Ingress/runtime | Binds tenant, project, actor, user, authority, routing, and accounting context independently of model output. |
| Trusted resolver | Combines the requested locator with the bound scope and either returns an in-scope object or returns missing/denied. |
| Workspace service | Copies bytes only after successful resolution into the current user/conversation/turn workspace. |
| ISO executor | Reads the resulting workspace; it receives no selector capable of changing the bound user or storage root. |

For built-in conversation refs, `ContextBrowser.get_turn_log(...)` supplies
`RuntimeCtx.user_id` to `materialize_turn(...)`. The conversation and turn may
come from the ref, while the user comes from runtime context. The index lookup
is therefore keyed by the bound user plus conversation and turn. A locator that
names another user's conversation finds no row in the bound user's scope.

For git-backed project state, the lineage root and remote refs include:

```text
tenant / project / user_id / conversation_id
```

A cross-conversation ref can select another conversation for the same bound
user. It cannot replace the tenant, project, or user segments. Safe-path checks
also reject absolute paths and parent traversal.

External owner refs such as `mem:`, `task:`, and `cnv:` go through a registered
trusted namespace resolver/rehoster. That service applies its own authorization
under the carried request identity. A custom resolver is trusted app code and
owns the same authorization obligation as any custom protected surface.

The security consequence is precise: compromising the agent may change which
locators and approved tools it requests. It does not change the bound user,
expand grants, expose a broader storage root, or place bytes into the workspace
when trusted resolution fails.

### ReAct integration

ReAct is KDCube's reactive agentic harness. The model works with logical refs,
timeline objects, and tool contracts. A model-selected ref remains an untrusted
locator. Physical filesystem access begins inside an execution tool only after
trusted runtime resolution under the bound user scope has materialized bytes
into the current workspace.

Each ReAct turn starts with a sparse workspace:

```text
logical refs proposed by ReAct
  conv:fi:...
  mem:...
  task:...
  cnv:...
       |
       | react.read / react.pull / react.checkout
       v
trusted resolver applies RuntimeCtx user/authority scope
  in scope -> materialize bytes
  outside scope -> missing/denied, no workspace bytes
       |
       v
current user/conversation/turn workspace
       |
       v
generated code reads current-turn physical paths
```

Historical conversation files and external owner refs require explicit
materialization. Editable project state requires checkout into
`turn_<current>/git/projects/...`. Produced files belong under
`turn_<current>/files/...`.

The physical workspace can live on infrastructure shared by many users. The
runtime derives the lineage from tenant, project, and the bound user, then
materializes the authorized execution tree. Generated code receives that tree,
rather than the shared workspace root or backing object store.

### LangGraph reference integration

The `ported-langgraph-agents@2026-07-13` reference app demonstrates the same
runtime outside ReAct. Its framework adapter:

1. exposes `run_python` as a normal LangChain tool in the selected agent's
   configured inventory;
2. creates a fresh per-turn workspace under the platform execution-workspace
   root;
3. binds the current communicator, request identity, hosting service, and
   `ToolSubsystem` for the graph turn;
4. calls the SDK `run_exec_tool_side_effects(...)` path using the deployment's
   selected in-memory, subprocess, Docker, or Fargate profile;
5. discovers produced files from the output diff, hosts them into conversation
   storage, and returns file refs to the LangGraph agent.

ReAct supplies the full logical-ref browser with `react.pull(...)` and
`react.checkout(...)`. Other agent adapters can send requested locators through
trusted, user-bound SDK resolvers before invoking the same isolated execution
path.
The reusable boundary is the materialized workspace plus the configured
executor; the agent framework remains responsible for when it asks to run code.

### Split supervisor and executor

The strongest documented generated-code boundary is split Docker execution:

| Trusted supervisor | Restricted executor |
| --- | --- |
| Restores portable request and accounting context. | Receives minimal safe execution metadata. |
| Rebuilds descriptors, storage clients, tools, and provider integrations. | Receives work, artifact output, executor-log, and supervisor-socket surfaces. |
| Has network according to deployment policy. | Runs with `--network none`. |
| Resolves and executes approved tools under the carried user authority. | Calls approved tools through the authenticated supervisor socket. |
| Keeps app/platform storage and secrets on the trusted side. | Runs with read-only root and narrow writable mounts. |

Local subprocess execution provides process/crash separation. Split
Docker/Fargate execution adds supervisor/executor credential separation,
network isolation, and narrow container mounts; documentation should name that
mode when making those stronger claims.

The isolated-execution guarantee is structural for generated code:

```text
model-generated code
  sees materialized workspace bytes
  writes bounded work/artifact paths
  reaches privileged operations through approved tool contracts
```

Trusted tools remain a separate authority path. In split execution, executor
tool stubs send validated calls over the authenticated socket and the trusted
implementation executes in the supervisor. Its guards, parameter validation,
grants, provider claims, execution policy, and economics still apply.

## 7. How The Boundaries Compose

### Platform browser request

```text
browser platform credential
  -> platform provider verifies session
  -> UserSession + REQUEST_CONTEXT
  -> shared proc worker
  -> app surface guard
  -> ReAct turn or trusted app operation
  -> scoped storage/tool/economics boundary
```

### Telegram-triggered ReAct turn

```text
Telegram proof
  -> Telegram authenticator verifies external actor
  -> Connection Hub resolves edge when platform authority is required
  -> actor remains Telegram; platform/economics projection is recorded
  -> cross-runtime context carries actor + projection
  -> conversation event submission
  -> shared processor schedules app turn
  -> ReAct receives logical context for that actor/conversation
```

An absent edge leaves the actor external. A platform-authority guard can then
refuse the operation or direct the user into the link flow.

### External automation calling KDCube

```text
delegated bearer handle
  -> delegated_client authenticator
  -> server-side grant/session lookup
  -> resource + operation/tool + resource_grants check
  -> delegate remains actor
  -> grantor projection applied only for the consented identity scope
  -> app or named-service operation
```

### Generated code using a connected account

```text
generated code
  -> approved mail/slack/named-service tool call
  -> trusted supervisor restores carried user context
  -> tool declares provider claims
  -> Connection Hub resolves current user's connected account
  -> credential stays on trusted side
  -> provider call
  -> bounded result or managed consent/reconnect response
```

## Enforcement Matrix

| Concern | Scope or subject | Enforcing component |
| --- | --- | --- |
| Deployment identity | tenant + project | assembly/runtime configuration and service startup |
| PostgreSQL project data | tenant + project schema | database deployment and scoped store implementation |
| Redis state | tenant + project namespace | namespace helpers and each Redis surface contract |
| Object/file storage | tenant + project, then app/user/conversation/turn as required | storage key/path builders and store APIs |
| Current user identity | authenticated actor and `UserSession` | ingress/auth resolver and request-context binding |
| Runtime identity continuity | request, authority, routing, and accounting descriptors | cross-runtime snapshot/bootstrap contract |
| Cross-authority access | actor, required authority, edge grants | Connection Hub authenticator, edge resolver, grant resolver, and surface guard |
| Delegated external client | delegate, grantor, resource, tools/operations, `resource_grants` | managed delegated-credential guard and server-side grant records |
| Connected provider account | current user, provider, connector app, claims, credential health | Connection Hub delegated-account broker and trusted integration tool |
| ReAct ref materialization | untrusted locator under bound tenant/project/user scope, then materialized current workspace | RuntimeCtx, ReAct ref resolver, pull/checkout, workspace service |
| Framework-adapted code workspace | adapter-requested locators resolved under carried identity, then per-turn work/output roots | agent adapter, scoped SDK resolver, workspace helpers, and isolated execution tool |
| Generated-code ambient access | current execution work/out surfaces and approved tool bridge | isolated supervisor/executor runtime |
| Spend | actor plus projected economics subject | accounting/economics context and policy enforcement |

## Engineering Rules

1. Bind tenant, project, actor, user, routing, and authority at platform entry
   points; model/tool arguments are untrusted locators inside that fixed scope.
2. Create detached tasks inside the intended context or persist and re-bind the
   required authority envelope for later work.
3. Build portable specs after the active user/agent/authority scope is bound.
4. Keep request and app-call context JSON-safe and free of secret/file payloads;
   confine trusted model configuration and descriptor secrets to the runtime
   modes that require them, then strip them from the split executor.
5. Use tenant/project and user-scoped SDK stores rather than constructing
   shared backend addresses in product code.
6. Declare the authority and grants required by protected surfaces.
7. Resolve cross-authority identity only when a boundary requires it; carry the
   resulting projection and provenance forward.
8. Keep the actor distinct from a projected platform, storage, or economics
   subject.
9. Resolve provider credentials through Connection Hub on the trusted side.
10. Resolve every model-proposed ref under the bound user/authority context,
    then materialize successful results before generated code, search, patch,
    or rendering receives physical bytes.
11. Use split isolated execution for the strongest generated-code network,
    mount, environment, and credential boundary.
12. Treat missing authority, edge, grant, session, or credential state as a
    refusal or managed consent/reconnect condition.

## Precise Language For Documentation And Publications

Use claims that name the boundary they describe:

| Topic | Precise statement |
| --- | --- |
| Tenant/project | A running KDCube deployment is bound to one tenant/project. Backing services may be dedicated or shared through PostgreSQL schemas, Redis namespaces, and object/file prefixes. |
| Concurrent users | Users share worker and processor infrastructure. KDCube binds each request to an authenticated actor and preserves that identity across supported runtime transitions. |
| Authorization | Cross-runtime context carries identity and authority facts; guarded services enforce the required authority and grants. |
| Connection Hub | External identities and delegated clients cross protected boundaries through explicit connection edges, server-side grants, and authority projection. |
| ReAct | ReAct may propose any logical ref string. The ref grants no authority: trusted resolution keeps tenant/project/user scope in RuntimeCtx, and generated code receives only successfully resolved bytes materialized into the current workspace. |
| Agent-framework integration | Agent adapters can bind the same per-turn workspace, isolated executor, trusted supervisor tools, and file-hosting path. The LangGraph reference app exposes this as `run_python`. |
| Isolated execution | In split execution, the restricted executor receives narrow work/output surfaces and reaches privileged operations through the trusted supervisor. |
| Trusted apps/tools | Apps and tools are trusted server code and use scoped stores, surface guards, and carried request identity. |
| Combined posture | KDCube combines namespace isolation at the deployment layer, identity and grant enforcement in the shared runtime, and structural isolation around model-generated code. |

Keep these guarantees attached to their enforcing boundaries. Shared
infrastructure can contain several tenant/project namespaces, trusted app code
uses authorization, and agent-generated code receives a materialized execution
workspace. The structural claim belongs to the generated-code boundary; the
authority claim belongs to guarded runtime surfaces.

## Primary Sources

Runtime and isolation:

- [Runtime Surfaces And Boundaries](README.md)
- [Cross-Runtime Context](cross-runtime-context-README.md)
- [Fenced Runtime Bootstrap And Reduce](fenced-runtime-bootstrap-and-reduce-README.md)
- [ISO Runtime](../exec/README-iso-runtime.md)
- [ReAct Workspace Model](../sdk/agents/react/workspace/workspace-model-README.md)
- [ReAct Realm Refs And Workspace Paths](../sdk/agents/react/react-realm-refs-and-workspace-paths-README.md)

Reusable agent-framework integration:

- `kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/platform/code_exec.py`
- `kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/platform/code_exec_tool.py`

Identity-bound materialization implementation:

- `kdcube_ai_app/apps/chat/sdk/solutions/react/browser.py`: historical turn lookup passes `RuntimeCtx.user_id` independently of the requested conversation.
- `kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py`: turn materialization queries the index by user, conversation, and turn.
- `kdcube_ai_app/apps/chat/sdk/solutions/react/git_workspace.py`: cross-conversation workspace resolution changes the conversation segment while retaining tenant, project, and user.
- `kdcube_ai_app/apps/chat/sdk/solutions/react/tools/tests/test_materialization_identity_scope.py`: regression coverage for both invariants.

Deployment and storage:

- [Platform Assembly Descriptor](../configuration/assembly-descriptor-README.md)
- [Runtime Configuration And Secrets](../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [Architecture](../arch/architecture-short.md)

Connection Hub and authority:

- [Connection Hub Solution](../sdk/solutions/connections/connection-hub-solution-README.md)
- [Authority Projection](../sdk/solutions/connections/authority-projection/authority-projection-README.md)
- [Connection Edges](../sdk/solutions/connections/connection-edges/connection-edges-README.md)
- [Delegation Edges](../sdk/solutions/connections/delegated-credentials/delegation-edges-README.md)
- [Delegated Credential Protocol Adapters](../sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md)
- [Delegated Connections](../sdk/solutions/connections/delegated-connections/delegated-connections-README.md)
- [Channel-First Connection Edge Flow](../sdk/solutions/connections/link-flows/channel-first-connection-edge-flow-README.md)
- [Platform-First Connection Edge Flow](../sdk/solutions/connections/link-flows/platform-first-connection-edge-flow-README.md)
- [Connection Hub Storage Model](../sdk/solutions/connections/storage-model/storage-model-README.md)
