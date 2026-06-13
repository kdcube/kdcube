<p align="center">
  <img src="assets/logo.png" alt="KDCube" width="240">
</p>

<p align="center">
  <strong><span style="color:#16a34a;font-size:1.15em;">The self-hosted control plane and runtime for governed AI agents and applications.</span></strong>
</p>

<p align="center">
  Tenancy, budgets, isolated execution, RBAC, and accounting as platform primitives —
  so teams whose data, prompts, tools, and execution must stay inside controlled
  infrastructure can run <em>more than one</em> AI application in production.
</p>

<p align="center">
  <a href="https://opensource.org/license/MIT">
    <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-16a34a">
  </a>
  <a href="https://pypi.org/project/kdcube-cli/">
    <img alt="PyPI package" src="https://img.shields.io/pypi/v/kdcube-cli?label=kdcube-cli">
  </a>
  <a href="https://pypistats.org/packages/kdcube-cli">
    <img alt="PyPI downloads" src="https://img.shields.io/pypi/dm/kdcube-cli?label=downloads">
  </a>
</p>

---

## Building one AI app is easy. Running ten — governed — is hard.

The first AI demo ships in a weekend. The trouble starts at the **second and third app in production**, when someone has to answer:

- Who is allowed to run what, and which surfaces are visible to which roles?
- What did each app cost, and how do we cap it before it surprises us?
- Where does untrusted, model-generated code actually execute?
- Which events are allowed to leave our boundary — and which never can?
- How do we update a live app without redeploying the whole service?

Managed agent services, flow builders, and agent frameworks each handle part of this well. KDCube focuses on the part that is hardest to retrofit: a **governed runtime and control plane you host yourself**, where isolation, budgets, RBAC, accounting, and an outbound event firewall are part of the platform rather than something you assemble per app.

KDCube is not just an agent framework, not just a chatbot, and not just a flow builder. It lets teams package full AI applications — chat, UI, APIs, MCP surfaces, scheduled jobs, configuration, state — as deployable units called **bundles**, and run them with platform controls.

## Who it is for

**Primarily:**

- **Platform and AI-infrastructure teams** running multiple AI apps or agents, who need one runtime with consistent governance instead of per-app scaffolding.
- **Regulated or data-sensitive organizations** — financial services, healthcare, defense, government, or any org where data, prompts, tools, and execution must stay inside controlled infrastructure.
- **Product/application teams and solution builders** packaging internal AI tools as deployable, governed apps with chat, UI, APIs, jobs, MCP, configuration, and owned domain state in one unit.

**Also for:**

- **Coding agents and engineers** building bundles — the docs are written to be navigable by both.
- **Operators** responsible for deployment, budgets, accounting, RBAC, isolation, and runtime policy.

It is probably not the first tool to reach for if you only need a one-off prompt demo, a pure no-code flow builder, or a fully managed vendor-hosted agent service.

## What becomes possible

KDCube is not only a place to run a chatbot. It is a way to turn internal systems, files, workflows, and human review loops into governed AI applications that can grow past the first demo.

- **A private AI app store for your organization.** Each bundle ships as a complete internal product with its own chat, UI, API, jobs, MCP surface, config, secrets, state, and permissions.
- **Domain objects the agent can actually understand.** Tasks, memories, files, incidents, cases, reports — or any namespace you define — become first-class refs with provider-owned schemas, previews, actions, and materialization paths.
- **A shared work surface, not just a chat transcript.** Chat, canvas, widgets, files, tasks, and tools exchange context through stable refs instead of copy-pasted text.
- **Governed automation at the edge of real systems.** Agents can inspect, update, cite, or attach objects only through configured tools, namespace contracts, and auth-aware provider operations.
- **AI products that can be operated.** Platform teams can see costs, enforce budgets, isolate execution, gate surface visibility, control event egress, and reload/apply configuration at runtime.
- **Reusable intelligence.** A bundle that owns a namespace can serve its objects to other bundles, agents, widgets, canvas, chat, and MCP clients without those consumers learning its storage model.

### See it running

