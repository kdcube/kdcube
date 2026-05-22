---
id: ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
title: "How To Configure And Run A Bundle"
summary: "Current bundle-development runtime workflow: tenant/project environment setup, descriptor staging, local-path and git bundles, configuration translation, start/stop/reload loop, configuration/secret scopes, and the rule that one machine may hold many local deployment snapshots but should not be treated as running many local compose-backed KDCubes at once."
tags: ["sdk", "bundle", "configuration", "runtime", "cli", "bundles.yaml"]
keywords: ["local bundle development workflow", "tenant project environment boundary", "descriptor driven runtime setup", "local path bundle loop", "git bundle loop", "bundle reload workflow", "runtime sandbox selection", "bundle config and secret scopes", "shared sdk widget sources", "bundle configurator workflow", "bundle deployer workflow", "current kdcube cli workflow", "multiple local runtime snapshots", "single active local compose deployment", "run multiple kdcubes on one machine", "kdcube bundle command", "patch bundle config cli", "patch bundle secret cli"]
updated_at: 2026-05-23
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
  - ks:docs/service/cicd/design/cli--as-control-plane-README.md
  - ks:docs/service/cicd/ngrok-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-new-cli-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---
# How To Configure And Run A Bundle

This page is the operational guide for local bundle runtime setup.

If you are not sure whether you should start here, start with
[how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md).

Tier 1 rule:

- this page is one part of the Tier 1 pack
- do not treat it as sufficient on its own
- read it together with the Tier 1 test, authoring, and configuration pages

Use it when you need to answer questions like:

- how do I start a local runtime from a descriptor set
- what does `--workdir` really point to
- where are the active descriptor files after install
- how do I point a bundle at my local source tree
- when should I rerun install vs `kdcube bundle reload`
- how do I avoid overwriting live bundle props/secrets with stale descriptor copies
- how do I make a localhost KDCube reachable through public HTTPS for external
  callbacks such as Telegram webhooks, OAuth callbacks, or remote-control style
  integrations
- how do I hand the repetitive local setup to an agent so it can configure the
  bundle, staged descriptors, ngrok, Telegram, and Gmail values with minimal
  questions

This page is not the primary source for bundle design or test strategy.
It documents the supported local CLI/runtime workflow for descriptor-backed
bundle development.

Critical Python import rule:

- bundle-local code must use package-relative imports such as
  `from .services.storage import ...`
- do not import bundle-local folders as top-level packages such as `services`,
  `apps`, `tools`, or `resources`
- the same applies to `tools_descriptor.py` and bundle-local tools; local tool
  specs should use `ref: "tools/name.py"`, while `module` is reserved for
  installed SDK/external modules
