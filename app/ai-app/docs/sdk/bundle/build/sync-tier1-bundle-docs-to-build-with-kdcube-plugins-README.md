---
id: ks:docs/sdk/bundle/build/sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md
title: "Tier 1 Bundle Pack For Build-With-KDCube Plugins"
summary: "Short handoff note for Claude Code and Codex plugin engineers describing the Tier 1 bundle-doc pack, the agent task facets it must support, and the minimal integration contract."
tags: ["sdk", "bundle", "plugins", "claude-code", "codex", "handoff", "tier-1"]
keywords: ["tier 1 bundle pack", "build with kdcube plugin", "claude code plugin", "codex plugin", "bundle docs pack", "bundle agent facets", "plugin doc links update"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
---
# Tier 1 Bundle Pack For Build-With-KDCube Plugins

Use this note as the handoff contract for the Build-with-KDCube plugins.

The current plugin code in the repo may be outdated.
This doc is the contract, not the old tree.

## Tier 1 Pack

These 5 docs form the compact Tier 1 build baseline and should be available together:

1. [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

This optional lifecycle doc should also be available:

6. [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)

It is used only after the user agrees to release, commit, tag, push, or update
a git-backed descriptor ref.

Preferred reading order:

1. navigation
2. test expectations
3. implementation design
4. configuration ownership
5. runtime and deployment wiring

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

- expose the 5 docs as one Tier 1 pack
- use [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md) as the first router
- make [how-to-test-bundle-README.md#1a-working-environment-for-agents](how-to-test-bundle-README.md#1a-working-environment-for-agents) the preflight before code or test changes
- keep the rest of the Tier 1 pack visible as the required baseline
- keep [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
  reachable for source-folder widget work, especially the `OUTDIR` /
  `<VI_BUILD_DEST_ABSOLUTE_PATH>` build command contract
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
- do not write `/bundles/...` into a seed/source descriptor that is also used by host-side IntelliJ/proc runs; first determine whether you are editing a seed descriptor or a staged runtime descriptor
- do not manually build `ui-src` into runtime bundle storage as the fix for stale bundle UI
- do not use source folder names or compiled example ids when the host provides `defaultAppBundleId`
- do not treat `bundles.yaml` example config as enabling built-in examples; `bundles_include_examples` owns that
- do not treat `singleton` as cross-process exclusivity or shared-storage initialization
- do not treat bundle `user_id` as always being a KDCube account id; it is the
  resolved bundle user scope and may be a mapped external identity such as a
  Telegram user
- do not expose model-facing tool parameters for runtime ids the model cannot
  know; use runtime context, `bundle_call_context`, job payload, or opaque refs
  returned by prior tools
- do not commit, tag, push, or update descriptor refs unless the user has
  explicitly agreed to the content release values

## Done When

The plugin handoff is clean when:

- the 5 Tier 1 docs are exposed as one pack
- the optional release lifecycle doc is available for user-approved releases
- the working environment preflight is visible before any test command
- the source-folder widget build contract is discoverable from Tier 1 routing
- the plugin can route to the best first page without hiding the rest of Tier 1
- old hardcoded doc links are updated to current paths