The demo landing scene runs several governed surfaces on a single page — each panel is a separate bundle widget with its own visibility and runtime boundary: a versatile chat agent, a pin-board canvas, durable user memories, a task tracker, usage/economics, and an industry-news feed. Context is dragged between them as refs; any surface can be summoned or dismissed. One runtime, many independently governed AI surfaces.

Project site and demo: **https://kdcube.tech/**

## Where KDCube fits

These categories overlap, and each is strong at what it is built for. KDCube's distinct combination is self-hosted and code-first, with tenancy, budgets, isolated execution, accounting, **and a reference agent runtime on board** as platform primitives rather than add-ons.

| | **KDCube** | Managed agent services<br>(AWS / Microsoft / Google) | Flow builders<br>(Dify / Flowise) | Agent frameworks<br>(LangGraph / CrewAI) |
| --- | :---: | :---: | :---: | :---: |
| Self-hosted; data stays in your boundary | ✅ | runs in their cloud | ✅ | n/a (a library) |
| Governed runtime: tenancy, RBAC, budgets | ✅ | partial, in their cloud | ❌ | ❌ |
| Isolated execution for untrusted code | ✅ | their sandbox | ❌ | ❌ |
| Reference agent runtime on board (ReAct) | ✅ | their runtime | ❌ | the loop; the runtime is yours |
| Domain objects as governed namespaces | ✅ | ❌ | ❌ | ❌ |
| Full apps (API, UI, MCP, cron), not one loop | ✅ | varies | flows only | loop only |
| Code-first | ✅ | low-code | low-code | ✅ |
| Control plane (enable / gate / reload / apply) | ✅ | partial | ❌ | ❌ |

The table is about fit, not ranking. KDCube's focus is governance depth plus a self-hosted runtime — including a reference ReAct agent runtime, so you are not assembling tenancy, budgets, isolation, and the agent loop yourself.

## Governance as platform primitives

These are the parts a security or platform review actually asks about, and in KDCube they are built into the runtime:

- **Isolated environments** — one `tenant/project` is one isolated environment with its own state, budgets, and configuration. One environment hosts many applications. Use separate environments for `dev`, `staging`, `prod`, or for deployments that must never share runtime state.
- **RBAC + surface visibility** — role-scoped control over the application *and* each individual surface (API, widget, MCP, job).
- **Budgets, rate limits, and accounting** — economic tiers, project budgets, service rate limits, price-table accounting, and billing hooks.
- **A real trust boundary** — trusted calls run through bundle tools; untrusted, model-generated code runs in isolated exec. These are not interchangeable.
- **Outbound event firewall** — bundle-level control over which events are allowed to leave the runtime toward the client.
- **Runtime configuration flows** — enable, disable, gate, reload, and re-apply apps and their surfaces without rebuilding or redeploying the whole service for every change.

## What you deploy: a `bundle`

A deployable AI application in KDCube is called a **bundle** — one folder of code and resources that carries backend, frontend, APIs, widgets, MCP surfaces, scheduled jobs, configuration, storage, and runtime behavior **together**. It is not a plugin or a prompt wrapper; it is one whole application that can expose several governed surfaces at once. KDCube loads a bundle from your local filesystem or from Git by repo ref plus the bundle path.

> **Vocabulary bridge** — if you think in platform terms:
> a **bundle** is one deployable AI app · a **tenant/project** is an isolated environment · **Bundle Admin** is the control plane · the platform as a whole is your **self-hosted agent control plane / governed AI runtime**.

Typical bundle structure:

```text
my.bundle@1-0/
  README.md
  AGENTS.md
  release.yaml
  entrypoint.py            # decorated surfaces: APIs, widgets, jobs, MCP, Data Bus handlers
  agents/
    main.py                # agent workflow when the bundle uses ReAct
  services/                # reusable bundle services/adapters
  tools/                   # optional bundle-local Python tools
  skills/                  # optional bundle-local skills
  config/
    bundles.template.yaml
    bundles.secrets.template.yaml
  interface/               # the bundle's public contract + OpenAPI
  ui/                      # main app + source-folder widgets or mini apps
  docs/  resources/  tests/  requirements.txt
  backend_bridge/          # optional Node/TS backend behind a narrow bridge
```

