---
id: ks:docs/service/environment/setup-for-ecs-README.md
title: "Setup For ECS"
summary: "How to run git‑defined bundles on ECS with bundles.yaml."
tags: ["service", "environment", "ecs", "bundles"]
keywords: ["task definition", "EFS", "secrets", "bundles.yaml"]
see_also:
  - ks:docs/service/environment/setup-for-dockercompose-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Setup for ECS (Bundles from Assembly Descriptor)

This guide shows how to run **git‑defined bundles** on ECS using a
`bundles.yaml` descriptor.

---

## 1) Task definition (proc)

Set these env vars on the **chat‑proc task**:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/config/bundles.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1

BUNDLE_GIT_RESOLUTION_ENABLED=1
BUNDLE_GIT_REDIS_LOCK=1
BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS=300
BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS=60
BUNDLE_GIT_ATOMIC=1

BUNDLES_ROOT=/bundles

# Optional (branch refs)
# BUNDLE_GIT_ALWAYS_PULL=1

# Optional (turn workspace snapshot; diagnostics only)
# REACT_PERSIST_WORKSPACE=0
```

If you use the **secrets sidecar** to store bundle secrets, the proc task
must be able to **write** (admin UI) and **read** them:

```bash
SECRETS_URL=http://kdcube-secrets:7777
SECRETS_ADMIN_TOKEN=<admin-token>   # required for admin UI to set secrets
SECRETS_TOKEN=<read-token>          # used by get_secret()
```

For long‑running bundles, keep sidecar tokens non‑expiring:

```bash
SECRETS_TOKEN_TTL_SECONDS=0
SECRETS_TOKEN_MAX_USES=0
```

For descriptor-backed secrets instead:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=s3://<bucket>/<prefix>/secrets.yaml
BUNDLE_SECRETS_YAML=s3://<bucket>/<prefix>/bundles.secrets.yaml
```

`secrets-file` also supports `file://...` URIs when descriptors are mounted
from EFS or baked into the image.

After the first successful startup, set:

```bash
BUNDLES_FORCE_ENV_ON_STARTUP=0
```

---

## 2) Mount assembly descriptor

Mount `bundles.yaml` into the task at:

```
 /config/bundles.yaml
```

Common options:
- **EFS** (shared config)
- **S3 download** at startup (init container or sidecar)
- **Baked into image** (if stable)

---

## 3) Git auth for private repos

Store SSH key + known_hosts in **Secrets Manager** (or SSM),
and mount them into the task:

```
/run/secrets/git_ssh_key
/run/secrets/git_known_hosts
```

Set env:

```bash
GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key
GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes

# OR use HTTPS token auth (SSH settings ignored if token is set)
# GIT_HTTP_TOKEN=ghp_xxx
# GIT_HTTP_USER=x-access-token
```

---

## 4) Bundles root storage

For git pulls, the bundles root must be writable:

- **Recommended:** mount EFS at `/bundles`
- Ensure access point uses uid/gid `1000`

---

## 5) Notes

- Redis remains the runtime source of truth.
- Use `BUNDLES_FORCE_ENV_ON_STARTUP=1` only for a single rollout.
- To enforce gateway config from env on each deploy, set
  `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` on ingress/proc/metrics tasks.
- Public repos don’t require SSH key/known_hosts.
- If you use `file://...` with `secrets-file`, mount those YAML files into the
  task filesystem at the referenced paths with write access.
- For `s3://...`, ensure the task role can both read and write the target objects.
