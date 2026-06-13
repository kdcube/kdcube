---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md
title: "Tier 1 Bundle Pack For Build-With-KDCube Plugins"
summary: "Short handoff note for Claude Code and Codex plugin engineers describing the Tier 1 bundle-doc pack, bundle events, the agent task facets it must support, and the minimal integration contract."
tags: ["sdk", "bundle", "plugins", "claude-code", "codex", "handoff", "tier-1"]
keywords: ["tier 1 bundle pack", "build with kdcube plugin", "claude code plugin", "codex plugin", "bundle docs pack", "bundle agent facets", "shared sdk widget source", "bundle events", "event sources", "artifact rehosters", "plugin doc links update"]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/browser/browser-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
---
# Tier 1 Bundle Pack For Build-With-KDCube Plugins

Use this note as the handoff contract for the Build-with-KDCube plugins.

The current plugin code in the repo may be outdated.
This doc is the contract, not the old tree.

The plugin should treat
[how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas](how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas)
as the canonical CLI command map. Do not duplicate that map across plugin
prompts; point agents there and let task-specific docs explain only what is
different for the current role.

## Link Conventions For Plugins

The plugin should preserve logical links instead of baking in one developer's
absolute paths:

- KDCube platform docs live under `repo:kdcube-ai-app/app/ai-app/docs/...`.
- `repo:kdcube-ai-app/...` resolves to the local KDCube platform repository.
- `repo:applications/...` resolves to the local applications/content
  repository.
- `repo:website/...` resolves to the local website repository.

If the plugin cannot resolve a repo alias, it should infer the checkout from
the workspace or ask the user for the repo path before editing files.

## Tier 1 Pack

These 7 docs form the compact Tier 1 build baseline and should be available together:

