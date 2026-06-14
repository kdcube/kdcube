---
id: repo:kdcube-ai-app/app/ai-app/docs/what-you-can-do-with-kdcube-README.md
title: "What You Can Do With KDCube"
summary: "Dense product and builder overview of KDCube: what it is, who it is for, why bundles matter, what surfaces, namespace services, and runtimes it supports, and how engineers or coding agents turn code into governed AI products."
tags: ["docs", "product", "overview", "sdk", "platform", "bundle", "agent", "react", "exec"]
keywords: ["what is kdcube", "what can kdcube do", "ai product platform", "bundle runtime", "bundle reload", "kdcube bundle", "build ai app", "wrap existing app", "coding agent build bundle", "claude code build bundle", "local to cloud workflow", "mcp endpoint", "react agent", "flagship react runtime", "announce", "steer followup", "user memory", "namespace services", "named services", "iso runtime", "distributed exec", "streaming widgets", "artifact provenance"]
updated_at: 2026-06-13
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/quick-start-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-state-machine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/system-instruction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-turn-workspace-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/why/why-not-simply-tool-calling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/sdk-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/skills-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/llm-streaming-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/distributed-exec-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
---
# What You Can Do With KDCube

KDCube is a platform and SDK for building, integrating, and operating AI
products that are larger than one prompt, one chat screen, or one model call.

Use KDCube when an AI system needs product structure:

- runtime configuration and secrets
- user and environment isolation
- chat plus APIs plus widgets plus MCP plus scheduled work
- per-agent tools and skills, files, artifacts, citations, memory, namespace
  services, and provenance
- a local-to-cloud delivery path that coding agents and engineers can follow

For the client integration map — iframe app UI, embedded control plane, direct
host browser clients, host-server clients, and backend-only KDCube apps — read
[How To Integrate With KDCube Apps](how-to-integrate-with-kdcube-apps-README.md).

## 1. KDCube In One Paragraph

KDCube hosts **bundles** inside isolated `tenant/project` environments. A bundle
is an application unit: it can contain backend logic, an agent workflow, tools,
skills, APIs, widgets, a full UI, MCP endpoints, cron jobs, storage, secrets,
and product-specific policy. The platform supplies the runtime envelope; the
bundle supplies the product behavior.

In short:

```text
tenant/project = isolated environment
bundle = runnable product/application unit
surface = how users or systems enter the bundle
runtime = how the platform executes and observes it
```

## 2. Who KDCube Is For

KDCube is useful for:

- product teams building governed AI assistants, workbenches, or workflow apps
- platform teams standardizing how multiple AI apps are deployed, isolated,
  accounted, and operated
- engineers wrapping existing apps, scripts, UIs, or webhooks into a managed
  runtime
- teams that need local development and cloud deployment to use the same bundle
  contract
- coding agents that need a clear target shape for building runnable AI
  products instead of loose prototypes

It is not only a chatbot template. Chat is one surface of a bundle.

## 3. What You Can Build

Common bundle shapes:

| Product shape | KDCube surfaces |
| --- | --- |
| internal AI workbench with files, tasks, and reports | chat/on-message, ReAct tools, canvas, widgets, rendering tools, hosted artifacts |
| internal operations tool | authenticated APIs, admin widgets, Data Bus handlers, storage, role policy |
| public integration endpoint | public `@api`, webhook auth, idempotent processing |
| Telegram or email assistant | SDK integration, public webhook/OAuth callback, user registry, delivery |
| browser/iframe app | bundle widget or full UI, operation APIs, shared SDK UI panels |
| docs or data assistant | knowledge source, MCP endpoint, search/fetch/read tools |
| scheduled automation | `@cron`, `@on_job`, jobs stream, task/memo solution |
| existing app wrapper | thin bundle adapter around existing backend/UI/business logic |
| namespace provider | ontological namespace ownership, provider-owned refs, schemas, search/get/upsert, block rendering, canvas actions, file hosting |

One bundle can expose several of these at once.

## 4. Why Bundles Matter

A bundle gives one product a stable runtime identity:

- one id in `bundles.yaml`
- one `entrypoint.py` that exposes decorated surfaces
- one bundle config namespace
- one bundle secret namespace
- one storage/cache namespace
- one place to attach tools, skills, widgets, MCP, scheduled jobs, and product
  policy

This lets an AI product be deployed, configured, reloaded, tested, and reasoned
about as a unit.

