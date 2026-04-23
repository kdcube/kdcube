---
id: ks:docs/sdk/bundle/build/sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md
title: "Sync Tier 1 Bundle Docs To Build-With-KDCube Plugins"
summary: "Handoff note for the Claude Code and Codex plugin maintainers describing the current Tier 1 bundle-doc contract that their plugin prompts, skills, and READMEs must follow."
tags: ["sdk", "bundle", "plugins", "claude-code", "codex", "handoff", "tier-1"]
keywords: ["plugin docs sync", "claude code plugin", "codex plugin", "build with kdcube", "tier 1 bundle docs", "bundle authoring reading order", "obsolete doc names"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
---
# Sync Tier 1 Bundle Docs To Build-With-KDCube Plugins

This note is for the developers maintaining the Claude Code plugin and the
Codex plugin for "build with KDCube".

The plugin implementation details may change. The doc contract below should not.

## Goal

Both plugins must route users through the same Tier 1 bundle-doc path.

These four docs are now the primary authoring path:

1. [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
4. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

Interpretation:

- `how-to-navigate-kdcube-docs`:
  first router for builders, wrapper/integrators, and general doc readers
- `how-to-write-bundle`:
  bundle design and code-structure guide
- `how-to-configure-and-run-bundle`:
  local runtime, descriptors, workdir, and `tenant/project` environment model
- `how-to-test-bundle`:
  validation playbook for proving the bundle against the runtime contract

## Required Plugin Behavior

The plugins should no longer treat the old scattered doc set as the primary
bundle-authoring path.

Instead:

- start with the 4 Tier 1 docs above
- use the navigation doc as the first router when the user has not yet said
  whether they are:
  - creating a new bundle
  - wrapping existing code into a bundle
  - integrating a bundle into a KDCube environment
- branch into deeper docs only when the question becomes specific

## Minimum Reading Order The Plugins Must Expose

1. `how-to-navigate-kdcube-docs-README.md`
2. `how-to-write-bundle-README.md`
3. `how-to-configure-and-run-bundle-README.md`
4. `how-to-test-bundle-README.md`

Then only if needed:

- `docs/configuration/bundle-runtime-configuration-and-secrets-README.md`
- `docs/sdk/bundle/bundle-platform-integration-README.md`
- `docs/sdk/bundle/bundle-runtime-README.md`
- `docs/sdk/bundle/versatile-reference-bundle-README.md`
- `docs/sdk/bundle/bundle-delivery-and-update-README.md`

## Role-Based Routing

### If the user wants to create a new bundle

Start with:

1. `how-to-navigate-kdcube-docs-README.md`
2. `how-to-write-bundle-README.md`

### If the user wants to wrap existing backend, UI, webhook, or cron code

Start with:

1. `how-to-navigate-kdcube-docs-README.md`
2. `how-to-write-bundle-README.md`

Then branch to:

- `bundle-platform-integration-README.md`
- `bundle-runtime-configuration-and-secrets-README.md`
- `versatile-reference-bundle-README.md`

### If the user wants to run or integrate a bundle locally

Start with:

1. `how-to-navigate-kdcube-docs-README.md`
2. `how-to-configure-and-run-bundle-README.md`

Then branch to:

- `bundle-runtime-configuration-and-secrets-README.md`
- `bundles-descriptor-README.md`
- `bundles-secrets-descriptor-README.md`
- `bundle-delivery-and-update-README.md`

### If the user wants to validate or debug a bundle

Start with:

1. `how-to-navigate-kdcube-docs-README.md`
2. `how-to-test-bundle-README.md`

## Old References That Must Be Removed Or Replaced

If the plugin prompts, skills, or READMEs still reference these old docs, they
must be updated:

- `docs/sdk/bundle/bundle-dev-README.md`
  -> `docs/sdk/bundle/bundle-developer-guide-README.md`
- `docs/sdk/bundle/bundle-reference-versatile-README.md`
  -> `docs/sdk/bundle/versatile-reference-bundle-README.md`
- `docs/sdk/bundle/bundle-props-secrets-README.md`
  -> `docs/configuration/bundle-runtime-configuration-and-secrets-README.md`
- `docs/sdk/bundle/bundle-ops-README.md`
  -> `docs/sdk/bundle/bundle-delivery-and-update-README.md`
- `docs/service/configuration/runtime-read-write-contract-README.md`
  -> `docs/configuration/runtime-read-write-contract-README.md`

More important than the filename changes:

- those old docs should no longer define the primary reading path
- the 4 Tier 1 docs above now define the entry contract

## Where To Apply The Sync

Apply this sync to whatever the plugin currently uses, for example:

- system prompt or plugin prompt
- bundle-builder skill
- onboarding README
- docs links embedded in the plugin
- Codex plugin instructions or equivalent skill files

The current plugin tree is not assumed to be stable, so this note does not rely
on one specific implementation layout.

## Acceptance Criteria

The plugin sync is done when all of this is true:

- the plugin no longer points to removed bundle doc filenames
- the plugin starts bundle-authoring users with the 4 Tier 1 docs
- the plugin uses the navigation doc as the first router when task type is
  still ambiguous
- the plugin still links deeper docs only as second-level references
- Claude and Codex plugin variants expose the same Tier 1 reading order
