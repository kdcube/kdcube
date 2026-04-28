---
id: ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
title: "Bundle Runtime Settings, Configuration, and Secrets"
summary: "Canonical author-facing configuration model for bundle code: how platform settings, bundle props and secrets, and user-scoped state are read, written, owned, stored, and exported."
tags: ["sdk", "configuration", "bundle", "props", "secrets"]
keywords: ["programmatic configuration access", "platform settings and secrets", "bundle scoped props and secrets", "user scoped props and secrets", "helper api selection", "ownership boundary", "live authority and export rules", "get_settings and get_secret", "bundle_prop and set_bundle_prop", "user prop and user secret CRUD"]
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-reserved-platform-properties-README.md
  - ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
---
# Bundle Runtime Settings, Configuration, and Secrets

This is the single SDK page bundle authors should use for programmatic access
to settings, props, and secrets.

Tier 1 rule:

- this page is one part of the Tier 1 pack
- do not treat it as sufficient on its own
- read it together with the Tier 1 test, authoring, and configure/run pages

Use this page when you need to answer questions like:

- which scope does this value belong to
- should this value live in platform/global config, bundle config, or user state
- which helper should read it
- which helper may write it
- where does it actually live at runtime
- what can be exported back out of the system

This page intentionally covers all relevant runtime value classes, not only
bundle-scoped values.

Tier 1 role of this page:

- use it first when your job is configuring a bundle
- use it first when you must translate an existing application's settings into
  KDCube terms
- use it after `how-to-write` when you need to decide where values belong
- use it before `how-to-configure-and-run` when descriptor modeling is still
  unclear

If you need the detailed storage and authority model after that, use:

- [runtime-configuration-and-secrets-store-README.md](runtime-configuration-and-secrets-store-README.md)

If you need the list of reserved bundle prop paths interpreted by the platform,
use:

- [bundle-reserved-platform-properties-README.md](../sdk/bundle/bundle-reserved-platform-properties-README.md)

## The three scopes

There are three scopes bundle authors must reason about:

1. platform/global
2. deployment-scoped bundle
3. user-scoped bundle

Across those scopes, there are six concrete data classes that matter in
practice:

1. platform/global props
2. platform/global secrets
3. deployment-scoped bundle props
4. deployment-scoped bundle secrets
5. user-scoped bundle props
6. user-scoped bundle secrets

Bundle code may read all six classes through the supported helpers.

Normal bundle code should write only:

- deployment-scoped bundle props
- deployment-scoped bundle secrets
- user-scoped bundle props
- user-scoped bundle secrets

Bundle code should not write platform/global props or platform/global secrets.
Those remain deployment-owned.

## Exact scope matrix

| Data class | Read API | Write API from bundle code | Ownership boundary | Live authority today | Export / ejection path |
|---|---|---|---|---|---|
| platform/global props | `get_settings()` for effective values; `get_plain("...")` for raw descriptor inspection | none supported | tenant + project deployment | promoted runtime config assembled from env plus descriptor files such as `assembly.yaml` and `gateway.yaml` | outside `kdcube export`; manage through deployment descriptors |
| platform/global secrets | `get_secret("canonical.key")` | none supported | tenant + project deployment | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | outside `kdcube export`; manage through deployment secret workflows |
| deployment-scoped bundle props | `self.bundle_prop(...)`, `self.bundle_props` | `await set_bundle_prop(...)` | tenant + project + bundle | configured bundle descriptor authority; Redis is the runtime cache. Recommended cloud mode is writable mounted `bundles.yaml` with `BUNDLES_DESCRIPTOR_PROVIDER=file`. | exported to `bundles.yaml`; `kdcube export` includes it |
| deployment-scoped bundle secrets | `get_secret("b:...")` | `await set_bundle_secret(...)` | tenant + project + bundle | configured secrets provider; in local `secrets-file` mode this is `bundles.secrets.yaml` | exported to `bundles.secrets.yaml` when the provider/export flow can reconstruct them |
| user-scoped bundle props | `get_user_prop(...)`, `get_user_props()` | `set_user_prop(...)`, `delete_user_prop(...)` | tenant + project + bundle + user | PostgreSQL `<SCHEMA>.user_bundle_props` | never exported to descriptors or bundle export |
| user-scoped bundle secrets | `get_user_secret(...)` | `set_user_secret(...)`, `delete_user_secret(...)` | tenant + project + bundle + user | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | never exported to descriptors or bundle export |

## Decide the scope before you write code

| If the value belongs to... | Use | Do not use |
|---|---|---|
| the environment or platform deployment as a whole | `get_settings()` or `get_secret("canonical.key")` | `self.bundle_prop(...)` |
| one bundle for the whole deployment | `self.bundle_prop(...)` or `get_secret("b:...")` | user props or user secrets |
| one user inside one bundle | `get_user_prop(...)` or `get_user_secret(...)` | `bundles.yaml` or `bundles.secrets.yaml` |

Examples:

- OpenAI API key for the deployment -> platform/global secret
- auth client id -> platform/global prop
- bundle feature flag or cron expression -> deployment-scoped bundle prop
- bundle webhook token shared by the deployment -> deployment-scoped bundle secret
- one user's theme preference -> user-scoped bundle prop
- one user's personal GitHub token -> user-scoped bundle secret

