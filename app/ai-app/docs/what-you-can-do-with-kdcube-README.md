---
id: repo:kdcube-ai-app/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
title: "What You Can Do With KDCube"
summary: "Builder-facing map of KDCube as an open-source AI application framework with an integrated production runtime: keep existing agents and product code, adopt one useful boundary, or compose complete multi-surface apps with identity, conversations, files, isolated execution, integrations, economics, and deployment services."
tags: ["docs", "product", "overview", "framework", "runtime", "platform", "app", "agent", "integration"]
keywords: ["what is kdcube", "what can kdcube do", "ai application framework", "production agent runtime", "keep existing agent", "langgraph integration", "multi-user chat", "isolated code execution", "connected accounts", "delegated operators", "named services", "scene", "canvas", "user memories", "application hosted website", "deploy from git", "agent economics"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-we-built-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/settle-your-solution-in-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/sites/application-sites-README.md
---
# What You Can Do With KDCube

KDCube is an open-source AI application framework with an integrated,
self-hosted production runtime.

Keep the agent, tools, UI, services, and storage that already make your product
useful. Add KDCube where it removes routine infrastructure: deployment,
authenticated APIs, ordered conversations, streaming, files, isolated code,
connected accounts, delegated access, user settings, or cost enforcement.

You do not have to adopt everything. One existing handler behind one app
operation is valid. So is a complete product with several agents, widgets,
websites, scheduled jobs, and domain services.

Application backends are trusted code selected by the deployment operator.
Generated code has a separate, profile-dependent isolation boundary. One
running deployment serves one effective `tenant/project` and may contain many
users and applications. Read the
[Security And Trust Model](arch/security-and-trust-model-README.md) for the
precise guarantees and non-guarantees behind this overview.

```text
your product owns                  KDCube can operate
-------------------------------    ---------------------------------
agent logic and prompts            ingress, scheduling, streaming
business services and tools        identity, grants, consent
product UI and interaction         conversations, files, events
domain data and rules              isolated execution, economics
                                   deployment, health, failure reports
```

For the shortest local path, read [Quick Start](quick-start-README.md). For
host-product integration choices, read
[How To Integrate With KDCube Apps](how-to-integrate-with-kdcube-apps-README.md).

## 1. Start With The Boundary You Need

| What you already have | First useful KDCube step | What stays yours |
| --- | --- | --- |
| Python agent or graph | Bind it to an app operation or conversation surface. | Framework, graph, prompts, tools. |
| Existing website | Consume KDCube APIs/streams or embed the ready chat component. | Product shell, design system, navigation. |
| Backend service or webhook | Expose one authenticated operation or public callback. | Handlers and domain storage. |
| Generated-code workflow | Attach the isolated execution tool and workspace contract. | Workflow and approved tool contract. |
| Gmail, Slack, or custom OAuth service | Let users connect accounts and approve the claims tools need. | Provider-facing product behavior. |
| Script, CI job, or external agent | Protect one REST or MCP resource with delegated grants. | External client implementation. |
| No existing AI surface | Start with the ready chat, ReAct agent, files, web search, and code execution. | Product-specific instructions, tools, skills, and policy. |

These paths are independent. The first boundary can remain the only one.

## 2. Framework, Runtime, And Platform

The framework is what builders develop against: SDKs, contracts,
configuration, components, and extension points.

The runtime is the active machinery that executes and enforces those contracts
under concurrency, failure, security, and cost constraints.

The platform is the framework and runtime together with shared operating
services that let independently authored apps work together.

```text
FRAMEWORK
  app model | SDK | config | tools | UI components | extension contracts
       |
       | declarations become enforced behavior
       v
RUNTIME
  ingress | scheduling | event order | storage | isolation | accounting
       |
       | shared operating services
       v
PLATFORM
  apps | users | connected accounts | hosting | settings | control surfaces
```

KDCube was built runtime-first. Production responsibilities were implemented
for real applications and then generalized into reusable framework contracts.

## 3. Build Small Apps Or Complete Products

An **app** is a descriptor-addressed application unit. It can expose any honest
combination of surfaces; none is universally required.

Every app can participate in two directions:

```text
as_provider
  what this app exposes to users, other apps, agents, or external clients

as_consumer
  what this app and each of its agents are configured to call or resolve
```

An app may be only a provider, only a consumer, or both. For example, a mail
app can provide a named-service realm and MCP facade; a workspace app can
consume that realm; and the same workspace app can also provide its own chat,
widgets, APIs, or website. Provider policy does not implicitly grant consumer
access, and consumer wiring does not publish a surface.

| App shape | Possible surfaces |
| --- | --- |
| Backend-only service | REST operations, public APIs, Data Bus handlers, jobs. |
| Existing agent host | One or several agents behind REST, webhooks, or conversations. |
| Ready assistant | Chat widget, ReAct agent, tools, skills, files, web search, isolated code. |
| Domain service | Named-service provider, MCP facade, storage, APIs, events. |
| Multi-surface workspace | Scene, chat, Canvas/Pinboard, memories, task or domain widgets. |
| Application website | Built main view served under an alias or dedicated host. |
| External-agent gateway | Managed MCP or REST resources protected by delegated credentials. |
| Scheduled automation | Cron scans, background jobs, provider integrations, accounted calls. |

The app package remains self-describing: runtime composition, human docs,
machine-readable interfaces, configuration templates, storage ownership,
tests, release metadata, and implementation journal describe the same app.
See [How To Write A KDCube App](sdk/bundle/build/how-to-write-bundle-README.md).

Compatibility note: current source, descriptors, routes, and CLI commands still
use `bundle` in identifiers such as `bundle_id`, `bundles.yaml`, and
`kdcube bundle reload`. Public prose uses **app**.

## 4. Serve An Existing Agent Or Use The Ready ReAct Agent

KDCube does not require one agent framework. An app can host LangGraph, CrewAI,
the Claude Agent SDK, custom Python, or another loop behind the same product
surfaces.

The reference `ported-langgraph-agents@2026-07-13` app keeps two LangGraph
agents as the decision runtimes. Small adapters connect model calls, stream
events, turn input/output, files, and accounting to KDCube. The graphs, prompts,
LangChain tools, and checkpointer keep their jobs.

Scaled serving changes the lifetime of the graph object, not the agent's
behavior. The reference app builds a fresh graph inside each turn, runs it only
for that bound user/conversation turn, and then discards it. It does not keep a
stateful graph on the long-lived app instance. Durable continuity belongs in
shared/checkpointed storage; only true connections, such as the checkpointer
connection, are reused across turns. Any worker can therefore serve the next
turn without depending on another worker's process memory.

Teams that want a ready agent can use KDCube ReAct. It already supports:

- a conversation-owned logical workspace;
- web, knowledge, file, rendering, and code-execution tools;
- skills and app-local Python tools;
- ordered timeline events, followups, steers, and recovery;
- named-service realms for memories, conversations, mail, Slack, and custom
  domains;
- chartered subagents for parallel assignments when enabled;
- runtime-governed multi-action and tool-execution policy.

An app can declare `surfaces.as_provider.bundle.default_chat: true` to serve the
SDK chat under the reserved `chat` widget alias. The declaration expresses
intent; merely inheriting a reactive entrypoint does not.

The tools available to each hosted agent are consumer wiring. App configuration
places Python tools, MCP connections, and named-service namespaces under
`surfaces.as_consumer`, including the agent-specific inventory under
`surfaces.as_consumer.agents.<agent_id>`. This lets one app host several agents
with different callable surfaces while exposing a separate set of provider
surfaces to the outside.

Read [Settle Your Solution In A KDCube App](recipes/kdcube_for_agents/settle-your-solution-in-kdcube-README.md),
[How To Construct A ReAct Agent](sdk/agents/react/how/how-to-construct-react-agent-README.md),
and [Work With Subagents](sdk/agents/react/work-with-subagents-README.md).

## 5. Give Users A Ready Multi-User Chat Experience

The reusable chat component can be embedded in an existing site or mounted in a
KDCube scene. It provides streaming conversations, attachments, files,
followups, steer controls, tool progress, model/capability selection, and
connection actions.

Each agent has its own administrator-granted inventory of tools, skills, MCP
servers, named-service operations, models, and optional helper agents. Users
may narrow that inventory for one conversation.

The picker is explicit:

```text
application inventory = ceiling
        |
        v
conversation draft in the picker
        |
        | Save changes
        v
conversation-scoped selection enforced on later turns
```

An optional user baseline supplies defaults for **future** conversations. A new
conversation materializes that baseline once; later baseline changes do not
rewrite existing conversations. Sending a chat message does not implicitly save
picker edits.

See [Conversation-Scoped Agent Capabilities](sdk/solutions/user-settings/capabilities-README.md).

## 6. Run Generated Code Without Giving It Platform Authority

KDCube's isolated execution service is reusable by ReAct and by other agent
adapters. The model or agent is an untrusted requester, not an authority
source.

```text
agent proposes a logical ref or tool arguments
        |
        | untrusted locator
        v
trusted resolver applies runtime-bound tenant/project/user/authority
        |
        +-- out of scope -> missing/denied; no bytes
        |
        `-- in scope -> materialize into a sparse execution workspace
                               |
                               v
                         isolated executor
```

In the strongest split-Docker profile, generated Python runs in a restricted
executor with no network, a minimal environment, and only the assigned work,
output, log, and supervisor-socket mounts. Platform stores, app storage,
descriptors, provider credentials, and other users' workspace roots stay on the
trusted side.

Approved tool calls use an authenticated supervisor bridge. Their trusted
implementations execute under the carried request identity, grants, provider
claims, and economics context. In this split profile, credentials do not move
into generated code.

Isolation is not one global switch: each tool declares where it runs — the
trusted supervisor process, a subprocess, or the isolated executor — and the
profile is selected per agent, under the operator-set ceiling. Other execution
profiles provide different isolation strengths; `no network` is specifically a
split-executor guarantee. Read
[Tenant, Project, User, Authority, And Execution Boundaries](runtime/tenant-project-user-and-execution-boundaries-README.md)
and [Isolated Execution](exec/README-iso-runtime.md).

## 7. Connect Accounts And External Operators In Both Directions

Two opposite delegation directions solve different problems:

```text
DELEGATED TO KDCUBE
user connects Gmail, Slack, or a custom OAuth/OIDC account
  -> trusted KDCube tools may use approved provider claims for that user

DELEGATED BY KDCUBE
user approves a script, CI job, or external agent client
  -> that delegate may use selected KDCube resources and operations
```

The first direction lets KDCube act through a user's external account. The
second lets external automation act inside KDCube on the user's behalf. Neither
is platform login.

An in-app agent is itself a delegated-by client. Each agent is its own grantee
(`kdcube-agent:<app>:<agent>`) and needs its own grant to a connected account,
scoped per account and per claim — reading and sending are separate claims.
Connecting an account does not authorize every agent to use it, and an agent does
not inherit the accounts the user connected; revoking either the connection or
the agent's grant stops the tool. See
[Agents Acting For The User](sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

Connection Hub owns the authority/provider registry, connection edges,
connected-account lifecycle, delegated credentials, consent, and runtime
projection. Apps may host login or consent presentation, while Connection Hub
keeps protocol and policy ownership.

Provider credentials stay in user-scoped secrets. Trusted tools resolve the
current user's connected account server-side. If access is missing or stale,
the SDK returns a structured connect, claim-upgrade, account-selection, or
reconnect action.

Consent is demand-driven: the attempted tool asks for exactly its declared
claims. The user may approve them or disable the tools that need them. A menu
render does not request consent.

Telegram and other channels begin as external identities. A verified channel
actor becomes platform-authorized only through an explicit connection edge and
authority projection.

Read [Connection Hub](sdk/solutions/connections/connection-hub-solution-README.md).

## 8. Expose Your Domain Through Named Services

Named services prevent an agent catalog from growing one bespoke tool shape per
domain. Every realm uses a fixed grammar:

```text
about | capabilities | schema | list | search | get
action | upsert | host_file | delete
```

The realm supplies its own nouns, refs, filters, actions, guards, and human
presentation. One self-description serves two readers:

```text
agent reads schema/about -> works the realm
user reads service card  -> understands, narrows, and consents
```

A complete provider declares nouns, search questions, bounded use cases,
guards, presentation, registration, and tests. Provider-backed realms declare
connected-account requirements; demand-driven consent and coverage UI follow
from that declaration rather than realm-specific consent code.

External clients can reach configured realms through the
`kdcube-services@1-0` named-services MCP gateway. Delegated namespace grants
guard the MCP boundary; provider claims or realm rules guard the operation
inside. Those are two different authorization layers.

Generated code in isolated execution can call named services through the
supervisor. Client policy travels in the portable runtime context, and a Data
Bus relay reaches the provider app under the original request identity when no
in-process caller exists.

Read [Namespace Services](sdk/namespace-services/README.md) and
[Named Services From Isolated Runtime](sdk/solutions/kdcube-services/named-services-from-isolated-runtime-README.md).

## 9. Compose Chat, Scene, Canvas, Memories, And Websites

Apps may expose UI, but they do not have to.

- **Chat** is a ready assistant surface or an embeddable component.
- **Scene** composes independently served widgets and routes declared surface
  commands and context between them.
- **Canvas/Pinboard** stores visual working context and provider-owned object
  refs without taking ownership of their meaning.
- **User memories** provide durable, user-visible, cross-conversation memory
  alongside conversation history.
- **Application-hosted websites** serve a built app main view as a complete
  multipage or SPA website.

Site declarations live under the app's `ui.main_view.site` config in
`bundles.yaml`. They compile into a validated, versioned site catalog. Each proc
routes from an immutable in-memory snapshot; a site request does not scan YAML
or query Redis. Aliases work under `/sites/{alias}`; clean dedicated-host paths
use a CDN/origin rewrite that preserves the viewer host.

Application-hosted websites and `@public_content` are separate. A website
serves an app's complete built main-view tree. Public content serves indexed
records, catalogs, metadata, and sitemaps.

Read [Component Recipes](recipes/components/README.md) and
[Application-Hosted Sites](sdk/solutions/sites/application-sites-README.md).
The browser shell itself is documented in
[Control Plane Web App](arch/control-plane-web-app-README.md).

## 10. Keep Conversation Work Ordered And Recoverable

Conversation work uses an ordered lane identified by:

```text
tenant + project + user + conversation + agent
```

Reactive ingress atomically stores accepted events in the lane and admits one
bodyless wake to the processor queue. Redis lane sequence defines event order;
the queue schedules work but does not define order.

One scheduled app turn opens the handler. Events arriving afterward can fold
into that live turn. Handler ownership, consumer heartbeat, an event-source
lease, processed cursor, and close gate prevent stale turns from consuming new
events or becoming conversation head.

The Conversation Event Bus is for conversation/agent context. The Data Bus is
for app-owned domain mutations independent of chat. Sharing a browser transport
does not merge them.

SSE relay state is also scoped by tenant, project, and session. A `stream_id`
selects one concrete connection only after the correct session clients have
been selected.

Read [External Events Journey](sdk/events/external-events-journey-and-handling-README.md)
and [Conversation Event Bus And Data Bus](service/comm/conversation-event-bus-and-data-bus-README.md).

## 11. Track And Enforce Economics

KDCube can govern model, embedding, web-search, and custom metered calls through
one lifecycle:

```text
verify -> reserve -> run -> settle actual usage
```

Accounting can attribute work to user, app, conversation, turn, flow, provider,
and model. A turn has no fixed price; its cost is the sum of the spendings inside
it. Helper-agent spend can roll up to the delegating work.

Observability alone reports what happened. Economics enforcement decides
whether work may begin and how actual usage is settled. Custom paid services
join only when they use the accounting/economics contracts.

Read [Economics Enforcement](economics/economic-enforcement-engine-README.md)
and [Accounting](accounting/accounting-README.md).

## 12. Deploy, Configure, Store, And Update Deliberately

App source can come from Git or a local path. Local and cloud deployments use
the same app contract.

```text
descriptors -> kdcube init -> kdcube start
app source/config change -> bundle config apply / bundle reload
platform source/image change -> kdcube refresh
```

Configuration ownership is split intentionally:

| Store or descriptor | Owns |
| --- | --- |
| `assembly.yaml` | Tenant/project runtime selection, platform/infra settings, active auth selection. |
| `bundles.yaml` | App registry, app source, non-secret config, surfaces, sites, provider declarations. |
| `secrets.yaml` / `bundles.secrets.yaml` | Platform and app secret references/values for the deployment lifecycle. |
| PostgreSQL | Durable metadata and product records owned by their subsystems. |
| Redis | Queues, lanes, relay, coordination, and selected fail-closed grant/session records. |
| App filesystem storage | App-owned mutable files; local/mounted locally, normally shared EFS in cloud. |
| Artifact storage | Separate artifact API backed by configured local or object storage. |
| User-scoped secrets | Connected external-provider credentials. |
| User settings | Durable user/app choices, including conversation-scoped agent selections. |

`bundle_storage_root()` is filesystem storage, not S3. Provider tokens do not
belong in account metadata files. Delegated bearer tokens are handles; managed
guards load server-side grant/session records for authority.

One running KDCube deployment is bound to one effective `tenant/project`. It may
serve many users and apps. PostgreSQL, Redis, object storage, and filesystems may
be dedicated or shared with other deployments through schemas, namespaces, and
prefixes; inside one deployment, users can share workers and infrastructure, so
request identity and scoped SDK contracts remain mandatory.

Read [Quick Start](quick-start-README.md),
[Configuration And Secrets](configuration/bundle-runtime-configuration-and-secrets-README.md),
and [Connection Hub Storage](sdk/solutions/connections/storage-model/storage-model-README.md).

## 13. A Practical Builder Path

```text
1. keep the existing product behavior
2. choose the first runtime boundary that saves work
3. wrap that boundary with a thin KDCube app adapter
4. declare only the surfaces the app actually provides
5. configure source, non-secret props, and secret references
6. run the app and test the real transport/storage/identity path
7. add another KDCube service only when it earns its place
```

Start with:

1. [Quick Start](quick-start-README.md)
2. [Architecture Of What You Build](arch/architecture-of-what-you-build-README.md)
3. [How To Write A KDCube App](sdk/bundle/build/how-to-write-bundle-README.md)
4. [How To Test An App](sdk/bundle/build/how-to-test-bundle-README.md)

## 14. Honest Boundaries

KDCube provides contracts and runtime enforcement, not magic.

- Existing agents need a small adapter at the product/runtime seam; integration
  is not a zero-code wrapper.
- Custom app/tool/provider code remains trusted code and owns authorization when
  it bypasses scoped SDK services.
- Isolation strength depends on the configured runtime profile.
- A connector app's provider claim ceiling does not replace provider-side
  scopes or workspace approval.
- Adding a named-service namespace to config does not widen an already issued
  delegated credential; the external client reconnects to consent again.
- Website host declarations do not provision DNS, certificates, or a CDN.
- Hot app reload does not replace a platform image rebuild when platform code
  changes.
- Evidence supports review; deployment retention/integrity policy and reviewers
  determine compliance.

The short framing is:

```text
Bring the agent. Keep the product.
Use KDCube for the parts that should become routine.
```
