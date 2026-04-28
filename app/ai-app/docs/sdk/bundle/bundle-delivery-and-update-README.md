---
id: ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
title: "Bundle Delivery And Update"
summary: "Operational guide for shipping and updating bundles: local-path or git delivery modes, descriptor-authoritative reloads, runtime override hygiene, and deployment-side bundle resolution."
tags: ["sdk", "bundle", "ops", "registry", "git", "reload", "deployment"]
keywords: ["bundle delivery modes", "local path bundles", "git defined bundles", "descriptor authoritative reload", "runtime override hygiene", "bundle deployment operations", "bundle registry updates", "bundle reload procedure"]
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
---
# Bundle Delivery And Update

This page is for delivery and update operations, not authoring.

If you are building a bundle, start with:

- [bundle-developer-guide-README.md](bundle-developer-guide-README.md)

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
kdcube reload <bundle_id> --workdir <runtime-workdir>
```

Example:

```bash
kdcube reload versatile@2026-03-31-13-36 --workdir ~/.kdcube/kdcube-runtime
```

`kdcube reload` is descriptor-authoritative:

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
2. run `kdcube reload <bundle_id>`

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

- mounted `bundles.yaml` authority
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
- resolve local storage through `self.bundle_storage_root()`
- use `bundle_storage_dir(...)` only in lower-level helpers that do not have an entrypoint instance
- do not persist operational state under the checked-out bundle code path

Why ops cares:
- local mode expects this data under the mounted bundle storage area
- cloud mode expects the same contract against the shared instance-visible storage layer
- this keeps reloads, upgrades, and mounted code paths separate from mutable runtime state

## When To Read Service Docs

Use service docs only when you are changing deployment descriptors, release packaging, or service-wide configuration.

For bundle-specific config semantics, stay in:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)