## Platform/global props and secrets

These are deployment-owned values, not bundle-owned values.

Use:

- `get_settings()` for effective typed runtime settings
- `get_secret("canonical.key")` for deployment-scoped platform/global secrets
- `get_plain("...")` only when you intentionally need the raw descriptor file

Typical examples:

- ports
- auth type and ids
- storage backend selection
- path roots
- deployment-wide API keys shared by many bundles

Do not store these in:

- `bundles.yaml`
- `bundles.secrets.yaml`
- user props
- user secrets

## Deployment-scoped bundle props and secrets

These values belong to one bundle inside one deployment environment.

Read effective bundle props through:

- `self.bundle_props`
- `self.bundle_prop("dot.path", default=...)`

Read deployment-scoped bundle secrets through:

- `get_secret("b:...")`

Write them through:

- `await set_bundle_prop(...)`
- `await set_bundle_secret(...)`

Typical use:

- feature flags
- cron expressions
- model selection
- MCP service configuration
- bundle UI configuration
- bundle-specific shared credentials

Important:

- `self.bundle_prop(...)` reads effective runtime bundle config
- `get_plain("b:...")` reads the raw mounted `bundles.yaml` file only
- these are not the same thing

### Reserved platform-owned bundle props still live here

Some bundle prop paths are interpreted specially by the platform.

They are still ordinary deployment-scoped bundle props from a storage and
ownership perspective.

They are not a fourth scope.

Common reserved paths:

| Path | Who interprets it | Effect |
|---|---|---|
| `role_models` | platform entrypoint/runtime | model-role routing |
| `embedding` | platform entrypoint/runtime | embedding provider/model override |
| `economics.reservation_amount_dollars` | economics entrypoint/runtime | reservation floor |
| `execution.runtime` | runtime/exec subsystem | bundle-level execution runtime routing |
| `exec_runtime` | runtime/exec subsystem | legacy alias for `execution.runtime` |
| `mcp.services` | MCP runtime/bootstrap | MCP transport/auth config |

Use the detailed page for those reserved paths:

- [bundle-reserved-platform-properties-README.md](../sdk/bundle/bundle-reserved-platform-properties-README.md)

## User-scoped bundle props and secrets

These values belong to one user inside one bundle inside one deployment.

Use:

- `get_user_prop(...)`
- `set_user_prop(...)`
- `delete_user_prop(...)`
- `get_user_secret(...)`
- `set_user_secret(...)`
- `delete_user_secret(...)`

Typical use:

- one user's preferences
- one user's personal integration tokens
- one user's bundle-managed non-secret operational state
- one user's bundle-managed secret operational state

Important ownership rule:

- the bundle is the logical owner of this state
- platform descriptors do not become the storage for this state
- if the bundle wants export/import for this state, the bundle must provide its
  own API or workflow

## Export and ejection rules

`kdcube export` is bundle-state export only.

It exports:

- `bundles.yaml`
- `bundles.secrets.yaml`

It does not export:

- `assembly.yaml`
- `gateway.yaml`
- `secrets.yaml`
- user props
- user secrets

So the rule is:

- deployment-scoped bundle config can be ejected back into bundle descriptors
- platform/global deployment config stays in deployment descriptors and
  deployment secret workflows
- user-scoped bundle state remains operational data unless the bundle provides
  its own export path

## What bundle code is allowed to mutate

Supported directly from normal bundle code:

- read platform/global props via `get_settings()`
- read platform/global secrets via `get_secret("canonical.key")`
- read deployment-scoped bundle props via `self.bundle_prop(...)`
- read deployment-scoped bundle secrets via `get_secret("b:...")`
- write deployment-scoped bundle props via `await set_bundle_prop(...)`
- write deployment-scoped bundle secrets via `await set_bundle_secret(...)`
- read/write user-scoped bundle props via `get_user_prop(...)`, `set_user_prop(...)`
- read/write user-scoped bundle secrets via `get_user_secret(...)`, `set_user_secret(...)`

That distinction matters:

- platform/global state is deployment-owned and not writable from normal bundle
  code
- deployment-scoped bundle writes are operational/configuration writes
- user-scoped writes are part of normal bundle runtime behavior

## Raw reads versus effective reads

Use these categories deliberately.

### Effective runtime reads

These are the values the runtime is actually meant to use:

- `get_settings()`
- `get_secret(...)`
- `self.bundle_prop(...)`
- `get_user_prop(...)`
- `get_user_secret(...)`

### Raw descriptor reads

These read mounted files as files:

- `get_plain(...)`
- `read_plain(...)`

They do not:

- merge code defaults
- include Redis bundle-prop overrides
- include user state
- persist changes anywhere

## Storage and authority model

The exact storage and authority model is intentionally documented separately.

Use:

- [runtime-configuration-and-secrets-store-README.md](runtime-configuration-and-secrets-store-README.md)

That page owns:

- mode-specific authority by local file mode vs `aws-sm`
- Redis cache role
- current bundle prop write path
- current bundle secret persistence path
- PostgreSQL and secrets-provider ownership for user state
- grouped AWS SM document layout
