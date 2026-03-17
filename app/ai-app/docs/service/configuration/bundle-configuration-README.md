---
id: ks:docs/service/configuration/bundle-configuration-README.md
title: "Bundle Configuration & Secrets"
summary: "How bundle config and bundle secrets are described, applied, and overridden at runtime."
tags: ["service", "configuration", "bundle", "secrets", "deployment"]
keywords: ["bundles.yaml", "bundles.secrets.yaml", "bundle props", "bundle secrets", "dot paths"]
see_also:
  - ks:docs/service/configuration/code-config-secrets-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
---
# Bundle Configuration & Secrets

Bundles have two kinds of inputs:
1) **Configuration** (non‑secret, safe to store)
2) **Secrets** (API keys, tokens, passwords)

We keep them in **separate files** with the **same shape**.

---

## 1) bundles.yaml (non‑secret)

```yaml
bundles:
  version: "1"
  default_bundle_id: "react@2026-02-10-02-44"
  items:
    - id: "app@2-0"
      name: "Customer App"
      repo: "git@github.com:org/customer-repo.git"
      ref: "bundle-v2026.02.22"
      subdir: "service/bundles"
      module: "app@2-0.entrypoint"
      config:
        model_id: "gpt-4o-mini"
        features: '{"web_search": true, "web_fetch": true}'
        embedding:
          provider: "openai"
          model: "text-embedding-3-small"
        role_models:
          solver.react.v2.decision.v2.strong:
            provider: "anthropic"
            model: "claude-sonnet-4-6"
          custom.agent.example:
            provider: "anthropic"
            model: "claude-3-5-haiku-20241022"
```

Notes:
- This file defines **what bundles exist** and their **non‑secret config**.
- It can be applied at deployment time (CLI/CI) and updated without service restart.
- `config` is the preferred key. `props` is accepted as a legacy alias.
- Nested YAML is preserved as a **nested dict** in `bundle_props`.
- Dot‑paths are **not expanded** at ingest time. Use a dot‑path resolver in code if needed.
- If `config.role_models` or `config.embedding` are present, they override the bundle’s
  `Config` at runtime (same path as `entrypoint.configuration` defaults in code).

### Overriding an existing role model
Example: override `solver.react.v2.decision.v2.strong` to use `claude-sonnet-4-6`:

```yaml
config:
  role_models:
    solver.react.v2.decision.v2.strong:
      provider: "anthropic"
      model: "claude-sonnet-4-6"
```

### Adding a new role model
Example: add a custom role entry:

```yaml
config:
  role_models:
    custom.agent.example:
      provider: "anthropic"
      model: "claude-3-5-haiku-20241022"
```

### Overriding embedding
Example: change embedding model:

```yaml
config:
  embedding:
    provider: "openai"
    model: "text-embedding-3-small"
```

---

## 2) bundles.secrets.yaml (secret)

```yaml
bundles:
  version: "1"
  items:
    - id: "app@2-0"
      secrets:
        openai:
          api_key: null
        stripe:
          secret_key: null
```

Notes:
- Same `items` shape as `bundles.yaml`, but **only secrets**.
- Secrets are injected into the secrets manager using **dot‑path keys**:
  - `bundles.<bundle_id>.secrets.openai.api_key`
  - `bundles.<bundle_id>.secrets.stripe.secret_key`
- Current behavior is **upsert-only**.
- If a secret is removed from `bundles.secrets.yaml`, it is **not**
  automatically deleted from the configured secrets provider.
- Removed secrets must be cleared explicitly via the admin UI/API or by an
  external secrets-sync process.

---

## 3) Runtime overrides (operational updates)

Bundle config can be changed at runtime (admin UI). These overrides are stored in
the bundle config store and layered on top of defaults.

Resolution order:
1) **Runtime overrides** (admin UI)
2) **bundles.yaml** defaults
3) **bundle defaults** (code)

**Effective props** are computed with a **deep merge**:
`code defaults → bundles.yaml → runtime overrides`.

### Authoritative env reset