Bundles can also be hot-reloaded during development and operations. `kdcube
bundle reload <bundle_id>` reapplies the descriptor-authoritative bundle registry,
evicts process-local bundle caches, refreshes bundle code/config for subsequent
requests, and broadcasts the change to running components. Platform image
changes still require the normal image rebuild/restart path.

## 5. Runtime Surfaces

KDCube bundles can expose:

- **chat/on-message**: assistant or workflow response to user messages
- **operations APIs**: authenticated backend actions for UI/admin tools
- **public APIs**: webhooks and external callbacks with bundle-owned auth
- **widgets**: embedded bundle UI, Telegram Mini App, or KDCube widget
- **main UI**: full browser app surface
- **MCP endpoints**: bundle-served tool/resource interface for external agents
- **Data Bus handlers**: durable app-domain messages, mutations, patches,
  annotations, and async state changes handled by `@data_bus_handler`
- **namespace-service provider**: ontological namespace ownership for refs,
  objects, schemas, files, actions, renderers, and resolvers that other KDCube
  surfaces can consume generically
- **cron**: scheduled scans or recurring jobs
- **jobs**: async work submitted to the platform job stream and handled by
  `@on_job`

The same product may use several surfaces, for example:

```text
Telegram message
  -> public webhook
  -> bundle entrypoint
  -> ReAct agent/tools
  -> streamed progress
  -> final Telegram response and hosted files

KDCube widget
  -> authenticated operation APIs
  -> Data Bus message for durable state mutation
  -> same bundle storage and user state

Canvas pin or external ref
  -> namespace-service provider resolves task:/mem:/domain: refs
  -> generic chat/canvas/ReAct surfaces render and act without hardcoded
     provider-specific logic
```

## 6. Flagship ReAct Runtime Model

KDCube's flagship agent runtime is ReAct: a timeline-driven runtime model for
agents that must work with tools, files, events, memory, external namespace
objects, and long-running work. It is the reference runtime used by the
`versatile@2026-03-31-13-36` bundle.

ReAct is not a provider-native tool-calling wrapper. The runtime owns the
protocol shape, timeline, workspace, recovery paths, budgets, and event-source
policies. The model is the decision module inside that runtime.

The ReAct runtime is a custom channeled protocol, not a vendor tool-calling
protocol. That is why it can carry rich events and artifacts:

- visible `thinking`/progress stream
- structured `ReactDecisionOutV2` actions
- streamed code channel for exec
- final summary channel for compact continuity
- tool-call and tool-result timeline artifacts
- live `followup` and `steer` events accepted through the shared timeline event
  bus while a turn is running
- an uncached ANNOUNCE block that re-renders operational state each decision
  round: budget, temporal context, open plans, live turn events, workspace
  state, memory hotsets, and other current signals
- protocol violation feedback visible to the next decision round

`followup` lets a running turn accept additional same-turn user input and can
grant bounded extra iteration credit. `steer` is a control event: it is recorded
on the current turn, interrupts active generation or cancellable tool phases
when possible, and gives ReAct a short finalize/reorient window.

ANNOUNCE is not decoration and not a cached transcript. It is the runtime's way
to put between-turn or between-round state directly in the model's attention
without rewriting old timeline blocks.

Read the ReAct model in this order:

1. [ReAct Runtime Flow](sdk/agents/react/flow-README.md)
2. [ReAct State Machine](sdk/agents/react/react-state-machine-README.md)
3. [ReAct Context Layout](sdk/agents/react/context-layout.md)
4. [ReAct System Instruction](sdk/agents/react/system-instruction-README.md)
5. [ReAct Tools](sdk/agents/react/react-tools-README.md)
6. [ReAct Turn Workspace](sdk/agents/react/react-turn-workspace-README.md)
7. [ReAct Event Sources](sdk/agents/react/event-source/event-source-README.md)
8. [Why Not Simply Tool Calling](sdk/agents/react/why/why-not-simply-tool-calling-README.md)

## 7. Tools, Skills, And Namespace Services

Tools are SDK/bundle capabilities, not just LLM function calls:

- `web_tools` search/fetch with source-pool provenance
- `rendering_tools` for PDF/DOCX/PPTX/PNG/HTML outputs
- `exec_tools` for isolated generated-code execution
- `ctx_tools` and `io_tools` for context, attachments, hosted files, and
  runtime reads
- `react.*` tools for workspace/artifact recovery, reading, patching, planning,
  and writing
