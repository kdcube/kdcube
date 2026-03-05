---
id: ks:docs/service/environment/setup-for-ecs-README.md
title: "Setup For ECS"
summary: "How to run git‑defined bundles on ECS with release.yaml."
tags: ["service", "environment", "ecs", "bundles"]
keywords: ["task definition", "EFS", "secrets", "release.yaml"]
see_also:
  - ks:docs/service/environment/setup-for-dockercompose-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/service-ecs-env-README.md
---
# Setup for ECS (Bundles from Release Descriptor)

This guide shows how to run **git‑defined bundles** on ECS using a
`release.yaml` descriptor.

---

## 1) Task definition (proc)

Set these env vars on the **chat‑proc task**:

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
```

After the first successful startup, set:

```bash
BUNDLES_FORCE_ENV_ON_STARTUP=0
```

---

## 2) Mount release descriptor

Mount `release.yaml` into the task at:

```
 /config/release.yaml
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
