---
id: ks:docs/sdk/bundle/build/sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md
title: "Tier 1 Bundle Pack For Build-With-KDCube Plugins"
summary: "Short handoff note for Claude Code and Codex plugin engineers describing the Tier 1 bundle-doc pack, the agent task facets it must support, and the minimal integration contract."
tags: ["sdk", "bundle", "plugins", "claude-code", "codex", "handoff", "tier-1"]
keywords: ["tier 1 bundle pack", "build with kdcube plugin", "claude code plugin", "codex plugin", "bundle docs pack", "bundle agent facets", "plugin doc links update"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/integrations/browser/browser-tools-README.md
  - ks:docs/service/cicd/ngrok-README.md
---
# Tier 1 Bundle Pack For Build-With-KDCube Plugins

Use this note as the handoff contract for the Build-with-KDCube plugins.

The current plugin code in the repo may be outdated.
This doc is the contract, not the old tree.

## Tier 1 Pack

These 6 docs form the compact Tier 1 build baseline and should be available together:

1. [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
4. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
5. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
6. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

This optional lifecycle doc should also be available:

7. [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)

It is used only after the user agrees to release, commit, tag, push, or update
a git-backed descriptor ref.

This conditional agent-integration doc should be available whenever the bundle
uses React tools/skills, file-producing tools, MCP, or Claude Code:

8. [../bundle-agent-integration-README.md](../bundle-agent-integration-README.md)

This conditional local-public-runtime doc should be available whenever local
KDCube must receive provider callbacks or remote calls:

9. [../../../service/cicd/ngrok-README.md](../../../service/cicd/ngrok-README.md)

It is used for Telegram webhooks, OAuth/Cognito callbacks, and other
callback/remote-control flows that need public HTTPS while the runtime is still
on localhost.

Preferred reading order:

1. navigation
2. test expectations
3. reusable SDK/platform building blocks
4. implementation design
5. configuration ownership
6. runtime and deployment wiring

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

- expose the 6 docs as one Tier 1 pack
- use [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md) as the first router
- make [how-to-test-bundle-README.md#1a-working-environment-for-agents](how-to-test-bundle-README.md#1a-working-environment-for-agents) the preflight before code or test changes
- keep [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
  visible before implementation so agents reuse SDK Tasks, Email, Telegram,
  Delivery, web/browser/rendering/exec tools, storage, widgets, jobs, MCP, and Claude Code blocks when
  they fit
- keep the rest of the Tier 1 pack visible as the required baseline
- keep [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
  reachable for source-folder widget work, especially the `OUTDIR` /
  `<VI_BUILD_DEST_ABSOLUTE_PATH>` build command contract
- keep [bundle-agent-integration-README.md](../bundle-agent-integration-README.md)
  reachable for React descriptors, file-producing tool contracts, MCP
  connector/server wiring, and Claude Code subprocess agents
- keep [browser-tools-README.md](../../integrations/browser/browser-tools-README.md)
  reachable for ReAct-side browser verification of generated HTML and widgets
- keep [ngrok-README.md](../../../service/cicd/ngrok-README.md) reachable for
  local public HTTPS runtime testing of Telegram webhooks, OAuth callbacks, and
  remote callback/control flows
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
- file-producing tools use the strict `ret.artifact_type == "files"` protocol
  with `ret.files[]`, or trusted tool-side `host_files(...)`
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
- do not use removed resource-level `enabled_config` decorator arguments for
  APIs or MCP; use bundle props/Admin resource overrides and configurable
  role/user-type paths where supported
- local CLI init must preserve `--set-secret` values in the staged active
  `config/secrets.yaml`; use `kdcube info --workdir ...` to verify the concrete
  initialized runtime
- ReAct max rounds are configurable through `ai.react.max_iterations` /
  `AI_REACT_MAX_ITERATIONS`, with per-bundle override through
  `config.react.max_iterations` or `react.max_iterations`
- bundle finalizers such as `on_turn_completed(...)` are for fast cleanup after
  success, error, or cancellation, not for expensive user-facing work
- do not commit, tag, push, or update descriptor refs unless the user has
  explicitly agreed to the content release values

## Done When

The plugin handoff is clean when:

- the 6 Tier 1 docs are exposed as one pack
- the optional release lifecycle doc is available for user-approved releases
- the working environment preflight is visible before any test command
- the source-folder widget build contract is discoverable from Tier 1 routing
- browser-tool verification is discoverable for generated HTML/widget behavior
- local-public-runtime guidance is discoverable for Telegram webhooks,
  OAuth/Cognito callbacks, and remote callback/control flows
- the plugin can route to the best first page without hiding the rest of Tier 1
- old hardcoded doc links are updated to current paths
