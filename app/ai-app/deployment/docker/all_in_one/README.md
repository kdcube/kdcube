# All-In-One Setup

## Services (what the Compose brings)

* **postgres-db**: `pgvector/pgvector:pg16` (volume: `postgres-data`)
* **postgres-setup**: applies KB schemas (depends on `postgres-db`)
* **redis**: `redis:latest` (passworded; volume `redis-data`, port exposed `5445:6379`)
* **dramatiq**: worker container (depends on Postgres + Redis, mounts `./kb-storage`)
* **kb**: Knowledge Base API (depends on Postgres + Redis, mounts `./kb-storage`)
* **chat**: Chat API (depends on Redis, mounts `./kb-storage` and **bind-mounts your agentic bundle**)

    * **Important**: set both `SITE_AGENTIC_BUNDLES_ROOT` (host path - "artifactory folder" where you place the bundles) and `AGENTIC_BUNDLES_ROOT` (the folder to which we mount artifactory with bundles inside the container) in env; Compose bind-mounts the former into the latter.
* **web-ui**: frontend
* **web-proxy**: Nginx (exposes `80/443` and `5173`)

### Notable environment options for Compose

* `REDIS_URL="redis://redis:6379/0"` and `REDIS_HOST=redis` for in-house redis
* `POSTGRES_HOST=postgres-db` for in-house Postgres
* Optional `KDCUBE_STORAGE_PATH`—when using FS, point both KB and Chat to the same root; Compose mounts `./kb-storage:/kb-storage`.
* For the proxy: mount `letsencrypt` and `nginx/webroot` if you terminate TLS.

> See `deployment/docker/all_in_one` for Dockerfiles and sample `*.env.*` files [here](sample_env).
Most of the env is documented in place.

## First run
Initialize Postgres (re-run if change the schemas)
```shell
  docker compose build postgres-setup && docker compose run --rm postgres-setup
```

### Create local KB persistent storage if does not exist (optional)
```shell
  mkdir "kb-storage" -p && chmod 777 "kb-storage"
```

### Create the data folders for the databases (if not done yet)
```shell
  mkdir -p ./data/{postgres,redis,neo4j/{data,logs,plugins,import}}
  chmod -R 0777 data
```

## Run

### Backend
```shell
  docker compose --profile "backend" up -d --remove-orphans
```
With image rebuild
```shell
  docker compose --profile "backend" up -d --remove-orphans --build
```

### Frontend
```shell
  docker compose --profile "frontend" up -d --remove-orphans
```
With image rebuild
```shell
  docker compose --profile "frontend" up -d --remove-orphans --build
```
## Quick commands

### Stop and remove backend services
```shell
  docker compose stop chat kb dramatiq && docker compose rm chat kb dramatiq -f
```

### Chat Rebuild
```shell
  docker compose stop chat && docker compose rm chat -f && docker compose up chat -d --build 
```

### KB Rebuild
```shell
  docker compose stop kb && docker compose rm kb -f && docker compose up kb -d --build 
```

### Dramatiq Rebuild
```shell
  docker compose stop dramatiq && docker compose rm dramatiq -f && docker compose up dramatiq -d --build 
```

### UI Rebuild
```shell
  docker compose stop web-ui && docker compose rm web-ui -f && docker compose up web-ui -d --build 
```

### Proxy Rebuild
```shell
  docker compose stop web-proxy && docker compose rm web-proxy -f && docker compose up web-proxy -d --build 
```

### Exec image
```shell
docker build -t py-code-exec:latest -f Dockerfile_Runner ../../..
```