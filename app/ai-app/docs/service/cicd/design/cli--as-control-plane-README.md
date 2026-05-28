---
id: ks:docs/service/cicd/design/cli--as-control-plane-README.md
title: "CLI As Control Plane"
summary: "Design for evolving the KDCube CLI from a local installer into an application-level control plane for multiple local and cloud deployments, while keeping tenant/project as the deployment boundary and defaulting local compose-backed starts to one active deployment at a time."
tags: ["service", "cicd", "cli", "control-plane", "design"]
keywords: ["kdcube init", "kdcube defaults", "kdcube run", "kdcube stop", "kdcube bundle reload", "kdcube config export", "tenant project", "profile", "control plane for multiple deployments", "single active local deployment rule", "multiple deployment snapshots on one machine"]
see_also:
  - ks:docs/service/cicd/cli-README.md
  - ks:docs/configuration/runtime-read-write-contract-README.md
  - ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-new-cli-README.md
---
# CLI As Control Plane

This page describes the target CLI model that is now being designed.

It is not the contract of the current CLI implementation.

Use this page to understand:

- why the CLI is moving away from the current wizard-centric flow
- what `tenant/project` should mean in the new model
- how one local machine can manage many deployments
- which parts remain isolated per deployment
- which commands belong to initialization, run control, bundle management, and export

For the current implemented CLI, use:

