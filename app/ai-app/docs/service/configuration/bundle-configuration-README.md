---
id: ks:docs/service/configuration/bundle-configuration-README.md
title: "Bundle Configuration & Secrets"
summary: "How bundle config and bundle secrets are described, applied, and overridden at runtime."
tags: ["service", "configuration", "bundle", "secrets", "deployment"]
keywords: ["bundles.yaml", "bundles.secrets.yaml", "bundle props", "bundle secrets", "dot paths"]
see_also:
  - ks:docs/service/configuration/code-config-secrets-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
---
# Bundle Configuration & Secrets

Bundles have two deployment-scoped inputs:
1) **Configuration** (non‑secret, safe to store)
2) **Secrets** (API keys, tokens, passwords)

We keep them in **separate files** with the **same shape**.

There is also a third category which is intentionally **not** part of deployment descriptors:

3) **User-scoped bundle props** (non-secret per-user mutable state)

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
- Some property paths are platform-reserved and have built-in behavior:
  - `role_models`
  - `embedding`
  - `economics.reservation_amount_dollars`
  - `execution.runtime`
  - `mcp.services`
- Canonical reference for these keys:
  [docs/sdk/bundle/bundle-platform-properties-README.md](../../sdk/bundle/bundle-platform-properties-README.md).

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

### Overriding execution runtime
Example: enable per-bundle Fargate exec routing:

```yaml
config:
  execution:
    runtime:
      mode: "fargate"
      enabled: true
      cluster: "arn:aws:ecs:eu-west-1:100258542545:cluster/kdcube-staging-cluster"
      task_definition: "kdcube-staging-exec"
      container_name: "exec"
      subnets: ["subnet-xxxx", "subnet-yyyy"]
      security_groups: ["sg-xxxx"]
      assign_public_ip: "DISABLED"
```

Notes:
- `execution.runtime` is the canonical path.
- `exec_runtime` is accepted as a legacy alias.
- Missing keys fall back to proc service env vars such as `FARGATE_CLUSTER`.

Example: define multiple runtime profiles for one bundle and select the default:

```yaml
config:
  execution:
    runtime:
      default_profile: "fargate"
      profiles:
        docker:
          mode: "docker"
          image: "py-code-exec:latest"
          network_mode: "host"
          cpus: "1.5"
          memory: "2g"
          extra_args: ["--pids-limit", "256"]
        fargate:
          mode: "fargate"
          enabled: true
          cluster: "arn:aws:ecs:eu-west-1:100258542545:cluster/kdcube-staging-cluster"
          task_definition: "kdcube-staging-exec"
          container_name: "exec"
          subnets: ["subnet-xxxx", "subnet-yyyy"]
          security_groups: ["sg-xxxx"]
          assign_public_ip: "DISABLED"
```

Notes:
- `profiles` is bundle-scoped: it declares the runtimes that this bundle supports.
- `default_profile` selects the resolved default used by generic exec-tool calls.
- bundle code can still choose another supported profile explicitly at runtime.
- Docker profiles may define Docker-specific keys such as `image` and `network_mode`.

### Configuring MCP services in bundle props
Example: define MCP connectors in `bundles.yaml` with named bundle secrets:

```yaml
config:
  mcp:
    services:
      mcpServers:
        docs:
          transport: http
          url: https://mcp.internal.example.com
          auth:
            type: bearer
            secret: bundles.react.mcp@2026-03-09.secrets.docs.token
        firecrawl:
          transport: stdio
          command: npx
          args: ["-y", "firecrawl-mcp"]
          env:
            FIRECRAWL_API_KEY: ${secret:bundles.react.mcp@2026-03-09.secrets.firecrawl.api_key}
```

Notes:
- `mcp.services` is the preferred platform contract for MCP connector config.
- `MCP_SERVICES` env is still accepted only as a legacy/local-dev fallback.
- `auth.secret` is preferred for HTTP/SSE auth.
- `${secret:...}` inside stdio `env` values resolves via `get_secret()` when the MCP subprocess is started.

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
        docs:
          token: null
        firecrawl:
          api_key: null
