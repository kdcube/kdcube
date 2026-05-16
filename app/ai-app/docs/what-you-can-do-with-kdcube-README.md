---
id: ks:docs/what-you-can-do-with-kdcube-README.md
title: "What You Can Do With KDCube"
summary: "Dense product and builder overview of KDCube: what it is, who it is for, why bundles matter, what surfaces and runtimes it supports, and how engineers or coding agents turn code into runnable AI products."
tags: ["docs", "product", "overview", "sdk", "platform", "bundle", "agent"]
keywords: ["what is kdcube", "what can kdcube do", "ai product platform", "bundle runtime", "kdcube bundle", "build ai app", "wrap existing app", "coding agent build bundle", "claude code build bundle", "local to cloud workflow", "mcp endpoint", "react agent", "user memory", "streaming widgets", "artifact provenance"]
updated_at: 2026-05-16
see_also:
  - ks:docs/quick-start-README.md
  - ks:docs/README.md
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/tools/sdk-tools-README.md
  - ks:docs/sdk/skills/skills-README.md
  - ks:docs/sdk/streaming/llm-streaming-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
---
# What You Can Do With KDCube

KDCube is a platform and SDK for building, integrating, and operating AI
products that are larger than one prompt, one chat screen, or one model call.

Use KDCube when an AI system needs product structure:

- runtime configuration and secrets
- user and environment isolation
- chat plus APIs plus widgets plus MCP plus scheduled work
- tools, skills, files, artifacts, citations, memory, and provenance
- a local-to-cloud delivery path that coding agents and engineers can follow

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

- product teams building AI assistants or workflow apps
- platform teams standardizing how AI apps are deployed
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
| AI copilot with files and reports | chat/on-message, ReAct tools, rendering tools, hosted artifacts |
| internal operations tool | authenticated APIs, admin widgets, storage, role policy |
| public integration endpoint | public `@api`, webhook auth, idempotent processing |
| Telegram or email assistant | SDK integration, public webhook/OAuth callback, user registry, delivery |
| browser/iframe app | bundle widget or full UI, operation APIs, shared SDK UI panels |
| docs or data assistant | knowledge source, MCP endpoint, search/fetch/read tools |
| scheduled automation | `@cron`, `@on_job`, jobs stream, task/memo solution |
| existing app wrapper | thin bundle adapter around existing backend/UI/business logic |

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

## 5. Runtime Surfaces

KDCube bundles can expose:

- **chat/on-message**: assistant or workflow response to user messages
- **operations APIs**: authenticated backend actions for UI/admin tools
- **public APIs**: webhooks and external callbacks with bundle-owned auth
- **widgets**: embedded bundle UI, Telegram Mini App, or KDCube widget
- **main UI**: full browser app surface
- **MCP endpoints**: bundle-served tool/resource interface for external agents
- **cron**: scheduled scans or recurring jobs
- **jobs**: async work submitted to the platform job stream and handled by
  `@on_job`

The same product may use several surfaces, for example:

```text
Telegram message
  -> public webhook
  -> bundle workflow
  -> ReAct agent/tools
  -> streamed progress
  -> final Telegram response and hosted files

KDCube widget
  -> authenticated operation APIs
  -> same bundle storage and user state
```

## 6. Agent Runtime And Tools

KDCube supports agents without reducing the platform to a tool-calling wrapper.

The ReAct runtime is a custom channeled protocol, not a vendor tool-calling
protocol. That is why it can carry rich events and artifacts:

- visible `thinking`/progress stream
- structured `ReactDecisionOutV2` actions
- streamed code channel for exec
- final summary channel for compact continuity
- tool-call and tool-result timeline artifacts
- protocol violation feedback visible to the next decision round

Tools are SDK/bundle capabilities, not just LLM function calls:

- `web_tools` search/fetch with source-pool provenance
- `rendering_tools` for PDF/DOCX/PPTX/PNG/HTML outputs
- `exec_tools` for isolated generated-code execution
- `ctx_tools` and `io_tools` for context, attachments, hosted files, and
  runtime reads
