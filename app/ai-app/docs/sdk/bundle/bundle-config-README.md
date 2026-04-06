---
id: ks:docs/sdk/bundle/bundle-config-README.md
title: "Bundle Configuration"
summary: "How bundle defaults, bundle props, reserved platform properties, and secrets fit together for bundle developers."
tags: ["sdk", "bundle", "configuration", "secrets", "bundle_props"]
keywords: ["bundle_props", "configuration", "get_secret", "bundles.yaml", "redis overrides", "platform properties"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
---
# Bundle Configuration

This doc is the bundle-developer view of configuration and secrets.

## Four inputs

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

## Effective precedence

For non-secret config, effective precedence is:

1. code defaults
2. `bundles.yaml`
3. runtime/admin overrides

The result is exposed to the bundle as `bundle_props`.

## What to use where

| Need | Use |
|---|---|
| non-secret default config in code | `configuration` / `bundle_props_defaults` |
| non-secret deploy-time config | `bundles.yaml` |
| non-secret live override | admin/runtime bundle props override |
| secret values | `get_secret("dot.path.key")` |
| platform-reserved behavior knobs | reserved bundle property paths |

## Non-secret config

Read effective non-secret config from:
- `self.bundle_props`
- `self.bundle_prop("dot.path", default=...)`

Typical examples:
- role/model selection
- MCP connector config
- bundle feature flags
- runtime profiles
- knowledge-source selection

## Secrets

Read secrets with:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

token = get_secret("bundles.my.bundle.secrets.api.token")
```

Use secrets for:
- API keys
- bearer tokens
- git credentials
- external connector credentials

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

## Where to define defaults

Use bundle code defaults for stable defaults that should travel with the bundle:

```python
@property
def configuration(self):
    config = dict(super().configuration)
    config.setdefault("my_feature", {"enabled": True})
    return config
```

Use `setdefault(...)` patterns so external overrides can still win.

## Config vs storage

Do not confuse configuration with stored state:

- configuration says how the bundle should behave
- storage holds data the bundle produces or caches

Examples:
- `bundle_props["knowledge"]["repo"]` is configuration
- cloned repo files under `BUNDLE_STORAGE_ROOT/...` are storage

## Related docs

- authoring guide: [bundle-dev-README.md](bundle-dev-README.md)
- lifecycle and storage surfaces: [bundle-lifecycle-README.md](bundle-lifecycle-README.md)
- storage backends: [bundle-storage-cache-README.md](bundle-storage-cache-README.md)
- external/source-of-truth config and secrets format:
  [../../service/configuration/bundle-configuration-README.md](../../service/configuration/bundle-configuration-README.md)