- [cli-README.md](../cli-README.md)
- [how-to-configure-and-run-bundle-README.md](../../../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

## Goal

The target CLI should behave as an application-level control plane for KDCube
deployments.

That means one machine should be able to manage many deployments, where a
deployment may be:

- local
- cloud

The local machine does not become the infrastructure control plane for all of
those deployments.

It becomes the operator entrypoint that knows:

- which deployment is being targeted
- where its descriptor state lives
- which platform snapshot or release that deployment uses
- how to start, stop, reload, inspect, and export that deployment

## Core design terms

### Deployment

A deployment is one isolated KDCube environment addressed by:

- `tenant`
- `project`

Operationally, the pair `tenant/project` is the deployment namespace.

### Tenant/project

`tenant/project` is not only an application label.

In this target model, it identifies the deployment sandbox that encloses:

- platform/global deployment config and secrets
- deployment-scoped bundle props and secrets
- user-scoped bundle state for that deployment
- the selected platform snapshot or release for that deployment
- the runtime data stores used by that deployment

So `tenant/project` is the unit that the CLI should target for:

- init
- run / stop
- bundle reload
- export
- cloud profile targeting

### Profile

A profile is the connection target used by the CLI to operate on a deployment.

Initial expected shapes:

- local
- remote/cloud

The profile answers:

- where the deployment runs
- how the CLI reaches it
- which provider or account context is needed

Profile does not replace `tenant/project`.

Profile chooses the transport and environment.
`tenant/project` chooses the logical deployment inside that environment.

### Workdir

The workdir remains the local workspace root for a deployment snapshot.

In the target model, a workdir should still be able to hold a concrete
deployment snapshot that includes:

- the staged descriptor set
- the selected platform checkout or release materialization
- the local runtime data for that deployment when the deployment is local

## Why the CLI is changing

The current CLI is good at bootstrapping one local runtime, but it exposes the
runtime directory more directly than the deployment concept.

That creates two problems:

1. the operator thinks in terms of directories rather than deployments
2. the same machine cannot cleanly manage many local and cloud deployments
   through one command model

The new CLI design fixes that by making the deployment namespace first-class:

- `tenant`
- `project`
- `profile`
- defaults for those values

## Target command phases

The new CLI is intentionally split into phases.

## 1. `kdcube init` (first-time setup only) + `kdcube refresh` (re-init)

Purpose of `kdcube init`:

- bootstrap a deployment workdir
- materialize the descriptor set into it
- materialize the selected platform snapshot into it

It does not start docker compose. It refuses if the target workdir is already
initialized; use `kdcube refresh` for re-init.

Target shape (primary form — composes under platform default base
`~/.kdcube/kdcube-runtime`):

```bash
kdcube init \
  --tenant <tenant> --project <project> \
  [--descriptors-location <dir>] \
  [--upstream | --latest | --release <ref>] \
  [--build]
```

Advanced placement (non-default base):

```bash
kdcube init --workdir <full-namespaced-path> [...]
kdcube init --workdir-base <base> --tenant <t> --project <p> [...]
```

Default source selector:

- `--latest`

If no descriptor folder is given, `init` uses the selected platform source and
its bundled default descriptors as the bootstrap input.

If tenant/project is passed without an explicit descriptor folder, the CLI is
allowed to patch the default descriptor set with that namespace during init.

Purpose of `kdcube refresh`:

- re-init an existing deployment workdir without touching staged descriptors
- stop the stack if running, optionally rebuild platform images, restart

Target shape:

```bash
kdcube refresh \
  {--tenant <t> --project <p>} | {--workdir <full>} \
  [--latest | --upstream | --release <ref>] \
  [--path <repo>] [--build] [--no-restart]
```

`refresh` never modifies `assembly.yaml`, `secrets.yaml`, `bundles.yaml`,
`bundles.secrets.yaml`, or `gateway.yaml`. When no targeting flags are given
and no defaults are set, refresh targets the currently-recorded running
deployment. `--latest`, `--upstream`, and `--release <ref>` reuse the existing
runtime descriptors but select a new platform source before rebuild/restart.
Explicit `--path` without one of those selectors restages a dirty local source
tree into the runtime repo cache before rebuild.
deployment from `~/.kdcube/cli-lock.json`.

## 2. `kdcube defaults`

Purpose:

- set the operator defaults used by later commands

Target shape:

```bash
kdcube defaults \
  --default-project <project> \
  --default-tenant <tenant> \
  --default-workdir <workdir>
```

These defaults let normal commands omit repeated targeting flags.

## 3. `kdcube info`

Two intended levels:

### Global info

```bash
kdcube info
```

Shows:

- current defaults
- default workdir
- default tenant
- default project

If defaults are not configured, it should show that directly instead of
guessing.

### Deployment info

```bash
kdcube info --workdir <deployment-workdir>
```

Shows:

- resolved tenant/project
- installed platform version or source snapshot
- active descriptor paths
- data directories
- runtime mode

In the future, this same deployment-targeted info should also be accessible by
namespace selection, not only by explicit workdir.

## 4. `kdcube start` / `kdcube stop`

Purpose:

- start or stop one deployment

Target shape:

```bash
kdcube start {--project <project> --tenant <tenant>} | {--workdir <workdir>}
kdcube stop  {--project <project> --tenant <tenant>} | {--workdir <workdir>}
```

Safety rule:

- if another local deployment is already running, the CLI should warn and ask
  the operator to stop it first before starting a new one

This is an operator-safety rule, not a statement that concurrent local
deployments are fundamentally impossible forever.

Desired interpretation:

- many deployment snapshots on disk: yes
- many remote deployments managed by one CLI: yes
- many concurrently running local compose-backed deployments by default: no

If true concurrent local multi-instance support is ever introduced, it should be
an explicit advanced mode with per-deployment compose naming and per-deployment
published port allocation.

## 5. `kdcube bundle reload`

Purpose:

- reload one bundle after `bundles.yaml` or `bundles.secrets.yaml` changed

Target shape:

```bash
kdcube bundle reload \
  {--workdir <workdir>} | {--tenant <tenant> --project <project>} \
  --bundle-id <bundle_id>
```

This command is for deployment-scoped bundle state only.

It is not:

- a platform/global descriptor reload
- a user-state export/import operation

## 6. `kdcube config export`

Purpose:

- export the operationally mutable deployment-scoped bundle state

Target shape:

```bash
kdcube config export \
  --profile <profile> \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir <dir>
```

Current export target:

- `bundles.yaml`
- `bundles.secrets.yaml`

It does not export:

- platform/global deployment descriptors
- platform/global deployment secrets
- user props
- user secrets

That split is intentional and must remain aligned with the runtime contract in:

- [runtime-read-write-contract-README.md](../../../configuration/runtime-read-write-contract-README.md)

If bundles want to export user-scoped business state, they must expose their own
bundle-level export APIs.

## Snapshot model vs single shared local installation

The main unresolved design tension is this:

- Should one local machine enforce a single KDCube installation for all
  deployments?
- Or should each deployment keep its own platform snapshot?

The recommended answer is:

- keep per-deployment snapshots

## Why per-deployment snapshots are preferred

Because a deployment is not only a config namespace.

It is also the compatibility boundary for:

- the selected platform version
- the deployment descriptor set
- the bundle set expected to run with that platform version

If the CLI forced one single platform version on the whole machine, the
operator would be forced to upgrade every deployment at once.

That would remove a useful safety property:

- the operator can keep an older deployment snapshot that is known to work

Per-deployment snapshots let the operator:

- test new platform versions per deployment
- keep older local snapshots when bundles are not yet migrated
- debug one environment without destabilizing another

## Why a shared local data folder is not the current target

A shared local data folder sounds simpler at first, but it would weaken the
deployment boundary.

Today each `tenant/project` deployment should continue to own its own:

- PostgreSQL data
- Redis data
- bundle runtime data
- descriptor snapshot
- platform snapshot

That means the local CLI is not becoming a full shared infra control plane.

It becomes an application-level control plane over isolated deployments.

That is acceptable and is the safer current design.

## What the local machine becomes

In this design, one local host can manage many deployments, but each deployment
remains isolated.

So the local machine becomes:

- a deployment operator workstation
- a launcher for local deployment sandboxes
- a client for remote/cloud deployment operations

It does not become:

- one shared global runtime with common Redis/Postgres data for all
  `tenant/project` pairs

## Migration from the current CLI

Current implemented model:

- centered on the initialized runtime workdir
- can infer tenant/project from descriptors
- can reuse one runtime snapshot

Target model:

- centered on deployment identity first
- workdir becomes the concrete materialization of that deployment snapshot
- profile becomes the connection selector for local vs cloud

This means the future CLI should preserve the useful current behavior:

- staged descriptor authority under `config/`
- per-deployment snapshot isolation
- bundle export for deployment-scoped bundle state

But it should expose that behavior through a cleaner command model.

## Non-goals of this design

This design does not currently propose:

- exporting user props or user secrets through the CLI
- one shared local Postgres/Redis store across all deployments
- one forced global platform version on the operator machine
- replacing deployment descriptors with ad hoc bundle-local side files such as
  `.kdcube/bundles.yaml`

That last pattern is intentionally avoided because it weakens the explicit
deployment-owned descriptor model.

## Actionable design rules

If you need the shortest version of the design, keep these rules:

- `tenant/project` is the deployment namespace
- a deployment namespace encloses config, bundle state, user state, and the
  selected platform snapshot
- profiles choose where the deployment is reached, not what the deployment is
- local deployments remain isolated per `tenant/project`
- per-deployment snapshots are preferred over one forced global platform
  installation
- CLI export remains limited to deployment-scoped bundle props and bundle
  secrets
