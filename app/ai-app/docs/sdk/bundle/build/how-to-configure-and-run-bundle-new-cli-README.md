---
id: ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-new-cli-README.md
title: "How To Configure And Run A Bundle With The New CLI"
summary: "Forward-looking bundle runtime guide for the planned deployment-first CLI model with init/defaults/start/stop/reload/export."
tags: ["sdk", "bundle", "configuration", "runtime", "cli", "control-plane"]
keywords: ["new kdcube cli", "kdcube init", "kdcube defaults", "kdcube start", "kdcube stop", "kdcube reload", "kdcube export", "tenant project"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/service/cicd/design/cli--as-control-plane-README.md
  - ks:docs/service/configuration/runtime-read-write-contract-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
---
# How To Configure And Run A Bundle With The New CLI

This page describes the intended bundle-development workflow once the new CLI
command model is in place.

It is not the contract of the current CLI implementation.

Use the current workflow page for what exists now:

- [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

Use the CLI design page for the platform-level rationale:

- [cli--as-control-plane-README.md](../../../service/cicd/design/cli--as-control-plane-README.md)

## What changes in the new model

Today the local workflow is centered on the initialized runtime workdir.

In the new model, the workflow becomes deployment-first.

That means the primary object is:

- one deployment addressed by `tenant/project`

The workdir still exists, but it becomes the concrete local snapshot of that
deployment rather than the main concept the operator has to think about first.

Important:

- deployment isolation by `tenant/project` already exists today
- per-deployment platform snapshots already exist today
- per-deployment Postgres/Redis/runtime data already exist today

So the new CLI does not introduce deployment isolation.

What it changes is the operator-facing command model:

- current CLI: workdir-first
- new CLI: deployment-first

## What `tenant/project` means in the new model

`tenant/project` is the deployment namespace.

For bundle development, that namespace encloses:

- platform/global deployment config and secrets
- deployment-scoped bundle props and bundle secrets
- user-scoped bundle state for that deployment
- the selected platform snapshot or release for that deployment
- the local runtime data for that deployment when the deployment is local

So when you choose `tenant/project`, you are not only choosing:

- which bundle registry to edit

You are also choosing:

- which descriptor set is active
- which platform snapshot is active
- which local data stores belong to that environment

This meaning is not new.

The current CLI already uses `tenant/project` to derive and isolate one local
runtime snapshot. The new CLI only makes that deployment identity first-class in
the commands.

Practical interpretation:

- use a separate `tenant/project` for different customers when they need fully
  isolated environment data
- use a separate `tenant/project` for different lifecycle stages such as
  `dev`, `staging`, and `prod`
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

Examples:

- `tenant-a/prod`
- `tenant-a/staging`
- `demo/dev`

So in the new CLI model, just like today:

- one `tenant/project` = one environment
- one environment can host many bundles
- do not create one `tenant/project` per bundle unless you really want a whole
  new isolated environment

That is why the CLI should target deployments first, not individual bundles
first.

## Scope model remains the same

The CLI model changes.
The runtime state model does not.

The same scope rules still apply:

| Scope | Live authority | Export behavior |
|---|---|---|
| platform/global props and secrets | deployment descriptors and deployment secret provider | outside bundle export |
| deployment-scoped bundle props and secrets | `bundles.yaml` / `bundles.secrets.yaml` or the configured provider authority | exported by `kdcube export` |
| user-scoped bundle props and secrets | PostgreSQL and the configured secrets provider | never exported by `kdcube export` |

Use these reference pages for the exact contract:

- [runtime-read-write-contract-README.md](../../../service/configuration/runtime-read-write-contract-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)

## One Environment Can Host Many Bundles

The deployment namespace is the environment boundary.

It is not the application-module boundary.

So one `tenant/project` environment can contain many bundles at once, for
example:

- an admin bundle
- a public-facing bundle
- a background automation bundle
- an MCP integration bundle

Those bundles share the same deployment boundary while staying separate as
application units.

Operational rule:

- use a new `tenant/project` only when you need a new isolated environment for
  customer separation or stage separation
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

## What A Bundle Means In The New CLI Guide

A bundle is still the end-to-end application unit.

A bundle may include:

- backend execution logic
- APIs
- widgets
- iframe UI
- scheduled jobs
- deployment-scoped bundle config
- deployment-scoped bundle secrets
- optional per-user state

So the new CLI changes how you target deployments, not what a bundle is.

## Intended command flow

The target CLI is intentionally split into phases.

## 1. Bootstrap one deployment with `kdcube init`

Typical forms:

```bash
kdcube init --tenant demo --project news --latest
```

```bash
kdcube init \
  --tenant demo \
  --project news \
  --release 2026.4.23.17
```

```bash
kdcube init \
  --descriptors-location /abs/path/to/descriptors \
  --tenant demo \
  --project news
```

What this should do:

- choose or create the deployment snapshot workdir
- materialize the descriptor set into it
- materialize the selected platform snapshot into it
- record enough metadata so the deployment can later be started, stopped,
  reloaded, and inspected without repeating the whole bootstrap

It does not start docker compose.

## 2. Set operator defaults with `kdcube defaults`

Typical form:

```bash
kdcube defaults \
  --default-tenant demo \
  --default-project news \
  --default-workdir ~/.kdcube
```

This reduces repetition for normal daily use.

## 3. Inspect defaults or one deployment with `kdcube --info`

Global info:

```bash
kdcube --info
```

Deployment info:

```bash
kdcube --info --workdir ~/.kdcube/demo__news
```

Expected deployment-level output:

- tenant/project
- active workdir
- active descriptor paths
- platform snapshot or release
- data directories
- runtime mode

## 4. Start and stop one deployment

Start:

```bash
kdcube start --tenant demo --project news
```

Stop:

```bash
kdcube stop --tenant demo --project news
```

Or explicitly by workdir:

```bash
kdcube start --workdir ~/.kdcube/demo__news
kdcube stop  --workdir ~/.kdcube/demo__news
```

Bundle developer rule:

- think in terms of starting or stopping one deployment sandbox
- not in terms of rebuilding every runtime from scratch each time

## 5. Reload one bundle after descriptor changes

If you changed:

- `bundles.yaml`
- `bundles.secrets.yaml`

then the intended command is:

```bash
kdcube reload \
  --tenant demo \
  --project news \
  --bundle-id my.bundle@1-0
```

Or:

```bash
kdcube reload \
  --workdir ~/.kdcube/demo__news \
  --bundle-id my.bundle@1-0
```

This is still only for deployment-scoped bundle state.

It does not reload:

- user props
- user secrets
- platform/global deployment descriptors

## 6. Export deployment-scoped bundle state

Target form:

```bash
kdcube export \
  --profile local \
  --tenant demo \
  --project news \
  --out-dir /tmp/kdcube-export
```

Or for cloud:

```bash
kdcube export \
  --profile CLOUD1 \
  --tenant demo \
  --project news \
  --aws-region eu-west-1 \
  --out-dir /tmp/kdcube-export
```

Expected export:

- `bundles.yaml`
- `bundles.secrets.yaml`

Not exported:

- user props
- user secrets
- platform/global descriptors
- platform/global secrets

## The planned local-machine model

The new CLI should let one local machine manage many deployments.

But each deployment still remains isolated.

That means:

- one host may have many deployment snapshots
- each snapshot may use a different KDCube platform version
- each snapshot may carry its own descriptor set
- each local deployment still owns its own Postgres and Redis data

This is an application-level control-plane model, not a shared infra control
plane.

This isolation model is also not new.

What is new is that the CLI should expose and manage those already-isolated
deployments directly through `tenant/project` and `profile`, instead of making
the operator think primarily in terms of one reused runtime workdir.

## Why that matters to bundle developers

Because bundles often break or need adjustment across platform versions.

If the CLI forced one global local KDCube installation, bundle developers would
lose an important safety tool:

- keeping one known-good deployment snapshot while testing another one

The preferred model is:

- deployment snapshots stay versioned per `tenant/project`

That lets bundle developers:

- pin one bundle environment to a working platform snapshot
- test a newer release in a different deployment snapshot
- move deployments forward intentionally instead of all at once

## Typical new-CLI workflows

## Workflow A: start a new local bundle sandbox

1. initialize a deployment
2. start it
3. point the bundle entry at a local path
4. reload the bundle after code or bundle-descriptor changes

Example:

```bash
kdcube init --tenant demo --project news --latest
kdcube start --tenant demo --project news
kdcube reload --tenant demo --project news --bundle-id my.bundle@1-0
```

## Workflow B: keep two local deployment snapshots on different platform versions

Example:

```bash
kdcube init --tenant demo --project stable --release 2026.4.23.17
kdcube init --tenant demo --project next --latest
```

The point is not the names.
The point is that the CLI should allow separate deployment sandboxes with
separate platform snapshots.

## Workflow C: use the same operator model for local and cloud

Local:

```bash
kdcube start --tenant demo --project news
```

Cloud export:

```bash
kdcube export \
  --profile CLOUD1 \
  --tenant demo \
  --project news \
  --aws-region eu-west-1 \
  --out-dir /tmp/kdcube-export
```

Same deployment identity.
Different profile/transport.

## Actionable rules for the future model

If you only keep the essentials:

- `tenant/project` is the deployment sandbox
- the workdir is the local snapshot of that deployment
- one machine may manage many deployment snapshots
- each deployment snapshot may use a different platform version
- deployment-scoped bundle state is exportable
- user-scoped state is never part of CLI bundle export
- reload is for `bundles.yaml` / `bundles.secrets.yaml` changes, not for user state
