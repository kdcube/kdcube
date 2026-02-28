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

Available templates:
- `nginx/nginx_proxy_ssl_hardcoded.conf`
- `nginx/nginx_proxy_ssl_cognito.conf`
- `nginx/nginx_proxy_ssl_delegated_auth.conf` (requires proxylogin)

4. Start:

```bash
docker compose up -d --build
```

## Prepare local data directories

```shell
mkdir -p ./data/{clamav,kdcube-storage,exec-workspace} ./logs/{chat-ingress,chat-proc}
```

```shell
chmod -R 0777 ./logs
```

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

- `HOST_BUNDLES_PATH` (host) → `AGENTIC_BUNDLES_ROOT` (container)

**Optional release descriptor (recommended):**

- Set `HOST_BUNDLE_DESCRIPTOR_PATH` in `.env` (host path to `release.yaml`)
- Inside container it mounts to `/config/release.yaml`
- In `.env.proc`, set:
  - `AGENTIC_BUNDLES_JSON=/config/release.yaml`

If you leave `HOST_BUNDLE_DESCRIPTOR_PATH` unset, `/dev/null` is mounted and the loader
falls back to inline `AGENTIC_BUNDLES_JSON` or the Redis registry.

## Notes

- `postgres-setup` runs once to bootstrap schemas in the managed Postgres.
- This stack assumes Redis/Postgres are reachable from containers (VPC/SG/localhost).
- If you don’t use delegated auth, you can leave `proxylogin` unused or comment it out in compose.