- MCP tools exposed by configured MCP servers or bundle-served MCP endpoints
- named-service tools that connect an agent to configured external namespace
  providers such as task, memory, canvas, or domain-specific systems
- bundle-local Python tools wired per agent through
  `surfaces.as_consumer.agents.<agent>.tools`

Skills are also first-class. Platform/shared skills and custom bundle skills
provide workflow instructions and domain guidance that the agent can load before
using tools. New bundles wire skills per agent through
`surfaces.as_consumer.agents.<agent>.skills`.

Namespace services are the bridge between app-owned domain systems and common
runtime surfaces. A provider is the ontological owner of a namespace such as
`task:`, `mem:`, or a domain-specific prefix: it defines what the refs mean,
which object kinds exist, which schemas and mutations are valid, how files are
materialized, how blocks render, how URIs resolve, and which UI/canvas actions
are meaningful. A consumer connects chat, canvas, ReAct, widgets, or MCP to
that namespace by configuration, not by embedding provider-specific logic into
shared components.

See:

- [Namespace Services](sdk/namespace-services/README.md)
- [Namespace Service Integration](sdk/namespace-services/integration-README.md)
- [Named Services Tools](sdk/tools/named-services-tools-README.md)
- [Tool Subsystem](sdk/tools/tool-subsystem-README.md)
- [Custom Skills](sdk/skills/custom-skills-README.md)

## 8. Execution Runtime

KDCube includes an execution subsystem for generated code, heavy tools, and
tool runtimes that should not run inside the main server process.

Runtime modes include:

- in-process execution for safe lightweight tools
- isolated local subprocess execution for tools that need crash containment
- Docker ISO runtime with a trusted supervisor and restricted executor
- split Docker mode, where the supervisor and executor run in sibling
  containers with different mounts and privileges
- distributed/Fargate exec, where workspaces and bundle snapshots are
  transported to a remote ECS task

The ISO runtime is not tied to ReAct. It is reusable platform execution
infrastructure: a bundle can use it directly, expose it as a tool to another
agent, or serve a tool surface backed by it. It also acts as an execution
boundary:

- generated Python can run without network access or secret material
- approved tools can still be proxied through the supervisor
- artifacts, logs, executed programs, and tool outputs are merged back into the
  same KDCube artifact/timeline model
- bundle code roots, bundle readonly data, descriptor payloads, and execution
  workspaces are kept as separate surfaces

This lets bundles use generated-code execution, rendering tools, web/search
tools, and custom isolated helpers while preserving an auditable result
contract.

## 9. Streaming And Artifacts

KDCube is designed for observable long-running work.

The runtime can stream:

- model progress
- tool progress
- code snippets and execution contracts
- search queries, search results, and fetch results
- generated canvas artifacts
- final answers
- file metadata and hosted downloadable outputs

This is why a user can see a report, spreadsheet, search widget, code contract,
or generated document appear as work progresses, not only after the final model
message.

Artifacts keep provenance:

- logical paths such as `fi:`, `tc:`, `ar:`, `so:`, and repository refs such as `repo:kdcube-ai-app/app/ai-app/docs/...`
- tool call/result records
- visibility (`internal` vs user-visible/external)
- source-pool citation tokens and replacement
- hosted-file metadata

## 10. Memory And Continuity

KDCube has several memory layers with different owners and lifetimes:

| Layer | Purpose |
| --- | --- |
| timeline | current and recent turn evidence, tool calls, artifacts, messages |
| working summaries | compact continuity after pruning/compaction |
| internal conversation notes | agent-authored anchors inside conversation memory |
| durable user memory | user-visible, user-owned memory records with widget management, search, snapshots, and reconciliation |

Durable user memory is a standalone subsystem. ReAct can use it by:

- reading a top-N hotset in ANNOUNCE when enabled
- searching/reading memory when memory tools are enabled
- proposing or writing memory only when bundle policy allows it

User memory does not replace conversation memory. It extends the agent with
curated, cross-conversation user facts/preferences/state.

## 11. How Configuration Is Organized

Use this split:

| Scope | Examples |
| --- | --- |
| `assembly.yaml` | tenant/project, auth, ports, platform ref, storage, infra, ReAct runtime limits |
| `secrets.yaml` | platform-level service secrets |
| `gateway.yaml` | gateway capacity, throttling, process limits |
| `bundles.yaml` | enabled bundles, bundle props, source refs, non-secret config |
| `bundles.secrets.yaml` | bundle-specific secrets |
| user state | per-user credentials, preferences, runtime choices, memory records |

