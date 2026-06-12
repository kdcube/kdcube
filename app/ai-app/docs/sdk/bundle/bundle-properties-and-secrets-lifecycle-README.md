---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
title: "Bundle Properties And Secrets Lifecycle"
summary: "Concise bundle-author lifecycle for code defaults, descriptor/admin bundle props, effective runtime props, bundle secrets, user-scoped state, and what is merged versus only stored as authority."
tags: ["sdk", "bundle", "configuration", "props", "secrets", "lifecycle", "descriptor"]
keywords: ["bundle props lifecycle", "bundle secrets lifecycle", "configuration_defaults", "bundle_props_defaults", "effective bundle props", "bundles.yaml config", "bundles.secrets.yaml", "descriptor authority", "redis bundle props cache", "set_bundle_prop", "set_bundle_secret", "bundle props merge", "bundle props materialization"]
updated_at: 2026-05-22
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-reserved-platform-properties-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
---
# Bundle Properties And Secrets Lifecycle

This page is the concise bundle-author view of bundle props and secrets.

Use it when you need to know:

- where `configuration_defaults()` fits
- whether descriptor values are merged with code defaults
- what `self.bundle_prop(...)` reads
- what is written to `bundles.yaml` or `bundles.secrets.yaml`
- why Bundle Admin can show `defaults` and `props` separately
- why secrets, user props, and request context are not part of the same merge

For the full cross-platform configuration model, use
[Bundle Runtime Settings, Configuration, and Secrets](../../configuration/bundle-runtime-configuration-and-secrets-README.md).
For the store-level authority model, use
[Runtime Configuration and Secrets Store](../../configuration/runtime-configuration-and-secrets-store-README.md).

## Bundle Code Access Contract

Use helpers, not backing fields:

| Need | Read API |
| --- | --- |
| Effective non-secret bundle config | `self.bundle_prop("path.to.value", default)` |
| Whole effective props snapshot | `dict(self.bundle_props or {})` only when a snapshot is required |
| Current bundle deployment secret | `await get_secret("b:path.to.secret")` |
| Sync-only code path | `get_secret("b:path.to.secret")` |

Do not read deployment secrets from `self.bundle_secrets`, `config.secrets`, or
raw descriptor helpers. Secrets have no code defaults and are not merged with
props.

## Terms

| Term | Meaning |
| --- | --- |
| Code defaults | Non-secret bundle defaults declared by bundle code, usually through `configuration_defaults()` and the `configuration` property on a `BaseEntrypoint` family class. |
| Descriptor/admin props | Non-secret deployment-scoped bundle overrides stored in the configured bundle descriptor authority, usually `bundles.yaml -> bundles.items[].config` in file-backed mode. |
| Effective bundle props | Runtime view used by bundle code and platform route checks: code defaults deep-merged with descriptor/admin props. |
| Bundle secrets | Deployment-scoped bundle secrets stored in the configured secrets provider, represented as `bundles.secrets.yaml` in local secrets-file mode or provider-backed state in cloud mode. |
| User-scoped state | Per-user bundle props/secrets. This is separate operational state and is not merged into deployment-scoped bundle props. |

## Lifecycle Diagram

```text
Bundle source code
  entrypoint.configuration_defaults()
  entrypoint.configuration
          |
          v
  bundle_props_defaults
  (code defaults only)
          |
          | deep merge at runtime
          v
+-----------------------------+
| effective bundle props      |
| self.bundle_prop("a.b")     |
+-----------------------------+
          ^
          |
          | deployment/admin overrides
          |
bundles.yaml / descriptor authority
  bundles.items[].config
          ^
          |
          | set_bundle_prop(...)
          | Bundle Admin props write
          | kdcube bundle --set-config
          |
operator / bundle code writes
```

Runtime cache and notification path:

```text
descriptor authority
      |
      | load or write
      v
Redis bundle-props cache
(descriptor/admin props, not code defaults)
      |
      | publish bundles.props.update
      v
cached bundle instance
      |
      | refresh_bundle_props()
      v
deep_merge(bundle_props_defaults, descriptor/admin props)
      |
      v
on_props_changed(previous_props, current_props, ...)
```

Request-time REST/widget/MCP path:

```text
incoming operation/widget/MCP request
      |
      v
load or reuse entrypoint instance
      |
      v
read descriptor/admin props from authority
      |
      v
_apply_rest_bundle_props_to_workflow(...)
      |
      v
deep_merge(code defaults, descriptor/admin props)
      |
      v
platform checks + bundle handler
enabled.*, visibility, ui.widgets.*, role_models, embedding, execution.runtime
```