`bundles.yaml` is the authoritative descriptor for descriptor-backed bundle props.
When proc startup runs with `BUNDLES_FORCE_ENV_ON_STARTUP=1`, or when an operator
uses **Reset from env**, the platform rebuilds the Redis props layer from the
current `bundles.yaml` content.

That reset is authoritative:
- props present in `bundles.yaml` are written to Redis
- props removed from `bundles.yaml` are deleted from Redis
- runtime/admin overrides stored in Redis are discarded by that reset

This is what makes `bundles.yaml` able to fully control bundle props, together
with the defaults defined in bundle code.

### Admin UI: Save props
The props editor always shows the **full effective props**.
When you click **Save props**, the editor contents are stored as the **override object**.
This means:
- The effective configuration stays exactly as shown.
- If you want minimal overrides, use the dot‑path editor to set only the keys you need.

Example — override just one role model:
```json
{
  "role_models": {
    "solver.react.v2.decision.v2.strong": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-6"
    }
  }
}
```

Example — override embedding:
```json
{
  "embedding": { "provider": "openai", "model": "text-embedding-3-large" }
}
```

**Recommendation:** use the dot‑path editor for precise updates without losing
siblings in nested objects (for example,
`role_models.solver.react.v2.decision.v2.strong.model`).

If proc startup has `BUNDLES_FORCE_ENV_ON_STARTUP=1`, remember that runtime
edits are operational overrides only. They remain effective until the next env
reset/startup, at which point `bundles.yaml` is re-applied authoritatively.

Secrets are resolved by the secrets manager using dot‑path keys. The UI should
never expose secret values; it only indicates whether a secret is set.

Operational secret updates from the bundle admin always go through the
configured secrets provider:
- local compose: `secrets-service` (`kdcube-secrets`)
- AWS/ECS: `aws-sm`
- process-local testing: `in-memory`

**Important (local sidecar):** bundle secrets can be requested long after
startup. When using `bundles.secrets.yaml`, keep sidecar read tokens
non‑expiring in the workdir `.env`:
- `SECRETS_TOKEN_TTL_SECONDS=0`
- `SECRETS_TOKEN_MAX_USES=0`

**Admin UI UX:** the bundle secrets panel is **write‑only** and never shows
values. It does show **known keys** (stored in Redis) so operators can see
which secrets are set for a bundle.

When secrets are provisioned via `bundles.secrets.yaml`, the CLI also stores
the key list under `bundles.<bundle_id>.secrets.__keys` in the configured
provider, so the admin UI can display keys even before any UI edits.

Important:
- unlike `bundles.yaml` props reset, `bundles.secrets.yaml` is currently not
  applied authoritatively
- env reset/startup does not auto-delete secrets removed from the descriptor

---

## 4) In bundle code

Bundle defaults are defined in `entrypoint.configuration_defaults` (base) and
bundle overrides of `entrypoint.configuration`.
Effective props are exposed via `bundle_props` (defaults + runtime overrides).

**Important:** `configuration` is a **property**. If you override it, use
`super().configuration` (no `()`) and apply defaults via `setdefault` so
external overrides from `bundles.yaml` and the admin UI still win.

See:
`apps/chat/sdk/solutions/chatbot/entrypoint.py` (`configuration`, `bundle_props`).

Secrets should be read via `get_secret()` with the dot‑path key:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

api_key = get_secret("bundles.app@2-0.secrets.openai.api_key")
```

### Inspect effective props in Redis
Bundle props are stored per tenant/project:

```
kdcube:config:bundles:props:<tenant>:<project>:<bundle_id>
```

Example:

```bash
redis-cli GET "kdcube:config:bundles:props:demo-tenant:demo-project:react.doc@2026-03-02-22-10"
```

### Dot‑path access for config (code-side)
If you want to read config via dot‑paths while keeping nested structure intact,
resolve at access time:

```python
def get_path(d, path, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

features = get_path(bundle_props, "features")
web_search = get_path(bundle_props, "features.web_search")
```
