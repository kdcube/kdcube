---
id: ks:deployment/docker/local-infra-stack/README.md
title: "Local Infra Stack (Docker Compose)"
summary: "Run Postgres, Redis, ClamAV, and proxylogin locally for KDCube development."
tags: ["deployment", "docker", "infra", "postgres", "redis", "clamav", "proxylogin"]
keywords: ["local infra", "docker compose", "postgres-setup", "schema bootstrap", "redis password", "clamav", "proxylogin"]
see_also:
  - ks:docs/ops/deployment-options-index-README.md
  - ks:docs/service/environment/service-dev-env-README.md
---
# Local Infra Stack (Docker Compose)

This compose stack runs **infra services only** (Postgres, Redis, ClamAV, proxylogin).  
Use it when you run KDCube services locally (IDE/venv) or in a separate stack.

---

## Quick start

1. Copy sample envs:

```shell
cp sample_env/.env ./.env
cp sample_env/.env.postgres.setup ./.env.postgres.setup
cp sample_env/.env.proxylogin ./.env.proxylogin
```

2. Edit `.env` (required):

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- `REDIS_PASSWORD`

Optional (compose‑only):
- `POSTGRES_MAX_CONNECTIONS` (override Postgres `max_connections`; default `200`)

3. Edit `.env.postgres.setup` (one‑shot schema bootstrap):

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- `TENANT_ID`, `PROJECT_ID`
- `POSTGRES_PORT`, `POSTGRES_SSL` if you need non‑default connection settings

4. Prepare data folders:

```shell
mkdir -p ./data/{postgres,redis,clamav-db}
chmod -R 0777 ./data
```

5. Start infra:

```shell
docker compose up -d
```

Or with rebuild
```shell
docker compose up --build --force-recreate -d
```

---

## Postgres setup job (one‑shot)

The `postgres-setup` service bootstraps schemas and tenant/project rows.  
It uses `.env.postgres.setup` for connection + target tenant/project.

Run explicitly when needed:

```shell
docker compose run --rm postgres-setup
```

---

## Common maintenance

```shell
# Rebuild postgres-setup
docker compose stop postgres-setup && docker compose rm -f postgres-setup
docker compose build postgres-setup --no-cache && docker compose up -d postgres-setup

# Rebuild proxylogin
docker compose stop proxylogin && docker compose rm -f proxylogin
docker compose build proxylogin --no-cache && docker compose up -d proxylogin

# Restart redis
docker compose stop redis && docker compose rm -f redis
docker compose up -d --build redis
```