Secrets use a separate path:

```text
bundles.secrets.yaml or configured secrets provider
      |
      v
await get_secret("b:group.key")

bundle service override:
      |
      v
await get_secret("openai.api_key")
      |
      +-- first: current bundle secret services.openai.api_key
      +-- then: platform/global secret services.openai.api_key
```

Descriptor apply/export path:

```text
seed descriptor directory
bundles.yaml + bundles.secrets.yaml
          |
          | kdcube bundle config apply --descriptors-location <dir>
          v
active runtime bundle authority
workdir/config/bundles.yaml + bundles.secrets.yaml
          |
          | Bundle Admin / set_bundle_prop / set_bundle_secret
          v
new live deployment-scoped bundle state
          |
          | kdcube config export --out-dir <dir>
          v
reviewable descriptor output
bundles.yaml + bundles.secrets.yaml
```

`bundle config apply` is a user/operator action for reapplying seed bundle
descriptors to an existing runtime. It does not touch `assembly.yaml`,
`gateway.yaml`, or `secrets.yaml`. Export is the reverse safety valve: use it
before replacing runtime bundle descriptors when Bundle Admin or runtime writes
may have made the live state newer than the seed files.

## What Is Merged

Effective runtime bundle props are merged.

For `BaseEntrypoint` family bundles:

1. Constructor initializes `self.bundle_props` from `bundle_props_defaults`.
2. `bundle_props_defaults` is computed from code defaults with external props temporarily absent.
3. `on_bundle_load()` calls `refresh_bundle_props(...)` when tenant/project are known.
4. `refresh_bundle_props(...)` reads descriptor/admin props from the configured authority or Redis cache and deep-merges them over code defaults.
5. REST, MCP, public API, and widget routes also apply descriptor/admin props to the workflow before evaluating route checks or calling decorated methods.

This means bundle code should normally read:

```python
self.bundle_prop("my_feature.enabled", default=True)
```

Those are effective runtime props, not raw descriptor reads.

Merge semantics:

- dictionaries merge recursively
- descriptor/admin values override code defaults at the same path
- scalar values and lists replace the default value at that path
- reserved platform paths such as `enabled.*`, `role_models`, `embedding`,
  `ui.widgets.*`, `execution.runtime`, and `mcp.services` are still stored in
  the same bundle props layer; they are special only because platform code
  interprets them

## What Is Not Merged Or Materialized

The effective merge does not mean every store is rewritten with every default.

| Thing | Behavior |
| --- | --- |
| `bundles.yaml` | Stores descriptor/admin props, normally deployment overrides under `items[].config`. Code defaults are not automatically materialized into the file just because a bundle loads. |
| Bundle Admin props read | Returns persisted `props` and code `defaults` separately. The UI may present an effective view, but the API keeps both pieces visible. |
| Bundle Admin merge write | Merges the submitted patch into the persisted descriptor/admin props, not into a hidden copy of every code default. |
| `reset-code` admin action | Intentionally materializes current code defaults into persisted bundle props. Use it only when that is the desired operator action. |
| `read_plain("b:...")` / `get_plain("b:...")` | Reads raw descriptor data. It is not the same as `self.bundle_prop(...)`. |
| YAML anchors and aliases | Parsed before runtime sees the document. A later admin write serializes materialized values, not the original anchor links. |
| Bundle secrets | Not merged into bundle props. They are read through the secrets provider. |
| User-scoped props/secrets | Not merged into deployment-scoped bundle props or exported to descriptors. |
| `bundle_call_context` | Request-scoped overlay for the current invocation only. It is not durable config. |

## Where Each Surface Reads From

| Surface | Reads | Notes |
| --- | --- | --- |
| Bundle Python code | `self.bundle_prop(...)` | Effective props after code defaults and descriptor/admin props are merged. Use `self.bundle_props` only when a full snapshot is required. |
| API/MCP/widget route visibility | Effective workflow props | The route layer applies descriptor/admin props to the workflow before checking `enabled.*`, roles, visibility overrides, and other platform-interpreted paths. |
| Source-folder widget serving | Effective workflow props | `ui.widgets.<alias>.src_folder` and `build_command` may live in code defaults, with descriptors carrying only deployment overrides. |
| Bundle Admin props GET | `props` plus `defaults` | Exposes persisted props and code defaults separately so an admin can see both authority and default shape. |
| Bundle Admin props write | Descriptor/admin props authority | Updates persisted non-secret bundle props, refreshes Redis cache, and publishes a props update. |
| Raw descriptor helpers | Descriptor file/value only | Use only when you intentionally need raw deployment descriptor state. |
| Secrets helpers | Configured secrets provider | `b:` keys resolve within the current bundle. Service helpers resolve bundle-specific provider keys before platform/global fallback. |

