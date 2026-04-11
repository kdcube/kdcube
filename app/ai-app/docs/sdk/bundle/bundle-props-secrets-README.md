---
id: ks:docs/sdk/bundle/bundle-props-secrets-README.md
title: "Bundle Props and Secrets"
summary: "How a bundle reads effective props, raw descriptor config, bundle secrets, and user-scoped secrets, and where each layer is stored."
tags: ["sdk", "bundle", "props", "secrets", "configuration"]
keywords: ["bundle_props", "get_plain", "get_secret", "get_user_secret", "bundles.yaml", "bundles.secrets.yaml", "secrets.yaml", "redis overrides"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
---
# Bundle Props and Secrets

This document is the bundle-developer view of:
- effective bundle props
- raw descriptor reads through `get_plain(...)`
- bundle-level secrets
- user-scoped secrets

The important split is:
- `self.bundle_prop(...)` reads the bundle's effective config
- `get_plain(...)` reads raw mounted descriptor YAML
- `get_secret(...)` reads bundle/platform secrets
- `get_user_secret(...)` reads user-scoped secrets

For secrets, there are two recommended namespaces:
- no prefix or `a:` -> platform/global secrets
- `b:` -> the current bundle's secrets

Fully qualified canonical keys such as `bundles.<bundle_id>.secrets...` and
`users.<user_id>...` are still accepted by the low-level API, but they are the
internal/global form, not the normal bundle-facing form.

## Four config layers

```text
1) Code defaults
   entrypoint.configuration / bundle_props_defaults

2) Bundle descriptor config
   bundles.yaml -> items[].config

3) Runtime/admin overrides
   Redis / admin props API

4) Secrets
   get_secret("dot.path.key")
```

## What to use when

| Need | Use | Backing store |
|---|---|---|
| effective non-secret bundle config | `self.bundle_prop("dot.path")` / `self.bundle_props` | code defaults + `bundles.yaml` + Redis overrides |
| raw deploy-time descriptor values | `get_plain("...")` / `get_plain("b:...")` | mounted `assembly.yaml` / `bundles.yaml` |
| bundle-level secrets | `get_secret("b:dot.path")` | configured secrets provider |
| user-scoped secrets | `get_user_secret("some.key")` | configured secrets provider |
| mutable bundle-owned state | bundle storage, not props | bundle storage backend |

If the data is mutable business state, do not put it into props. Use storage.

## Effective bundle props

Read effective non-secret config from:
- `self.bundle_props`
- `self.bundle_prop("dot.path", default=...)`

For non-secret config, precedence is:
1. code defaults
2. `bundles.yaml`
3. runtime/admin overrides

The merged result is exposed to the bundle as `bundle_props`.

Typical examples:
- role/model selection
- MCP connector config
- runtime profiles
- feature flags
- knowledge-source selection
- scheduled job config through `@cron(..., expr_config=...)`

Example:

```python
enabled = self.bundle_prop("features.sync.enabled", False)
profile = self.bundle_prop("execution.runtime.default_profile", "local")
services = self.bundle_prop("mcp.services", {})
```

### Where effective props come from

```yaml
bundles:
  version: "1"
  items:
    - id: "demo.bundle@1-0"
      config:
        features:
          sync:
            enabled: true
        execution:
          runtime:
            default_profile: "local"
```

That descriptor layer is loaded into Redis per:
- tenant
- project
- bundle

The supported live override path is the admin props API:
- `GET /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props/reset-code`

Runtime/admin overrides are stored in Redis under a per-tenant/project/bundle key.
The current key format is:

```text
kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}
```

### Can a bundle write props on the fly?

Not through `get_plain(...)`.

The supported live write path is the admin/runtime props API, which writes the
override layer into Redis. There is no first-class SDK helper like
`set_bundle_prop(...)`.

If you mutate `self.bundle_props` directly in code, that is only in-memory and
not persisted.

### What resets those props?

- `reset-code` resets the Redis override layer to the bundle's code defaults
- `kdcube --bundle-reload <bundle_id>` is descriptor-authoritative:
  - reapplies the registry from `bundles.yaml`
  - rebuilds the descriptor-backed props layer in Redis
  - clears proc bundle caches
- env/descriptor force-reset paths can also replace the Redis props layer
  authoritatively from `bundles.yaml`

## Raw descriptor reads with `get_plain(...)`

`get_plain(...)` is different from `self.bundle_prop(...)`.

It reads the mounted YAML descriptors directly:
- no prefix or `a:` -> `assembly.yaml`
- `b:` -> `bundles.yaml`

Examples:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_plain

host_bundles_root = get_plain("paths.host_bundles_path")
default_bundle_id = get_plain("b:default_bundle_id")
raw_items = get_plain("b:items", [])
```

What matters:
- it is raw descriptor inspection
- it is cached by descriptor file mtime/size
- it is not tenant/project scoped
- it does not include Redis runtime overrides
- it has no write path

Use it when the bundle needs raw platform or descriptor data, not when it wants
its own effective runtime props.

## Bundle-level secrets

Read secrets with:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

token = get_secret("b:api.token")
```

Namespace rules for `get_secret(...)`:

| Key form | Meaning | Recommended use |
|---|---|---|
| `get_secret("services.openai.api_key")` | platform/global secret | yes |
| `get_secret("a:services.openai.api_key")` | platform/global secret | yes, explicit form |
| `get_secret("b:api.token")` | current bundle secret | yes |
| `get_secret("bundles.demo.bundle@1-0.secrets.api.token")` | fully qualified bundle secret | only for low-level/admin/explicit cross-scope access |
| `get_secret("users.user-1.bundles.demo.bundle@1-0.secrets.api.token")` | fully qualified user/bundle secret | do not use in normal bundle code; use `get_user_secret(...)` |

Use secrets for:
- API keys
- bearer tokens
- git credentials
- external connector credentials
- JSON credentials content such as Google service-account files
- sensitive identifiers such as Cognito pool ids when the bundle treats them as secrets

The underlying canonical bundle secret namespace is:

```text
bundles.<bundle_id>.secrets.<dot.path>
```

But normal bundle code should prefer:

```python
get_secret("b:<dot.path>")
```

because KDCube resolves the current bundle automatically from request context.

### Can a bundle read another bundle's secret?

Normal bundle code should treat the answer as **no**.

Use these rules:
- read your own bundle's deployment secrets via `get_secret("b:...")`
- read platform/global shared secrets via `get_secret("...")` or `get_secret("a:...")`
- read user-scoped secrets via `get_user_secret(...)`

Today, the low-level API still accepts fully qualified canonical keys, so code
that explicitly asks for `bundles.<other_bundle_id>.secrets...` can bypass the
nice `b:` shorthand. That is an internal/administrative capability, not the
recommended bundle contract. Bundle code should not depend on cross-bundle
secret reads.

If two bundles need the same secret, choose one of these instead:
- move it to platform/global scope
- duplicate it into each bundle's own secret scope
- expose the needed capability through an API, not direct secret sharing

`get_secret(...)` is backed by the configured runtime secrets provider. Current
provider modes are:
- `secrets-service`
- `aws-sm`
- `secrets-file`
- `in-memory`

`secrets-file` reads `secrets.yaml` and `bundles.secrets.yaml` directly through
the storage backend (`file://...` or `s3://...`). It is useful for local
debugging, static deployments, and descriptor-driven setups. Admin/UI secret
updates persist back into those descriptors when the backing location is writable.

Do not put secrets into:
- `bundle_props`
- `bundles.yaml` config blocks
- long-lived logs or generated artifacts

Example bundle secret descriptor:

```yaml
bundles:
  version: "1"
  items:
    - id: "demo.bundle@1-0"
      secrets:
        integrations:
          docs_api_token: "..."
```

Supported admin/API write path:
- `POST /admin/integrations/bundles/{bundle_id}/secrets`

List current known keys:
- `GET /admin/integrations/bundles/{bundle_id}/secrets`

## User-scoped secrets

Use user-scoped secrets when the secret belongs to the current user inside the
bundle, not to the deployment.

Read and write from bundle code:

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

For normal bundle code, prefer these helpers over raw `get_secret("users....")`
keys. They keep the scope explicit and use the current request context
correctly.

If `user_id` and `bundle_id` are omitted, KDCube resolves them from the current
request context.

The stored logical key is:

```text
users.<user_id>.bundles.<bundle_id>.secrets.<dot.path>
```

If there is no bundle scope, the user-secret namespace falls back to:

```text
users.<user_id>.secrets.<dot.path>
```

Supported REST write path for the current authenticated user:
- `POST /bundles/{tenant}/{project}/{bundle_id}/user-secrets`

### Where bundle and user secrets are stored

That depends on the configured provider:

| Provider | Bundle-level secrets | User-scoped secrets |
|---|---|---|
| `secrets-service` | secrets service | secrets service |
| `aws-sm` | AWS Secrets Manager | AWS Secrets Manager |
| `secrets-file` | `bundles.secrets.yaml` | `secrets.yaml` |
| `in-memory` | process memory | process memory |

For `secrets-file`, the split is important:
- keys under `bundles.<bundle_id>.secrets...` go to `bundles.secrets.yaml`
- user keys under `users.<user_id>...` go to `secrets.yaml`

## Practical decision rules

Use:
- `self.bundle_prop(...)` when bundle code needs its effective config
- `get_plain(...)` when code must inspect raw descriptor YAML
- `get_secret(...)` for deployment- or bundle-scoped secret values
- `get_user_secret(...)` for per-user secret values
- bundle storage for mutable non-secret business state

## Reserved platform properties

Most bundle props are bundle-defined.

Some property paths are reserved by the platform, for example:
- `role_models`
- `embedding`
- `economics.reservation_amount_dollars`
- `execution.runtime`
- `mcp.services`

Canonical reference:
- [bundle-platform-properties-README.md](bundle-platform-properties-README.md)

## Defaults in code

Use bundle code defaults for stable defaults that should travel with the bundle:

```python
@property
def configuration(self):
    config = dict(super().configuration)
    config.setdefault("my_feature", {"enabled": True})
    return config
```

Use `setdefault(...)` patterns so external overrides can still win.

## Local prototyping loop

For localhost bundle iteration, keep the split clear:

1. code defaults
2. descriptor-backed config in `bundles.yaml`
3. runtime/admin overrides in Redis

Recommended local flow:
- mount one host bundles root into proc as `/bundles`
- in `bundles.yaml`, point the bundle to the container-visible path such as `/bundles/my.bundle`
- edit code and/or `bundles.yaml`
- run:
  - `kdcube --workdir <runtime-workdir> --bundle-reload <bundle_id>`

That reload is descriptor-authoritative and is the right path when the
descriptor is your source of truth.

## Related docs

- authoring guide: [bundle-dev-README.md](bundle-dev-README.md)
- lifecycle and storage surfaces: [bundle-lifecycle-README.md](bundle-lifecycle-README.md)
- runtime surfaces: [bundle-runtime-README.md](bundle-runtime-README.md)
- storage backends: [bundle-storage-cache-README.md](bundle-storage-cache-README.md)
- reserved platform props: [bundle-platform-properties-README.md](bundle-platform-properties-README.md)
- external/source-of-truth config and secrets format:
  [../../service/configuration/bundle-configuration-README.md](../../service/configuration/bundle-configuration-README.md)
