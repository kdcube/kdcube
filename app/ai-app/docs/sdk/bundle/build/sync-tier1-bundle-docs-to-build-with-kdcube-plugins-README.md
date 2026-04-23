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
---
# Tier 1 Bundle Pack For Build-With-KDCube Plugins

Use this note as the handoff contract for the Build-with-KDCube plugins.

The current plugin code in the repo may be outdated.
This doc is the contract, not the old tree.

## Tier 1 Pack

These 5 docs form one compact Tier 1 pack and should be available together:

1. [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

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
- keep the rest of the Tier 1 pack visible as the required baseline
- branch to deeper docs only after Tier 1

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

## Done When

The plugin handoff is clean when:

- the 5 Tier 1 docs are exposed as one pack
- the plugin can route to the best first page without hiding the rest of Tier 1
- old hardcoded doc links are updated to current paths
