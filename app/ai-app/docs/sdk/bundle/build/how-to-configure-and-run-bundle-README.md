---
id: ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
title: "How To Configure And Run A Bundle"
summary: "Practical guide for using KDCube with bundle descriptors, local path bundles, git bundles, bundle props/secrets, and the local runtime workflow."
tags: ["sdk", "bundle", "configuration", "runtime", "cli", "bundles.yaml"]
keywords: ["how to configure bundle", "bundle runtime", "bundles.yaml", "bundles.secrets.yaml", "assembly.yaml", "kdcube build upstream", "kdcube info"]
see_also:
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/service/configuration/runtime-read-write-contract-README.md
  - ks:docs/service/cicd/design/cli--as-control-plane-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-new-cli-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
---
# How To Configure And Run A Bundle

This page is the operational guide for local bundle runtime setup.

Use it when you need to answer questions like:

- how do I start a local runtime from a descriptor set
- what does `--workdir` really point to
- where are the active descriptor files after install
- how do I point a bundle at my local source tree
- when should I rerun install vs `--bundle-reload`
- how do I avoid overwriting live bundle props/secrets with stale descriptor copies

This page is not the primary source for bundle design or test strategy.
It also documents the CLI/runtime model that exists now, not the planned
deployment-first CLI redesign.

Important:

- `tenant/project` isolation already exists in the current model
- the planned CLI redesign does not introduce that isolation
- the redesign changes how the operator targets and manages deployments

Use the companion docs for those:

- [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
- [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)

For exact descriptor schemas, use:

- [bundles-descriptor-README.md](../../../service/configuration/bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](../../../service/configuration/bundles-secrets-descriptor-README.md)
- [assembly-descriptor-README.md](../../../service/configuration/assembly-descriptor-README.md)
- [runtime-read-write-contract-README.md](../../../service/configuration/runtime-read-write-contract-README.md)
- [how-to-configure-and-run-bundle-new-cli-README.md](how-to-configure-and-run-bundle-new-cli-README.md)
- [cli--as-control-plane-README.md](../../../service/cicd/design/cli--as-control-plane-README.md)

## How This Page Fits In The Bundle Lifecycle

Use this page for the operational phases of bundle work:

1. choose a canonical descriptor directory
2. install or update a local runtime from that descriptor set
3. point bundle entries at local paths or git refs
4. apply descriptor changes correctly
5. verify what the runtime is actually using
6. export live bundle state when admin/runtime changes must be kept

## Current Mental Model

### 1. The runtime is a concrete workspace under `workdir`

The local runtime is not only a CLI command. It is a concrete workspace that contains:

- `config/install-meta.json`
- `config/assembly.yaml`
- `config/secrets.yaml`
- `config/bundles.yaml`
- `config/bundles.secrets.yaml`
- `config/gateway.yaml`
- `.env` files
- runtime data under `data/`

Those files under `workdir/config/` are the active runtime inputs.

### 2. `--descriptors-location` stages the descriptor set into the runtime

When you run `kdcube` with `--descriptors-location`, the CLI copies the canonical descriptor set into:

```text
<runtime>/config/
```

After that, the runtime uses the staged copies.

That means:

- the source descriptor directory is an input to install/update
- the staged files under `workdir/config/` are the live local runtime authority
- editing the source directory later does nothing until you rerun install

This is the main point that older workflow descriptions often got wrong.

### 3. Bundle descriptors are now file-backed local runtime authority

In local descriptor-backed mode, deployment-scoped bundle configuration lives in:

- `workdir/config/bundles.yaml`
- `workdir/config/bundles.secrets.yaml`

Bundle Admin and runtime updates can persist back into those files.

So treat them as live operational state, not only as seed examples.

### 4. `--workdir` often resolves to a namespaced runtime

The CLI can derive a concrete runtime directory from assembly context:

```text
<base_workdir>/<tenant>__<project>
```

If you pass a base workdir like:

```text
~/.kdcube/kdcube-runtime
```

and the descriptor set says:

- tenant = `mytenant`
- project = `myproject`

then the actual runtime usually becomes:

```text
~/.kdcube/kdcube-runtime/mytenant__myproject
```

If there are multiple runtimes under one parent directory, pass the concrete namespaced runtime explicitly.

### 5. What `tenant/project` means today

In the current CLI/runtime model, `tenant/project` is the namespace that
selects one concrete local runtime sandbox.

That sandbox encloses all three state scopes used by a local deployment:

- platform/global deployment config and secrets
- deployment-scoped bundle props and bundle secrets
- user-scoped bundle state for that deployment

Operationally, the namespace also selects:

- the concrete workdir under `<base_workdir>/<tenant>__<project>`
- the platform version or source snapshot staged for that runtime
- the Postgres/Redis data used by that runtime

So today `tenant/project` is not only a label used by routing.

It is the boundary of one local deployment snapshot.

Practical interpretation:

- use a separate `tenant/project` when you need full storage isolation between
  different applications or customer environments
- use a separate `tenant/project` when you want different lifecycle stages such
  as `dev`, `staging`, and `prod`

Examples:

- `tenant-a/prod`
- `tenant-a/staging`
- `demo/dev`
- `demo/test`

Inside one `tenant/project`, bundles share the same environment boundary:

- the same platform snapshot
- the same deployment-scoped config/secrets boundary
- the same PostgreSQL/Redis deployment data stores

So `tenant/project` is the right boundary for:

- multiple customers
- multiple isolated product environments
- multiple lifecycle stages of the same system

It is not the right boundary for:

- splitting one environment into many application modules that should still run
  together

### 6. What `tenant/project` does not mean yet

The current CLI does not implement a true shared local control plane over many
deployments.

Today the safer operating model is:

- each `tenant/project` runtime keeps its own staged descriptor set
- each `tenant/project` runtime keeps its own platform snapshot
- each `tenant/project` runtime keeps its own Postgres/Redis data

That means a local machine can host many deployment snapshots, but they remain
isolated from each other.

This is intentional today because it lets bundle developers keep one known-good
runtime snapshot while testing another one with a newer platform version.

For the planned deployment-first CLI model, use:

- [how-to-configure-and-run-bundle-new-cli-README.md](how-to-configure-and-run-bundle-new-cli-README.md)
- [cli--as-control-plane-README.md](../../../service/cicd/design/cli--as-control-plane-README.md)

## Config And Secret Scopes In The Local Runtime

Use this as the quick decision table for bundle development.

All rows below are inside one current `tenant/project` runtime sandbox.

For the exact helper contract and cloud-mode differences, use:

- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)
- [runtime-read-write-contract-README.md](../../../service/configuration/runtime-read-write-contract-README.md)