- `react.*` tools for workspace/artifact recovery, reading, patching, planning,
  and writing
- bundle-local tools through `tools_descriptor.py`

Skills are also first-class. They provide workflow instructions and domain
guidance that the agent can load before using tools.

## 7. Streaming And Artifacts

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

- logical paths such as `fi:`, `tc:`, `ar:`, `so:`, `ks:`
- tool call/result records
- visibility (`internal` vs user-visible/external)
- source-pool citation tokens and replacement
- hosted-file metadata

## 8. Memory And Continuity

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

## 9. How Configuration Is Organized

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

## 10. Turning Code Into A Bundle

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
- put bundle-visible tools in `tools_descriptor.py`
- put bundle-visible skills in `skills_descriptor.py`
- define deployment config in `config/bundles.template.yaml`
- define deployment secrets in `config/bundles.secrets.template.yaml`
- document public routes, widgets, MCP, jobs, and config in `interface/README.md`
- test with the shared bundle docs/test path before release

Start with the Tier 1 bundle docs:

1. [sdk/bundle/build/how-to-navigate-kdcube-docs-README.md](sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
2. [sdk/bundle/build/how-to-test-bundle-README.md](sdk/bundle/build/how-to-test-bundle-README.md)
3. [sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
4. [sdk/bundle/build/how-to-write-bundle-README.md](sdk/bundle/build/how-to-write-bundle-README.md)
5. [configuration/bundle-runtime-configuration-and-secrets-README.md](configuration/bundle-runtime-configuration-and-secrets-README.md)
6. [sdk/bundle/build/how-to-configure-and-run-bundle-README.md](sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

## 11. Coding-Agent Instruction Seed

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
- tools_descriptor.py exposes agent tools
- skills_descriptor.py exposes agent skills
- config templates define bundle props/secrets
- interface docs define routes/widgets/MCP/jobs/config
- tests verify imports, descriptors, and runtime behavior

Run locally with kdcube init/start/reload using the active descriptor set.
Do not invent runtime paths or config scopes; use the docs and reference bundle.
```

## 12. KDCube Copilot And Docs MCP

The built-in `kdcube.copilot@2026-04-03-19-05` bundle is intended to help users
ask questions about KDCube itself. It can be configured with a documentation
knowledge source and exposes a bundle-served docs MCP endpoint:

```python
@mcp(alias="kdcube-doc", route="public")
def kdcube_doc_mcp(self, **kwargs):
    return self._build_doc_reader_mcp_app(name_suffix="kdcube-doc")
```

For a demo assistant, the expected answer range is:

- surface explanation: what KDCube is and who it is for
- product explanation: why bundles and tenant/project environments matter
- builder explanation: how to create or wrap a bundle
- operator explanation: how descriptors, secrets, storage, auth, and deployment
  fit together
- implementation explanation: ReAct, tools, skills, streaming, artifacts,
  memory, MCP, jobs, widgets
- limitation explanation: what must still be configured explicitly and what
  cannot be inferred

This page and the quick start are intentionally dense because they are often
among the first documents a doc-reader agent retrieves.

## 13. Implementation Limits And Honest Boundaries

KDCube gives a runtime contract, not magic:

- descriptor sets must still be correct for the target environment
- public callbacks need reachable HTTPS origins
- local platform-code changes require image rebuilds
- bundle secrets must be supplied through secret channels
- widgets must use the KDCube frame/runtime origin for API calls
- agent quality depends on visible docs, loaded skills, available tools, and
  configured model/runtime budgets
- durable memory writes require explicit policy and result verification
- MCP/docs knowledge sources must point at the intended repo/ref/root

Good KDCube agents should explain these limits instead of hiding them.

## 14. Short Practical Framing

If you need one compact explanation:

```text
KDCube is a runtime for AI product bundles.
One tenant/project is an isolated environment.
One bundle is one runnable product unit.
A bundle can expose chat, APIs, widgets, MCP, cron, jobs, tools, skills, and UI.
Descriptors wire the environment; entrypoint decorators expose the bundle.
The same contract supports local development, demo, staging, and cloud deployment.
```
