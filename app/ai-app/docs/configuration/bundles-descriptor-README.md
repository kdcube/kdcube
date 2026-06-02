---
id: ks:docs/configuration/bundles-descriptor-README.md
title: "Bundles Descriptor"
summary: "Bundle registry and non-secret bundle deployment configuration in bundles.yaml: default bundle, git or local bundle sources, module paths, and bundle-scoped config."
tags: ["service", "configuration", "bundle", "bundle-registry", "deployment", "descriptor"]
keywords: ["bundle registry", "default bundle selection", "git bundle source", "local path bundle source", "bundle module mapping", "bundle configuration", "bundle inventory", "file-backed bundle authority", "bundle reload workflow", "deployment bundle catalog"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/configuration/service-runtime-configuration-mapping-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
---
# Bundles Descriptor

`bundles.yaml` defines:

- which bundles exist
- which bundle is the default
- non-secret bundle descriptor fields such as:
  - `repo`
  - `ref`
  - `subdir`
  - `module`
  - `path`
  - `config`

It does not carry bundle secrets.

This page is about the descriptor contract itself.
For the operational local workflow for reusing a runtime, changing bundle roots, and running `kdcube`, use:

- [how-to-configure-and-run-bundle-README.md](../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| raw value from `bundles.yaml` | `read_plain("b:...")` / `get_plain("b:...")` | Reads the descriptor file directly |
| effective current bundle config | `self.bundle_prop("...")` | Reads the active runtime bundle authority, not the raw file |
| startup reseed from bundle descriptor authority | `BUNDLES_FORCE_ENV_ON_STARTUP=1` | Proc resets the bundle registry from the current local descriptor authority on startup |

### File-resolution and runtime env vars

| Env var | Descriptor path or role | Primary API / behavior | Modes |
|---|---|---|---|
| `BUNDLES_YAML_DESCRIPTOR_PATH` | explicit `bundles.yaml` path | `read_plain("b:...")`, local descriptor authority | direct local service run; explicit override in compose/Kubernetes |
| `HOST_BUNDLES_DESCRIPTOR_PATH` | host file staged into `/config/bundles.yaml` | CLI installer mount | CLI local compose |
| `PLATFORM_DESCRIPTORS_DIR` | fallback directory containing `bundles.yaml` | descriptor file discovery | direct local service run |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | startup flag, not a YAML field | proc reseeds from current local descriptor authority | proc |
| `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | `assembly.yaml` proc bundle setting | periodic scheduler catch-up from active bundle descriptor authority; `0` disables | proc |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | git bundle items in `bundles.yaml` | enables git bundle resolution | proc |

### Descriptor fields that matter to runtime

| `bundles.yaml` field | Used by | Meaning |
|---|---|---|
| `bundles.default_bundle_id` | `read_plain("b:default_bundle_id")`, runtime routing | default bundle |
| `bundles.items[].id` | runtime registry | bundle identifier |
| `bundles.items[].repo` / `ref` / `subdir` / `module` | proc git resolution | git-backed bundle definition |
| `bundles.items[].path` / `module` | proc local-path loading | local development bundle definition |
| `bundles.items[].config` | `self.bundle_prop("...")` | non-secret effective bundle config |

### Reserved runtime config under `config`

Most values under `bundles.items[].config` belong only to the bundle. A small
set is platform-reserved and interpreted by the runtime.

`config.enabled` is the platform-owned feature gate override section for bundle
surfaces. Bundle code, decorators, and bundle `configuration_defaults()` define
the default state. Descriptor config only overrides those defaults. If a key is
absent here, the runtime uses the code default; if a key is present, that value
overrides the default. Most descriptors should contain only intentional
overrides, usually `false` for a rare disable.

Canonical keys:

| Surface | Key |
|---|---|
| bundle | `enabled.bundle` |
| API | `enabled.api["<route>.<alias>.<METHOD>"]` |
| MCP | `enabled.mcp.<alias>` |
| widget | `enabled.widget.<alias>` |
| cron | `enabled.cron.<alias>` |

Do not mirror every enabled API/widget/cron as `true` in deployment descriptors.
That makes descriptors noisy and can leave stale explicit overrides after code
defaults change.

`config.execution.runtime` controls per-bundle execution runtime routing and
per-run ISO runtime limits. `config.exec_runtime` is the legacy alias.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        execution:
          runtime:
            mode: "docker"              # none | local | docker | fargate | external
            container_strategy: "split" # optional: combined | split; docker mode only
            max_file_bytes: "50m"       # overrides assembly default for this bundle exec call
            max_exec_workspace_delta_bytes: "100m" # overrides assembly default for this bundle exec call
            max_workspace_bytes: "150m" # optional cap for the active workspace before finalization/offload
            workspace_monitor_interval_s: 0.5
            descriptor_payload_scope: active_bundle # optional: filter bundle descriptors sent to supervisor
```

Platform defaults for these limits live in `assembly.yaml` under
`platform.services.proc.exec`. Bundle overrides are applied only to the
execution run that uses that bundle profile.

By default, Docker/Fargate supervisors receive full descriptor payloads because
the supervisor is platform trusted. Set
`execution.runtime.descriptor_payload_scope: active_bundle` when you want
`bundles.yaml` and `bundles.secrets.yaml` narrowed to the caller bundle before
transport. Platform descriptors (`assembly.yaml`, `gateway.yaml`, global
`secrets.yaml`) remain full.

`config.memory` is the reserved User Memory subsystem config for bundles that
derive from the memory entrypoint mixin. It is deployment-scoped config, not the
user memory records themselves.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        memory:
          enabled: true
          announce:
            enabled: true
            limit: 6
            scope_filter: current_bundle # current_bundle | all_user_memories
          tools:
            enabled: true
            allow_write: false # keep read-only until durable writes are policy-approved
            default_scope_filter: current_bundle
            embedding_enabled: true
          widget:
            enabled: true
            allow_write: true
            default_scope_filter: current_bundle
            allow_all_user_memories: true
            ensure_schema: true
          reconciliation:
            enabled: true
          snapshots:
            enabled: true
        ui:
          widgets:
            memories:
              enabled: true
            versatile_webapp:
              shared_sources:
                memory_widget:
                  src_folder: sdk://context/memory/ui/widget/memories
                  target: _shared/memory-widget
```

`memory.enabled` gates the subsystem. `memory.announce` projects a read-only
hotset into ReAct announce context. `memory.tools` controls search/read/write
tools. `memory.widget` enables user-owned CRUD in the Memory widget.
`memory.reconciliation` and `memory.snapshots` control maintenance jobs and
restore points. `ui.widgets.memories.enabled` exposes the built widget
route; the memory mixin supplies the default source folder/build command.
`ui.widgets.<alias>.shared_sources` is optional and materializes reusable
SDK UI source into that widget build workspace; this is useful for external-git
bundles that want to mount platform widgets as direct React components.

`config.events` overrides the platform event recording defaults for this bundle.
Platform defaults come from `assembly.yaml -> events.record.*`. Bundle-level
fields are merged on top of assembly defaults field-by-field: a bundle can
override only `enabled`, only `selector`, or both. The `selector` list is
replaced as a whole when present — lists are not concatenated.

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        events:
          record:
            persist:
              enabled: true
              selector:
                - "accounting.usage"
                - "chat.turn.summary"
                - "chat.conversation.accepted"
            telemetry:
              enabled: true
              selector:
                - "accounting.usage"
```

| Key | Effect |
|---|---|
| `events.record.persist.enabled` | enables/disables `conv.artifacts.events` artifact for this bundle |
| `events.record.persist.selector` | event types saved into the artifact; replaces the assembly default list |
| `events.record.telemetry.enabled` | enables/disables telemetry sink flush for this bundle |
| `events.record.telemetry.selector` | event types shipped to the telemetry sink; replaces the assembly default list |

## Two supported bundle styles

### 1. Git bundles

Use:

- `repo`
- `ref`
- `subdir`
- `module`

This is the normal shape for:

- AWS deployment
- any non-local deployment
- local compose when you want the runtime to resolve bundles from git

Bundle-root example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "acme.marketing@2-0"
  items:
    - id: "acme.marketing@2-0"
      repo: "git@github.com:example-org/acme-platform.git"
      ref: "main"
      subdir: "src/marketing_bundle"
      module: "entrypoint"
      config:
        features:
          news: true
```

Parent-subdir example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "marketing-bundle@2-0"
  items:
    - id: "marketing-bundle@2-0"
      repo: "git@github.com:example-org/acme-platform.git"
      ref: "main"
      subdir: "src"
      module: "marketing-bundle@2-0.entrypoint"
      config:
        features:
          news: true
```

`module` is a Python import path, so dots are package separators. If the bundle
directory name contains literal dots, use the bundle-root shape unless the
filesystem layout intentionally mirrors the dotted package path.

### 2. Local path bundles

Use:

- `path`
- `module`

This is for local development only.

The correct `path` value depends on which descriptor copy is being consumed.

#### Local path bundles: exact rule

Seed/source descriptors under the repository descriptor set are often consumed
by both:

- CLI init/staging
- host-side processor runs, such as IntelliJ launches

For those seed/source descriptors, use the host-visible concrete bundle root:

```yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      path: "/Users/you/src/my-repo/src/my_bundle"
      module: "entrypoint"
```

When the CLI stages descriptors into a Docker-backed runtime, the staged copy
under `workdir/config/` may be rewritten to the runtime-visible mount path:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
```

Do not edit the seed/source descriptor to `/bundles/...` only because the
Docker runtime copy uses that shape. The seed descriptor may still need to run
from the host.

#### `path` and `module`: only two valid forms

There are exactly two supported shapes for local path bundles.

##### Preferred form: `path` is the bundle root

If `path` already points to the actual bundle directory, use:

```yaml
path: /Users/you/src/my-repo/src/my_bundle
module: entrypoint
```

This is the preferred form because it is explicit and does not depend on loader fallback behavior.

##### Secondary form: `path` is the parent directory

If `path` points to the parent directory that contains the bundle directory, use:

```yaml
path: /Users/you/src/my-repo/src
module: my_bundle.entrypoint
```

This works because bundle root resolution supports:

- `<path>/<module_base>`
- `<path>/<module_base as package path>`

Do not mix the two forms without a reason.

Avoid this hybrid form:

```yaml
path: /Users/you/src/my-repo/src/my_bundle
module: my_bundle.entrypoint
```

It may still load because of fallback behavior, but it is not the clear contract to rely on.

#### Local path bundle must stay a pure path bundle

If you switch a bundle to local `path:` mode, do not leave git fields on the same entry.

Do not mix:

- `path`
- `repo`
- `ref`
- `subdir`

on one bundle entry.

For a local path bundle, keep only:

- `id`
- `name` if needed
- `path`
- `module`
- `config`

## `bundles.yaml` by run mode

### CLI local compose

Authority:

- staged into `workdir/config/bundles.yaml`

Typical use:

- local path bundles while editing source code on the host
- or git bundles resolved into local cache

Important local settings:

- `assembly.paths.host_bundles_path`
- `assembly.paths.host_managed_bundles_path`

Important runtime rule:

- an initialized runtime reuses its existing `workdir/config/*.yaml`
- running `kdcube refresh --tenant <t> --project <p> --upstream --build` does
  not reseed default descriptors
- if you want different bundle roots or bundle entries in that runtime, edit `workdir/config/assembly.yaml` and `workdir/config/bundles.yaml` directly

For the step-by-step workflow, use:

- [how-to-configure-and-run-bundle-README.md](../../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

Reload workflow:

- edit local bundle code
- keep `bundles.yaml` stable
- run:

```bash
kdcube bundle reload <bundle_id> --workdir <runtime-workdir> --path <repo-root>
```

That reapplies the mounted descriptor and clears proc bundle caches.

### Direct local service run

Authority:

- the file pointed to by `BUNDLES_YAML_DESCRIPTOR_PATH`

Important distinction:

- `BUNDLES_YAML_DESCRIPTOR_PATH` controls plain descriptor reads and file-backed
  bundle descriptor authority
- proc can load/reset from that bundle descriptor authority directly

For direct proc debug, set at least:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
```

If you want startup env reset to rebuild the registry from the file, set:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

### AWS deployment

Authority:

- in `aws-sm`, live deployment-scoped bundle descriptor authority is grouped
  AWS SM docs
- `bundles.yaml` is the deployment import/export format and runtime-readable
  snapshot

Supported operational model:

- bundles from git only
- do not use local `path:` bundles
- do not use local host bundle roots

## Local development rule

For local development with code edits on the host:

- define the bundle in `bundles.yaml` with `path: /bundles/...`
- set `assembly.paths.host_bundles_path` to the matching host root
- run KDCube via the CLI compose path
- use `kdcube bundle reload <bundle_id>` after code changes

That is the correct local dev contract.

For AWS deployment:

- define the bundle from git
- do not use `path`
- do not use `assembly.paths.host_bundles_path`
