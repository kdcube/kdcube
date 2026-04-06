---
id: ks:docs/service/environment/service-compose-env-README.md
title: "Service Compose Env"
summary: "Compose env file guide; source of truth is sample_env in deployment folders."
tags: ["service", "environment", "compose", "env"]
keywords: ["sample_env", "docker-compose", "ingress env", "proc env", "metrics env"]
see_also:
  - ks:docs/service/environment/service-dev-env-README.md
  - ks:docs/service/environment/setup-for-dockercompose-README.md
  - ks:docs/service/environment/service-ecs-env-README.md
---
# Service Compose Env (Docker Compose)

This document describes env files used by docker‑compose deployments.  
**Source of truth** is always the sample envs in the deployment folders.

## Compose Options + Env Files

### 1) `all_in_one_kdcube` (local infra + UI)
Folder: `deployment/docker/all_in_one_kdcube`

| File | Purpose |
| --- | --- |
| `sample_env/.env` | Compose paths + build contexts + UI config mount |
| `sample_env/.env.ingress` | Ingress service env |
| `sample_env/.env.proc` | Proc service env |
| `sample_env/.env.metrics` | Metrics service env |
| `sample_env/.env.postgres.setup` | DB setup job env |
| `sample_env/.env.proxylogin` | Proxylogin env (optional) |
| `sample_env/.env.frontend` | Frontend env (optional) |
| `nginx/conf/nginx_ui.conf` | UI nginx config (mounted at runtime) |
| `nginx/conf/nginx_proxy*.conf` | Proxy nginx templates used for the runtime-mounted proxy config |

### 2) `custom-ui-managed-infra` (custom UI + managed DB/Redis)
Folder: `deployment/docker/custom-ui-managed-infra`

| File | Purpose |
| --- | --- |
| `sample_env/.env` | Compose paths + build contexts + UI config mount |
| `sample_env/.env.ingress` | Ingress service env |
| `sample_env/.env.proc` | Proc service env |
| `sample_env/.env.metrics` | Metrics service env |
| `sample_env/.env.postgres.setup` | DB setup job env (targets managed DB) |
| `sample_env/.env.proxylogin` | Proxylogin env (optional) |

## Top‑Level Compose `.env` (paths & build context)

These are used by compose for mounts/builds. See `sample_env/.env` in the target folder.

| Variable | Purpose |
| --- | --- |
| `HOST_KDCUBE_STORAGE_PATH` | Host path mounted to `/kdcube-storage` |
| `HOST_BUNDLES_PATH` | Host bundles path mounted to `/bundles` |
| `HOST_EXEC_WORKSPACE_PATH` | Host exec workspace mounted to `/exec-workspace` |
| `UI_BUILD_CONTEXT` | UI repo root for Docker build |
| `UI_DOCKERFILE_PATH` | UI Dockerfile path (relative to `UI_BUILD_CONTEXT`) |
| `UI_SOURCE_PATH` | UI source dir (relative) |
| `NGINX_UI_CONFIG_FILE_PATH` | UI nginx config used by the UI image build. If omitted in `assembly.yaml`, CLI falls back to built-in `nginx_ui.conf`. |
| `PATH_TO_FRONTEND_CONFIG_JSON` | Generated runtime config JSON mounted to UI container as `/config.json` |
| `PROXY_BUILD_CONTEXT` | Proxy build context |
| `PROXY_DOCKERFILE_PATH` | Proxy Dockerfile path |
| `NGINX_PROXY_CONFIG_FILE_PATH` | Proxy nginx template selected for the runtime copy |
| `NGINX_PROXY_RUNTIME_CONFIG_PATH` | Runtime nginx config file mounted into the proxy container |
| `KDCUBE_CONFIG_DIR/nginx_ui.conf` | UI nginx config mount (all_in_one_kdcube) |
| `KDCUBE_CONFIG_DIR/nginx_proxy*.conf` | Proxy nginx runtime config mount (all_in_one_kdcube) |

Frontend/runtime config behavior:
- If `frontend.frontend_config` is provided in `assembly.yaml`, the CLI uses it as
  the template for the generated runtime `config.json`.
- If it is omitted, the CLI falls back to a built-in template by auth mode:
  - `simple` -> `config.hardcoded.json`
  - `cognito` -> `config.cognito.json`
  - `delegated` -> `config.delegated.json`
- The generated runtime config patches:
  - `tenant`
  - `project`
  - `routesPrefix` from `proxy.route_prefix`
- For delegated defaults, root `company` also fills:
  - `auth.totpAppName`
  - `auth.totpIssuer`

Proxy/runtime config behavior:
- The CLI copies the selected nginx proxy template into the workdir config folder.
- It patches `routesPrefix` from `proxy.route_prefix`.
- If `proxy.ssl: true` and root `domain` is set, it also replaces `YOUR_DOMAIN_NAME`
  in the runtime nginx SSL config and default Let’s Encrypt cert paths under
  `/etc/letsencrypt/live/<domain>/...`.

## Per‑Service Env (ingress/proc/metrics)

Each service has its own `.env.*` file. See the samples for the complete set:

- `sample_env/.env.ingress`
- `sample_env/.env.proc`
- `sample_env/.env.metrics`

**Note (proc only):** Git‑defined bundles require `BUNDLE_GIT_*` and optional `GIT_SSH_*` or `GIT_HTTP_TOKEN`.
env vars in `.env.proc`. See [docs/sdk/bundle/bundle-ops-README.md](../../sdk/bundle/bundle-ops-README.md).

## Secrets Provider Options

Supported runtime providers are:
- `secrets-service`
- `aws-sm`
- `secrets-file`
- `in-memory`

For descriptor-backed secrets in compose, set:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=file:///config/secrets.yaml
BUNDLE_SECRETS_YAML=file:///config/bundles.secrets.yaml
```

The runtime reads those URIs through the storage backend, so `file://...` and
`s3://...` are both valid.

For `file://...` in compose, make sure the referenced YAML files are mounted
into the container at those same paths.

`secrets-file` persists updates back into those YAML descriptors. For `file://...`,
make sure the proc container has the files mounted read-write. For `s3://...`,
the runtime identity must have object write permissions.

## Shared Requirements

- **Same `GATEWAY_CONFIG_JSON`** for ingress/proc/metrics.
- For CI/CD, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` to overwrite cached config on startup.
- `tenant` + `project` are required and must be in `GATEWAY_CONFIG_JSON`.
- Use `POSTGRES_HOST` / `REDIS_HOST` for managed services; omit for local‑infra if compose provides them.
- `TENANT_ID` / `DEFAULT_PROJECT_NAME` are not supported.
- If you enable `pgadmin`, set `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD`
  in `.env.postgres.setup` (samples provide defaults).

For the full config schema, see [docs/service/gateway-README.md](../gateway-README.md).