| Scope | Typical examples | Read / write API | Live authority in the local runtime | Export / ejection path |
|---|---|---|---|---|
| platform/global props | ports, auth ids, storage backends, path roots | `get_settings()` for effective values; `get_plain("...")` for raw descriptor inspection; no supported write API from bundle code | staged `assembly.yaml` and `gateway.yaml` under `workdir/config/`, plus env | not part of `kdcube --export-live-bundles`; manage through the deployment descriptor set |
| platform/global secrets | deployment-wide API keys, auth secrets | `get_secret("canonical.key")`; no supported write API from bundle code | `secrets.yaml` only when `secrets-file` is active; otherwise the configured secrets provider | not part of `kdcube --export-live-bundles`; manage through deployment secret workflows |
| deployment-scoped bundle props | feature flags, cron expressions, model selection, bundle UI config | read: `self.bundle_prop(...)`; write: `await set_bundle_prop(...)` | `workdir/config/bundles.yaml` when file-backed descriptor mode is active, with Redis as runtime cache | exported by `kdcube --export-live-bundles` to `bundles.yaml` |
| deployment-scoped bundle secrets | webhook secrets, shared API tokens, bundle-specific credentials | read: `get_secret("b:...")`; write: `await set_bundle_secret(...)` | `workdir/config/bundles.secrets.yaml` only in local `secrets-file` mode; otherwise the configured secrets provider | exported by `kdcube --export-live-bundles` to `bundles.secrets.yaml` when the provider/export flow can reconstruct them |
| user-scoped bundle props | one user's preferences or bundle-managed non-secret state | read/write: `get_user_prop(...)`, `set_user_prop(...)`, `delete_user_prop(...)` | PostgreSQL user bundle props table | never exported |
| user-scoped bundle secrets | one user's personal tokens or credentials managed by the bundle | read/write: `get_user_secret(...)`, `set_user_secret(...)`, `delete_user_secret(...)` | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | never exported |

