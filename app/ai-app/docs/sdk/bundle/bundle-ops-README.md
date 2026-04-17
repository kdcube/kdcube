---
id: ks:docs/sdk/bundle/bundle-ops-README.md
title: "Bundle Ops"
summary: "Bundle delivery and update guide: local reload, registry updates, descriptor-authoritative config, and deployment-side bundle resolution."
tags: ["sdk", "bundle", "ops", "registry", "git", "reload", "deployment"]
keywords: ["bundle reload", "AGENTIC_BUNDLES_JSON", "bundles registry", "bundles.yaml", "bundle git", "kdcube cli"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
---
# Bundle Ops Guide

This page is for delivery and update operations, not authoring.

If you are building a bundle, start with:

- [bundle-dev-README.md](bundle-dev-README.md)

## Delivery Modes

Choose one delivery mode per deployment:

- mounted local path
  - bundle code exists on disk and is mounted into proc
- git-defined bundle
  - proc clones bundle code from git during registry sync

Request-time bundle resolution does **not** pull from git. Git sync happens during startup or config refresh.

## Local Descriptor-Driven Development

Recommended local loop:

1. keep the bundle under the configured host bundles root
2. point `bundles.yaml` to the container-visible path `/bundles/<bundle-folder>`
3. build once:

```bash
kdcube --descriptors-location <dir> --build
```

4. after code or descriptor changes:

```bash
kdcube --workdir <runtime-workdir> --bundle-reload <bundle_id>
```

Example:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime --bundle-reload versatile@2026-03-31-13-36
```

`--bundle-reload` is descriptor-authoritative:

- reapplies the bundle registry from descriptor/env state
- rebuilds descriptor-backed bundle props from `bundles.yaml`
- clears in-process bundle caches in proc

Use it after changing:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

## Config Update Rules

### Local mode

Local mode is the only mode where editing mounted descriptors directly is the normal operational path.

Typical change:

1. edit `bundles.yaml` and/or `bundles.secrets.yaml`
2. run `kdcube --bundle-reload <bundle_id>`

### Runtime-only override

Admin/runtime overrides are for temporary live changes.

They are **not** the durable source of truth when you later reload from descriptors.

If you want the change to survive reload and future upgrades:

- put it into `bundles.yaml` or `bundles.secrets.yaml`

### Export before upgrade

If a deployment accumulated live bundle config and you need to carry it forward before an upgrade:

- export live bundle descriptors with the CLI
- then treat the exported descriptors as the new source of truth

The exact descriptor-management docs will live under `docs/service/descriptors/`, but the important bundle rule is simple:

- local mounted descriptors can be edited directly
- non-local deployments should treat descriptor export/import as the controlled update path

## Runtime Registry Source of Truth

At runtime the active bundle registry lives in Redis.

Proc loads it on startup and updates it when registry/config refresh is triggered.

Relevant controls include:

- `AGENTIC_BUNDLES_JSON`
- `BUNDLES_FORCE_ENV_ON_STARTUP`
- git bundle resolution env vars

Those are deployment controls. They are not bundle authoring APIs.

## Operational Note On Bundle Local Storage

If a bundle keeps local instance-visible state such as:
- a cloned repo
- a prepared index
- a cron workspace
- a mutable local cache

that state should live under the platform-managed bundle local storage root, not under the bundle source tree.

For bundle authors this means:
- resolve local storage through `self.bundle_storage_root()` or `bundle_storage_dir(...)`
- do not persist operational state under the checked-out bundle code path

Why ops cares:
- local mode expects this data under the mounted bundle storage area
- cloud mode expects the same contract against the shared instance-visible storage layer
- this keeps reloads, upgrades, and mounted code paths separate from mutable runtime state

## When To Read Service Docs

Use service docs only when you are changing deployment descriptors, release packaging, or service-wide configuration.

For bundle-specific config semantics, stay in:

- [bundle-props-secrets-README.md](bundle-props-secrets-README.md)
- [bundle-platform-properties-README.md](bundle-platform-properties-README.md)
