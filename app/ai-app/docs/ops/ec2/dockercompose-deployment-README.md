---
id: ks:docs/ops/ec2/dockercompose-deployment-README.md
title: "Dockercompose Deployment"
summary: "EC2 docker‑compose deployment for platform + custom UI + bundles (customer‑repo driven)."
tags: ["ops", "ec2", "docker-compose", "deployment", "custom-ui", "bundles"]
keywords: ["docker compose", "dc-infra", "release.yaml", "frontend config", "nginx", "bundles mount", "env files", "host folders"]
see_also:
  - ks:docs/ops/ecs/ecs-deployment-README.md
  - ks:docs/ops/deployment-options-index-README.md
  - ks:docs/ops/ops-overview-README.md
---
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

### Bundle shared local storage (optional)

Bundles can use a shared local filesystem to store read‑only assets, indexes,
or any bundle‑specific data that should be reused across instances. If you use
`ks:` resolvers, this is where they read from.

In docker‑compose, mount it explicitly and set:

```
HOST_BUNDLE_STORAGE_PATH=/path/to/bundle-storage
BUNDLE_STORAGE_ROOT=/bundle-storage
```

This path must be writable by the proc container if the bundle builds
indexes or prepares data on startup.

### Bundles delivery (mounted path vs git)

**Mounted path (current EC2 default):**
Bundles are mounted into `/bundles` inside chat‑proc.  
In `.env.proc`, define `AGENTIC_BUNDLES_JSON` with `path=/bundles` and `module=<bundle_folder>.entrypoint`.  
You can set `AGENTIC_BUNDLES_JSON` to a JSON/YAML file path mounted into the container.  
Recommended mount:
```
HOST_BUNDLE_DESCRIPTOR_PATH=/path/to/release.yaml
```
Then inside the container:
```
AGENTIC_BUNDLES_JSON=/config/release.yaml
```
If `HOST_BUNDLE_DESCRIPTOR_PATH` is unset, compose mounts `/dev/null` and the loader
falls back to inline `AGENTIC_BUNDLES_JSON` or Redis.
Set `BUNDLE_GIT_RESOLUTION_ENABLED=0`.  
Optionally set `BUNDLES_FORCE_ENV_ON_STARTUP=1` for one rollout.

**Git‑defined bundles (optional):**
Provide `repo/ref/subdir` in `AGENTIC_BUNDLES_JSON`.  
Set `BUNDLE_GIT_RESOLUTION_ENABLED=1`.  
Set `BUNDLE_GIT_REDIS_LOCK=1` (each instance pulls once).  
Provide `GIT_SSH_KEY_PATH` / `GIT_SSH_KNOWN_HOSTS` for private repos.
Alternatively, use `GIT_HTTP_TOKEN` for HTTPS auth.

**Rule:** set `subdir` to the **parent bundles directory** and use `module: "<bundle_folder>.entrypoint"`.

**Git prerequisites (proc container):**
- `git` binary installed in the proc image (already included in KDCube proc image).
- If using SSH: mount the private key and known_hosts into the container, e.g.:
  ```
  /run/secrets/git_ssh_key
  /run/secrets/git_known_hosts
  ```
  and set:
  ```
  GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key
  GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts
  ```
  or use HTTPS token auth:
  ```
  GIT_HTTP_TOKEN=ghp_xxx
  GIT_HTTP_USER=x-access-token
  ```

---

## Nginx proxy config

Customer repo provides nginx templates (dev/prod). Example:

- `<customer-repo>/ops/<...>/nginx_proxy.conf`

Point to it via:
```
NGINX_PROXY_CONFIG_FILE_PATH=<customer-repo>/ops/<...>/nginx_proxy.conf
```

For non‑SSL dev, prefer the HTTP‑only config. For SSL deployments, use the
SSL templates (cognito/delegated auth) and ensure certs are mounted.

---

## First‑time setup on EC2

Run from **platform repo**:

```bash
cd <platform-repo>/app/ai-app/deployment/docker/custom-ui-managed-infra

alias dc-infra='docker compose -f docker-compose.yaml'

# Create required data dirs (if using local paths)
mkdir -p data/{kdcube-storage,exec-workspace,bundle-storage}
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
