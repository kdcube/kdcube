---
id: ks:docs/configuration/assembly-descriptor-README.md
title: "Platform Assembly Descriptor"
summary: "Platform-level non-secret deployment configuration in assembly.yaml: tenant/project identity, auth, ports, storage backends, local runtime paths, and frontend/runtime wiring."
tags: ["service", "configuration", "platform", "deployment", "assembly", "descriptor"]
keywords: ["platform deployment identity", "tenant and project scope", "auth and cognito settings", "service port layout", "storage and workspace backends", "runtime path wiring", "bundle descriptor provider", "frontend build metadata", "local compose topology", "aws deployment mapping"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/configuration/service-runtime-configuration-mapping-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
  - ks:docs/configuration/gateway-descriptor-README.md
---
# Platform Assembly Descriptor

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

### `auth.turnstile_development_token`

`auth.turnstile_development_token` is an optional installer-facing setting for
local or development registration flows that use Cloudflare Turnstile.

When it is set to a non-placeholder value, the CLI installer writes it into the
generated frontend runtime config as:

```json
{
  "auth": {
    "turnstileDevelopmentToken": "XXXX.DUMMY.TOKEN.XXXX"
  }
}
```

A frontend that supports this field can submit that token instead of rendering
the Turnstile widget. Leave the field empty in shared, staging, and production
descriptors unless that environment is intentionally using Cloudflare's test
credentials.

### `frontend.config`

`frontend.config` is public browser config. The installer and
`GET /api/cp-frontend-config` use the same builder and merge this section into
the generated frontend config. Do not put secrets here.

Example:

```yaml
frontend:
  config:
    auth:
      authType: "delegated"      # simple | cognito | delegated
      totpAppName: "Example App"
      totpIssuer: "Example App"
      apiBase: "/auth/"
    routesPrefix: "/chatbot"
    debug:
      injectDebugCommands: false
      animateStreaming: true
```

Use this section for browser-only deployment differences, for example a local
development auth proxy path or a custom SPA route prefix. `auth.turnstile_development_token`
is still read from the `auth` section and is published as
`auth.turnstileDevelopmentToken` when it is non-placeholder.

If `frontend.config.auth.authType` is omitted, it is derived from top-level
auth: `auth.type: simple` emits browser `authType: simple`, `auth.type:
cognito` emits `authType: cognito`, and `auth.type: delegated` emits
`authType: delegated`. The older browser value `hardcoded` is a legacy alias
for `simple`; new descriptors should use `simple`. `oauth` is not a deployment
auth mode; use `cognito` for the OSS browser Cognito/OIDC flow.

### `platform.services.<component>.exec`

`platform.services.proc.exec` owns platform defaults for isolated Python
execution. Access these defaults through `get_settings().PLATFORM.EXEC`.

Example:

```yaml
platform:
  services:
    proc:
      exec:
        exec_workspace_root: ""
        py_code_exec_image: "py-code-exec:latest"
        py_code_exec_timeout: 600
        py_code_exec_network_mode: "host"
        py_code_exec_container_strategy: "split"
        max_file_bytes: "100m"
        max_workspace_bytes: "250m"
        workspace_monitor_interval_s: 0.5
```

| Field | Settings API | Meaning |
|---|---|---|
| `exec_workspace_root` | `get_settings().PLATFORM.EXEC.EXEC_WORKSPACE_ROOT` | container-visible exec workspace root |
| `py_code_exec_image` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_IMAGE` | Docker image for the ISO runtime |
| `py_code_exec_timeout` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_TIMEOUT` | default Python execution timeout in seconds |
| `py_code_exec_network_mode` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_NETWORK_MODE` | Docker network mode for the ISO supervisor container |
| `py_code_exec_container_strategy` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_CONTAINER_STRATEGY` | `split` runs supervisor and generated code in separate containers and is the default; `combined` keeps the older single exec container |
| `max_file_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_FILE_BYTES` | max single generated file size per isolated run |
| `max_workspace_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_WORKSPACE_BYTES` | max net-new workdir/outdir bytes per isolated run |
| `workspace_monitor_interval_s` | `get_settings().PLATFORM.EXEC.PY.EXEC_WORKSPACE_MONITOR_INTERVAL_S` | polling interval for workspace quota enforcement |

The ISO runtime passes the limit values into the isolated executor as internal
`EXEC_*` transport env vars. Those env vars are not the operator-facing source
of configuration; set the descriptor fields above instead.

Bundles may override these limits for their own execution profile through
bundle props (`config.execution.runtime` or legacy `config.exec_runtime`). The
override is applied only to that bundle run.

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