Tool and skill wiring is **config-first and per agent**: the bundle declares which tools, namespaces, operations, and skills each agent id may use under `surfaces.as_consumer.agents.<agent>.tools` and `surfaces.as_consumer.agents.<agent>.skills`. Capability is granted per agent, not globally.

Python is the KDCube-native shell. Selected backend logic can live in Node or TypeScript behind a narrow bridge — see [Bundle Node backend bridge](app/ai-app/docs/sdk/bundle/bundle-node-backend-bridge-README.md) and [Node backend sidecar](app/ai-app/docs/sdk/node/node-backend-sidecar-README.md).

## Control over the agent and its runtime

Hosting is half of it. KDCube also gives the bundle author direct control over how the agent perceives and acts — the parts most stacks leave implicit:

- **How events and objects are represented to the agent.** A tool or domain object declares, through `@event_source(...)` and event policies (`react_phase = block_production / timeline_projection / announce_production / compaction_projection`), exactly how its result becomes timeline blocks and ANNOUNCE, and what the model actually sees. The agent's view of a domain object is authored, not accidental.
- **Timeline and context policies, including "cold" history.** Context length is bounded by compaction (a hard ceiling that summarizes and drops older blocks) and by age-based TTL pruning; after a cold cache, history collapses into compact semantic summary cards instead of a full replay. You can hook `on_before_compaction` / `on_after_compaction`, set the trigger (`context_max_tokens`), and tune how many recent turns stay intact and where prompt-cache breakpoints land.
- **Data retention and exposure.** Timeline retention is TTL-based (`cache_ttl_seconds`, `cache_keep_recent_turns`), and what the agent can pull into its workspace is bounded by explicit read caps. Retention and exposure are configuration, not inherited defaults.
- **Custom ontologic namespaces.** A bundle can own a semantic namespace — `task:`, `mem:`, `cnv:`, or one you define — by implementing a **named service provider**. The provider owns refs, objects, schemas, previews, actions, block rendering, file hosting, and URI resolution for that namespace. Consumers connect chat, canvas, and agents to those surfaces by configuration, without embedding bundle-specific logic in common components.
- **Materialization both ways.** Agents can pull namespace refs into their isolated workspace as real files, and host agent-produced files back into a provider-owned namespace — under the provider's contract and the caller's auth context.
- **Per-agent model routing.** Default models per agent role, overridable by bundle config and again per invocation (`bundle_call_context.role_models`).

In short: you decide, deliberately, what the agent sees, what it can touch, what it remembers, and what it is allowed to emit.

### More than one runtime per app

One bundle is not limited to one agent or one runtime. Inside a single app you can combine blocks and give each the boundary it needs:

- **ReAct Agent** — the on-board reference agent runtime, strongly isolated, for shared and user-facing work: timeline-driven orchestration, a per-turn git-based isolated workspace, subagents, mid-turn steer and follow-up, context compaction, ANNOUNCE, and tool-driven execution. It is **not** built on provider-native tool-calling — the loop is controlled by the platform runtime — so a model that follows the ReAct contract, including non-tool-calling models, can be the reasoning brain.
- **Claude Code** — for owner/admin coding flows and complex coding pipelines. Useful, but **not** trust-equivalent to ReAct Agent.
- **Custom Python agents** — domain-specific flows.
- **Isolated exec** — untrusted, generated code under control.
- **`@venv(...)`** — dependency-heavy Python leaf helpers.

The same bundle also holds ordinary backend logic, APIs, widgets, cron, and MCP that don't need an agent runtime at all. Streaming and transport surfaces run over REST, SSE, Socket.IO, and MCP.

Built-in SDK/runtime capabilities include Neuro Search, rendering tools (PDF / PPTX / DOCX / HTML / PNG), a managed shared Playwright browser runtime, custom tools and skills, and MCP-connected tool surfaces.

## What you can build

