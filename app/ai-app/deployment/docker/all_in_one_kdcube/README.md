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

1. Copy sample envs and edit as needed:

```bash
cp sample_env/.env.postgres.setup ./.env.postgres.setup
cp sample_env/.env.ingress ./.env.ingress
cp sample_env/.env.proc ./.env.proc
cp sample_env/.env.metrics ./.env.metrics
cp sample_env/.env.frontend ./.env.frontend
# Optional (if you enable proxylogin):
# cp sample_env/.env.proxylogin ./.env.proxylogin
```

2. Ensure bundle/exec paths are set (used by `chat-proc`):

- `HOST_BUNDLES_PATH` = host folder that contains all bundles
- `AGENTIC_BUNDLES_ROOT` = bundle root inside container (usually `/bundles`)
- `HOST_EXEC_WORKSPACE_PATH` = host exec workspace

3. Pick the OpenResty config you want (set in `.env.frontend`):

- `nginx/conf/nginx_proxy.conf` (no TLS, no proxylogin)
- `nginx/conf/nginx_proxy_ssl.conf` (TLS, no proxylogin)
- `nginx/conf/nginx_proxy_ssl_delegated_auth.conf` (TLS + proxylogin)

4. Frontend config examples live in:

- `frontend/config.json` (simple)
- `frontend/config.cognito.json`
- `frontend/config.hardcoded.json`

4. Start the stack:

```bash
docker compose up -d --build
```

## Prepare data directories

```shell
mkdir -p ./data/{postgres,redis,clamav-db,neo4j/{data,logs,plugins,import}}
```

```shell
chmod -R 0777 data
```

## Ports (defaults)

- Ingress API: `8010`
- Processor API: `8020`
- Metrics: `8090` (bound to localhost)
- Web UI: `5173`
- OpenResty: `80` / `443`

## Bundles

`chat-proc` **mounts bundles from the host**:

- `HOST_BUNDLES_PATH` (host) → `AGENTIC_BUNDLES_ROOT` (container)

This is how bundle code becomes available to the processor in this setup.

## Notes

- `postgres-setup` runs once after Postgres is healthy and creates schemas.
- Data persists under `./data/*`.
- Proxylogin is disabled by default in compose; enable it if you use delegated auth.
