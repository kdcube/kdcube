---
id: ks:docs/sdk/bundle/bundle-props-secrets-README.md
title: "Bundle Props and Secrets"
summary: "Bundle-facing config and secret scopes, their read/write APIs, and the current source-of-truth/storage model for local and production deployments."
tags: ["sdk", "bundle", "props", "secrets", "configuration"]
keywords: ["bundle_props", "get_plain", "get_secret", "get_user_secret", "bundles.yaml", "bundles.secrets.yaml", "secrets.yaml", "redis overrides"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/bundles-secrets-descriptor-README.md
---
# Bundle Props and Secrets

This document defines the exact config and secret scopes bundle authors interact
with.

There are three scopes:

1. platform/global
2. deployment-scoped bundle
3. user-scoped bundle

Across those scopes, there are six concrete state classes that matter in
practice:

1. platform/global props
2. platform/global secrets
3. deployment-scoped bundle props
4. deployment-scoped bundle secrets
5. user-scoped bundle props
6. user-scoped bundle secrets

Bundle code may read all six classes through the supported helpers, but normal
bundle code should write only:

- deployment-scoped bundle props
- deployment-scoped bundle secrets
- user-scoped bundle props
- user-scoped bundle secrets

Bundle code should not write platform/global props or platform/global secrets.
Those remain deployment-owned.

## Exact scope matrix

| Data class | Read API | Write API from bundle code | Scope | Live authority today | Export / ejection path |
|---|---|---|---|---|---|
| platform/global props | `get_settings()` for effective values; `get_plain("...")` for raw `assembly.yaml` reads | none supported | tenant + project deployment | promoted runtime config assembled from env plus descriptor files such as `assembly.yaml` and `gateway.yaml` | outside `kdcube --export-live-bundles`; manage through deployment descriptors, not bundle export |
| platform/global secrets | `get_secret("canonical.key")` | none supported | tenant + project deployment | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | outside `kdcube --export-live-bundles`; manage through deployment secret workflows, not bundle export |
| deployment-scoped bundle props | `self.bundle_prop(...)`, `self.bundle_props` | `await set_bundle_prop(...)` | tenant + project + bundle | configured bundle descriptor authority; Redis is the runtime cache. In ECS the recommended authority is writable mounted `bundles.yaml` on EFS with `BUNDLES_DESCRIPTOR_PROVIDER=file`. | exported to `bundles.yaml`; `kdcube --export-live-bundles` includes it |
| deployment-scoped bundle secrets | `get_secret("b:...")` | `await set_bundle_secret(...)` | tenant + project + bundle | configured secrets provider; in local `secrets-file` mode this is `bundles.secrets.yaml` | exported to `bundles.secrets.yaml`; `kdcube --export-live-bundles` includes it when the provider/export flow can reconstruct bundle secrets |
| user-scoped bundle props | `get_user_prop(...)`, `get_user_props()` | `set_user_prop(...)`, `delete_user_prop(...)` | tenant + project + bundle + user | PostgreSQL `<SCHEMA>.user_bundle_props` | never exported to descriptors or bundle export |
| user-scoped bundle secrets | `get_user_secret(...)` | `set_user_secret(...)`, `delete_user_secret(...)` | tenant + project + bundle + user | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | never exported to descriptors or bundle export |

The most important split is:

- `get_settings()` reads effective platform/global runtime config
- `get_secret("canonical.key")` reads deployment-scoped platform/global secrets
- `self.bundle_prop(...)` reads the bundle's effective non-secret runtime config
- `get_plain(...)` reads raw mounted descriptor files only
- `get_secret("b:...")` reads deployment-scoped bundle secrets
- `get_user_prop(...)` / `set_user_prop(...)` are user-scoped non-secret state
- `get_user_secret(...)` / `set_user_secret(...)` are user-scoped secret state

If the data is mutable business data rather than config or credentials, use bundle storage instead.

## Use the right scope

| If the value belongs to... | Use | Do not use |
|---|---|---|
| the environment or platform deployment as a whole | `get_settings()` or `get_secret("canonical.key")` | `self.bundle_prop(...)` |
| one bundle for the whole deployment | `self.bundle_prop(...)` or `get_secret("b:...")` | user props/secrets |
| one user inside one bundle | `get_user_prop(...)` or `get_user_secret(...)` | `bundles.yaml`, `bundles.secrets.yaml` |

Examples:

- OpenAI API key for the deployment -> platform/global secret
- bundle feature flag or cron expression -> deployment-scoped bundle prop
- bundle webhook token shared by the deployment -> deployment-scoped bundle secret
- one user's theme preference -> user-scoped bundle prop
- one user's personal GitHub token -> user-scoped bundle secret

## What is actually supported today

### From inside bundle code

Supported directly:

- read platform/global props via `get_settings()`
- read platform/global secrets via `get_secret("canonical.key")`
- read deployment-scoped bundle props via `self.bundle_prop(...)`
- read deployment-scoped bundle secrets via `get_secret("b:...")`
- write deployment-scoped bundle props via `await set_bundle_prop(...)`
- write deployment-scoped bundle secrets via `await set_bundle_secret(...)`
- read/write user-scoped bundle props via `get_user_prop(...)`, `set_user_prop(...)`
- read/write user-scoped bundle secrets via `get_user_secret(...)`, `set_user_secret(...)`

That distinction matters:

- platform/global state is deployment-owned and not writable from normal bundle code
- user-scoped writes are part of normal bundle runtime behavior
- deployment-scoped bundle writes are still operational/configuration writes

## Platform/global props and secrets

These are not bundle-scoped values.

Use:

- `get_settings()` for effective typed runtime settings
- `get_secret("canonical.key")` for deployment-scoped platform/global secrets
- `get_plain("...")` only when you intentionally need the raw descriptor file

Examples:

- ports
- auth client ids
- storage backend selection
- external service deployment keys shared by many bundles

Do not store these in:

- `bundles.yaml`
- `bundles.secrets.yaml`
- user props
- user secrets

Do not expect them from:

- `self.bundle_prop(...)`
- `kdcube --export-live-bundles`

Operational export rule:

- deployment-scoped bundle export is only for `bundles.yaml` and
  `bundles.secrets.yaml`
- platform/global config stays in deployment descriptors and deployment secrets
- user-scoped data stays operational and is never part of descriptor export

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

`bundles.yaml` is the deployment-scoped bundle descriptor.

That means the intended source-of-truth policy is:

- deployment-scoped bundle prop changes should be persisted into `bundles.yaml`
- exports should reconstruct `bundles.yaml` from the current deployment-scoped bundle state
- user-scoped state should remain outside descriptors

For cloud deployments, the recommended architecture is:

- keep secrets in the configured secrets provider such as AWS Secrets Manager
- keep deployment-scoped bundle descriptors and non-secret bundle props in writable mounted `bundles.yaml`
- set `BUNDLES_DESCRIPTOR_PROVIDER=file`

Reason:

- bundle props are configuration, not secrets
- this avoids single-document size limits in Secrets Manager
- it keeps the deployment-scoped bundle state exportable as YAML

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
- the same write also persists into the configured bundle descriptor authority
  - recommended cloud mode: writable mounted `bundles.yaml`
  - optional fallback mode: grouped bundle descriptor docs in `aws-sm`
- the write publishes `bundles.props.update` on Redis
- proc listens to that channel and reconciles bundle-scheduler state in
  [processor.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py)

So the current implementation is:

| Mode | Runtime read authority | Persistent authority today |
|---|---|---|
| `BUNDLES_DESCRIPTOR_PROVIDER=file` | Redis, backfilled from mounted `bundles.yaml` | mounted writable `bundles.yaml` |
| `BUNDLES_DESCRIPTOR_PROVIDER=aws-sm` | Redis, backfilled from grouped bundle descriptor docs | grouped bundle descriptor docs in AWS Secrets Manager |
| other non-file providers | Redis | provider-specific operational path if configured |

The important runtime guarantee is:

- bundles can update their own deployment-scoped props
- bundle admin can update deployment-scoped props
- both paths persist the same effective props into the authoritative store
- proc reacts to the props update channel so scheduler-driven behavior stays aligned

### Descriptor reset behavior

These paths are descriptor-authoritative today:

- `kdcube --bundle-reload <bundle_id>`
- env/descriptor force-reset paths
- startup reset paths that replay the current bundle descriptor authority

Those paths replace Redis bundle props from the configured bundle descriptor authority.
In the recommended cloud setup, that authority is the mounted `bundles.yaml`.

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

### Example: bundle-defined inbound auth contract

For bundle-authenticated MCP or public API hooks, a clean split is:

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

That gives the bundle a stable contract with its clients:

- the prop tells clients which header name to send
- the secret stores the expected token
- proc does not verify it for MCP, and does not verify it for
  `@api(..., route="public", public_auth="bundle")`; the bundle verifies it
  itself

Use this for any bundle-owned inbound auth contract. For full code, use the
worked examples in [bundle-transports-README.md](bundle-transports-README.md).

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
  [../../service/configuration/bundles-descriptor-README.md](../../service/configuration/bundles-descriptor-README.md)