- **Internal AI workbenches** where chat, canvas, documents, tasks, memory, and domain widgets cooperate on one shared surface.
- **Operational assistants** that inspect and update owned systems through explicit provider contracts.
- **Governed task/case/incident systems** where attachments, evidence, state changes, and summaries stay inside the organization boundary.
- **AI-backed APIs, webhooks, and MCP surfaces** that let external tools and coding agents consume the same governed capabilities.
- **Scheduled AI pipelines** for reports, monitoring, ingestion, review queues, and background enrichment.
- **Customer or employee-facing mini apps** with their own frontend plus server-side tools, jobs, config, and authorization.
- **Reusable namespace providers** that make domain objects available to chat, canvas, agents, and other bundles without leaking implementation details.

## Quick start

```bash
pipx install kdcube-cli   # or: pip install kdcube-cli
kdcube
```

Prerequisites: Python 3.9+, Git, Docker.

KDCube runs on a single machine with Docker Compose, and the same environment model carries to EC2-style and ECS-based deployments. You do not need a cloud control plane to start. The CLI supports guided local setup, descriptor-driven installs, released or source builds, and the local bundle prototyping/reload flow.

- [Quick Start](app/ai-app/docs/quick-start-README.md)
- [CLI installer](app/ai-app/src/kdcube-ai-app/kdcube_cli/README.md)
- [CLI deployment docs](app/ai-app/docs/service/cicd/cli-README.md)

## For builders and operators

KDCube documents itself for engineers **and** coding agents: a compact Tier 1 authoring pack, a reference bundle, explicit configuration/runtime ownership rules, and local run/reload/test guidance. An agent can act as creator, integrator, configurator, deployer, or QA on real bundle work.

**Start with the product map:**

- [What You Can Do With KDCube](app/ai-app/docs/what-you-can-do-with-kdcube-README.md)

**Build a bundle (Tier 1 pack, read together):**

1. [How To Navigate KDCube Bundle Docs](app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md)
2. [How To Test A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md)
3. [How To Write A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md)
4. [How To Assemble A Bundle With SDK Building Blocks](app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
5. [Bundle Runtime Settings, Configuration, and Secrets](app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md)
6. [How To Configure And Run A Bundle](app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

**Reference app:**

- [Versatile reference bundle](app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md) · [`versatile@2026-03-31-13-36`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36)

Specialized examples: [`with-isoruntime@2026-02-16-14-00`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00) for direct isolated exec · [`node.bridge.mcp@2026-04-24`](app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/node.bridge.mcp@2026-04-24) for bundle-local Node/TS backend logic.

**Domain objects, tools, and skills:**

- [Namespace Services](app/ai-app/docs/sdk/namespace-services/README.md) · [Integration walkthrough](app/ai-app/docs/sdk/namespace-services/integration-README.md)
- [Tool subsystem](app/ai-app/docs/sdk/tools/tool-subsystem-README.md) · [Named service tools](app/ai-app/docs/sdk/tools/named-services-tools-README.md)
- [Custom skills](app/ai-app/docs/sdk/skills/custom-skills-README.md)
- [Canvas SDK solution](app/ai-app/docs/sdk/solutions/canvas/canvas-sdk-solution-README.md)
- [ReAct docs](app/ai-app/docs/sdk/agents/react) · [Claude Code integration](app/ai-app/docs/sdk/agents/claude/claude-code-README.md)

**Operate it:**

- [Bundles descriptor](app/ai-app/docs/configuration/bundles-descriptor-README.md)
- [Runtime surfaces and boundaries](app/ai-app/docs/runtime/README.md)
- [Bundle runtime](app/ai-app/docs/sdk/bundle/bundle-runtime-README.md)
- [Conversation event bus and Data Bus](app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md)
- [Architecture](app/ai-app/docs/arch) · [Service docs](app/ai-app/docs/service) · [Exec / isolation](app/ai-app/docs/exec) · [Configuration](app/ai-app/docs/configuration)

**Full docs index:** [app/ai-app/docs/README.md](app/ai-app/docs/README.md)

---

<p align="center"><sub>
Self-hosted AI agent control plane · governed AI runtime with a reference ReAct agent · for platform and AI-infrastructure teams in regulated and data-sensitive organizations.
</sub></p>
