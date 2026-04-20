---
id: ks:docs/service/environment/setup-for-dockercompose-README.md
title: "Setup For Dockercompose"
summary: "How to run git‑defined bundles with docker‑compose."
tags: ["service", "environment", "docker-compose", "bundles"]
keywords: ["all_in_one_kdcube", "custom-ui-managed-infra", "volume mounts", "bundles.yaml"]
see_also:
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/setup-for-ecs-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Setup for Docker Compose (Bundles from bundles.yaml)

This guide shows how to run **git‑defined bundles** using docker‑compose
(`all_in_one_kdcube` or `custom-ui-managed-infra`).

When you use the CLI with `assembly.yaml`, it can also generate:
- runtime frontend `config.json`
- runtime nginx proxy config

from descriptor data plus optional templates.

---

## 1) Host paths (.env in compose folder)

Set these in the **compose `.env`** (paths on the host):

```bash
# Assembly + bundles descriptors (mounted into /config/*.yaml)
HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH=/absolute/path/to/assembly.yaml
HOST_BUNDLES_DESCRIPTOR_PATH=/absolute/path/to/bundles.yaml

# Local path bundles root on host (mounted into /bundles)
HOST_BUNDLES_PATH=/absolute/path/to/bundles
BUNDLES_ROOT=/bundles

# Managed bundles root on host (mounted into /managed-bundles)
HOST_MANAGED_BUNDLES_PATH=/absolute/path/to/managed-bundles
MANAGED_BUNDLES_ROOT=/managed-bundles

# Optional SSH auth for private repos
HOST_GIT_SSH_KEY_PATH=/absolute/path/to/.ssh/id_ed25519
HOST_GIT_KNOWN_HOSTS_PATH=/absolute/path/to/.ssh/known_hosts
```

These are mounted by compose into the runtime services that need them.
`read_plain(...)` uses:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

Bundle path rule:

- local manual bundles use `HOST_BUNDLES_PATH` and are visible inside proc as `/bundles/...`
- managed bundles are materialized under `HOST_MANAGED_BUNDLES_PATH` and are visible inside proc as `/managed-bundles/...`
- local path bundle descriptors must point to `/bundles/...`, not to the raw host path

Example:

- host folder: `/Users/you/dev/bundles/my.bundle`
- descriptor path: `/bundles/my.bundle`

If you use `all_in_one_kdcube`, nginx configs are mounted from
`KDCUBE_CONFIG_DIR` (defaults to `./config`). Copy these once:

```bash
cp nginx/conf/nginx_ui.conf ./config/nginx_ui.conf
cp nginx/conf/nginx_proxy.conf ./config/nginx_proxy.conf
```

If you use the CLI instead of managing compose env files by hand:
- `frontend.frontend_config` is optional
- `frontend.nginx_ui_config` is optional
- `proxy.route_prefix` is applied to both frontend `routesPrefix` and runtime nginx config
- `proxy.ssl: true` + root `domain` also patches `YOUR_DOMAIN_NAME` in the runtime
  nginx SSL config and default Let’s Encrypt cert paths

---

## 2) Proc env (.env.proc)

Set these in the **proc env file**:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/config/bundles.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1

BUNDLE_GIT_RESOLUTION_ENABLED=1
BUNDLE_GIT_REDIS_LOCK=1
BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS=300
BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS=60
BUNDLE_GIT_ATOMIC=1

BUNDLES_ROOT=/bundles

# Option A: writable secrets sidecar
# Needed for admin UI to write secrets; values are sent to the sidecar.
SECRETS_PROVIDER=secrets-service
SECRETS_URL=http://kdcube-secrets:7777
SECRETS_ADMIN_TOKEN=${SECRETS_ADMIN_TOKEN}

# Bundle secrets can be read long after startup, so keep sidecar
# tokens non-expiring for local compose:
SECRETS_TOKEN_TTL_SECONDS=0
SECRETS_TOKEN_MAX_USES=0

# Option B: descriptor-backed secrets
# SECRETS_PROVIDER=secrets-file
# GLOBAL_SECRETS_YAML=file:///config/secrets.yaml
# BUNDLE_SECRETS_YAML=file:///config/bundles.secrets.yaml

# Optional (branch refs)
# BUNDLE_GIT_ALWAYS_PULL=1

# Optional (turn workspace snapshot; diagnostics only)
# REACT_PERSIST_WORKSPACE=0

# SSH inside container (matches the mounted paths)
GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key
GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes

# OR use HTTPS token auth (SSH settings ignored if token is set)
# GIT_HTTP_TOKEN=ghp_xxx
# GIT_HTTP_USER=x-access-token
```

After the first successful startup, set:

```bash
BUNDLES_FORCE_ENV_ON_STARTUP=0
```

---

## 3) Notes

- The processor reads bundles from `bundles.yaml`.
- Redis is the runtime source of truth; `BUNDLES_FORCE_ENV_ON_STARTUP=1`
  performs a one‑time overwrite.
- For custom UI compose, `PATH_TO_FRONTEND_CONFIG_JSON` should point to the
  generated runtime config file, not directly to a source template.
- If `frontend.frontend_config` is omitted in `assembly.yaml`, the CLI falls back
  to a built-in template based on auth mode:
  - `simple` -> `config.hardcoded.json`
  - `cognito` -> `config.cognito.json`
  - `delegated` -> `config.delegated.json`
- To enforce gateway config from env on every restart, set
  `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` in ingress/proc/metrics env.
- If using **public repos**, you can omit the SSH variables.
- For `postgres-setup`, keep `TENANT_ID` / `PROJECT_ID` in `.env.postgres.setup`
  aligned with the tenant/project in `GATEWAY_CONFIG_JSON`.
- If you use `file://...` with `secrets-file`, mount those YAML files into the
  proc container at the referenced paths with write access.
- `secrets-file` persists admin/UI secret edits back into the referenced YAMLs.
