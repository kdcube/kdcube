## Compose to run local infra services used by KDCube APP

Before running, copy the sample envs:

```shell
cp sample_env/.env ./.env
cp sample_env/.env.postgres.setup ./.env.postgres.setup
cp sample_env/.env.proxylogin ./.env.proxylogin
```

Set at minimum in `.env`:
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- `POSTGRES_MAX_CONNECTIONS`
- `REDIS_PASSWORD`

Set in `.env.postgres.setup` (for schema bootstrap):
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- `TENANT_ID`, `PROJECT_ID`
```shell
mkdir -p ./data/{postgres,redis,clamav-db,neo4j/{data,logs,plugins,import}}
```

```shell
chmod -R 0777 data 
```

```shell
docker compose up -d
```

### Postgres setup job (one‑shot)

The `postgres-setup` service runs a one‑time bootstrap (schemas/tenant/project).
It uses `sample_env/.env.postgres.setup` for configuration.

Run it explicitly if needed:

```shell
docker compose run --rm postgres-setup
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
