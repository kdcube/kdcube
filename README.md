# KDCube — Platform and SDK for AI applications you control

KDCube is a self-hosted platform, SDK, runtime, and control plane for AI
applications. It is not a workflow loop wrapped around a model. It packages
backend logic, APIs, widgets, full frontend surfaces, MCP, scheduled jobs,
configuration, state, streaming, RBAC, and agent/runtime behavior into
deployable application units that run inside isolated environments.

Inside KDCube, those deployable application units are called `bundles`.
A bundle is a folder with code and resources that KDCube can load directly
from your local filesystem, or from Git by repository ref plus the path to the
bundle folder inside that repo. In practice, almost anything that can run
inside the KDCube / FastAPI runtime can be packaged as a bundle. It is not a
narrow plugin or a prompt wrapper. It is the platform term for one application
that can contain backend, frontend, jobs, tools, configuration, storage, and
runtime behavior together.

One `tenant/project` is one isolated KDCube environment. One environment can
host many applications. In KDCube terms, each of those applications is one
bundle.

KDCube runs on a single-node local machine with Docker Compose, and the same
environment model carries to EC2-style and ECS-based deployments. You do not
need a cloud control plane to start.

## What KDCube Gives You

- a platform + SDK for building new AI apps or wrapping existing systems
- full applications, not just workflow loops: backend, frontend, APIs,
  widgets, MCP, cron, streaming, storage, and runtime control in one unit
- a live control plane for enabling, disabling, gating, and hot-reapplying
  bundles and their APIs, widgets, MCP surfaces, and jobs
- Bundle Admin operations instead of deployment ceremony: add or update the
  app entry, press save, and apply changes with zero downtime
- scalable by construction: start on one machine, then keep the same bundle
  and environment model across machines with shared Redis, Postgres, blob
  storage, and Git-backed sources
- RBAC and execution boundaries around the application and its individual
  surfaces
- a bundle-level outbound firewall for controlling which events are allowed to
  leave the runtime toward the client
- an execution model with ReAct Agent, Claude Code, custom Python flows,
  `@venv(...)`, and isolated exec
- a real trust boundary: trusted tool calls run through bundle tools; untrusted
  generated code runs in isolated exec
- economics and accounting: economic tiers, project budgets, service rate
  limits, price-table accounting, and billing hooks
- attachments, artifacts, and bundle storage on local FS, blob storage, or
  Git-backed stores
- streaming and transport surfaces over REST, SSE, Socket.IO, and MCP

## Mental Model

- `tenant/project` = one isolated KDCube environment
- one environment can host many applications
- one application unit inside that environment is called a `bundle`
- bundle surfaces can be controlled independently and updated live

Use separate environments for `dev`, `staging`, `prod`, or for parallel
isolated deployments that must not share runtime state, budgets, or
configuration.

## What You Can Build

- AI copilots and assistants with custom workflows and domain logic
- internal operational tools with authenticated APIs, admin widgets, and
  scheduled jobs
- public AI-backed APIs, webhooks, and MCP surfaces
- full iframe-based applications with their own frontend
- scheduled or background AI pipelines
- wrappers around existing services or codebases that need a governed runtime
- multi-surface products where chat, API, widget, UI, and cron logic belong to
  the same application

## Application Shape In KDCube

The main unit in KDCube is an application that the platform internally calls a
bundle. Concretely, a bundle is a folder with code, descriptors, and optional
UI/resources that KDCube resolves either from the local filesystem or from a
Git repo at a specific ref and bundle path. Almost anything that can run in the
KDCube / FastAPI runtime can be packaged this way. A bundle is not one
workflow loop. It is one application package, and it can expose several
surfaces at once. That is the normal model, not an edge case.

Typical bundle structure:

```text
my.bundle@1-0/
  entrypoint.py
  orchestrator/
    workflow.py
  tools_descriptor.py
  skills_descriptor.py
  tools/
  skills/
  ui/
  ui-src/
  resources/
  tests/
  requirements.txt
  backend_bridge/
```

Python remains the KDCube-native shell. If you need selected backend logic in
Node or TypeScript, keep the KDCube surface in Python and place the external
backend behind a narrow bridge.

Public reference for that pattern:

- [Bundle Node backend bridge](app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md)
- [Node backend sidecar](app/ai-app/docs/sdk/node/node-backend-sidecar-README.md)
- [`node.bridge.mcp@2026-04-24`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24)

## Build With Agents Too

KDCube now documents itself in a way that works for both engineers and coding
agents.

The docs include:

- a compact Tier 1 bundle-authoring pack
- a reference bundle that demonstrates the platform shape
- explicit configuration and runtime ownership rules
- local run, reload, and test guidance

This means an agent can help with real bundle work as:

- creator
- integrator
- configurator
- deployer
- local QA
- integration QA
- document reader

Start here:

- [What You Can Do With KDCube](app/ai-app/docs/what-you-can-do-with-kdcube-README.md)
- [How To Navigate KDCube Bundle Docs](app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)

## Quick Start

Install the bootstrap CLI and launch the setup wizard:

```bash
pipx install kdcube-cli
kdcube
```

Alternative:

