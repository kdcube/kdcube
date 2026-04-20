---
id: ks:deployment/docker/custom-ui-managed-infra/README.md
title: "Custom UI + Managed Infra (Docker Compose)"
summary: "Run KDCube services with a custom UI and OpenResty proxy against managed Postgres/Redis."
tags: ["deployment", "docker", "compose", "custom-ui", "managed-infra", "nginx", "openresty"]
keywords: ["custom frontend", "managed postgres", "managed redis", "openresty", "proxylogin", "bundles", "assembly descriptor"]
see_also:
  - ks:docs/ops/deployment-options-index-README.md
  - ks:docs/service/environment/service-compose-env-README.md
---
# Custom UI + Managed Infra (Docker Compose)

This compose stack runs **KDCube services** plus a **custom frontend** and **OpenResty proxy**, while using **managed Postgres/Redis** (no local DB/Redis containers).

**Runs**
- Postgres schema bootstrap (`postgres-setup`, one-shot)
- ClamAV (`clamav`)
- Chat ingress (`chat-ingress`)
- Chat processor (`chat-proc`)
- Metrics service (`metrics`)
- Custom Web UI (`web-ui`)
- OpenResty reverse proxy (`web-proxy`)
- Optional proxylogin (`proxylogin`, used only for delegated auth)

## Quick start

1. Copy sample envs and edit:

```bash
cp sample_env/.env ./.env
cp sample_env/.env.postgres.setup ./.env.postgres.setup
cp sample_env/.env.ingress ./.env.ingress
cp sample_env/.env.proc ./.env.proc
cp sample_env/.env.metrics ./.env.metrics
cp sample_env/.env.proxylogin ./.env.proxylogin
```

2. Configure **managed Postgres/Redis**:

- `.env.postgres.setup` → `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `.env.ingress` / `.env.proc` / `.env.metrics` → `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_URL`

3. Configure custom UI + proxy:

- Edit `.env` paths for `UI_BUILD_CONTEXT`, `UI_SOURCE_PATH`, `PATH_TO_FRONTEND_CONFIG_JSON`, etc.
- Choose an OpenResty config template under `nginx/` and set `NGINX_PROXY_CONFIG_FILE_PATH` in `.env`.
  - If a path is **relative**, it is interpreted relative to `UI_BUILD_CONTEXT`.

Available templates:
- `nginx/nginx_proxy_ssl_hardcoded.conf`
- `nginx/nginx_proxy_ssl_cognito.conf`
- `nginx/nginx_proxy_ssl_delegated_auth.conf` (requires proxylogin)

4. Start:

```bash
docker compose up -d --build
```

## Prepare local data + logs directories

```shell
mkdir -p ./data/{clamav,kdcube-storage,exec-workspace,bundle-storage} \
  ./logs/{chat-ingress,chat-proc}
```

```shell
chmod -R 0777 ./logs
```

### Linux permissions (multi‑user hosts)

Both `chat-ingress` and `chat-proc` write logs to `/logs` inside the container.
That path is a **bind mount** to `./logs` on the host. On Linux, the container
user (UID 1000) must have write access to that host folder.

**Recommended options:**

1. **Per‑user workdir (best):** keep `./logs` under your own home directory.
2. **Shared group:** set SGID + group write on the logs dir.
3. **Simple:** `chmod -R 0777 ./logs` (good enough for dev).

## UI integration notes

Your frontend should route API traffic through OpenResty and use a stable `routesPrefix`.

Recommended UI config fields:
- `routesPrefix: "/chatbot"` (UI is served under `/chatbot/*`)
- Auth config depends on your chosen mode:
  - `authType: "hardcoded"` (static token)
  - `authType: "cognito"` (browser sends Cognito tokens)
  - `authType: "delegated"` with `apiBase: "/auth/"` (requires proxylogin)

Backend API routes are **not** under `routesPrefix`:
- `/sse/*`
- `/api/chat/*`
- `/api/integrations/*`

## Bundles

`chat-proc` mounts bundles from the host:

- `HOST_BUNDLES_PATH` (host) → `BUNDLES_ROOT` (container) for local path bundles
- `HOST_MANAGED_BUNDLES_PATH` (host) → `MANAGED_BUNDLES_ROOT` (container) for managed bundles

**Knowledge space storage (for doc/knowledge bundles):**

- `HOST_BUNDLE_STORAGE_PATH` (host) → `BUNDLE_STORAGE_ROOT` (container)
- Set `BUNDLE_STORAGE_ROOT=/bundle-storage` in `.env.proc`

**Workspace descriptors (recommended):**

- Set `KDCUBE_CONFIG_DIR` in `.env` to the runtime workspace config directory
- Compose mounts that directory into the container at `/config`
- proc reads and updates `bundles.yaml` there as its local bundle descriptor authority

## Notes

- `postgres-setup` runs once to bootstrap schemas in the managed Postgres.
- This stack assumes Redis/Postgres are reachable from containers (VPC/SG/localhost).
- If you don’t use delegated auth, you can leave `proxylogin` unused or comment it out in compose.
- `docker-entrypoint.sh` is used by **chat‑proc only** (it configures Docker socket
  access and ensures the exec workspace is writable). Ingress does **not** use it.

## 3) Common operations

```bash
# Rebuild ingress (no deps)
 dc-infra build chat-ingress && dc-infra up -d --no-deps chat-ingress

# Rebuild processor (no deps)
 dc-infra build chat-proc && dc-infra up -d --no-deps chat-proc

# Rebuild UI (no deps)
 dc-infra build web-ui && dc-infra up -d --no-deps web-ui

# Rebuild proxylogin (no deps)
 dc-infra build proxylogin --no-cache && dc-infra up -d --no-deps proxylogin

# Rebuild proxy (no deps)
 dc-infra build web-proxy && dc-infra up -d --no-deps web-proxy

# Logs
 dc-infra logs -f chat-ingress
```

---
