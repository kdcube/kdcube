# KDCube Builder — Claude Code Plugin

Developer documentation for the `kdcube-builder` Claude Code plugin.

Source: `app/ai-app/src/kdcube-ai-app/builder_plugin`.

The plugin turns natural-language requests inside Claude Code into concrete
actions on a local KDCube runtime and its bundles. The user types things like
*"start KDCube"*, *"reload telegram-bot"*, *"wrap my FastAPI app into a bundle"*,
*"add a cron job to this bundle"* — and the plugin dispatches the right
sequence of commands, descriptor edits, and bundle-authoring steps.

## What it does

- **Bundle authoring** — create bundles from scratch, wrap an existing app into
  a bundle, or extend an existing bundle with new features. This is the core
  value of the plugin: Claude reads the bundle docs and the reference bundle
  before writing any code, so the output matches the current KDCube SDK.
- **Local runtime control** — start / stop / reload / status against a local
  KDCube stack.
- **Descriptor setup** — bootstrap a clean local descriptor profile for a
  bundle, or point a profile at an existing descriptor directory.
- **Verification** — run the shared bundle test suite, verify a bundle reload
  actually took effect, and drive the chat UI in a browser via Playwright.
- **Low-level CLI** — direct access to `kdcube` CLI flows (secrets injection,
  cleanup, live-bundle export).

## Docs in this directory

- [architecture.md](./architecture.md) — layout, the three layers (manifest /
  skills / CLI), state directories, workdir resolution.
- [skills.md](./skills.md) — skill catalog and the `kdcube-dev` intent map that
  routes natural-language requests.
- [bundle-authoring.md](./bundle-authoring.md) — how the plugin creates, wraps,
  and extends bundles; read-order for docs; placement and registration rules.
- [runtime-flows.md](./runtime-flows.md) — first-time setup (bootstrap /
  use-descriptors) and the reload + verify cycle.
- [extending.md](./extending.md) — adding subcommands and skills, gotchas to
  know before changing the plugin.

## Install and distribution

Install instructions and marketplace layout live in the plugin's own README:
`app/ai-app/src/kdcube-ai-app/builder_plugin/README.md`. This directory is only
about the plugin's internals.