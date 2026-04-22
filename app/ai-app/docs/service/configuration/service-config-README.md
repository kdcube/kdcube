---
id: ks:docs/service/configuration/service-config-README.md
title: "Service Config"
summary: "Runtime env mapping for descriptors and the exact differences between CLI compose, direct local service runs, and AWS deployment."
tags: ["service", "configuration", "env", "descriptors"]
keywords: ["env vars", "assembly.yaml", "bundles.yaml", "HOST_BUNDLES_PATH", "ASSEMBLY_YAML_DESCRIPTOR_PATH"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/service/configuration/runtime-read-write-contract-README.md
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/secrets-descriptor-README.md
---
# Service Config

This is the cross-descriptor runtime mapping document.

It explains:

- which descriptor owns which part of runtime config
- how the three runtime modes differ
- which values matter only in CLI compose
- which values matter only for direct local process runs
- which values should not appear in AWS descriptors

The canonical contract tables now live in the per-descriptor pages:

- [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)
- [assembly-descriptor-README.md](assembly-descriptor-README.md)
- [bundles-descriptor-README.md](bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
- [secrets-descriptor-README.md](secrets-descriptor-README.md)
- [gateway-descriptor-README.md](gateway-descriptor-README.md)

## Three runtime modes

| Mode | Main authority | Runtime shape |
|---|---|---|
| CLI local compose (`kdcube`) | staged descriptors in `workdir/config` plus rendered `.env*` files | docker compose with `/config` mounts |
| Direct local service run | the files and env vars you pass to the process explicitly | host-run `web_app.py` / direct proc or ingress |
| AWS deployment | deployment stack plus provider-backed live state | ECS/EFS/S3/Secrets Manager |

## Descriptor-backed env vars

The detailed env/API mapping for each descriptor is documented in the
per-descriptor pages. This section keeps only the cross-descriptor overview.

### Descriptor file locations

| Env var | Descriptor file | Modes | Meaning |
|---|---|---|---|
| `ASSEMBLY_YAML_DESCRIPTOR_PATH` | `assembly.yaml` | direct local run | Explicit path for plain reads from `assembly.yaml`. |
| `BUNDLES_YAML_DESCRIPTOR_PATH` | `bundles.yaml` | direct local run | Explicit path for plain reads and file-backed bundle descriptor authority. |
| `HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH` | `assembly.yaml` | CLI local compose | Host file mounted into `/config/assembly.yaml`. |
| `HOST_BUNDLES_DESCRIPTOR_PATH` | `bundles.yaml` | CLI local compose | Host file mounted into `/config/bundles.yaml`. |
| `HOST_SECRETS_YAML_DESCRIPTOR_PATH` | `secrets.yaml` | CLI local compose, `secrets-file` only | Host file mounted into `/config/secrets.yaml`. |
| `HOST_BUNDLES_SECRETS_YAML_DESCRIPTOR_PATH` | `bundles.secrets.yaml` | CLI local compose, `secrets-file` only | Host file mounted into `/config/bundles.secrets.yaml`. |

### Identity, auth, and ports

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `COGNITO_REGION` | `auth.cognito.region` | `assembly.yaml` | CLI local compose, AWS deployment |
| `COGNITO_USER_POOL_ID` | `auth.cognito.user_pool_id` | `assembly.yaml` | CLI local compose, AWS deployment |
| `COGNITO_APP_CLIENT_ID` | `auth.cognito.app_client_id` | `assembly.yaml` | CLI local compose, AWS deployment |
| `COGNITO_SERVICE_CLIENT_ID` | `auth.cognito.service_client_id` | `assembly.yaml` | CLI local compose, AWS deployment |
| `CHAT_APP_PORT` | `ports.ingress` | `assembly.yaml` | CLI local compose |
| `CHAT_PROCESSOR_PORT` | `ports.proc` | `assembly.yaml` | CLI local compose |
| `METRICS_PORT` | `ports.metrics` | `assembly.yaml` | CLI local compose |
| `KDCUBE_UI_PORT` | `ports.ui` | `assembly.yaml` | CLI local compose |
| `KDCUBE_UI_SSL_PORT` | `ports.ui_ssl` | `assembly.yaml` | CLI local compose |
| `KDCUBE_PROXY_HTTP_PORT` | `ports.proxy_http` | `assembly.yaml` | CLI local compose |
| `KDCUBE_PROXY_HTTPS_PORT` | `ports.proxy_https` | `assembly.yaml` | CLI local compose |

Notes:

- `ports.proxy_http` and `ports.proxy_https` are compose-only host port
  overrides
- Kubernetes and AWS deployment do not use those fields as their public service
  exposure contract

### Secrets provider and secrets-file inputs

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `SECRETS_PROVIDER` | `secrets.provider` | `assembly.yaml` | all modes |
| `GLOBAL_SECRETS_YAML` | n/a | `secrets.yaml` | direct local run, CLI local compose in `secrets-file` mode |
| `BUNDLE_SECRETS_YAML` | n/a | `bundles.secrets.yaml` | direct local run, CLI local compose in `secrets-file` mode |

### Bundle registry and bundle authority

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `BUNDLES_YAML_DESCRIPTOR_PATH` | `bundles.yaml` | local bundle descriptor authority | proc in direct local run; optional explicit path in compose/k8s |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | n/a | current bundle descriptor authority | proc in all modes |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | bundle items use `repo` / `ref` | `bundles.yaml` | proc in all modes |
| `BUNDLES_PRELOAD_ON_START` | n/a | not descriptor-backed by default | proc |

Important distinction:

- `BUNDLES_YAML_DESCRIPTOR_PATH` controls plain reads and file-backed authority
- proc startup/reset can use bundle descriptor authority directly

### Workspace and Claude session backends

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `REACT_WORKSPACE_IMPLEMENTATION` | `storage.workspace.type` | `assembly.yaml` | CLI local compose, direct local run |
| `REACT_WORKSPACE_GIT_REPO` | `storage.workspace.repo` | `assembly.yaml` | CLI local compose, direct local run |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | `storage.claude_code_session.type` | `assembly.yaml` | CLI local compose, direct local run |
| `CLAUDE_CODE_SESSION_GIT_REPO` | `storage.claude_code_session.repo` | `assembly.yaml` | CLI local compose, direct local run |

Git repo transport for workspace/session stores:

- `storage.workspace.repo` and `storage.claude_code_session.repo` may use either HTTPS or SSH
  remote forms
- if HTTPS token auth is configured through `services.git.http_token`, the shared git helper
  prefers that path and may normalize SSH-style remotes to HTTPS before invoking git
- if SSH transport is intended, configure the matching SSH settings:
  - `services.git.git_ssh_key_path`
  - `services.git.git_ssh_known_hosts`
  - `services.git.git_ssh_strict_host_key_checking`

Operational guidance:

- HTTPS + PAT is usually the simpler deployment/runtime choice
- SSH is supported, but it additionally requires mounted key and host-verification material

## The `assembly.paths.*` keys you asked about

| Env var | Descriptor path | CLI local compose | Direct local run | AWS deployment |
|---|---|---|---|---|
| `HOST_KDCUBE_STORAGE_PATH` | `paths.host_kdcube_storage_path` | relevant | optional | not used |
| `HOST_BUNDLES_PATH` | `paths.host_bundles_path` | relevant for non-managed local path bundles | optional | not used |
| `HOST_MANAGED_BUNDLES_PATH` | `paths.host_managed_bundles_path` | relevant for platform-managed bundles (git/example) | optional | not used |
| `HOST_BUNDLE_STORAGE_PATH` | `paths.host_bundle_storage_path` | relevant | optional | not used |
| `HOST_EXEC_WORKSPACE_PATH` | `paths.host_exec_workspace_path` | relevant | optional | not used |

Rule:

- in CLI compose these are mount-driving topology settings
- in direct local runs they are just host-path settings if the process needs
  them
- in AWS deployment they should be omitted or ignored

## What to set for direct local proc debugging

If you run proc or ingress directly on the host, start with:

```bash
ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
```

If proc should seed or reset the bundle registry from that same descriptor:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

If you use file-backed secrets:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=file:///abs/path/to/secrets.yaml
BUNDLE_SECRETS_YAML=file:///abs/path/to/bundles.secrets.yaml
```

Set `HOST_*` path vars only if local runtime behavior needs those host
directories.

## What not to carry into AWS descriptors

Do not copy these local-only values into AWS/ECS deployment descriptors:

- `paths.host_kdcube_storage_path`
- `paths.host_bundles_path`
- `paths.host_bundle_storage_path`
- `paths.host_exec_workspace_path`

Cloud deployment owns storage and topology separately.

## Data access API summary

For the one-page read/write contract across helpers, see:

- [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)

| Data kind | API |
|---|---|
| effective typed platform setting | `get_settings()` |
| raw `assembly.yaml` value | `read_plain("...")` |
| raw `bundles.yaml` value | `read_plain("b:...")` |
| effective current bundle config | `self.bundle_prop("...")` |
| platform/global secret | `get_secret("canonical.key")` |
| bundle-scoped secret | `get_secret("b:group.key")` |

## Related docs

- Descriptor ownership and mode differences:
  - [descriptors-README.md](../cicd/descriptors-README.md)
- One-page runtime helper contract:
  - [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)
- Per-descriptor docs:
  - [assembly-descriptor-README.md](assembly-descriptor-README.md)
  - [bundles-descriptor-README.md](bundles-descriptor-README.md)
  - [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
  - [secrets-descriptor-README.md](secrets-descriptor-README.md)
  - [gateway-descriptor-README.md](gateway-descriptor-README.md)