Two hard rules:

- `kdcube --export-live-bundles` is a bundle-state export only. It exports `bundles.yaml` and `bundles.secrets.yaml`. It does not export `assembly.yaml`, `gateway.yaml`, `secrets.yaml`, user props, or user secrets.
- Bundle Admin writes live deployment-scoped bundle state only. It does not rewrite platform/global deployment descriptors.

## One Environment Can Host Many Bundles

One `tenant/project` runtime is one environment.

That environment can host many bundles at the same time.

This is the normal model:

- one environment
- many bundles

Use multiple bundles when you want multiple application modules to share the
same environment boundary.

Examples:

- one admin bundle
- one public-facing bundle
- one background automation bundle
- one MCP integration bundle

All of those can live inside the same `tenant/project` if they belong to the
same environment and should share its deployment boundary.

Create a new `tenant/project` only when you need a new isolated environment.

## What A Bundle Is In This Guide

In this guide, a bundle should be read as an end-to-end application unit.

A bundle may include:

- backend execution logic
- authenticated or public APIs
- widgets
- iframe UI
- scheduled jobs
- bundle-scoped config
- bundle-scoped secrets

So a bundle is not only one backend handler and not only one frontend widget.

A real bundle can be a full application module with:

- BE
- FE
- deployment-scoped config/secrets
- optional per-user state

Multiple such bundles can run inside one `tenant/project` environment.

## Which Files Do What

### `assembly.yaml`

`assembly.yaml` controls runtime topology and platform wiring:

- tenant/project identity
- platform repo/ref
- host path roots
- bundle mount roots
- storage roots
- auth/infra/runtime settings

For local path bundles, `assembly.yaml` is where the host-side roots belong.

### `bundles.yaml`

`bundles.yaml` controls bundle definitions and deployment-scoped non-secret bundle props:

- which bundles exist
- which one is default
- whether a bundle is local-path or git-backed
- bundle props under `config:`

Operationally, this is the file that says which application modules are present
inside the current `tenant/project` environment.

### `bundles.secrets.yaml`

`bundles.secrets.yaml` controls deployment-scoped bundle secrets:

- shared API tokens
- webhook secrets
- MCP tokens
- external service credentials that are bundle-scoped

### `secrets.yaml` and `gateway.yaml`

These still matter to a local runtime, but they are not the main bundle authoring focus:

- `secrets.yaml` holds non-bundle service/runtime secrets for local install
- `gateway.yaml` holds gateway config

For non-interactive local install, the descriptor set should be complete and internally consistent.

## Recommended Local Workflow

Use a canonical descriptor directory and let `kdcube` stage it into the runtime.

Recommended command shape:

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors
```

Without `--build`, this runs the release-image flow using:

- `assembly.platform.ref`
- or `--latest`
- or `--release <ref>`

### Release-image install from `assembly.platform.ref`

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors
```

Use this when you want the normal local runtime based on a released platform version.

### Release-image install from an explicit release

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors \
  --release 2026.4.23.17
```

### Release-image install from the latest known platform release

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors \
  --latest
```

### Source build from a released platform ref

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors \
  --build
```

Use this when you want to build locally from the selected release source rather than pull release images.

### Source build from upstream `origin/main`

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors \
  --build --upstream
```

Important:

- `--upstream` requires `--build`
- `--upstream` requires either `--descriptors-location` or an already initialized runtime

Use this when you are validating current platform source, not when you only need to update bundle descriptors.

## Inspecting The Runtime You Already Have

