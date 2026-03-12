---
id: ks:docs/service/environment/setup-dev-env-README.md
title: "Setup Dev Env"
summary: "Minimal env and SSH setup to load bundles from bundles.yaml in local dev."
tags: ["service", "environment", "setup", "dev", "git"]
keywords: ["bundles.yaml", "AGENTIC_BUNDLES_JSON", "GIT_SSH_KEY_PATH", "known_hosts"]
see_also:
  - ks:docs/service/environment/setup-for-dockercompose-README.md
  - ks:docs/service/environment/service-dev-env-README.md
  - ks:docs/service/environment/setup-for-ecs-README.md
---
# Setup Dev Env (Bundles from Assembly Descriptor)

This guide shows the **minimal env variables** needed to load bundles from a
`bundles.yaml` descriptor during local development, and how to prepare SSH
credentials for private git repos.

If you already use `kdcube-setup` (PyPI package: `kdcube-cli`), you can reuse the generated env files
from `workdir/config` instead of copying sample envs manually.

---

## 1) Minimal env for bundles (proc)

Add these to your `chat-proc` env (e.g. `apps/chat/proc/.env.proc`):

```bash
# Path to assembly descriptor (YAML or JSON)
AGENTIC_BUNDLES_JSON=/absolute/path/to/bundles.yaml

# Overwrite Redis registry on startup (use once per rollout)
BUNDLES_FORCE_ENV_ON_STARTUP=1

# Enable git bundle resolution
BUNDLE_GIT_RESOLUTION_ENABLED=1

# Serialize git pulls per instance
BUNDLE_GIT_REDIS_LOCK=1
BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS=300
BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS=60

# Atomic clone (clone to temp dir then rename)
BUNDLE_GIT_ATOMIC=1

# Where bundles are stored on disk
AGENTIC_BUNDLES_ROOT=/absolute/path/to/bundles

# Secrets sidecar (bundle secrets via admin UI)
SECRETS_URL=http://kdcube-secrets:7777
SECRETS_ADMIN_TOKEN=<admin-token>   # required for admin UI to set secrets
SECRETS_TOKEN=<read-token>          # used by get_secret()

# Keep tokens non-expiring if you use bundle secrets at runtime
SECRETS_TOKEN_TTL_SECONDS=0
SECRETS_TOKEN_MAX_USES=0

# Optional (turn workspace snapshot; diagnostics only)
# REACT_PERSIST_WORKSPACE=0
```

Optional (if using branch refs):

```bash
BUNDLE_GIT_ALWAYS_PULL=1
```

After the first successful startup, set:

```bash
BUNDLES_FORCE_ENV_ON_STARTUP=0
```

---

## 2) SSH for private git repos

If your bundles live in **private repos**, configure SSH:

```bash
GIT_SSH_KEY_PATH=/absolute/path/to/private_key
GIT_SSH_KNOWN_HOSTS=/absolute/path/to/known_hosts
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

### Create a key (if you don’t have one)

```bash
ssh-keygen -t ed25519 -C "some comment"
```

This creates:
- Private key: `~/.ssh/id_ed25519`
- Public key: `~/.ssh/id_ed25519.pub` (add this to GitHub/GitLab)

### Generate `known_hosts`

GitHub:
```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
```

GitLab:
```bash
ssh-keyscan gitlab.com >> ~/.ssh/known_hosts
```

Then set:
```bash
GIT_SSH_KNOWN_HOSTS=~/.ssh/known_hosts
```

---

## 3) Notes

- The processor reads bundles from `bundles.yaml`.
- Redis remains the runtime source of truth; the env only overwrites it when
  `BUNDLES_FORCE_ENV_ON_STARTUP=1`.
- For gateway config enforcement in local dev, set
  `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` in ingress/proc/metrics env.
