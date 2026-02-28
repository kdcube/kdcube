# EC2 Docker‑Compose Deployment (Custom UI + Bundles)

This doc consolidates the **customer‑repo EC2 compose** notes into a single platform‑side reference.
Use it when deploying **KDCube platform + custom UI + bundles** on an EC2 host.

---

## Prerequisites

Two repos must be checked out on the EC2 host:

- **Platform repo** (KDCube): services + compose + dockerfiles
- **Customer repo**: UI + bundles + nginx config

---

## Compose file (platform repo)

Use:

```
<platform-repo>/app/ai-app/deployment/docker/custom-ui-managed-infra/docker-compose.yaml
```

---

## Required env files

**Platform repo (compose folder):**
- `.env` (paths + build contexts)
- `.env.ingress`
- `.env.proc`
- `.env.metrics`
- `.env.postgres.setup` (only if DB setup is needed)
- `.env.proxylogin` (optional)

**Customer repo:**
- `.env.ui.build`

---

## UI build + runtime config

UI build inputs (customer repo):
- `Dockerfile_UI`
- `nginx_ui.conf`

**Config is not baked into the image.**
At runtime, docker‑compose mounts:

```
PATH_TO_FRONTEND_CONFIG_JSON=<customer-repo>/ops/<...>/config.json
```

This becomes:
```
/usr/share/nginx/html/config.json
```

For ECS later:
- `FRONTEND_CONFIG_JSON` **or** `FRONTEND_CONFIG_S3_URL`

---

## Bundles path

Set this in the platform compose `.env` so bundles are mounted into chat‑proc:

```
HOST_BUNDLES_PATH=<customer-repo>/path/to/bundles
```

---

## Nginx proxy config

Customer repo provides nginx templates (dev/prod). Example:

- `<customer-repo>/ops/<...>/nginx_proxy.conf`

Point to it via:
```
NGINX_PROXY_CONFIG_FILE_PATH=<customer-repo>/ops/<...>/nginx_proxy.conf
```

---

## First‑time setup on EC2

Run from **platform repo**:

```bash
cd <platform-repo>/app/ai-app/deployment/docker/custom-ui-managed-infra

alias dc-infra='docker compose -f docker-compose.yaml'

# Create required data dirs (if using local paths)
mkdir -p data/{kdcube-storage,exec-workspace}
chmod -R 0777 data

# If using OpenResty + ACME
mkdir -p nginx/webroot

# Build exec image (from platform repo)
docker build -t py-code-exec:latest -f Dockerfile_Exec ../../..

# Initialize DB (if needed)
dc-infra build postgres-setup && dc-infra run --rm postgres-setup

# Start services
dc-infra up -d
```

---

## Common operations

```bash
# Rebuild ingress
 dc-infra stop chat-ingress && dc-infra rm chat-ingress -f && dc-infra up chat-ingress -d --build

# Rebuild processor
 dc-infra stop chat-proc && dc-infra rm chat-proc -f && dc-infra up chat-proc -d --build

# Rebuild UI
 dc-infra stop web-ui && dc-infra rm web-ui -f && dc-infra up web-ui -d --build

# Rebuild proxylogin
 dc-infra stop proxylogin && dc-infra rm proxylogin -f && dc-infra build proxylogin --no-cache && dc-infra up proxylogin -d

# Rebuild proxy
 dc-infra stop web-proxy && dc-infra rm web-proxy -f && dc-infra up web-proxy -d --build

# Logs
 dc-infra logs -f chat-ingress
```

---

## ECS transition (next)

ECS deployment and env templates live in the platform repo:

- `docs/ops/ecs/ecs-deployment-README.md`
- `deployment/ecs/`

When moving to ECS, prefer **baked bundle images** (no host mounts).