### Show active runtime info

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --info
```

This prints:

- resolved workdir
- config/data/docker dirs
- repo root
- install mode / platform ref
- active assembly and bundles descriptor paths
- host/container bundle roots
- host/container managed bundle roots
- bundle storage roots

Use `--info` whenever you are not sure which runtime or mount mapping you are actually using.

### Stop the runtime

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --stop
```

With volumes removed:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --stop --remove-volumes
```

## Local Path Bundles vs Git Bundles

### Local path bundles

Use a local path bundle when you are actively editing code from your source tree.

Recommended entry:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      name: "My Bundle"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
```

This means:

- `path` is the bundle root as seen inside the runtime
- `module` is resolved inside that root

Do not keep these on the same entry:

- `repo`
- `ref`
- `subdir`

### Git bundles

Use a git-backed bundle when you want pinned, managed, versioned delivery.

Typical shape:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      repo: "git@github.com:org/repo.git"
      ref: "2026.4.23.17"
      subdir: "src/my_bundle"
      module: "entrypoint"
```

Do not mix local-path and git fields on the same bundle entry.

## Host Paths vs Runtime Paths

This is the most common source of mistakes.

Example:

- host root in `assembly.yaml`: `/Users/you/src`
- runtime bundles root: `/bundles`

If the host bundle path is:

```text
/Users/you/src/my-repo/src/my_bundle
```

then the bundle entry in `bundles.yaml` must use the runtime-visible path:

```text
/bundles/my-repo/src/my_bundle
```

So:

- host path roots belong in `assembly.yaml`
- runtime-visible bundle paths belong in `bundles.yaml`

## Managed vs Non-Managed Bundle Roots

Keep non-managed local bundles and managed bundles separate.

Non-managed bundles:

- your local source-tree bundles

Managed bundles:

- git-resolved bundles
- built-in example bundles materialized by the platform

In practice:

- `assembly.yaml -> paths.host_bundles_path` points to the host root for non-managed local bundles
- `assembly.yaml -> paths.host_managed_bundles_path` points to the runtime-managed bundle cache

Use `kdcube --info` to verify both host/container mappings.

## Applying Changes Correctly

### If you changed the canonical descriptor source directory

If you edited files in the source descriptor directory passed via `--descriptors-location`, rerun install so those changes are restaged into the runtime:

```bash
kdcube \
  --path /abs/path/to/kdcube-ai-app \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /abs/path/to/descriptors
```

This is required because `--bundle-reload` does not read arbitrary external descriptor directories. It reuses the runtime’s staged descriptor files.

### If you changed `bundles.yaml` or `bundles.secrets.yaml` inside the active runtime

After editing:

```text
<runtime>/config/bundles.yaml
<runtime>/config/bundles.secrets.yaml
```

apply the change with:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --bundle-reload my.bundle@1-0
```

`--bundle-reload`:

- validates that the bundle id exists in the active runtime descriptor
- requires `chat-proc` to be running
- reapplies the runtime descriptor
- clears the target bundle from proc caches

Use this for:

- bundle props changes
- bundle secrets changes
- enable/disable flag changes
- switching a bundle entry between local path and git, if the runtime topology itself did not change

It does not reload:

- user props
- user secrets
- platform/global descriptor files such as `assembly.yaml`, `gateway.yaml`, or `secrets.yaml`

### If you changed platform/runtime topology

Rerun install, not only `--bundle-reload`.

Typical cases:

- `assembly.yaml`
- host path roots
- storage roots
- compose/runtime shape
- platform version selection
- source-build vs release-image mode

### If you only changed local bundle code

If the bundle is already mounted as a local path bundle, you often only need a reload:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --bundle-reload my.bundle@1-0
```

Use a full reinstall only when code changes depend on wider runtime/platform changes.

## Bundle Props, Secrets, And `enabled_config`

Deployment-scoped non-secret bundle config goes in `bundles.yaml`.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
      config:
        api:
          header_name: "X-My-Bundle-Token"
        features:
          admin_export:
            enabled: false
```

Deployment-scoped secret bundle config goes in `bundles.secrets.yaml`.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      secrets:
        api:
          shared_token: "replace-me"