Do not put secrets in non-secret props. Do not put per-user runtime state into
deployment descriptors.

## 12. Turning Code Into A Bundle

When wrapping existing code, keep the boundary clean:

```text
existing product code
  -> reusable service/helper/module
  -> thin KDCube bundle adapter
  -> decorated surfaces in entrypoint.py
  -> descriptors for config/secrets/source wiring
  -> local runtime test
```

Practical rules:

- keep business logic reusable and testable outside the bundle class
- keep KDCube decorators and runtime calls close to `entrypoint.py`
- expose APIs/widgets/MCP/cron/jobs through platform decorators
- expose durable domain mutations through Data Bus handlers when clients submit
  app-owned state changes
- expose namespace services when the app owns a domain ontology that other
  KDCube surfaces should resolve, render, search, or mutate generically
- wire agent tools in `surfaces.as_consumer.agents.<agent>.tools`
- wire agent skills in `surfaces.as_consumer.agents.<agent>.skills`
- define deployment config in `config/bundles.template.yaml`
- define deployment secrets in `config/bundles.secrets.template.yaml`
- document public routes, widgets, MCP, jobs, Data Bus subjects,
  namespace-service refs/schemas, and config in `interface/README.md`
- test with the shared bundle docs/test path before release

Start with the Tier 1 bundle docs:

1. [sdk/bundle/build/how-to-navigate-kdcube-docs-README.md](sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
2. [sdk/bundle/build/how-to-test-bundle-README.md](sdk/bundle/build/how-to-test-bundle-README.md)
3. [sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
4. [sdk/bundle/build/how-to-write-bundle-README.md](sdk/bundle/build/how-to-write-bundle-README.md)
5. [configuration/bundle-runtime-configuration-and-secrets-README.md](configuration/bundle-runtime-configuration-and-secrets-README.md)
6. [sdk/bundle/build/how-to-configure-and-run-bundle-README.md](sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

## 13. Coding-Agent Instruction Seed

Use this when asking Claude Code, Codex, or another coding agent to build a
bundle:

```text
You are building a KDCube bundle, not a standalone script.

Read first:
- docs/what-you-can-do-with-kdcube-README.md
- docs/quick-start-README.md
- docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
- docs/sdk/bundle/build/how-to-test-bundle-README.md
- docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
- docs/sdk/bundle/build/how-to-write-bundle-README.md
- docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md

Then inspect the versatile reference bundle for the nearest pattern.

Build the bundle as a thin KDCube adapter around product logic:
- entrypoint.py exposes surfaces with decorators
- agents/main.py contains the primary ReAct workflow when the bundle uses ReAct
- config/bundles.template.yaml wires agent tools under surfaces.as_consumer.agents.<agent>.tools
- config/bundles.template.yaml wires agent skills under surfaces.as_consumer.agents.<agent>.skills
- config templates define bundle props/secrets
- interface docs define routes/widgets/MCP/jobs/Data Bus subjects/namespace services/config
- tests verify imports, descriptors, and runtime behavior

Run locally with kdcube init/start/bundle reload using the active descriptor set.
Do not invent runtime paths or config scopes; use the docs and reference bundle.
```

## 14. Implementation Limits And Honest Boundaries

KDCube gives a runtime contract, not magic:

- descriptor sets must still be correct for the target environment
- public callbacks need reachable HTTPS origins
- local platform-code changes require image rebuilds
- bundle reload updates bundle code/config, not platform code baked into images
- bundle secrets must be supplied through secret channels
- widgets must use the KDCube frame/runtime origin for API calls
- agent quality depends on visible docs, loaded skills, available tools, and
  configured model/runtime budgets
- durable memory writes require explicit policy and result verification
- MCP/docs knowledge sources must point at the intended repo/ref/root when a
  deployment exposes such a surface

## 15. Short Practical Framing

If you need one compact explanation:

```text
KDCube is a runtime for AI product bundles.
One tenant/project is an isolated environment.
One bundle is one runnable product unit.
A bundle can expose chat, APIs, widgets, MCP, cron, jobs, tools, skills, and UI.
A bundle can also own durable Data Bus handlers and act as a namespace-service
provider: the ontological owner of refs, schemas, objects, files, renderers,
and actions for a domain namespace.
Descriptors wire the environment; entrypoint decorators expose the bundle.
Bundle reload updates bundle code/config without rebuilding the platform image.
The same contract supports local development and cloud deployment.
```
