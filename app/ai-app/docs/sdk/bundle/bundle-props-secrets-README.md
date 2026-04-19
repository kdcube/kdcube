---
id: ks:docs/sdk/bundle/bundle-props-secrets-README.md
title: "Bundle Props and Secrets"
summary: "Bundle-facing config and secret scopes, their read/write APIs, and the current source-of-truth/storage model for local and production deployments."
tags: ["sdk", "bundle", "props", "secrets", "configuration"]
keywords: ["bundle_props", "get_plain", "get_secret", "get_user_secret", "bundles.yaml", "bundles.secrets.yaml", "secrets.yaml", "redis overrides"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
---
# Bundle Props and Secrets

This document covers the four bundle-facing state classes that matter in practice:

1. deployment-scoped bundle props
2. deployment-scoped bundle secrets
3. user-scoped bundle props
4. user-scoped bundle secrets

For now, bundle code should **not** write platform/global props or platform/global secrets.

## What lives where

| Data class | Read API | Write API | Scope | Live authority today | Export / descriptor behavior |
|---|---|---|---|---|---|
| deployment-scoped bundle props | `self.bundle_prop(...)`, `self.bundle_props` | `await set_bundle_prop(...)` | tenant + project + bundle | mounted writable `bundles.yaml` when present; Redis is the runtime cache; grouped bundle descriptor docs are fallback only when no mounted file exists | exported to `bundles.yaml` |
| deployment-scoped bundle secrets | `get_secret("b:...")` | `await set_bundle_secret(...)` | tenant + project + bundle | configured secrets provider; in local `secrets-file` mode this is `bundles.secrets.yaml` | exported to `bundles.secrets.yaml` |
| user-scoped bundle props | `get_user_prop(...)`, `get_user_props()` | `set_user_prop(...)`, `delete_user_prop(...)` | tenant + project + bundle + user | PostgreSQL `<SCHEMA>.user_bundle_props` | never exported to descriptors |
| user-scoped bundle secrets | `get_user_secret(...)` | `set_user_secret(...)`, `delete_user_secret(...)` | tenant + project + bundle + user | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | never exported to descriptors |

The most important split is:

- `self.bundle_prop(...)` reads the bundle's effective non-secret runtime config
- `get_plain(...)` reads raw mounted descriptor files only
- `get_secret("b:...")` reads deployment-scoped bundle secrets
- `get_user_prop(...)` / `set_user_prop(...)` are user-scoped non-secret state
- `get_user_secret(...)` / `set_user_secret(...)` are user-scoped secret state

If the data is mutable business data rather than config or credentials, use bundle storage instead.

## What is actually supported today

### From inside bundle code

Supported directly:

- read deployment-scoped bundle props via `self.bundle_prop(...)`
- read deployment-scoped bundle secrets via `get_secret("b:...")`
- write deployment-scoped bundle props via `await set_bundle_prop(...)`
- write deployment-scoped bundle secrets via `await set_bundle_secret(...)`
- read/write user-scoped bundle props via `get_user_prop(...)`, `set_user_prop(...)`
- read/write user-scoped bundle secrets via `get_user_secret(...)`, `set_user_secret(...)`

That distinction matters:

- user-scoped writes are part of normal bundle runtime behavior
- deployment-scoped bundle writes are still operational/configuration writes

## Deployment-scoped bundle props

Read effective bundle props through:

- `self.bundle_props`
- `self.bundle_prop("dot.path", default=...)`

Example:

```python
enabled = self.bundle_prop("features.sync.enabled", False)
profile = self.bundle_prop("execution.runtime.default_profile", "local")
services = self.bundle_prop("mcp.services", {})
```

Typical use:

- model and role-model selection
- MCP service config
- runtime profiles
- feature flags
- scheduled job config such as `@cron(..., expr_config=...)`
- bundle-defined MCP inbound auth contract such as
  `mcp.inbound.auth.header_name`

### Source-of-truth policy for bundle props

`bundles.yaml` is the deployment-scoped bundle descriptor and is always mounted into the runtime.

That means the intended source-of-truth policy is:

- deployment-scoped bundle prop changes should be persisted into `bundles.yaml`
- exports should reconstruct `bundles.yaml` from the current deployment-scoped bundle state
- user-scoped state should remain outside descriptors

This is the policy the docs should describe.

### Current implementation for bundle props

There are two layers:

1. code defaults in bundle code
2. deploy-scoped props overrides

The effective runtime props are always assembled from those two layers.

The runtime cache key is:

```text
kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}
```

That Redis key is what proc reads first at runtime.

If Redis misses:

- if mounted `bundles.yaml` exists, proc backfills Redis from that file
- in `aws-sm`, proc backfills Redis from the grouped bundle descriptor doc

### Current write path for bundle props

Supported operational write path today:

- `await set_bundle_prop("dot.path", value)` from bundle code
- `GET /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props/reset-code`

Current behavior:

- `POST /.../props` always writes Redis
- if mounted `bundles.yaml` exists, that same write also updates the mounted file directly
- otherwise, in `aws-sm`, that same write updates the grouped bundle descriptor doc

So the current implementation is:

| Mode | Runtime read authority | Persistent authority today |
|---|---|---|
| mounted descriptor file present | Redis, backfilled from mounted `bundles.yaml` | mounted `bundles.yaml` |
| no mounted descriptor file, `aws-sm` configured | Redis, backfilled from grouped bundle descriptor docs | grouped bundle descriptor docs in AWS Secrets Manager |
| other non-file providers | Redis | provider-specific operational path if configured |

### Descriptor reset behavior

These paths are descriptor-authoritative today:

- `kdcube --bundle-reload <bundle_id>`
- env/descriptor force-reset paths
- startup env reset paths that replay `AGENTIC_BUNDLES_JSON` / `bundles.yaml`

Those paths replace Redis bundle props from the descriptor source, which is the mounted `bundles.yaml`.

### Raw descriptor reads are different

`get_plain(...)` is not the same thing as `self.bundle_prop(...)`.

Use:

- `get_plain("a:...")` or no prefix for `assembly.yaml`
- `get_plain("b:...")` for raw `bundles.yaml`

It reads mounted files only:

- no Redis overrides
- no tenant/project scoping
- no write path

Use it only when the bundle needs raw descriptor inspection.

## Deployment-scoped bundle secrets

Read bundle secrets with:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

token = get_secret("b:api.token")
```

Bundle-facing secret namespace:

```text
b:<dot.path>
```

Canonical internal namespace:

```text
bundles.<bundle_id>.secrets.<dot.path>
```

### Current write path for bundle secrets

Supported operational write path today:

- `await set_bundle_secret("dot.path", value)` from bundle code
- `POST /admin/integrations/bundles/{bundle_id}/secrets`
- `GET /admin/integrations/bundles/{bundle_id}/secrets`

Current storage depends on the configured secrets provider:

| Provider | Bundle secrets are written to |
|---|---|
| `aws-sm` | `<prefix>/bundles/{bundle_id}/secrets` |
| `secrets-file` | `bundles.secrets.yaml` |
| `secrets-service` | secrets service backend |
| `in-memory` | process memory only |

This means bundle secret writes in local descriptor mode already behave the way you want:

- in `secrets-file`, bundle secret writes go straight into `bundles.secrets.yaml`
- in `aws-sm`, bundle secret writes go straight into the provider authority

Do not put secrets into:

- `bundle_props`
- `bundles.yaml`
- logs
- artifacts

### Example: bundle-defined MCP auth contract

For bundle-authenticated MCP, a clean split is:

- bundle props define the non-secret client contract
- bundle secrets define the verification material

Example:

```yaml
# bundles.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      config:
        mcp:
          inbound:
            auth:
              header_name: "X-Partner-MCP-Token"
              scheme: "shared-header-secret"
```

```yaml
# bundles.secrets.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      secrets:
        mcp:
          inbound:
            auth:
              shared_token: "replace-in-real-deployment"
```

Then bundle code reads:

```python
header_name = self.bundle_prop("mcp.inbound.auth.header_name", "X-Partner-MCP-Token")
expected_token = get_secret("b:mcp.inbound.auth.shared_token")
```

That gives the bundle a stable contract with its MCP clients:

- the prop tells clients which header name to send
- the secret stores the expected token
- proc does not verify it for MCP; the bundle verifies it itself

Use this only for bundle-owned MCP auth. For full code, use the worked example
in [bundle-transports-README.md](bundle-transports-README.md).

## User-scoped bundle props

Use:

```python
from kdcube_ai_app.apps.chat.sdk.config import (
    get_user_prop,
    get_user_props,
    set_user_prop,
    delete_user_prop,
)

theme = get_user_prop("preferences.theme", default="light")
set_user_prop("preferences.theme", "dark")
snapshot = get_user_props()
delete_user_prop("preferences.theme")
```

These values are always written to PostgreSQL:

- project schema table: `<SCHEMA>.user_bundle_props`

This is true regardless of:

- local vs cloud
- `aws-sm` vs `secrets-file`
- ECS vs EC2 vs local proc

User props are:

- non-secret
- per-user
- per-bundle
- operational data, not deployment descriptors

They are never written to:

- `bundles.yaml`
- `bundles.secrets.yaml`
- CLI bundle export

## User-scoped bundle secrets

Use:

```python
from kdcube_ai_app.apps.chat.sdk.config import (
    get_user_secret,
    set_user_secret,
    delete_user_secret,
)

token = get_user_secret("git.http_token")
set_user_secret("git.http_token", "...")
delete_user_secret("git.http_token")
```

Logical namespace:

```text
users.<user_id>.bundles.<bundle_id>.secrets.<dot.path>
```

Current storage depends on the secrets provider:

| Provider | User secrets are written to |
|---|---|
| `aws-sm` | `<prefix>/users/{user_id}/bundles/{bundle_id}/secrets` |
| `secrets-file` | `secrets.yaml` |
| `secrets-service` | secrets service backend |
| `in-memory` | process memory only |

Important split in `secrets-file` mode:

- bundle secrets -> `bundles.secrets.yaml`
- user secrets -> `secrets.yaml`

User secrets are operational user data, not deployment descriptors.

They are never part of:

- `bundles.secrets.yaml`
- `bundles.yaml`
- `kdcube --export-live-bundles`

## Descriptor-first model

When `bundles.yaml` and `bundles.secrets.yaml` are mounted as writable descriptor files, the operating model is:

- mounted descriptors are the only source of truth for deployment-scoped bundle config
- env files only identify component role and descriptor locations
- deployment-scoped bundle writes go directly to bundle descriptor files when file-backed descriptor mode is enabled
- user-scoped writes never go to descriptors

Concretely:

### Descriptor-first policy

Deployment-scoped writes land here:

- bundle props -> `bundles.yaml`
- bundle secrets -> `bundles.secrets.yaml` in local `secrets-file` mode

User-scoped writes still land here:

- user props -> PostgreSQL
- user secrets -> configured secrets provider (`secrets.yaml` in local `secrets-file` mode)

### If descriptors are not the live authority

Then the platform still needs an explicit export path for deployment-scoped bundle state:

- export bundle props to `bundles.yaml`
- export bundle secrets to `bundles.secrets.yaml`

But never export:

- user props
- user secrets

That same split should hold in cloud:

- deployment-scoped bundle state is exportable/configurable
- user-scoped state is operational runtime data

## CLI export

Current CLI export path:

```bash
kdcube \
  --export-live-bundles \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

This exports:

- `bundles.yaml`
- `bundles.secrets.yaml`

It does **not** export:

- `secrets.yaml`
- user props
- user secrets
- bundle storage data

When local file-backed descriptors are mounted, `kdcube --export-live-bundles` exports directly from:

- mounted `bundles.yaml`
- mounted `bundles.secrets.yaml` when configured

When `aws-sm` is the authority, it reconstructs the export from grouped AWS Secrets Manager bundle docs.

## Decision rules

Use:

- `self.bundle_prop(...)` for effective non-secret bundle config
- `get_plain(...)` for raw descriptor inspection
- `get_secret("b:...")` for deployment-scoped bundle secrets
- `get_user_prop(...)` / `set_user_prop(...)` for per-user non-secret bundle state
- `get_user_secret(...)` / `set_user_secret(...)` for per-user secret state
- bundle storage APIs for mutable business data

Do not use bundle props or secrets for:

- conversation/business records
- large mutable data
- artifacts

## Related docs

- authoring guide: [bundle-dev-README.md](bundle-dev-README.md)
- runtime surfaces: [bundle-runtime-README.md](bundle-runtime-README.md)
- lifecycle and storage surfaces: [bundle-lifecycle-README.md](bundle-lifecycle-README.md)
- reserved platform props: [bundle-platform-properties-README.md](bundle-platform-properties-README.md)
- deployment config format:
  [../../service/configuration/bundle-configuration-README.md](../../service/configuration/bundle-configuration-README.md)