```

Notes:
- Same `items` shape as `bundles.yaml`, but **only secrets**.
- Bundle code normally reads these with `get_secret("b:...")`.
- The canonical internal namespace is still:
  - `bundles.<bundle_id>.secrets.openai.api_key`
  - `bundles.<bundle_id>.secrets.stripe.secret_key`
  - `bundles.<bundle_id>.secrets.docs.token`
  - `bundles.<bundle_id>.secrets.firecrawl.api_key`
- Current behavior is **upsert-only**.
- If a secret is removed from `bundles.secrets.yaml`, it is **not**
  automatically deleted from the configured secrets provider.
- Removed secrets must be cleared explicitly via the admin UI/API or by an
  external secrets-sync process.

---

## 3) Authoritative storage by deployment mode

### `secrets-file`

In `secrets-file` mode, the descriptor files themselves remain authoritative:

- `bundles.yaml`
- `bundles.secrets.yaml`

### `aws-sm`

In `aws-sm` mode, deployment-scoped bundle state is authoritative in grouped AWS
Secrets Manager documents:

- `<prefix>/bundles-meta`
- `<prefix>/bundles/<bundle_id>/descriptor`
- `<prefix>/bundles/<bundle_id>/secrets`

Where:

- `bundles-meta` holds registry-level metadata such as `default_bundle_id` and `bundle_ids`
- each `descriptor` document holds the effective deploy-scoped bundle item, including its plain props
- each `secrets` document holds the effective deploy-scoped bundle secrets

In this mode:

- Redis is cache only
- `bundles.yaml` and `bundles.secrets.yaml` are the descriptor/export format
- the CLI can export the current effective live descriptors from those AWS docs

---

## 4) Runtime bundle props

Bundle config can be changed at runtime from the bundle admin/API.

**Effective bundle props** are computed with a deep merge:

`code defaults → authoritative deploy-scoped bundle props`

So the current source of the deploy-scoped bundle props layer is:

- `bundles.yaml` in `secrets-file` mode
- `bundles/<bundle_id>/descriptor.props` in `aws-sm` mode

### Authoritative env reset

When proc startup runs with `BUNDLES_FORCE_ENV_ON_STARTUP=1`, or when an operator
uses **Reset from env**, the platform reapplies the descriptor-backed bundle state
from the active descriptor source.

That reset is authoritative:
- the authoritative deploy-scoped props layer is rewritten
- stale deploy-scoped props are removed
- Redis cache is rebuilt from that authoritative layer

In `aws-sm` mode, this also materializes:

- `bundles-meta`
- `bundles/<bundle_id>/descriptor`

for each deployment-scoped bundle.

### Admin UI / API: Save props

When an operator saves bundle props:

- Redis cache is updated
- the authoritative deploy-scoped bundle descriptor is updated too

So in `aws-sm` mode, bundle admin changes are not Redis-only anymore.

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

---

## 5) User-scoped bundle props

User-scoped non-secret props are separate from deployment descriptors.

They are for bundle-owned mutable user state such as:

- per-user preferences
- per-user UI state that the bundle wants to preserve
- user-approved bundle settings

They are **not** stored in:

- `bundles.yaml`
- `bundles.secrets.yaml`
- Redis bundle props cache
- AWS SM deployment-scoped bundle descriptor docs

They are stored in PostgreSQL per tenant/project schema in:

- `<SCHEMA>.user_bundle_props`

Bundle code should use the SDK helpers:

- `get_user_prop(...)`
- `get_user_props(...)`
- `set_user_prop(...)`
- `delete_user_prop(...)`

These helpers resolve:

- current user from request context
- current bundle from request context / bundle context

So normal bundle code does not need to hand-assemble storage keys.

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

Bundle defaults are defined in platform entrypoints plus the bundle’s own
`entrypoint.configuration`.
Effective props are exposed via `bundle_props` (defaults + runtime overrides).

Platform-reserved paths are documented in:
[docs/sdk/bundle/bundle-platform-properties-README.md](../../sdk/bundle/bundle-platform-properties-README.md).

**Important:** `configuration` is a **property**. If you override it, use
`super().configuration` (no `()`) and apply defaults via `setdefault` so
external overrides from `bundles.yaml` and the admin UI still win.

See:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py` (`configuration`, `bundle_props`)
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py`

Secrets should be read via `get_secret()`. For the current bundle, prefer the
bundle namespace shorthand:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

api_key = get_secret("b:openai.api_key")
```

Namespace rules:
- `get_secret("b:...")` -> current bundle secret
- `get_secret("...")` or `get_secret("a:...")` -> platform/global secret
- fully qualified `bundles.<bundle_id>.secrets...` is still accepted as the
  canonical internal form, but normal bundle code should not need it

Example: MCP config can consume the same bundle secrets directly:

```yaml
config:
  mcp:
    services:
      mcpServers:
        docs:
          transport: http
          url: https://mcp.internal.example.com
          auth:
            type: bearer
            secret: b:docs.token
        firecrawl:
          transport: stdio
          command: npx
          args: ["-y", "firecrawl-mcp"]
          env:
            FIRECRAWL_API_KEY: ${secret:b:firecrawl.api_key}
```

Meaning:
- `auth.secret` resolves through `get_secret("b:docs.token")`
- `${secret:...}` in stdio `env` values resolves through `get_secret()` when the MCP subprocess is started

### Inspect effective props in Redis
Bundle props are stored per tenant/project:

```
kdcube:config:bundles:props:<tenant>:<project>:<bundle_id>
```

Example:

```bash
redis-cli GET "kdcube:config:bundles:props:demo-tenant:demo-project:kdcube.copilot@2026-04-03-19-05"
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
