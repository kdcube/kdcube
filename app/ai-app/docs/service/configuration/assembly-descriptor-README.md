---
id: ks:docs/service/configuration/assembly-descriptor-README.md
title: "Assembly Descriptor"
summary: "What assembly.yaml controls, which fields are mode-specific, and when local host paths matter."
tags: ["service", "configuration", "assembly", "descriptor"]
keywords: ["assembly.yaml", "paths.host_bundles_path", "CLI", "AWS", "direct run"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/service/configuration/service-config-README.md
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/secrets-descriptor-README.md
---
# Assembly Descriptor

`assembly.yaml` is the platform-level non-secret descriptor.

It defines:

- deployment identity: tenant, project, domain, company
- auth mode and Cognito identifiers
- service ports
- storage and runtime backends
- local host-path topology for CLI compose and direct local debugging
- frontend build/image metadata for custom UI compose runs

It does not define:

- bundle inventory
- bundle secrets
- global secrets
- gateway throttling and route guards

Those belong to the other descriptor files.

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| effective typed runtime setting | `get_settings()` | Uses `assembly.yaml > env var > code default` for fields that are promoted in `config_scopes.py` |
| raw value from `assembly.yaml` | `read_plain("...")` / `get_plain("...")` | Unprefixed keys read `assembly.yaml` by default |
| explicit raw value from `assembly.yaml` | `read_plain("a:...")` | Same as unprefixed read, but explicit |

### File-resolution env vars

| Env var | Meaning | Modes |
|---|---|---|
| `ASSEMBLY_YAML_DESCRIPTOR_PATH` | Explicit file path used by `read_plain(...)` and descriptor-backed runtime reads | direct local service run |
| `HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH` | Host file staged/mounted into `/config/assembly.yaml` by the CLI installer | CLI local compose |
| `PLATFORM_DESCRIPTORS_DIR` | Fallback directory used when `ASSEMBLY_YAML_DESCRIPTOR_PATH` is not set | direct local service run |

### Promoted env vars resolved from `assembly.yaml`

These env vars are the direct runtime surface for assembly-backed settings.

| Env var | `assembly.yaml` path | Primary API | Modes |
|---|---|---|---|
| `SECRETS_PROVIDER` | `secrets.provider` | `get_settings()` | all modes |
| `COGNITO_REGION` | `auth.cognito.region` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_USER_POOL_ID` | `auth.cognito.user_pool_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_APP_CLIENT_ID` | `auth.cognito.app_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_SERVICE_CLIENT_ID` | `auth.cognito.service_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `CHAT_APP_PORT` | `ports.ingress` | `get_settings()` | CLI local compose |
| `CHAT_PROCESSOR_PORT` | `ports.proc` | `get_settings()` | CLI local compose |
| `METRICS_PORT` | `ports.metrics` | `get_settings()` | CLI local compose |
| `KDCUBE_UI_PORT` | `ports.ui` | `get_settings()` | CLI local compose |
| `KDCUBE_UI_SSL_PORT` | `ports.ui_ssl` | `get_settings()` | CLI local compose |
| `KDCUBE_PROXY_HTTP_PORT` | `ports.proxy_http` | `get_settings()` | CLI local compose |
| `KDCUBE_PROXY_HTTPS_PORT` | `ports.proxy_https` | `get_settings()` | CLI local compose |
| `REACT_WORKSPACE_IMPLEMENTATION` | `storage.workspace.type` | `get_settings()` | CLI local compose, direct local service run |
| `REACT_WORKSPACE_GIT_REPO` | `storage.workspace.repo` | `get_settings()` | CLI local compose, direct local service run |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | `storage.claude_code_session.type` | `get_settings()` | CLI local compose, direct local service run |
| `CLAUDE_CODE_SESSION_GIT_REPO` | `storage.claude_code_session.repo` | `get_settings()` | CLI local compose, direct local service run |

## Fields that are always meaningful

These sections are normal platform configuration in every mode:

- `context.*`
- `auth.*`
- `proxy.*`
- `ports.*`
- `storage.*`
- `infra.*`
- `aws.region`

They are consumed either:

- by the installer/deployment layer
- by runtime env rendering
- or by direct `read_plain(...)` reads from `assembly.yaml`

## Fields that are local-run only

`paths.*` is local-run topology, not cloud deployment topology.

Supported keys:

- `paths.host_kdcube_storage_path`
- `paths.host_bundles_path`
- `paths.host_managed_bundles_path`
- `paths.host_bundle_storage_path`
- `paths.host_exec_workspace_path`

These keys exist so the local installer and local runtime know which host
directories should back the container-visible paths.

## `paths.*` by run mode

| Field | CLI local compose | Direct local service run | AWS deployment |
|---|---|---|---|
| `host_kdcube_storage_path` | relevant; mounted into container-backed local storage | optional; relevant only if the process should use that host storage path | ignore |
| `host_bundles_path` | relevant for non-managed local path bundles; mounted as `/bundles` | optional; relevant only if proc needs a host-visible local bundle root | ignore |
| `host_managed_bundles_path` | relevant for platform-managed bundles; mounted as `/managed-bundles` | optional; separate host root for git-resolved/example bundles | ignore |
| `host_bundle_storage_path` | relevant; mounted as `/bundle-storage` | optional; relevant only if local runtime should use host file-backed bundle storage | ignore |
| `host_exec_workspace_path` | relevant; mounted as `/exec-workspace` | optional; relevant only if local exec runtime should use a host workspace root | ignore |

The rule is simple:

- use `paths.*` for local development and local compose
- do not rely on `paths.*` for AWS/ECS descriptors

## Local compose contract

In CLI compose mode, the installer promotes `assembly.paths.*` into main compose
env keys:

- `HOST_KDCUBE_STORAGE_PATH`
- `HOST_BUNDLES_PATH`
- `HOST_MANAGED_BUNDLES_PATH`
- `HOST_BUNDLE_STORAGE_PATH`
- `HOST_EXEC_WORKSPACE_PATH`

### `paths.*` -> runtime env mapping

| Env var | `assembly.yaml` path | Modes |
|---|---|---|
| `HOST_KDCUBE_STORAGE_PATH` | `paths.host_kdcube_storage_path` | CLI local compose |
| `HOST_BUNDLES_PATH` | `paths.host_bundles_path` | CLI local compose |
| `HOST_MANAGED_BUNDLES_PATH` | `paths.host_managed_bundles_path` | CLI local compose |
| `HOST_BUNDLE_STORAGE_PATH` | `paths.host_bundle_storage_path` | CLI local compose |
| `HOST_EXEC_WORKSPACE_PATH` | `paths.host_exec_workspace_path` | CLI local compose |

Those host directories are then mounted into the containers at stable
container-visible paths such as:

- `/kdcube-storage`
- `/bundles`
- `/managed-bundles`
- `/bundle-storage`
- `/exec-workspace`

So in `bundles.yaml`:

- non-managed local path bundles must use container-visible paths like `/bundles/...`
- platform-managed bundles are materialized under `/managed-bundles/...`
- not raw host paths from your laptop

## Direct local proc/ingress contract

When you run proc or ingress directly on the host, `assembly.yaml` is not
mounted automatically.

Use:

- `ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml`

If code uses plain descriptor reads, that is enough for `assembly.yaml`.

`paths.*` is optional in this mode. It matters only if the service itself must
resolve host-facing runtime directories.

Example:

- direct proc debug that uses local exec workspace or local bundle roots

If you only need plain config reads, `ASSEMBLY_YAML_DESCRIPTOR_PATH` is the
important setting, not `paths.*`.

## AWS deployment contract

For AWS/ECS deployment:

- `assembly.yaml` is deployment input
- runtime may still read a mounted `/config/assembly.yaml`
- storage and mount topology comes from the deployment stack, not from
  `paths.*`

Do not put laptop or EC2 host paths into production descriptors.

For cloud deployments:

- keep `context`, `auth`, `proxy`, `ports`, `storage`, `infra`, and
  deployment-facing settings
- omit or ignore `paths.*`

## Frontend section

`frontend.*` is relevant to the CLI custom-UI compose path.

It is installer-facing metadata for:

- which frontend repo to clone
- which ref to use
- which Dockerfile to build
- which UI source path to build
- which frontend runtime config template to patch

It is not consumed directly by the runtime services.

## Minimal examples

### CLI local compose with local path bundles

```yaml
context:
  tenant: demo
  project: demo-local

secrets:
  provider: secrets-file

paths:
  host_bundles_path: "/Users/you/src"
  host_bundle_storage_path: "/Users/you/.kdcube/runtime/data/bundle-storage"
  host_exec_workspace_path: "/Users/you/.kdcube/runtime/data/exec-workspace"
```

### Direct local proc debug

```yaml
context:
  tenant: demo
  project: demo-direct

secrets:
  provider: secrets-file
```

Then point the process to the file with:

```bash
ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml
```

Add `paths.*` only if the process really needs those host directories.

### AWS deployment

```yaml
context:
  tenant: acme
  project: prod

secrets:
  provider: aws-sm

storage:
  kdcube: "s3://..."
  bundles: "s3://..."
```

Do not carry over local `paths.*`.