## Source-Folder Widgets

For buildable widgets, the source/build config belongs in bundle defaults when
it is intrinsic to the bundle:

```python
def configuration_defaults(self):
    base = dict(super().configuration_defaults())
    ui = dict(base.get("ui") or {})
    widgets = dict(ui.get("widgets") or {})
    widgets["news"] = {
        "enabled": True,
        "src_folder": "ui/widgets/news",
        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
    }
    ui["widgets"] = widgets
    base["ui"] = ui
    return base
```

Descriptors should carry intentional deployment overrides:

```yaml
bundles:
  items:
    - id: news@2026-05-20-12-05
      config:
        enabled:
          widget:
            news: false
```

It is also acceptable for a seed descriptor to repeat `src_folder` and
`build_command` when the descriptor is meant to be self-documenting or when an
older runtime must be supported. In the current runtime contract, route-time
static widget serving evaluates effective props after merging code defaults and
descriptor/admin props.

If a source-folder widget is discovered but no static artifacts are built,
debug in this order:

1. entrypoint inherits a concrete `BaseEntrypoint` family class or implements
   the same `_ensure_ui_build(...)` contract
2. `@ui_widget(alias=...)` alias matches `ui.widgets.<alias>`
3. effective props contain `ui.widgets.<alias>.src_folder` and `build_command`
4. build command writes to `OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH>`
5. runtime logs do not show a bundle import/load failure

## Secrets Lifecycle

Bundle secrets are deployment-scoped and separate from non-secret bundle props.

Read a bundle secret:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

token = await get_secret("b:integrations.telegram.bot_token")
```

Write a deployment-scoped bundle secret:

```python
from kdcube_ai_app.apps.chat.sdk.config import set_bundle_secret

await set_bundle_secret("integrations.telegram.bot_token", token)
```

Use service-secret helpers for provider keys that support bundle-specific
override plus platform fallback:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

api_key = await get_secret("openai.api_key")
```

Resolution order for service secrets:

1. current bundle secret `services.<provider>.<key>`
2. platform/global secret `services.<provider>.<key>`

Secrets are never read from `bundles.yaml`, and bundle props are never used to
store secret values.

## Write Path

Use supported helpers or platform APIs rather than editing runtime files from
bundle code.

| Operation | Effect |
| --- | --- |
| `await set_bundle_prop(path, value)` | Persists deployment-scoped non-secret bundle prop through the configured bundle descriptor authority, updates Redis cache, and publishes `bundles.props.update`. |
| Bundle Admin props merge | Same authority/cache/update path, with admin actor metadata. |
| `kdcube bundle --set-config ...` | Patches the staged runtime descriptor for the active workdir and should be followed by reload when runtime behavior must change. |
| `kdcube bundle config apply --descriptors-location ...` | Reapplies seed `bundles.yaml` and optional `bundles.secrets.yaml` into an existing runtime. This is a user/operator descriptor-authority action, not a platform refresh. |
| `kdcube config export --out-dir ...` | Exports deployment-scoped live bundle descriptors back to `bundles.yaml` and `bundles.secrets.yaml` for review or seed descriptor updates. |
| `await set_bundle_secret(path, value)` | Persists deployment-scoped bundle secret through the configured secrets provider. |
| Bundle Admin secrets write | Writes bundle secrets through the configured secrets provider and tracks key metadata for admin/export flows. |

## Practical Rules

- Put stable, non-secret bundle-owned defaults in `configuration_defaults()`.
- Put deployment-specific non-secret overrides in `bundles.yaml` or through
  Bundle Admin/CLI config writes.
- Put deployment-scoped bundle secrets in `bundles.secrets.yaml` or the
  configured secrets provider.
- Put per-user state in user props/secrets, not in deployment bundle props.
- Use `self.bundle_prop(...)` for effective runtime config.
- Use `get_plain("b:...")` only for raw descriptor inspection.
- Do not expect code defaults to appear in `bundles.yaml` unless an operator
  explicitly materializes them through reset/export/write flows.