- see [bundle-runtime-README.md#critical-bundle-local-import-rule](../bundle-runtime-README.md#critical-bundle-local-import-rule)

Critical widget/browser runtime rule:

- opening KDCube inside another app does not make widget APIs call the outer
  app's origin
- widget API base must be the KDCube frame/runtime origin from `baseUrl`, or the
  widget frame's own `window.location.origin` as fallback
- if a deployment intentionally serves KDCube under the host app origin through
  a reverse proxy, that proxy must route `/platform`, `/api`, streaming paths,
  and `/api/integrations/...`
- do not fix widget networking by hardcoding local ports or external host
  application URLs
- if a widget or bundle UI needs live updates from a non-chat operation, reuse
  `/sse/stream` or Socket.IO and pass `KDC-Stream-ID` on the
  `/api/integrations/.../operations/...` call; backend code emits with the
  request-bound communicator
- see [bundle-client-communication-README.md#non-chat-bundle-events-over-the-shared-stream](../bundle-client-communication-README.md#non-chat-bundle-events-over-the-shared-stream)

Important:

- `tenant/project` isolation already exists in the current model
- the CLI uses that namespace to target one concrete runtime workdir
- one machine may hold many local deployment snapshots on disk
- one machine should not be treated as supporting many concurrently running
  local compose-backed KDCube stacks by default

Use the companion docs for those:

- [how-to-navigate-kdcube-docs-README.md](how-to-navigate-kdcube-docs-README.md)
- [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
- [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
- [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
- [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md)
- [bundle-client-communication-README.md](../bundle-client-communication-README.md)
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

Configuration rule:

- [bundle-properties-and-secrets-lifecycle-README.md](../bundle-properties-and-secrets-lifecycle-README.md)
  is the concise bundle-author page for how `configuration_defaults()`,
  descriptor/admin props, effective runtime props, bundle secrets, and
  materialization/export relate to each other
- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) is the
  canonical author-facing page for all props and secrets across all scopes:
  platform/global, deployment-scoped bundle, and user-scoped
- this page keeps only the operational/runtime summary needed while installing,
  reloading, and exporting a local deployment

SDK block configuration rule:

- when a bundle uses an SDK integration or solution, configure it through
  bundle props/secrets and user settings rather than hardcoded local constants
- use [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md)
  to find the package-level docs
- use the package external-prerequisites doc when provider setup is outside
  KDCube, for example Telegram BotFather/webhook setup or Google Cloud OAuth
  setup

Tier 1 role of this page:

- use it after `how-to-write` when you need a real local runtime
- use it together with `bundle-runtime-configuration-and-secrets` when your job
  is configuration modeling
- use it first when your main task is integrating an existing bundle into a
  `tenant/project` environment
- use it first when your main task is deploying, starting, stopping, reloading,
  or inspecting a bundle in a local KDCube environment
- use it when the problem is descriptor authority, reload behavior, workdir
  layout, or local runtime staging
- use [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md)
  when the job is not only understanding the runtime model but actually asking
  an agent to perform the setup, configure a bundle, run ngrok, register
  Telegram webhooks, or prepare Gmail OAuth values

For exact descriptor schemas, use:

- [bundles-descriptor-README.md](../../../configuration/bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](../../../configuration/bundles-secrets-descriptor-README.md)
- [assembly-descriptor-README.md](../../../configuration/assembly-descriptor-README.md)
- [runtime-configuration-and-secrets-store-README.md](../../../configuration/runtime-configuration-and-secrets-store-README.md)
- [how-to-configure-and-run-bundle-new-cli-README.md](how-to-configure-and-run-bundle-new-cli-README.md)
- [cli--as-control-plane-README.md](../../../service/cicd/design/cli--as-control-plane-README.md)
- [Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md)

## How This Page Fits In The Bundle Lifecycle

Use this page for the operational phases of bundle work:

1. choose a canonical descriptor directory
2. install or update a local runtime from that descriptor set
3. point bundle entries at local paths or git refs
4. apply descriptor changes correctly
5. verify what the runtime is actually using
6. export live bundle state when admin/runtime changes must be kept

For bundle shape, surface choice, and wrapper design, return to:

- [how-to-write-bundle-README.md](how-to-write-bundle-README.md)

## Canonical CLI Flow Schemas

This is the Tier 1 source of truth for the local CLI runtime flow. Other Tier 1
bundle-builder docs should point here instead of duplicating these diagrams.

### Init Once

Use `init` for first-time runtime creation or intentional reseeding.

```text
seed descriptors                         platform source
assembly.yaml / bundles.yaml / ...       --path / --upstream / --latest / --release
              |                                |
              +---------------+----------------+
                              v
              kdcube init --descriptors-location <dir> --build
                              |
                              v
       ~/.kdcube/kdcube-runtime/<tenant>__<project>/
       config/*.yaml + repo/ + compose/env files + data/
                              |
                              v
                         kdcube start
                              |
                              v
                  http://localhost:<port>/platform/chat
```

`init` creates the workdir, stages descriptors into `workdir/config`, prepares
env/compose files, and optionally builds images. It refuses to silently reuse an
already initialized workdir.

### Refresh Platform Runtime

Use `refresh` for an already initialized runtime. It preserves staged
descriptors.

```text
existing runtime workdir
config/*.yaml  ----------------------------- preserved
      |
      | optional platform source selector
      | none / --path / --upstream / --latest / --release
      v
kdcube refresh [selector] --build
      |
      +-- with --path: copy that local checkout into workdir/repo first
      +-- with --upstream/--latest/--release: update workdir/repo to that ref
      +-- with no selector: rebuild the already staged/recorded source
      +-- with --build: rebuild images
      +-- unless --no-restart: restart the stack
```

`kdcube refresh --build` does not copy the current shell checkout by itself.
Pass `--path /path/to/kdcube-ai-app` when the runtime should rebuild from that
local checkout. Use exactly one of `--path`, `--upstream`, `--latest`, or
`--release <ref>` when the platform source should change.

### Apply Bundle Config And Reload

Use `bundle config apply` when the user intentionally wants seed
`bundles.yaml` / `bundles.secrets.yaml` to replace the active runtime bundle
descriptor copy. Use `bundle reload` when the active runtime descriptor or
bundle source already changed and proc only needs to reload it.

```text
seed content descriptors
bundles.yaml + bundles.secrets.yaml
              |
              v
kdcube bundle config apply --descriptors-location <dir> [--dry-run]
              |
              v
active runtime bundle descriptors
workdir/config/bundles.yaml + bundles.secrets.yaml
              |
              v
kdcube bundle reload <bundle_id>
              |
              v
proc clears bundle cache and reloads code/config on the next request
```

`bundle config apply` does not rebuild platform images, restart Docker, or
touch `assembly.yaml`, `gateway.yaml`, or `secrets.yaml`. With `--reload`, it
also reloads the changed bundle ids after staging descriptor changes.

### Export Before Replacing Live Bundle State

```text
live runtime bundle authority
workdir/config/bundles.yaml + bundles.secrets.yaml
              |
              v
kdcube export --out-dir <dir>
              |
              v
portable bundle descriptors for review
bundles.yaml + bundles.secrets.yaml
```

Export writes bundle descriptors only. Local non-git bundle paths are normalized
back to host paths; git-backed entries keep repo/ref/subdir and drop incidental
materialized runtime paths.

## If Your Role Is Configurator Or Deployer

Use this page differently depending on the job.

### Configurator

Use this page to answer:

- which descriptor file should carry this value
- which values are staged into the runtime
- which values stay deployment-owned vs bundle-owned
- when editing the source descriptor folder is not enough because the runtime
  already has staged live files

But decide the actual scope first in:

- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

### Deployer

Use this page to answer:

- how to point one deployment at one bundle path or git ref
- how `--workdir` resolves
- when to rerun install versus using `kdcube bundle reload`
- how to inspect one runtime and how to avoid changing the wrong deployment
- how to think about one active local deployment versus many runtime snapshots

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

### 2. `kdcube init --descriptors-location` stages the descriptor set into the runtime

When you run `kdcube init --descriptors-location`, the CLI copies the canonical descriptor set into:

```text
<runtime>/config/
```

After that, the runtime uses the staged copies.

That means:

- the source descriptor directory is an input to install/update
- the staged files under `workdir/config/` are the live local runtime authority
- editing the source directory later does nothing by itself; for bundle
  descriptors only, the user can intentionally reapply seed
  `bundles.yaml` / `bundles.secrets.yaml` with
  `kdcube bundle config apply`

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

For broader deployment-first CLI design context, use:

- [how-to-configure-and-run-bundle-new-cli-README.md](how-to-configure-and-run-bundle-new-cli-README.md)
- [cli--as-control-plane-README.md](../../../service/cicd/design/cli--as-control-plane-README.md)

## Running Multiple KDCubes On One Machine

This is the important distinction:

- many local deployment snapshots on disk: yes
- many concurrently running local compose-backed KDCube stacks: no, not as a
  supported default

### 1. Many runtime directories on disk are already supported

One machine can hold many initialized local deployment snapshots, for example:

```text
workspace/
  tenant1__project1/
  tenant1__project2/
```

That is already part of the current `tenant/project` isolation model.

Each runtime snapshot keeps its own:

- staged descriptor set under `config/`
- local platform snapshot
- local PostgreSQL/Redis data
- local bundle props and bundle secrets authority

### 2. Many active local KDCubes are not the current supported mode

The current local compose workflow should be treated as one active deployment at
a time per machine.

Practical reason:

- the compose runtime still comes from one shared compose tree
- the local stack publishes fixed host ports such as PostgreSQL, Redis,
  ingress, and processor ports
- the current CLI does not define a multi-instance local contract with
  per-deployment compose project names and per-deployment port ranges

So a second explicit `--workdir` gives you a second deployment snapshot on disk,
not a clean guarantee of a second isolated running local KDCube stack.

### 3. What happens today

If multiple namespaced runtimes exist under one parent workspace and you pass
only the parent workspace, the CLI refuses to guess and requires the concrete
namespaced runtime path.

If you explicitly target another initialized workdir, the CLI can operate on
that workdir as a filesystem snapshot, but you should not assume this means two
independent local KDCubes can safely run side by side.

Operational rule today:

- use many workdirs for many deployment snapshots
- run one local compose-backed deployment at a time

### 4. Desired behavior

The desired local behavior is:

1. one machine can hold many deployment snapshots on disk
2. local `start` must target exactly one resolved deployment
3. if another local deployment is already running, `start` should refuse with a
   clear message and tell the operator which deployment is active
4. `stop` should affect only the targeted deployment
5. if the platform later adds true concurrent local deployments, that must be
   an explicit advanced mode with:
   - per-deployment compose project naming
   - per-deployment published port ranges
   - explicit runtime discovery and stop semantics

### 5. Local vs remote deployment targeting

The CLI should still let one machine manage many deployments.

That means:

- the CLI can target many deployments
- some of those deployments may be remote/cloud

It does not mean:

- many local compose-backed deployments should all run at once by default

## Config And Secret Scopes In The Local Runtime

Use this as the quick decision table for bundle development.

All rows below are inside one current `tenant/project` runtime sandbox.

For the exact helper contract and cloud-mode differences, use:

- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [runtime-configuration-and-secrets-store-README.md](../../../configuration/runtime-configuration-and-secrets-store-README.md)

| Scope | Typical examples | Read / write API | Live authority in the local runtime | Export / ejection path |
|---|---|---|---|---|
| platform/global props | ports, auth ids, storage backends, path roots | `get_settings()` for effective values; `get_plain("...")` for raw descriptor inspection; no supported write API from bundle code | staged `assembly.yaml` and `gateway.yaml` under `workdir/config/`, plus env | not part of `kdcube export`; manage through the deployment descriptor set |
| platform/global secrets | deployment-wide API keys, auth secrets | async read: `await get_secret_async("canonical.key")`; no supported write API from bundle code | `secrets.yaml` only when `secrets-file` is active; otherwise the configured secrets provider | not part of `kdcube export`; manage through deployment secret workflows |
| deployment-scoped bundle props | feature flags, cron expressions, model selection, bundle UI config | read: `self.bundle_prop(...)`; write: `await set_bundle_prop(...)` | `workdir/config/bundles.yaml` when file-backed descriptor mode is active, with Redis as runtime cache | exported by `kdcube export` to `bundles.yaml` |
| deployment-scoped bundle secrets | webhook secrets, shared API tokens, bundle-specific credentials | async read: `await get_secret_async("b:...")`; write: `await set_bundle_secret(...)` | `workdir/config/bundles.secrets.yaml` only in local `secrets-file` mode; otherwise the configured secrets provider | exported by `kdcube export` to `bundles.secrets.yaml` when the provider/export flow can reconstruct them |
| user-scoped bundle props | one user's preferences or bundle-managed non-secret state | read/write: `get_user_prop(...)`, `set_user_prop(...)`, `delete_user_prop(...)` | PostgreSQL user bundle props table | never exported |
| user-scoped bundle secrets | one user's personal tokens or credentials managed by the bundle | async read/write: `await get_user_secret_async(...)`, `await set_user_secret_async(...)`, `await delete_user_secret_async(...)` | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | never exported |

In the user-scoped rows, "user" means the bundle user scope, not necessarily a
KDCube control-plane account. A KDCube-authenticated widget may use the KDCube
user id, while a public Telegram Mini App may use a bundle-approved Telegram
scope or another stable external identity. Configure deployment credentials in
bundle secrets; keep personal OAuth tokens and preferences under the resolved
bundle user scope.

Two hard rules:

- `kdcube export` is a bundle-state export only. It exports `bundles.yaml` and `bundles.secrets.yaml`. It does not export `assembly.yaml`, `gateway.yaml`, `secrets.yaml`, user props, or user secrets.
- Bundle Admin writes live deployment-scoped bundle state only. It does not rewrite platform/global deployment descriptors.
- Sync secret helpers are compatibility APIs. In async bundle code, use
  `get_secret_async(...)`, `get_user_secret_async(...)`,
  `set_user_secret_async(...)`, and `delete_user_secret_async(...)`.

### Agent Role Model Configuration

Model selection for SDK agents is ordinary bundle config when it should be
durable for the deployment:

```yaml
items:
  - id: my.bundle@1-0
    config:
      role_models:
        report.writer:
          provider: anthropic
          model: claude-sonnet-4-6
        report.writer.lite:
          provider: anthropic
          model: claude-haiku-4-5
        solver.react.v2.decision.v2.regular:
          provider: anthropic
          model: claude-sonnet-4-6
```

Use this for environment-level policy. It is read from effective bundle props,
can be changed by descriptor/admin overrides, and survives reload.

Do not use descriptor props for a one-off user choice such as "run this API call
with lite/regular/strong". For that, the bundle should put
`role_models` in `bundle_call_context` around the current `@api`, `@mcp`,
`@cron`, `@on_message`, or `@on_job` execution. See
[bundle-agent-integration-README.md#model-selection-for-agent-roles](../bundle-agent-integration-README.md#model-selection-for-agent-roles).

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
- main UI
- scheduled jobs
- bundle-scoped config
- bundle-scoped secrets

So a bundle is not only one backend handler and not only one frontend widget.

Widget React apps are configured like main UI apps: the descriptor/effective
bundle config can enable `ui.widgets.<alias>` and the bundle code should
provide `src_folder` plus `build_command` defaults when the widget has source.
The processor/bundle-loader infra builds the source folder into shared bundle
storage and serves widget subpaths from the built app. Do not configure widgets
by pointing the platform directly at a built file.

Important descriptor/default boundary:

- `configuration_defaults()` is the right place for bundle-owned stable
  defaults used by `BaseEntrypoint` and workflow-side rebuild logic
- the current route-time static widget serving path evaluates effective bundle
  props after code defaults and descriptor/admin props are merged
- `bundles.yaml` still stores only descriptor/admin props; code defaults are
  not materialized into the descriptor unless an operator explicitly writes or
  resets them
- descriptors may repeat `src_folder` and `build_command` when a seed file must
  be self-documenting or support an older runtime, but the current runtime
  contract does not require repeating intrinsic widget defaults there
- if `/widgets/<alias>` or `/public/widgets/<alias>` says the widget has no
  built/static app, inspect effective props and bundle load/build logs before
  assuming descriptor values are missing

For React/Vite widgets, use the build command contract from
[bundle-widget-integration-README.md#source-folder-widget-apps](../bundle-widget-integration-README.md#source-folder-widget-apps):

```yaml
build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
```

The widget Vite config must write to `process.env.OUTDIR`; do not pass the
temporary output directory as a positional `vite build` argument. If runtime logs
show `vite build /.../.ui.build.tmp...`, the widget build contract is wrong or
an older platform runner is active.

This is per widget alias. Configuring
`ui.widgets.task_memo_webapp.src_folder/build_command` only changes the
`task_memo_webapp` route. Inherited legacy widgets such as `ai_bundles` keep
calling their decorated Python method unless `ui.widgets.ai_bundles`
also defines `src_folder` and `build_command`.

Reusable SDK widget UI uses build-time materialization. It is not an npm
package and not a runtime import from the monorepo.

If the widget imports SDK-owned UI code, the widget config must materialize the
same source through `shared_sources`. For built-in/reference bundles, this
should usually live in the bundle's `configuration_defaults()` so descriptors
only need `enabled: true`. The descriptor can still repeat the values when you
want the seed file to be self-documenting or to override defaults.

The required shape is:

```yaml
ui:
  widgets:
    versatile_webapp:
      enabled: true
      src_folder: ui/widgets/versatile_webapp
      build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
      shared_sources:
        memory_widget:
          src_folder: sdk://context/memory/ui/widget/memories
          target: _shared/memory-widget
        telegram_widget:
          src_folder: sdk://integrations/telegram/ui/widget.telegram
          target: _shared/telegram-widget
```

Use `sdk://...` for reusable descriptors. Absolute local paths are only for
temporary development.

Failure signal:

```text
Could not load /integrations/telegram/ui/widget.telegram/src/index.tsx
```

This means the widget imported `@kdcube/telegram-widget`, but
`sdk://integrations/telegram/ui/widget.telegram` was not materialized to the
target expected by the Vite alias. The same rule applies to
`@kdcube/memory-widget` and `sdk://context/memory/ui/widget/memories`.

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
- whether built-in example bundles are included in the effective runtime registry

For local path bundles, `assembly.yaml` is where the host-side roots belong.
It also carries platform-level ReAct caps and proc watchdog settings such as
`ai.react.context_max_tokens`, `ai.react.read_visible_*`, and
`platform.services.proc.service.chat_task_*`. Treat those as environment
operator controls, not bundle props. Use
[assembly-descriptor-README.md](../../../configuration/assembly-descriptor-README.md)
and
[service-runtime-configuration-mapping-README.md](../../../configuration/service-runtime-configuration-mapping-README.md)
for the exact mapping.

### `bundles.yaml`

`bundles.yaml` controls bundle definitions and deployment-scoped non-secret bundle props:

- which bundles exist
- which one is default
- whether a bundle is local-path or git-backed
- bundle props under `config:`

Operationally, this is the file that says which application modules are present
inside the current `tenant/project` environment.

Built-in example bundles are governed by the deployment switch in `assembly.yaml`:

- `platform.applications.bundles.bundles_include_examples: true` includes all packaged example bundles in the effective runtime registry
- `platform.applications.bundles.bundles_include_examples: false` keeps packaged examples disabled
- an item in `bundles.yaml` for a packaged example may carry config/props, but it is not the enable switch
- if examples are disabled, mentioning an example id in `bundles.yaml` must not make that example available

Memory-enabled bundles use `config.memory` plus the Memory widget flag:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        memory:
          enabled: true
          announce: {enabled: true, limit: 6, scope_filter: current_bundle}
          tools: {enabled: true, allow_write: false, default_scope_filter: current_bundle}
          widget: {enabled: true, allow_write: true, default_scope_filter: current_bundle}
          reconciliation: {enabled: true}
          snapshots: {enabled: true}
        ui:
          widgets:
            memories:
              enabled: true
```

`memory.enabled` gates the subsystem. `announce` is read-only context for ReAct,
`tools` controls memory search/read/write tools, `widget` controls user CRUD,
and reconciliation/snapshots control maintenance and restore flows. The bundle
must derive from the memory entrypoint mixin for this config to have effect.

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

## Public Local Runtime With Ngrok

Use [Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md)
when a local KDCube must be reachable by an external provider while it still
runs on localhost.

Typical cases:

- Telegram webhook delivery to a local bundle
- Telegram Mini App or other external browser callbacks
- Cognito/OAuth callback testing against a local frontend
- remote-control or callback-style integrations that need a public HTTPS URL

For the bundle-side Telegram wiring, use
[Telegram SDK Integration](../../integrations/telegram/telegram-README.md).
For BotFather, `setWebhook`, and Mini App setup, use
[Telegram External Prerequisites](../../integrations/telegram/telegram-external-prereq-README.md).

The ngrok flow uses one public HTTPS origin through a local reverse proxy:

```text
https://<ngrok-domain>
  -> local reverse proxy
      /api/integrations/* -> proc
      /sse/*, /api/*      -> ingress
      /*                  -> frontend
```

Do not expose proc as a separate public URL. Keep this descriptor-driven:

- `assembly.yaml` owns CORS, frontend browser config, auth, and ports
- `bundles.yaml` owns bundle public integration URLs such as Telegram
  `webhook_url`
- `bundles.secrets.yaml` or the configured secrets provider owns bot tokens,
  webhook secrets, OAuth client secrets, and related secret material

Restart ingress after `assembly.yaml` changes. Restart or reload proc after
bundle descriptor or bundle-secret changes.

## Recommended Local Workflow

Use a canonical descriptor directory and let `kdcube init` stage it into the runtime.

Before running bundle tests or interpreting failures, use the working
environment checklist in
[how-to-test-bundle-README.md#1a-working-environment-for-agents](how-to-test-bundle-README.md#1a-working-environment-for-agents).

Recommended command shape:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors
```

`--tenant`/`--project` is the primary form — the CLI composes the runtime
path under the platform default base
(`~/.kdcube/kdcube-runtime/<tenant>__<project>/`). For non-default placements,
use `--workdir <full-path>` or `--workdir-base <base> --tenant T --project P`.

When the local run needs platform service keys, stage them during init with
dotted descriptor keys:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --set-secret services.openai.api_key "sk-..." \
  --set-secret services.anthropic.api_key "sk-ant-..." \
  --set-secret services.brave.api_key "..." \
  --set-secret services.git.http_token "github_pat_..." \
  --set-secret git.http_token "github_pat_..."
```

For guided secret entry, use:

```bash
kdcube init --tenant <t> --project <p> --prompt-secrets
```

These values are written to the active runtime `config/secrets.yaml`, not to
`.env` files. They are applied to the staged runtime descriptor copy during
`init`. To set or rotate secrets on an *existing* runtime later, use
`kdcube bundle <id> --set-secret KEY VALUE` for bundle-scoped secrets, or
edit `<workdir>/config/secrets.yaml` directly for platform-global secrets.
Re-running `kdcube init` on an existing workdir is refused (it would be
ambiguous about whether to reseed); for a clean reseed, remove the workdir
first.

For delegated/proxy-login or hosted descriptors, the CLI stages concrete
runtime descriptors from seed descriptors. Placeholders such as tenant,
project, and domain values must be resolved in the staged runtime config before
the services start. After init, verify the active target with:

```bash
kdcube info --tenant <t> --project <p>
```

ReAct round limits can be set globally or per bundle:

- global runtime default: `ai.react.max_iterations` in `assembly.yaml`, or
  `AI_REACT_MAX_ITERATIONS` in env
- per-bundle override: `config.react.max_iterations` or
  `react.max_iterations` in the bundle's deployment-scoped props

The per-bundle prop wins over the assembly/env default. If neither is set, the
runtime fallback is `15`.

Live ReAct thinking rendering follows the same precedence:

- global runtime default: `ai.react.render_thinking` in `assembly.yaml`, or
  `AI_REACT_RENDER_THINKING` in env
- per-bundle override: `config.react.render_thinking` or
  `react.render_thinking` in the bundle's deployment-scoped props

This only controls whether live `react.thinking` blocks are rendered into the
active ReAct timeline. Pruned/compacted historical thinking is never rendered.

Rendered prompt snapshot debugging is separate from thinking visibility. Keep
`ai.react.debug_timeline: false` for normal deployments; enable it only when
you need to inspect exactly what the ReAct runtime sent to the model. A bundle
can override with `config.react.debug_timeline` or `react.debug_timeline`.

Then start the initialized runtime:

```bash
kdcube start --tenant <t> --project <p>
```

Without `--build`, `init` stages descriptors and generates runtime env files.
The platform source/ref is selected using:

- `assembly.platform.ref`
- or `--latest`
- or `--release <ref>`
- or `--upstream`
- or explicit `--path /abs/path/to/kdcube-ai-app` when you intentionally want
  to test a dirty local platform checkout

`--tenant`/`--project` (or `--workdir`/`--workdir-base` in advanced placements)
answers where the runtime should be installed. `--path` answers which local
platform source tree should be staged for this runtime. In descriptor-driven
`init`, explicit `--path` without `--upstream`, `--latest`, or `--release`
copies tracked files plus untracked-but-not-ignored files into the namespaced
runtime workdir and uses that staged copy. Gitignored runtime/data files are
not copied.

Use `init --build` when you want images prepared before starting. After the
runtime exists, to rebuild images on platform-source updates, use
`kdcube refresh --tenant <t> --project <p> --build` — it never touches staged
descriptors. Normal operator flow is:

1. `init` prepares the runtime (once)
2. `init --build` optionally prebuilds images on the first run
3. `start` starts containers
4. `refresh --build` re-runs the build/restart cycle on later platform updates

`refresh` accepts the same platform source selectors as `init`. Use
`kdcube refresh --tenant <t> --project <p> --latest --build`,
`--upstream --build`, or `--release <ref> --build` when an already-initialized
runtime should move to another platform ref without restaging descriptors.
Explicit `--path` without one of those selectors restages dirty local platform
source into `<workdir>/repo` before rebuilding.

### Initialize from `assembly.platform.ref`

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors
```

Use this when you want the normal local runtime based on a released platform version.

### Initialize from an explicit release

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --release 2026.4.23.17
```

### Initialize from the latest known platform release

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --latest
```

### Prebuild images from a released platform ref

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --build
```

Use this when you want to build locally from the selected release source before
starting containers.

### Prebuild images from dirty local platform sources

```bash
kdcube init --tenant <t> --project <p> \
  --path /abs/path/to/kdcube-ai-app \
  --descriptors-location /abs/path/to/descriptors \
  --build
```

Use this when you need to test uncommitted platform changes. The CLI copies the
local checkout into the concrete runtime workdir and builds from that staged
copy.

### Initialize from upstream `origin/main`

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --upstream
```

### Prebuild images from upstream `origin/main`

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /abs/path/to/descriptors \
  --upstream \
  --build
```

Important:

- `--upstream` selects the upstream source/ref and does not require `--build`
- `--build` on `init` builds images after staging the runtime and does not start containers
- `--upstream` requires `--descriptors-location` for fresh init
- explicit `--path` without `--upstream`, `--latest`, or `--release` is the dirty-local-source flow
- to rebuild images on an *already-initialized* runtime later, run
  `kdcube refresh --tenant <t> --project <p> --build` (descriptors are
  preserved). Add `--latest`, `--upstream`, or `--release <ref>` to refresh the
  existing runtime to another platform source while still preserving staged
  descriptors.

Use this when you are validating current platform source, not when you only need to update bundle descriptors.

## Inspecting The Runtime You Already Have

### Show active runtime info

```bash
kdcube info --tenant mytenant --project myproject
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
kdcube stop --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject
```

With volumes removed:

```bash
kdcube stop --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject --remove-volumes
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
      path: "/Users/you/src/my-repo/src/my_bundle"
      module: "entrypoint"
```

This means:

- in a seed/source local descriptor, `path` is the host-visible bundle root
- after CLI init/staging for a Docker runtime, the runtime copy under
  `workdir/config/` may be rewritten to the container-visible mount path
- `module` is resolved inside that root

For a brand-new bundle skeleton, keep the documentary descriptor shapes inside
the bundle as `config/bundles.template.yaml` and
`config/bundles.secrets.template.yaml`, then copy/adapt those values into the
active deployment descriptors. The skeleton checklist lives in
[how-to-write-bundle-README.md#1b1-new-bundle-skeleton-checklist](how-to-write-bundle-README.md#1b1-new-bundle-skeleton-checklist).

Do not keep these on the same entry:

- `repo`
- `ref`
- `subdir`

### Git bundles

Use a git-backed bundle when you want pinned, managed, versioned delivery.

There are two valid shapes.

Bundle-root shape:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      repo: "git@github.com:org/repo.git"
      ref: "2026.4.23.17"
      subdir: "src/my_bundle"
      module: "entrypoint"
```

Parent-subdir shape:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      repo: "git@github.com:org/repo.git"
      ref: "2026.4.23.17"
      subdir: "src"
      module: "my_bundle.entrypoint"
```

The parent-subdir shape is useful when a repo contains multiple bundles under
one source parent. Bundle code, `tools_descriptor.py`, and bundle-local tool
modules must use package-relative bundle-local imports and must not use
top-level package fallbacks for bundle-local folders. Bundle-local tool specs
should use `ref: "tools/name.py"` so the tool subsystem can keep them tied to
the bundle root and rewrite them for isolated/distributed execution. The
authoring rule is in
[how-to-write-bundle-README.md#1b2-bundle-local-import-rule](how-to-write-bundle-README.md#1b2-bundle-local-import-rule),
and the runtime rationale is in
[bundle-runtime-README.md#critical-bundle-local-import-rule](../bundle-runtime-README.md#critical-bundle-local-import-rule).

When cutting a new git-backed bundle ref, use the optional public release
procedure in
[how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md)
before updating the active descriptor `ref`.

`module` is a Python import path. Dots are package separators. If the bundle
directory name itself contains dots, prefer the bundle-root shape unless the
directory layout intentionally matches the dotted package path.

Do not mix local-path and git fields on the same bundle entry.

## Host Paths vs Runtime Paths

This is the most common source of mistakes.

There are two descriptor copies in the local workflow:

| Descriptor copy | Typical consumer | Local bundle `path` form |
| --- | --- | --- |
| seed/source descriptor under `deployment/cicd/.../descriptors/...` | CLI init input, host-side IntelliJ/proc runs | host-visible path, for example `/Users/you/src/my-repo/src/my_bundle` |
| staged runtime descriptor under `workdir/config/` | the running initialized runtime | whatever the runtime can see; Docker runs may use rewritten `/bundles/...` paths |

If the host bundle path in a seed descriptor is:

```text
/Users/you/src/my-repo/src/my_bundle
```

the CLI can stage/rewrite the runtime copy to:

```text
/bundles/my-repo/src/my_bundle
```

So:

- do not hand-edit seed descriptors to `/bundles/...` when they are also used
  by host-side runs
- do not hand-edit staged Docker runtime descriptors back to `/Users/...` if
  the processor consuming them runs inside the container
- always check which descriptor copy you are editing before fixing path bugs

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

Use `kdcube info` to verify both host/container mappings.

Built-in examples live in the managed-bundle path when the deployment includes
examples. They do not need one `bundles.yaml` item per example unless you are
supplying deployment-scoped config for that example.

## Applying Changes Correctly

### User Flow: Apply Seed Bundle Descriptors

This is a user/operator flow. It is not an autonomous agent bootstrap step. Use
it only when the user intentionally edited `bundles.yaml` or
`bundles.secrets.yaml` in the source descriptor directory passed via
`--descriptors-location` and wants to reapply only those bundle content
descriptors to the existing runtime.

Agents may explain this path and prepare or run a dry-run. They should run the
write/reload form only when the user explicitly grants permission to apply the
selected seed descriptors on the user's behalf.

```bash
kdcube bundle config apply \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id> \
  --descriptors-location /abs/path/to/descriptors \
  --dry-run

kdcube bundle config apply \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id> \
  --descriptors-location /abs/path/to/descriptors \
  --reload
```

`bundle config apply` stages only `bundles.yaml` and, when present in the
source directory, `bundles.secrets.yaml` into the active runtime config
directory. It does not rebuild images, restart Docker, or modify
`assembly.yaml`, `secrets.yaml`, or `gateway.yaml`. If the source directory does
not contain `bundles.secrets.yaml`, the existing runtime secrets descriptor is
preserved. Host local bundle paths in the source descriptor are translated to
the runtime-visible `/bundles/...` path before writing the active runtime
descriptor.

### If you changed `bundles.yaml` or `bundles.secrets.yaml` inside the active runtime

You can edit the staged files directly:

```text
<runtime>/config/bundles.yaml
<runtime>/config/bundles.secrets.yaml
```

For targeted config or secret changes, use `kdcube bundle` instead of editing
YAML by hand:

```bash
kdcube bundle <bundle_id> \
  --set-config key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>

kdcube bundle <bundle_id> \
  --set-secret key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>

kdcube bundle <bundle_id> \
  --del-config key.path \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

Apply either kind of change with:

```bash
kdcube bundle reload <bundle_id> --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`reload`:

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

Rerun install or refresh the runtime topology, not only `kdcube bundle reload`.

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
kdcube bundle reload my.bundle@1-0 --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject
```

Use a full reinstall only when code changes depend on wider runtime/platform changes.

### If you changed a custom main-view UI source

For `ui.main_view` bundles, source changes belong in the bundle `ui/main`
directory.

Do not fix stale UI by manually building into:

```text
<bundle_storage_root>/ui
```

The supported path is:

- the bundle UI requests the HTML entrypoint through `/api/integrations/static/{tenant}/{project}/{bundle_id}`
- the bundle UI loader checks the `ui/main` signature
- the loader builds into bundle storage when needed
- the static route serves the refreshed hashed assets

After changing `ui/main`, reload or reselect the bundle so the bundle UI requests
the HTML entrypoint again. If the UI is still stale, inspect loader logs and the
served hashed asset before changing runtime storage manually.

### If you changed a file-producing tool

No descriptor flag enables file hosting for a tool. The contract is part of the
React/tool runtime.

The tool must either:

- return `{"ok": true, "ret": {"artifact_type": "files", "files": [...]}}`
- or call `host_files(...)` from a trusted bundle/catalog tool after it
  materializes the files

The runtime must have normal conversation storage for hosted file links to be
created. Generated executor code should call a catalog tool through
`agent_io_tools.tool_call(...)` when it needs files. `host_files(...)` is for
trusted bundle/catalog tools.

`host_files(...)` also requires prepared runtime scope: an active
`ToolSubsystem` with hosting service, tenant, project, user id, conversation id,
turn id, conversation storage, and output directory. Normal React workflows
prepare this through `BaseWorkflow.build_react(...)`; isolated execution
prepares it through `bootstrap_bind_all(...)`. Without that prep the helper
raises a runtime error instead of creating an unscoped artifact.

## Bundle Props, Secrets, And Canonical `enabled.*`

Deployment-scoped non-secret bundle config goes in `bundles.yaml`.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/Users/you/src/my-repo/src/my_bundle"
      module: "entrypoint"
      config:
        api:
          header_name: "X-My-Bundle-Token"
        enabled:
          api:
            "admin_export.POST": false
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

To patch a config or secret key without editing the files by hand, use `kdcube bundle` against the staged runtime descriptors:

```bash
# Set a config value
kdcube bundle <bundle_id> \
  --set-config key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>

# Set a secret
kdcube bundle <bundle_id> \
  --set-secret key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>

# Delete a config or secret key (raises an error if the key does not exist)
kdcube bundle <bundle_id> --del-config key.path \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
kdcube bundle <bundle_id> --del-secret key.path \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`kdcube bundle` only patches the staged files. It does not edit the original
source descriptor directory. Run `kdcube bundle reload` afterward to apply the
change.

Feature switches for bundle surfaces live under `enabled.*` in bundle props,
not in secrets. The platform derives the canonical path from decorator
metadata:

| Decorator | Canonical bundle-props path |
| --- | --- |
| `@bundle_entrypoint(...)` | `enabled.bundle` |
| `@api(alias=A, method=M, route=R, ...)` | `enabled.api["R.A.M"]` (flat key) |
| `@mcp(alias=A, ...)` | `enabled.mcp.A` |
| `@ui_widget(alias=A, ...)` | `enabled.widget.A` |
| `@cron(alias=A, ...)` | `enabled.cron.A` |

Treat this section as deployment overrides over code defaults. Bundle code,
decorators, and bundle `configuration_defaults()` define the default enabled
state. Descriptor config is needed only when a deployment intentionally
overrides that default, most commonly `false` for a rare disable. Do not mirror
every enabled API/widget/cron as `true`.

Example bundle code and props pair:

```python
@api(
    alias="admin_export",
    route="operations",
    method="POST",
    user_types=("privileged",),
)
async def admin_export(self, **kwargs):
    return {"ok": True}
```

```yaml
config:
  enabled:
    api:
      "admin_export.POST": false
```

Operational behavior:

- missing key means the code default applies
- `false`, `0`, `disable`, `disabled`, and `off` disable the surface
- resetting an explicit enabled override should remove/null the key, not write
  a permanent `true`

After changing the prop, apply it with:

```bash
kdcube bundle reload my.bundle@1-0 --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject
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
export OUT_DIR=/tmp/live-bundles

kdcube export \
  --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject \
  --out-dir "$OUT_DIR"

ls -lh "$OUT_DIR"
```

In local descriptor-backed mode, this exports from the active runtime workspace descriptor files.
Export normalizes runtime paths back to seed-descriptor shape:

- local non-git bundle paths such as `/bundles/...` are translated back to host
  paths using `assembly.yaml` / `.env` bundle mount mappings
- git-backed bundle descriptors keep `repo` / `ref` / `subdir`; an incidental
  materialized `path` is removed from the export

This makes the export usable as a reviewed source descriptor update instead of
leaking container-only paths into reusable host descriptors.

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
- user scope does not have to be a KDCube account id; public integrations may
  resolve a bundle-owned external identity such as a Telegram user
- descriptors may configure how external users are allowed or mapped, but the
  resulting per-user state remains runtime state

Descriptor export/reload is for deployment-scoped bundle config, not per-user business state.

## First-Run Bootstrap vs Existing Runtime

### Existing runtime

Use the existing runtime when you want to:

- keep current config and data
- keep the current staged runtime descriptors
- validate a new platform source build against the current runtime

Example:

```bash
kdcube init \
  --workdir ~/.kdcube/kdcube-runtime/mytenant__myproject \
  --upstream \
  --build
```

This reuses the initialized runtime, refreshes the platform source from
upstream, and rebuilds images without starting containers. Run `kdcube start`
afterward when you want to start the stack.

### Fresh runtime

Use a new workdir when you want to test:

- first-run bootstrap
- default descriptor seeding
- clean local runtime setup

But for normal bundle development, prefer a descriptor-driven initialized runtime instead of ad hoc manual prompting.

## Common Mistakes

- Editing the canonical descriptor source directory and expecting the running runtime to pick it up automatically.
- Forgetting that the staged runtime files under `workdir/config/` are the active local authority.
- Confusing seed/source descriptor paths with staged runtime paths. Local seed
  descriptors and host-side proc runs need host paths; Docker runtime copies may
  need rewritten `/bundles/...` paths.
- Treating `bundles.yaml` example config as the switch that enables built-in examples.
- Manually building a custom bundle UI into runtime storage instead of letting the bundle UI loader refresh it.
- Mixing `path` with `repo`/`ref`/`subdir` in the same bundle entry.
- Importing bundle-local folders as process-global top-level packages, such as
  `from services...`, `from apps...`, or `import tools`. Those names can
  collide across bundles in one processor process.
- Expecting `--upstream` to rebuild images. It only selects the upstream source/ref; add `init --build` to prebuild images.
- Assuming the base `--workdir` is the concrete runtime when the CLI has resolved a namespaced runtime under it.
- Using `kdcube bundle reload` before the stack is running.
- Overwriting live bundle-admin changes with stale descriptor source files.

## What To Remember

If you only remember the essentials, remember these:

- the active local runtime authority is under `workdir/config/`
- `--descriptors-location` stages descriptor files into that runtime
- `bundles.yaml` owns bundle definitions and non-secret deployment props
- `bundles.secrets.yaml` owns deployment-scoped bundle secrets
- `bundles_include_examples` in `assembly.yaml` owns built-in example availability
- local path bundle entries must match the descriptor consumer: host paths for
  seed/source descriptors and host-side proc runs, runtime-visible paths for
  staged Docker-consumed runtime copies
- custom main-view UI source is rebuilt by the bundle UI loader, not by manual runtime-storage builds
- file-producing tools use the React/tool runtime file contract, not a
  `bundles.yaml` switch
- rerun install or refresh runtime topology when platform descriptors changed
- use `kdcube bundle config apply --descriptors-location <dir> --dry-run`, then
  `--reload`, when the user intentionally changed source
  `bundles.yaml` / `bundles.secrets.yaml` and wants to reapply them to an
  existing runtime without a platform refresh
- use `kdcube bundle <bundle_id> --set-config / --set-secret / --del-config / --del-secret` for targeted staged config or secret patches
- use `kdcube bundle reload <bundle_id>` when you changed active runtime bundle descriptors or need proc cache eviction
- use `kdcube info --workdir <path>` to inspect the runtime you are actually using
- use `kdcube export` before overwriting runtime bundle state with older descriptor copies

For the exact read/write helper contract behind those rules, use:

- [bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
