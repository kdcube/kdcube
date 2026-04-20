---
id: ks:docs/service/configuration/bundles-descriptor-README.md
title: "Bundles Descriptor"
summary: "How bundles.yaml works in local compose, direct local runs, and AWS deployment."
tags: ["service", "configuration", "bundles", "descriptor"]
keywords: ["bundles.yaml", "path bundles", "git bundles", "bundle-reload", "BUNDLES_YAML_DESCRIPTOR_PATH"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/service/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/service/configuration/service-config-README.md
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

Example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "my.bundle@1-0.entrypoint"
```

If your real host folder is:

```text
/Users/you/src/my-repo
```

then `assembly.yaml` must map the host root:

```yaml
paths:
  host_bundles_path: "/Users/you/src"
```

and the bundle descriptor still uses `/bundles/...`.

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

Reload workflow:

- edit local bundle code
- keep `bundles.yaml` stable
- run:

```bash
kdcube --bundle-reload <bundle_id> --workdir <runtime-workdir> --path <repo-root>
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
- use `kdcube --bundle-reload <bundle_id>` after code changes

That is the correct local dev contract.

For AWS deployment:

- define the bundle from git
- do not use `path`
- do not use `assembly.paths.host_bundles_path`
