---
id: ks:docs/service/environment/setup-for-dockercompose-README.md
title: "Setup For Dockercompose"
summary: "How to run git‑defined bundles with docker‑compose."
tags: ["service", "environment", "docker-compose", "bundles"]
keywords: ["all_in_one_kdcube", "custom-ui-managed-infra", "volume mounts", "release.yaml"]
see_also:
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/setup-for-ecs-README.md
  - ks:docs/service/environment/service-compose-env-README.md
---
# Setup for Docker Compose (Bundles from Release Descriptor)

This guide shows how to run **git‑defined bundles** using docker‑compose
(`all_in_one_kdcube` or `custom-ui-managed-infra`).

---

## 1) Host paths (.env in compose folder)

Set these in the **compose `.env`** (paths on the host):

```bash
# Release descriptor (mounted into /config/release.yaml)
HOST_BUNDLE_DESCRIPTOR_PATH=/absolute/path/to/release.yaml

# Bundles root on host (mounted into /bundles)
HOST_BUNDLES_PATH=/absolute/path/to/bundles
AGENTIC_BUNDLES_ROOT=/bundles

# Optional SSH auth for private repos
HOST_GIT_SSH_KEY_PATH=/absolute/path/to/.ssh/id_ed25519
HOST_GIT_KNOWN_HOSTS_PATH=/absolute/path/to/.ssh/known_hosts
```

These are mounted by compose into the **chat‑proc** container.

If you use `all_in_one_kdcube`, nginx configs are mounted from
`KDCUBE_CONFIG_DIR` (defaults to `./config`). Copy these once:

```bash
cp nginx/conf/nginx_ui.conf ./config/nginx_ui.conf
cp nginx/conf/nginx_proxy.conf ./config/nginx_proxy.conf
```

---

## 2) Proc env (.env.proc)

Set these in the **proc env file**:

```bash
AGENTIC_BUNDLES_JSON=/config/release.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1

BUNDLE_GIT_RESOLUTION_ENABLED=1
BUNDLE_GIT_REDIS_LOCK=1
BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS=300
BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS=60
BUNDLE_GIT_ATOMIC=1

AGENTIC_BUNDLES_ROOT=/bundles

# Optional (branch refs)
# BUNDLE_GIT_ALWAYS_PULL=1

# Optional (turn workspace snapshot; diagnostics only)
# REACT_PERSIST_WORKSPACE=0

# SSH inside container (matches the mounted paths)
GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key
GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

After the first successful startup, set:

```bash
BUNDLES_FORCE_ENV_ON_STARTUP=0
```

---

## 3) Notes

- The processor reads the `bundles` section directly from `release.yaml`.
- Redis is the runtime source of truth; `BUNDLES_FORCE_ENV_ON_STARTUP=1`
  performs a one‑time overwrite.
- To enforce gateway config from env on every restart, set
  `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` in ingress/proc/metrics env.
- If using **public repos**, you can omit the SSH variables.
- For `postgres-setup`, keep `TENANT_ID` / `PROJECT_ID` in `.env.postgres.setup`
  aligned with the tenant/project in `GATEWAY_CONFIG_JSON`.
