---
id: ks:docs/ops/ec2/dockercompose-deployment-README.md
title: "Dockercompose Deployment"
summary: "EC2 docker‑compose deployment for platform + custom UI + bundles using descriptor-driven compose setup."
tags: ["ops", "ec2", "docker-compose", "deployment", "custom-ui", "bundles"]
keywords: ["docker compose", "dc-infra", "bundles.yaml", "frontend config", "nginx", "bundles mount", "env files", "host folders"]
see_also:
  - ks:docs/ops/ecs/ecs-deployment-README.md
  - ks:docs/ops/deployment-options-index-README.md
  - ks:docs/ops/ops-overview-README.md
---
# EC2 Docker‑Compose Deployment (Custom UI + Bundles)

This doc consolidates the **descriptor-driven EC2 compose** notes into a single platform-side reference.
Use it when deploying **KDCube platform + a custom UI repo + bundles** on an EC2 host.

---

## Prerequisites

Two repos are typically involved on the EC2 host:

- **Platform repo** (KDCube): services + compose + dockerfiles
- **Custom app repo**: UI sources, optional UI/nginx templates, and optional custom bundles

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

**Custom app repo:**
- `.env.ui.build`

---

## UI build + runtime config

UI build inputs (custom app repo):
- `Dockerfile_UI`
- `nginx_ui.conf`

**Config is not baked into the image.**
At runtime, docker‑compose mounts:

```
PATH_TO_FRONTEND_CONFIG_JSON=<generated runtime config path>
```

This becomes:
```
/usr/share/nginx/html/config.json
```

The CLI generates this runtime config from:
- `frontend.frontend_config` if provided in `assembly.yaml`
- otherwise a built-in default based on auth mode (`hardcoded`, `cognito`, `delegated`)

The generated config always patches:
- `tenant`
- `project`
- `routesPrefix` from `proxy.route_prefix`

For delegated defaults, if root `company` is set in `assembly.yaml`, the CLI also
fills `auth.totpAppName` and `auth.totpIssuer`.

---

## Bundles path

Set this in the platform compose `.env` so bundles are mounted into chat‑proc:

```
HOST_BUNDLES_PATH=<custom-app-repo>/path/to/bundles
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
HOST_BUNDLES_DESCRIPTOR_PATH=/path/to/bundles.yaml
```
Then inside the container:
```
AGENTIC_BUNDLES_JSON=/config/bundles.yaml
```
If `HOST_BUNDLES_DESCRIPTOR_PATH` is unset, compose mounts `/dev/null` and the loader
falls back to inline `AGENTIC_BUNDLES_JSON` or Redis.
Assembly is mounted separately via:
```
HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH=/path/to/assembly.yaml
```
and is available for plain runtime reads through `read_plain(...)`.
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

The CLI selects the runtime proxy template from platform-shipped nginx configs
based on:
- auth mode (`simple`, `cognito`, `delegated`)
- `proxy.ssl`
- compose mode (`custom-ui-managed-infra`)

For delegated auth with SSL enabled, it uses:
- `deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_delegated_auth.conf`

For non-SSL delegated auth, it uses:
- `deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_delegated.conf`

The runtime proxy config is generated into the workdir `config/` folder.
The CLI patches:
- `routesPrefix` from `proxy.route_prefix`
- `YOUR_DOMAIN_NAME` from root `domain` when `proxy.ssl: true`

This means `domain` is required for automated SSL proxy rendering.
The default SSL templates assume Let’s Encrypt certs live at:
- `/etc/letsencrypt/live/<domain>/fullchain.pem`
- `/etc/letsencrypt/live/<domain>/privkey.pem`

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

If you use the CLI instead of managing `.env` files by hand, the recommended flow is:
1. provide `assembly.yaml`, `secrets.yaml`, `gateway.yaml`, `bundles.yaml`, and optionally `bundles.secrets.yaml`
2. let the CLI render `config/.env*`, runtime frontend config, and runtime nginx config
3. run compose with the generated workdir

For direct runtime descriptor reads, see:
- [docs/service/configuration/descriptor-plain-config-README.md](../../service/configuration/descriptor-plain-config-README.md)

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

- [docs/ops/ecs/ecs-deployment-README.md](../ecs/ecs-deployment-README.md)
- `deployment/ecs/`

When moving to ECS, prefer **baked bundle images** (no host mounts).
