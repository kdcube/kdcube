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
| `BUNDLE_GIT_RESOLUTION_ENABLED` | git bundle items in `bundles.yaml` | enables git bundle resolution | proc |

### Descriptor fields that matter to runtime

| `bundles.yaml` field | Used by | Meaning |
|---|---|---|
| `bundles.default_bundle_id` | `read_plain("b:default_bundle_id")`, runtime routing | default bundle |
| `bundles.items[].id` | runtime registry | bundle identifier |
| `bundles.items[].repo` / `ref` / `subdir` / `module` | proc git resolution | git-backed bundle definition |
| `bundles.items[].path` / `module` | proc local-path loading | local development bundle definition |
| `bundles.items[].config` | `self.bundle_prop("...")` | non-secret effective bundle config |

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

Example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "acme.marketing@2-0"
  items:
    - id: "acme.marketing@2-0"
      repo: "git@github.com:example-org/acme-platform.git"
      ref: "main"
      subdir: "src/acme/bundles/marketing"
      module: "acme.marketing@2-0.entrypoint"
      config:
        features:
          news: true
```

### 2. Local path bundles

Use:

- `path`
- `module`

This is for local development only.

The `path` must be container-visible, not host-visible.

#### Local path bundles: exact rule

The host parent root belongs in `assembly.yaml`.
The concrete bundle root belongs in `bundles.yaml`.

Use this split:

```yaml
# assembly.yaml
paths:
  host_bundles_path: "/Users/you/src"
```

```yaml
# bundles.yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
```

The host folder:

```text
/Users/you/src
```

is mounted into the runtime as:

```text
/bundles
```

So the bundle entry must use the container path under `/bundles`, not the host path under `/Users/...`.

#### `path` and `module`: only two valid forms

There are exactly two supported shapes for local path bundles.

##### Preferred form: `path` is the bundle root

If `path` already points to the actual bundle directory, use:

```yaml
path: /bundles/my-repo/src/my_bundle
module: entrypoint
```

This is the preferred form because it is explicit and does not depend on loader fallback behavior.

##### Secondary form: `path` is the parent directory

If `path` points to the parent directory that contains the bundle directory, use:

```yaml
path: /bundles/my-repo/src
module: my_bundle.entrypoint
```

This works because bundle root resolution supports:

- `<path>/<module_base>`
- `<path>/<module_base as package path>`

Do not mix the two forms without a reason.

Avoid this hybrid form:

```yaml
path: /bundles/my-repo/src/my_bundle
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
- running `kdcube --workdir <runtime> --build --upstream` does not reseed default descriptors
- if you want different bundle roots or bundle entries in that runtime, edit `workdir/config/assembly.yaml` and `workdir/config/bundles.yaml` directly

For the step-by-step workflow, use:

- [how-to-configure-and-run-bundle-README.md](../../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

Reload workflow:

- edit local bundle code
- keep `bundles.yaml` stable
- run:

```bash
kdcube reload <bundle_id> --workdir <runtime-workdir> --path <repo-root>
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
- use `kdcube reload <bundle_id>` after code changes

That is the correct local dev contract.

For AWS deployment:

- define the bundle from git
- do not use `path`
- do not use `assembly.paths.host_bundles_path`