1. [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
4. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
5. [../bundle-properties-and-secrets-lifecycle-README.md](../bundle-properties-and-secrets-lifecycle-README.md)
6. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
7. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

Add [../bundle-subsystem-integration-README.md](../bundle-subsystem-integration-README.md)
whenever the task mounts or changes an existing SDK subsystem such as memory,
canvas, tasks, Telegram, or delivery. Plugin prompts should not inline that
checklist; they should link to it.

Add [how-to-avoid-common-bundle-integration-failures-README.md](how-to-avoid-common-bundle-integration-failures-README.md)
whenever the task touches bundle-local imports, widget origins/assets, widget
visibility, live events, Data Bus, authored event policies, or resolver
registration. Plugin prompts should surface it as a recipe page, not duplicate
its contents.

This optional lifecycle doc should also be available:

8. [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)

It is used only after the user agrees to release, commit, tag, push, or update
a git-backed descriptor ref.

This conditional agent-integration doc should be available whenever the bundle
uses React tools/skills, file-producing tools, MCP, or Claude Code:

9. [../bundle-agent-integration-README.md](../bundle-agent-integration-README.md)

This conditional local-runtime agent doc should be available whenever the user
expects the plugin agent to configure and run the local deployment, not only
describe how it works:

10. [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md)

It is used when the plugin should let an agent configure and run the local
runtime end to end: discover paths, initialize the workdir, wire a bundle into
the staged descriptors, patch bundle props/secrets, start or verify ngrok,
register Telegram webhooks, prepare Gmail OAuth config, and report only the
external provider steps it cannot complete.

This lower-level local-public-runtime doc should remain reachable from that
coding-agent runbook:

11. [../../../service/cicd/ngrok-README.md](../../../service/cicd/ngrok-README.md)

It is used for Telegram webhooks, OAuth/Cognito callbacks, and other
callback/remote-control flows that need public HTTPS while the runtime is still
on localhost.

The common failure recipes that plugins must surface early now live in
[how-to-avoid-common-bundle-integration-failures-README.md](how-to-avoid-common-bundle-integration-failures-README.md).
That page owns the recurring rules for bundle-local imports, widget origins and
assets, widget visibility, live operation events, Data Bus boundaries, authored
event-source policies, subsystem mounting, and resolver ownership.

Preferred reading order:

1. navigation
2. test expectations
3. reusable SDK/platform building blocks
4. implementation design
5. configuration ownership
6. runtime and deployment wiring
7. local runtime bootstrap, when the coding agent must perform setup

## Agent Model

The plugin should support one agent that can combine these task facets:

- creator
- integrator
- configurator
- deployer
- local QA
- integration QA
- document reader

These are not separate personas.
They are routing hints for one planning agent.

## How To Incorporate This In The Plugin

Recommended:

- expose the 7 baseline docs as one Tier 1 pack
- use [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md) as the first router
- make [how-to-test-bundle-README.md#1a-working-environment-for-agents](how-to-test-bundle-README.md#1a-working-environment-for-agents) the preflight before code or test changes
- keep [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
  visible before implementation so agents reuse SDK Tasks, Email, Telegram,
  Delivery, web/browser/rendering/exec tools, storage, widgets, jobs, MCP, and Claude Code blocks when
  they fit
- keep the rest of the Tier 1 pack visible as the required baseline
- keep [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
  reachable for source-folder widget work, especially the `OUTDIR` /
  `<VI_BUILD_DEST_ABSOLUTE_PATH>` build command contract and shared SDK UI
  materialization via `shared_sources`
- keep [bundle-client-communication-README.md](../bundle-client-communication-README.md)
  and [bundle-transports-README.md](../bundle-transports-README.md) reachable
  for browser/backend communication, especially non-chat bundle events over
  `/sse/stream` or Socket.IO with `KDC-Stream-ID` and
  `comm.service_event(...)`
- keep [Conversation Event Bus And Data Bus](../../../service/comm/conversation-event-bus-and-data-bus-README.md),
  [Data Bus](../../../service/comm/data-bus-README.md), and
  [Gateway Descriptor](../../../configuration/gateway-descriptor-README.md)
  reachable for widget/domain mutations: Data Bus uses `messages[]`,
  `@data_bus_handler(...)`, and gateway
  `gateway.data_bus.ingress.publish_limits` before durable writes
- keep [bundle-agent-integration-README.md](../bundle-agent-integration-README.md)
  reachable for React descriptors, file-producing tool contracts, MCP
  connector/server wiring, Claude Code subprocess agents, and the common
  model-selection recipe that uses `config.role_models` for defaults and
  `bundle_call_context.role_models` for one API/MCP/cron/chat/job invocation
- keep [bundle-events-README.md](../bundle-events-README.md) reachable for
  authored external events, event-source policies, story-aware UI flows, and
  artifact namespace rehosters
- keep [browser-tools-README.md](../../integrations/browser/browser-tools-README.md)
  reachable for ReAct-side browser verification of generated HTML and widgets
- keep [ngrok-README.md](../../../service/cicd/ngrok-README.md) reachable for
  local public HTTPS runtime testing of Telegram webhooks, OAuth callbacks, and
  remote callback/control flows
- keep [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md)
  reachable when a user expects the agent to perform local setup autonomously
  instead of only explaining the runtime model
- keep [Telegram SDK Integration](../../integrations/telegram/telegram-README.md)
  and
  [Telegram External Prerequisites](../../integrations/telegram/telegram-external-prereq-README.md)
  reachable when the bundle needs a Telegram bot, webhook, Mini App, registry,
  or Telegram delivery path
- branch to deeper docs only after Tier 1
- expose the release procedure as optional and user-approved, not automatic

This can be done in any implementation shape:

- prompt
- skill
- plugin README
- embedded links
- packaged resource bundle

Using the pack almost as-is is fine.

## Link Update Rule

Docs were rearranged.

So if the plugin still uses hardcoded links to older bundle or configuration
docs, update those links to the current paths.

Do not keep the old scattered doc paths as the primary bundle-authoring route.

## Agent Guardrails

The plugin should steer agents away from these recurring mistakes:

- do not recommend bare `python3` or bare `pytest` before proving the project venv
- do not interpret async test failures until `pytest-asyncio` is installed in the active venv
- do not start a new bundle without the skeleton files from `how-to-write-bundle-README.md#1b1-new-bundle-skeleton-checklist`
- do not reimplement provider/runtime mechanics before checking the SDK
  building-block map
- do not register bundle-local tools with `module: "tools.name"`; use
  `ref: "tools/name.py"` and package-relative imports inside the tool module
- do not write `/bundles/...` into a seed/source descriptor that is also used by host-side IntelliJ/proc runs; first determine whether you are editing a seed descriptor or a staged runtime descriptor
- do not manually build `ui/main` into runtime bundle storage as the fix for stale bundle UI
- do not use source folder names or compiled example ids when the host provides `defaultAppBundleId`
- do not treat `bundles.yaml` example config as enabling built-in examples; `bundles_include_examples` owns that
- do not treat `singleton` as cross-process exclusivity or shared-storage initialization
- do not treat bundle `user_id` as always being a KDCube account id; it is the
  resolved bundle user scope and may be a mapped external identity such as a
  Telegram user
- do not expose model-facing tool parameters for runtime ids the model cannot
  know; use runtime context, `bundle_call_context`, job payload, or opaque refs
  returned by prior tools
- do not mutate durable `role_models` when the user requested a one-off
  lite/regular/strong agent run; bind `bundle_call_context.role_models` around
  the downstream SDK agent/React/tool call and re-bind inside later `@on_job`
  handlers from the job payload when needed
- file-producing tools use the strict `ret.artifact_type == "files"` protocol
  with `ret.files[]`, or trusted tool-side `host_files(...)`
- custom tool result handling should be expressed as event-source policies when
  the tool owns how its result becomes timeline blocks or artifact rows
- bundles with wizard/canvas/snapshot events should include their configured
  event modules, event-source specs, docs, tests, and release scope
- externally tracked artifact refs visible to ReAct need a namespace rehoster
  that materializes them into the appropriate `fi:` namespace
- `host_files(...)` documentation states that it requires prepared tool context
  from `BaseWorkflow.build_react(...)` or isolated `bootstrap_bind_all(...)`
- generated executor code gets files by calling a catalog tool through
  `agent_io_tools.tool_call(...)`; `host_files(...)` is for trusted
  bundle/catalog tools
- use `browser_tools` for real browser verification when static checks are not
  enough; keep screenshots optional and avoid routing `browser_tools.*` through
  isolated exec
- use the ngrok local-public-runtime guide when localhost must receive external
  provider callbacks; do not expose proc through a separate public URL
- for Telegram integration, use the SDK bundle wiring checklist; do not
  hand-roll user registry, webhook duplicate handling, Mini App `initData`
  verification, or Telegram delivery if the SDK subsystem fits
- use `react.pull` before relying on historical `fi:` files locally, and use
  `react.checkout` only when a prior `files/...` path must become editable in
  the current turn
- use `react.rg` -> `react.read` -> `react.patch` for precise text artifact
  repair; timeline line numbers are display-only and must never be emitted into
  patch or replacement content
- do not describe bundle UI as a special bundle iframe; bundles expose UI
  surfaces that KDCube serves, and iframes are a host/client embedding choice
- do not duplicate SDK-owned widget panels such as User Memory or Telegram
  admin/channels in every bundle; use `shared_sources` plus a host wrapper that
  injects the bundle operation caller
- do not create raw bundle-owned WebSocket/SSE endpoints just to stream live
  progress from a bundle operation; use the existing SSE/Socket.IO session
  stream plus `KDC-Stream-ID` and the request-bound communicator
- do not use removed resource-level `enabled_config` decorator arguments for
  APIs or MCP; use bundle props/Admin resource overrides and configurable
  role/user-type paths where supported
- local CLI init must preserve `--set-secret` values in the staged active
  `config/secrets.yaml`; use `kdcube info --workdir ...` to verify the concrete
  initialized runtime
- ReAct max rounds are configurable through `ai.react.max_iterations` /
  `AI_REACT_MAX_ITERATIONS`, with default-agent override through
  `config.react.default_agent.max_iterations` and named-agent override through
  `config.react.<agent_key>.max_iterations` or
  `config.react.agents.<agent_key>.max_iterations`
- ReAct live thinking rendering is configurable through
  `ai.react.render_thinking` / `AI_REACT_RENDER_THINKING`, with the same
  default-agent/named-agent override shape; pruned/compacted historical
  thinking is not rendered
- ReAct rendered prompt snapshot debugging is controlled separately by
  `ai.react.debug_timeline` / `AI_REACT_DEBUG_TIMELINE`, with the same
  default-agent/named-agent override shape; keep it off unless diagnosing the
  exact rendered model context
- User Memory subsystem config is reserved under `config.memory` for bundles
  that derive from the memory entrypoint mixin; the widget route also needs
  `config.ui.widgets.memories.enabled: true`
- inherited widgets are real surfaces; use `enabled.widget.<alias>: false` to
  suppress one, and use `ui.widgets.<alias>.src_folder/build_command` to replace
  the served static UI for the same inherited alias
- bundle finalizers such as `on_turn_completed(...)` are for fast cleanup after
  success, error, or cancellation, not for expensive user-facing work
- do not commit, tag, push, or update descriptor refs unless the user has
  explicitly agreed to the content release values

Memory config example to keep in the Tier 1 docs:

```yaml
config:
  memory:
    enabled: true
    announce: {enabled: true, limit: 6, scope_filter: current_bundle}
    tools: {enabled: true, allow_write: false, default_scope_filter: current_bundle}
    widget: {enabled: true, allow_write: true, default_scope_filter: current_bundle}
    reconciliation: {enabled: true}
    snapshots: {enabled: true}
  ui:
    widgets:
      memories:
        enabled: true
```

## Done When

The plugin handoff is clean when:

- the 7 Tier 1 baseline docs are exposed as one pack
- the optional release lifecycle doc is available for user-approved releases
- the working environment preflight is visible before any test command
- the source-folder widget build contract is discoverable from Tier 1 routing
- browser-tool verification is discoverable for generated HTML/widget behavior
- local-public-runtime guidance is discoverable for Telegram webhooks,
  OAuth/Cognito callbacks, and remote callback/control flows
- the plugin can route to the best first page without hiding the rest of Tier 1
- old hardcoded doc links are updated to current paths
