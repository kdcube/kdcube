---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
title: "Architecture Of What You Build"
summary: "Builder architecture for KDCube apps: optional surface families, provider and consumer directions, existing or ready agents, scenes, named-service realms, events, websites, storage, authority, and package contracts."
status: current
tags: ["arch", "architecture", "apps", "surfaces", "provider", "consumer", "named-services", "scene"]
updated_at: 2026-07-18
keywords: ["KDCube app architecture", "as provider", "as consumer", "app surfaces", "named service", "scene", "default chat"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-we-built-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/scene-README.md
---
# Architecture Of What You Build

KDCube does not require every app to become a chat app, an agent, or a large
platform package. You choose the product surface you need and add other
capabilities independently.

An app may wrap work you already have, use KDCube building blocks, or combine
both. Existing LangGraph, CrewAI, Claude Agent SDK, custom Python, REST, MCP,
and frontend code can remain responsible for its domain logic.

## The App Is A Boundary

An app is a descriptor-addressed package that owns a coherent part of the
product:

```text
app
  runtime composition
  provided and consumed surfaces
  configuration and secret references
  storage ownership
  interface declarations
  UI, agents, tools, providers, or jobs when present
  tests, release metadata, and implementation journal
```

Current source and descriptors still use technical names such as `bundle_id`
and `bundles.yaml`. Those are implementation identifiers; this documentation
uses **app** for the builder-facing concept.

An app backend is trusted deployment code selected by the operator. It is not
the same security class as generated code sent to an isolated executor. See
[Security And Trust Model](security-and-trust-model-README.md) before choosing
which code belongs in an app or behind an isolation boundary.

## Two Directions, Chosen Independently

The central design question is not only "what does my app expose?" It is also
"what is my app allowed and configured to consume?"

```text
                         your app
                            |
          +-----------------+------------------+
          |                                    |
          v                                    v
surfaces.as_provider                  surfaces.as_consumer
what others can use                   what this app can use
          |                                    |
API / operations                      Python tool connections
widgets / main view                    MCP service connections
MCP endpoints                         per-agent tool allow-lists
default chat intent                    named-service namespaces
visibility / managed auth              UI object resolvers
```

An app can be:

- a provider only, such as a backend API or named-service realm;
- a consumer only, such as an agent using existing services;
- both, such as a workspace that serves chat and widgets while consuming mail,
  Slack, memories, task services, Python tools, and MCP servers.

Provider policy and consumer wiring are separate. Allowing an agent to call an
MCP server does not publish that server as the app's own MCP endpoint. Exposing
an API does not automatically allow the app's agents to call it.

## Choose Only The Surface Families You Need

| Surface family | Use it when the app needs to... |
| --- | --- |
| API / operations | Serve synchronous product operations, callbacks, or webhooks. |
| UI widget / main view | Add a focused interface or own the app's primary view. |
| Default chat | Serve the ready SDK chat under the reserved `chat` alias. |
| Agent | Host one or several existing agent loops or KDCube ReAct agents. |
| MCP | Provide or consume model-callable tools/resources. |
| Named service | Expose a domain as typed objects and bounded operations through a fixed grammar. |
| Data Bus | Process durable app-domain messages independent of chat. |
| Conversation events | Send ordered context into a current or future app/agent turn. |
| Job / cron | Run background or scheduled work. |
| Integration | Connect channels, provider accounts, or external callbacks. |
| Scene / Canvas | Compose browser surfaces or preserve visual working context. |
| Website | Serve the app's complete built main-view file tree. |

No row is mandatory for every app. Declare a surface only when the runtime and
package actually provide it.

## Existing Agent Or Ready ReAct

You can keep an existing agent as the decision runtime and adapt its inputs,
streamed events, outputs, files, and accounting to KDCube. The
`ported-langgraph-agents@2026-07-13` reference app demonstrates this approach.

In a horizontally scaled app, the agent runner cannot keep a stateful graph in
one worker and assume the next turn returns there. The reference app rebuilds
the graph inside every turn from configuration and shared/checkpointed state;
the graph instance exists for that turn only. Long-lived app instances retain
connections, not graph or conversation state.

Or use KDCube ReAct when you want a ready harness with a logical workspace,
tools, skills, files, web search, named services, conversation events,
subagents, and isolated code execution.

For either choice, consumer configuration can give each agent a separate
inventory:

```text
surfaces.as_consumer.agents.main
  tools: Python + MCP + named-service connections
  skills: app-granted skill catalog

surfaces.as_consumer.agents.reviewer
  tools: a narrower or different catalog
  skills: reviewer-specific skills
```

The administrator grants the inventory. The user may narrow supported models,
tools, skills, service operations, and helper agents for one conversation. The
chat picker keeps a local draft; only **Save changes** persists it.

## Compose Browser Surfaces

A Scene is an optional host page for widgets. It owns layout, frame mounting,
shared configuration, event fan-out, and declared surface-command delivery.
The mounted apps still own their product behavior and authorization.

```text
scene
  +-- chat widget
  +-- canvas / pinboard
  +-- memories widget
  +-- connection or domain widget
  `-- app-specific components

surface command
  one component asks another declared surface to open or focus

context drag
  a typed object ref moves between surfaces without losing its kind
```

Scene component entries live on the consumer side because the scene consumes
other UI surfaces. Configuration merges descriptor entries over code defaults
by alias. Cross-app mounting is supported.

Apps that do not need a Scene can embed the chat or another widget directly in
an existing website. An app may also expose its built main view as a complete
website through the application-site catalog.

## Model A Domain As A Named Service

Named services avoid one bespoke tool vocabulary per domain. Providers expose
self-describing realms through a stable grammar:

```text
provider.about        provider.capabilities
object.schema         object.list          object.search
object.get            object.action        object.upsert
object.host_file      object.delete
```

The provider advertises only operations it actually serves. An agent reads
`object.schema` before constructing provider-encoded action payloads and
verifies the result of state-changing actions.

A complete realm has seven authoring declarations:

```text
1. nouns          typed object kinds and self-contained refs
2. questions      documented search/filter vocabulary
3. use cases      named, bounded operations and actions
4. guards         internal rules or connected-account requirements
5. presentation   human purpose, labels, object descriptions, claim labels
6. registration   provider discovery and metadata
7. tests          agent and human projections
```

One self-description serves two readers:

```text
agent -> reads about/schema -> works the realm
user  -> reads service card -> understands, narrows, and consents
```

Presentation describes; it does not authorize. Internal realms enforce their
own access rules. Provider-backed realms declare connected-account claims, and
demand-driven consent is raised only when an attempted operation needs claims
the current user does not hold.

## Connect Apps And External Operators

Apps can consume each other through synchronous operations, named services,
MCP, conversation events, Data Bus messages, and UI surfaces. Use the mechanism
that matches the ownership of the work:

```text
synchronous app result       call_bundle_operation / API
conversation context         Conversation Event Bus
app-domain mutation          Data Bus
agent/domain object access   named service or MCP
browser composition          Scene and surface commands
```

External agents and automation can call protected MCP or REST resources with
delegated KDCube credentials. Connected external accounts point the other way:
they let trusted KDCube tools call services such as mail or Slack for the
current user. Do not merge these two delegation directions.

A hosted or in-app agent is a delegated-by client in its own right: its account
access is a per-agent grant, scoped per connected account and per claim, and
independent of which accounts the user connected. See
[Agents Acting For The User](../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

## Define Execution And Data Boundaries

The app decides which code is trusted and which work requires isolation.

```text
trusted app/tool code
  runs under carried request identity and explicit service contracts

generated or untrusted code
  receives a sparse materialized workspace
  runs in a per-agent execution profile, under the operator ceiling
  reaches approved tools through the trusted supervisor boundary
```

Model-proposed refs and paths are untrusted locators. Trusted runtime resolvers
bind tenant/project/user/authority before returning bytes. This requester versus
resolver distinction matters more than asking the model to remember a tenant
filter.

Placement is per tool — a tool declares whether it runs trusted, in a
subprocess, or in the isolated executor — and privileged side-effects cross the
boundary by reference: the trusted side holds the credentials, performs the
operation, and returns a bounded result.

Storage is also explicit. App filesystem storage, artifact storage, user
settings, conversation records, connected-account secrets, and Redis runtime
state have separate owners; an app should use the owning SDK contract rather
than opening deployment files or stores directly.

## Keep The Package Synchronized

A production app is one contract expressed in several files. Runtime
decorators, interface declarations, descriptors, configuration templates,
storage documentation, tests, stable docs, release metadata, and journal
entries must describe the same surfaces.

`AGENTS.md` is an operational implementation contract for coding agents, not a
second user README. Secret templates contain placeholders only. Journals record
decisions but do not replace stable documentation.

## A Practical Builder Route

```text
1. choose one useful surface
2. wrap existing code or select an SDK building block
3. declare provider surfaces and consumer dependencies separately
4. bind identity, storage, visibility, and execution policy
5. run locally against the real transport
6. add only the next surface the product needs
7. release and update from the app source/ref
```

Start with [How To Write A KDCube App](../sdk/bundle/build/how-to-write-bundle-README.md),
then follow the recipe for the selected surface. Reference apps show concrete
patterns; they do not replace the canonical package contract.

## Read Next

- [What You Can Do With KDCube](../what-you-can-do-with-kdcube-README.md)
- [How To Write A KDCube App](../sdk/bundle/build/how-to-write-bundle-README.md)
- [Bundles Descriptor](../configuration/bundles-descriptor-README.md)
- [Named-Service Providers](../sdk/namespace-services/providers-README.md)
- [Scene Recipe](../recipes/components/scene-README.md)
- [Chat With A ReAct Agent](../recipes/components/chat-with-react-agent-README.md)
- [Application-Hosted Sites](../sdk/solutions/sites/application-sites-README.md)
