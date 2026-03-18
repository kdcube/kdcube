# All-in-one KDCube (local dev)

This compose stack runs a full local KDCube environment with **managed local infra + UI + proxy** so you can develop and evaluate bundles quickly.

**Runs**
- Postgres + pgvector (`postgres-db`)
- Postgres schema bootstrap (`postgres-setup`)
- Redis (`redis`)
- ClamAV (`clamav`)
- Chat ingress (`chat-ingress`)
- Chat processor (`chat-proc`)
- Metrics service (`metrics`)
- Web UI (`web-ui`)
- OpenResty reverse proxy (`web-proxy`)
- Optional `proxylogin` (commented out by default)

## Quick start

1. Copy sample envs and edit as needed (recommended layout: `config/`):

```bash
mkdir -p ./config
cp sample_env/.env ./config/.env
cp sample_env/.env.postgres.setup ./config/.env.postgres.setup
cp sample_env/.env.ingress ./config/.env.ingress
cp sample_env/.env.proc ./config/.env.proc
cp sample_env/.env.metrics ./config/.env.metrics
# Nginx configs (mounted into containers at runtime)
cp nginx/conf/nginx_ui.conf ./config/nginx_ui.conf
cp nginx/conf/nginx_proxy.conf ./config/nginx_proxy.conf
# Optional (if you enable proxylogin):
# cp sample_env/.env.proxylogin ./config/.env.proxylogin
```

2. Ensure bundle/exec paths are set (used by `chat-proc`):

- `HOST_BUNDLES_PATH` = host folder that contains all bundles
- `AGENTIC_BUNDLES_ROOT` = bundle root inside container (usually `/bundles`)
- `HOST_BUNDLE_STORAGE_PATH` = host folder for shared bundle local storage
- `BUNDLE_STORAGE_ROOT` = shared bundle local storage root inside container (e.g. `/bundle-storage`)
- `HOST_EXEC_WORKSPACE_PATH` = host exec workspace

3. Pick the OpenResty config you want (set in `.env`):

- `nginx/conf/nginx_proxy.conf` (no TLS, no proxylogin)
- `nginx/conf/nginx_proxy_ssl.conf` (TLS, no proxylogin)
- `nginx/conf/nginx_proxy_ssl_delegated_auth.conf` (TLS + proxylogin)

You can swap the proxy config by copying the desired file into
`./config/nginx_proxy.conf` (no rebuild required).

4. Frontend config examples live in:

- `frontend/config.json` (simple)
- `frontend/config.cognito.json`
- `frontend/config.hardcoded.json`

These are mounted at runtime as `/usr/share/nginx/html/config.json` via
`PATH_TO_FRONTEND_CONFIG_JSON` in `.env`.

## UI config source of truth

The web UI always loads its runtime config from `/config.json` inside the
`web-ui` container. Docker compose mounts the host file defined by
`PATH_TO_FRONTEND_CONFIG_JSON` to:

`/usr/share/nginx/html/config.json`

If the UI is calling the wrong tenant/project, verify:
- `PATH_TO_FRONTEND_CONFIG_JSON` in your `.env`
- `curl http://localhost:<ui_port>/config.json`

5. Start the stack:

```bash
docker compose --env-file ./config/.env up -d --build
```

Open the UI:
- `http://localhost:${KDCUBE_UI_PORT}/chatbot/chat` (via proxy, omit `:${KDCUBE_UI_PORT}` if it is `80`)

## Prepare data + logs directories

```shell
mkdir -p ./data/{postgres,redis,clamav-db,neo4j/{data,logs,plugins,import},bundle-storage} \
  ./logs/{chat-ingress,chat-proc}
```

```shell
chmod -R 0777 data
chmod -R 0777 logs
```

### Linux permissions (multi‑user hosts)

Both `chat-ingress` and `chat-proc` write logs to `/logs` inside the container.
That path is a **bind mount** to `./logs` on the host. On Linux, the container
user (UID 1000) must have write access to that host folder.

**Recommended options:**

1. **Per‑user workdir (best):** keep `./logs` under your own home directory.
2. **Shared group:** set SGID + group write on the logs dir.
3. **Simple:** `chmod -R 0777 ./logs` (good enough for dev).

If you use the CLI installer, it pre‑creates `./logs/chat-ingress` and
`./logs/chat-proc` and makes them writable.

## Ports (defaults)

- Ingress API: `8010`
- Processor API: `8020`
- Metrics: `8090` (bound to localhost)
- Web UI: `${KDCUBE_UI_PORT}` (default `5174` in sample env)
- OpenResty: `80` / `443`

## Bundles

`chat-proc` **mounts bundles from the host**:

- `HOST_BUNDLES_PATH` (host) → `AGENTIC_BUNDLES_ROOT` (container)

**Knowledge space storage (doc/knowledge bundles):**

- `HOST_BUNDLE_STORAGE_PATH` (host) → `BUNDLE_STORAGE_ROOT` (container)

This is how bundle code becomes available to the processor in this setup.

**Optional assembly descriptor (recommended):**

- Set `HOST_BUNDLES_DESCRIPTOR_PATH` in `.env` (host path to `bundles.yaml`)
- Inside container it mounts to `/config/bundles.yaml`
- In `.env.proc`, set:
  - `AGENTIC_BUNDLES_JSON=/config/bundles.yaml`

If you leave `HOST_BUNDLES_DESCRIPTOR_PATH` unset, `/dev/null` is mounted and the loader
falls back to inline `AGENTIC_BUNDLES_JSON` or the Redis registry.

### Rebuild Code Execution Image
```bash
docker build -t py-code-exec:latest -f Dockerfile_Exec ../../..
```

## Notes

- `postgres-setup` runs once after Postgres is healthy and creates schemas.
- `pgadmin` requires `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD`
  in `.env.postgres.setup` (sample env provides defaults).
- Data persists under `./data/*`.
- Proxylogin is disabled by default in compose; enable it if you use delegated auth.
- `docker-entrypoint.sh` is used by **chat‑proc only** (it configures Docker socket
  access and ensures the exec workspace is writable). Ingress does **not** use it.


```bash
alias dc-infra='docker compose -f docker-compose.yaml'
```

```shell
docker compose stop postgres-setup && docker compose rm postgres-setup -f && docker compose build postgres-setup --no-cache && docker compose up postgres-setup -d
```

```shell
docker compose stop proxylogin && docker compose rm proxylogin -f && docker compose build proxylogin --no-cache && docker compose up proxylogin -d
```

```shell
docker compose stop redis && docker compose rm redis -f && docker compose up redis -d --build
```

```shell
docker compose stop proxylogin && docker compose rm proxylogin -f && docker compose build --no-cache && docker compose up proxylogin -d
```

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
 dc-infra logs -f chat-proc
```
