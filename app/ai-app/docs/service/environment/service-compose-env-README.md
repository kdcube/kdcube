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
| `nginx/conf/nginx_proxy.conf` | Proxy nginx config (mounted at runtime) |

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
| `NGINX_UI_CONFIG_FILE_PATH` | Nginx config used by UI image |
| `PATH_TO_FRONTEND_CONFIG_JSON` | Runtime config JSON mounted to UI container |
| `PROXY_BUILD_CONTEXT` | Proxy build context |
| `PROXY_DOCKERFILE_PATH` | Proxy Dockerfile path |
| `NGINX_PROXY_CONFIG_FILE_PATH` | Proxy nginx config used at runtime |
| `KDCUBE_CONFIG_DIR/nginx_ui.conf` | UI nginx config mount (all_in_one_kdcube) |
| `KDCUBE_CONFIG_DIR/nginx_proxy.conf` | Proxy nginx config mount (all_in_one_kdcube) |

## Per‑Service Env (ingress/proc/metrics)

Each service has its own `.env.*` file. See the samples for the complete set:

- `sample_env/.env.ingress`
- `sample_env/.env.proc`
- `sample_env/.env.metrics`

**Note (proc only):** Git‑defined bundles require `BUNDLE_GIT_*` and optional `GIT_SSH_*` or `GIT_HTTP_TOKEN`.
env vars in `.env.proc`. See `docs/sdk/bundle/bundle-ops-README.md`.

## Shared Requirements

- **Same `GATEWAY_CONFIG_JSON`** for ingress/proc/metrics.
- For CI/CD, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` to overwrite cached config on startup.
- `tenant` + `project` are required and must be in `GATEWAY_CONFIG_JSON`.
- Use `POSTGRES_HOST` / `REDIS_HOST` for managed services; omit for local‑infra if compose provides them.
- `TENANT_ID` / `DEFAULT_PROJECT_NAME` are not supported.
- If you enable `pgadmin`, set `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD`
  in `.env.postgres.setup` (samples provide defaults).

For the full config schema, see `docs/service/gateway-README.md`.