```bash
pip install kdcube-cli
kdcube
```

Prerequisites:

- Python 3.9+
- Git
- Docker

Start here:

- [CLI installer](app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md)
- [CLI deployment docs](app/ai-app/docs/service/cicd/cli-README.md)
- [Quick Start](app/ai-app/docs/quick-start-README.md)

## Start Here If You Want To Build Bundles

Read this Tier 1 pack together:

1. [How To Navigate KDCube Bundle Docs](app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
2. [How To Test A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md)
3. [How To Write A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md)
4. [Bundle Runtime Settings, Configuration, and Secrets](app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [How To Configure And Run A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

Primary reference bundle:

- [Versatile reference bundle doc](app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md)
- [`versatile@2026-03-31-13-36`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36)

Specialized examples:

- [`kdcube.copilot@2026-04-03-19-05`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05)
  for bundle-defined `ks:` knowledge space and builder copilot behavior
- [`with-isoruntime@2026-02-16-14-00`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00)
  for direct isolated exec
- [Node/TS backend bridge doc](app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md)
- [Node/TS backend sidecar doc](app/ai-app/docs/sdk/node/node-backend-sidecar-README.md)
- [`node.bridge.mcp@2026-04-24`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24)
  for wrapping bundle-local Node or TypeScript backend logic

## Application Runtime Composition

One KDCube application is not limited to one agent or one runtime.

Inside one bundle you can combine multiple logic blocks and give each block the
runtime and safety boundary it needs:

- ReAct Agent for timeline-first orchestration, shared/user-facing assistants,
  full virtualization, isolated workspace work, ANNOUNCE, and tool-driven execution
- Claude Code for owner/admin coding flows and complex coding pipelines where a
  persistent coding agent is useful
- custom Python agents for domain-specific flows
- isolated exec for generated code and controlled execution
- `@venv(...)` for dependency-heavy Python leaf helpers

Agents are only one part of that picture.
The same bundle may also contain ordinary backend logic, APIs, widgets, cron
jobs, MCP surfaces, and internal helper code that do not need the same runtime.

This is not an exclusive `either/or` choice.

One bundle can, for example:

- receive a chat request through the platform chat channel
- route that request into the bundle execution path
- use ReAct Agent for orchestration, workspace work, tools, and shared chat behavior
- call Claude Code for selected owner/admin coding tasks inside the same app
- delegate selected untrusted code to isolated exec
- use `@venv(...)` helpers for dependency-heavy Python leaf jobs
- keep other steps in ordinary Python bundle code

Current product entry paths for chat are:

- SSE-backed chat ingress
- Socket.IO-backed chat ingress

Those channels currently carry the chat request and attachments into the bundle
execution path. In normal chat flow, non-`steer` and non-`followup` messages
are routed into the bundle's main execution path. Conceptually, the channel is
still a platform message transport, not a restriction to one internal agent
mode.

Security and isolation rule:

- ReAct Agent is the strongly isolated runtime for shared and user-facing agent work
- Claude Code is useful for some complex coding flows, especially owner/admin scenarios
- do not treat Claude Code and ReAct Agent as equivalent from a trust-boundary perspective

Important: ReAct Agent is not based on provider-native tool-calling protocol.
The loop is controlled by the platform runtime, not by a model-specific
tool-call format. That lets you use non-tool-calling models as the reasoning
brain when they can follow the ReAct contract.

Read more:

- [ReAct docs](app/ai-app/docs/sdk/agents/react)
- [Claude Code integration](app/ai-app/docs/sdk/agents/claude/claude-code-README.md)
- [Bundle runtime](app/ai-app/docs/sdk/bundle/bundle-runtime-README.md)

## Deployment Model

KDCube supports:

- local Docker Compose for development and small deployments
- EC2-style deployments
- ECS-based hosted deployments

The CLI supports:

- guided local setup
- descriptor-driven installs
- latest released builds
- upstream source builds
- local bundle prototyping and bundle reload flow

Read more:

- [CLI installer](app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md)
- [Configuration docs](app/ai-app/docs/configuration)
- [Deployment docs](app/ai-app/docs/service/cicd)

## Documentation

Start here:

- [What You Can Do With KDCube](app/ai-app/docs/what-you-can-do-with-kdcube-README.md)
- [Docs index](app/ai-app/docs/README.md)
- [Quick Start](app/ai-app/docs/quick-start-README.md)

Builder-oriented:

- [SDK bundle docs](app/ai-app/docs/sdk/bundle)
- [Bundle docs index](app/ai-app/docs/sdk/bundle/bundle-index-README.md)
- [How To Navigate KDCube Bundle Docs](app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
- [Versatile reference bundle](app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md)
- [Tools docs](app/ai-app/docs/sdk/tools)
- [Skills docs](app/ai-app/docs/sdk/skills)

Platform-oriented:

- [Architecture docs](app/ai-app/docs/arch)
- [Service docs](app/ai-app/docs/service)
- [Exec / isolation docs](app/ai-app/docs/exec)

## Community

If you want to build AI applications fast but still control runtime, tools,
costs, deployment, provenance, and operations, KDCube is aimed at that use
case.

Project site:

- https://kdcube.tech/