```

`enabled_config` values belong in bundle props, not in secrets.

Example bundle code:

```python
@api(
    alias="admin_export",
    route="operations",
    method="POST",
    user_types=("privileged",),
    enabled_config="features.admin_export.enabled",
)
async def admin_export(self, **kwargs):
    return {"ok": True}
```

Operational behavior:

- missing path means enabled
- `false`, `0`, `disable`, `disabled`, and `off` disable the surface

After changing the prop, apply it with:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --bundle-reload my.bundle@1-0
```

## What Happens When Bundle Admin Changes Props Or Secrets

In local descriptor-backed mode:

- deployment-scoped bundle prop changes persist into the active runtime `bundles.yaml`
- deployment-scoped bundle secret changes persist into the active runtime `bundles.secrets.yaml`

They do not persist into:

- `assembly.yaml`
- `gateway.yaml`
- `secrets.yaml`
- user-scoped runtime state

So if bundle admin changes live config, the runtime files under `workdir/config/` become the newest state.

If you later rerun install from an older source descriptor directory, you can overwrite that newer live state.

That is why you should export live bundle state before replacing runtime descriptors with stale source copies.

## Exporting Live Bundle State

To export the current effective bundle descriptors:

```bash
kdcube \
  --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject \
  --export-live-bundles \
  --out-dir /tmp/live-bundles
```

In local descriptor-backed mode, this exports from the active runtime workspace descriptor files.

Current export includes:

- `bundles.yaml`
- `bundles.secrets.yaml`

Current export does not include:

- `assembly.yaml`
- `gateway.yaml`
- `secrets.yaml`
- user props
- user secrets
- platform/global secrets
- bundle storage payloads

Use it when:

- bundle admin changed props/secrets
- runtime code changed live bundle state you want to keep
- you want to sync the current runtime authority back into a canonical descriptor directory

## User-Scoped State Is Different

Do not expect user-scoped state to be reconstructed from descriptor files.

User-scoped runtime state is not deployment-scoped descriptor state:

- user props do not belong in `bundles.yaml`
- user secrets do not belong in `bundles.secrets.yaml`

Descriptor export/reload is for deployment-scoped bundle config, not per-user business state.

## First-Run Bootstrap vs Existing Runtime

### Existing runtime

Use the existing runtime when you want to:

- keep current config and data
- keep the current staged runtime descriptors
- validate a new platform source build against the current runtime

Example:

```bash
kdcube \
  --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject \
  --build --upstream
```

This reuses the initialized runtime and rebuilds from upstream source.

### Fresh runtime

Use a new workdir when you want to test:

- first-run bootstrap
- default descriptor seeding
- clean local runtime setup

But for normal bundle development, prefer a descriptor-driven initialized runtime instead of ad hoc manual prompting.

## Common Mistakes

- Editing the canonical descriptor source directory and expecting the running runtime to pick it up automatically.
- Forgetting that the staged runtime files under `workdir/config/` are the active local authority.
- Passing host paths in `bundles.yaml` instead of runtime-visible `/bundles/...` paths.
- Mixing `path` with `repo`/`ref`/`subdir` in the same bundle entry.
- Using `--upstream` without `--build`.
- Assuming the base `--workdir` is the concrete runtime when the CLI has resolved a namespaced runtime under it.
- Using `--bundle-reload` before the stack is running.
- Overwriting live bundle-admin changes with stale descriptor source files.

## What To Remember

If you only remember the essentials, remember these:

- the active local runtime authority is under `workdir/config/`
- `--descriptors-location` stages descriptor files into that runtime
- `bundles.yaml` owns bundle definitions and non-secret deployment props
- `bundles.secrets.yaml` owns deployment-scoped bundle secrets
- local path bundles should use runtime-visible `/bundles/...` paths
- rerun install when you changed the canonical source descriptor set or runtime topology
- use `--bundle-reload` when you changed active runtime bundle descriptors or need proc cache eviction
- use `--info` to inspect the runtime you are actually using
- use `--export-live-bundles` before overwriting runtime bundle state with older descriptor copies

For the exact read/write helper contract behind those rules, use:

- [runtime-read-write-contract-README.md](../../../service/configuration/runtime-read-write-contract-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)
