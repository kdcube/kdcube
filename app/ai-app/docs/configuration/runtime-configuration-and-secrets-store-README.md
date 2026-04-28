---
id: ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
title: "Runtime Configuration and Secrets Store"
summary: "Detailed runtime storage model behind settings, props, and secrets helpers: authoritative stores by mode, Redis cache behavior, PostgreSQL user props, provider-backed secrets, and export boundaries."
tags: ["service", "configuration", "runtime", "storage", "secrets", "helpers"]
keywords: ["authoritative configuration stores", "redis effective bundle props cache", "postgres user bundle properties", "provider backed user secrets", "bundle prop persistence path", "bundle secret persistence path", "grouped aws secrets layout", "descriptor authority by mode", "exportable versus non exportable state", "runtime storage model"]
see_also:
  - ks:docs/configuration/runtime-read-write-contract-README.md
  - ks:docs/configuration/service-runtime-configuration-mapping-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
---
# Runtime Configuration and Secrets Store

This page documents the detailed storage and authority model behind runtime
configuration and secret helpers.

Use this page when you need to know:

- which store is authoritative for each data class
- what Redis does and does not own
- what changes in local file mode vs `aws-sm`
- where bundle prop writes persist
- what `kdcube export` can reconstruct

If you are deciding where a value belongs as a bundle author, start with:

- [bundle-runtime-configuration-and-secrets-README.md](bundle-runtime-configuration-and-secrets-README.md)

If you need the helper API contract, use:

- [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)

## The six data classes

| Data class | Authority today | Runtime cache | Export behavior |
|---|---|---|---|
| platform/global props | promoted runtime config assembled from env plus descriptor files such as `assembly.yaml` and `gateway.yaml` | none as a dedicated separate config store | outside bundle export |
| platform/global secrets | configured secrets provider; in local `secrets-file` mode this is `secrets.yaml` | none as a separate dedicated cache | outside bundle export |
| deployment-scoped bundle props | configured bundle descriptor authority | Redis effective bundle props cache | exported to `bundles.yaml` |
| deployment-scoped bundle secrets | configured secrets provider; in local `secrets-file` mode this is `bundles.secrets.yaml` | provider-backed lookup; no separate Redis secret store | exported to `bundles.secrets.yaml` only when provider/export flow can reconstruct them |
| user-scoped bundle props | PostgreSQL `<SCHEMA>.user_bundle_props` | no separate Redis authority | never exported |
| user-scoped bundle secrets | configured secrets provider | no separate Redis authority | never exported |

## Mode-specific authority

### CLI local compose

| Data class | Authority |
|---|---|
| platform/runtime non-secret config | staged descriptors under `/config`, plus env |
| deployment-scoped bundle props | mounted `bundles.yaml`, with Redis as runtime cache |
| deployment-scoped bundle secrets | `bundles.secrets.yaml` only when `secrets-file` is active; otherwise configured provider |
| platform/global secrets | `secrets.yaml` only when `secrets-file` is active; otherwise configured provider |
| user props | PostgreSQL |
| user secrets | configured secrets provider |

### Direct local service run

| Data class | Authority |
|---|---|
| platform/runtime non-secret config | files and env passed explicitly to the process |
| deployment-scoped bundle props | mounted or explicitly addressed `bundles.yaml`, with Redis runtime cache if used |
| deployment-scoped bundle secrets | `bundles.secrets.yaml` in `secrets-file` mode, otherwise configured provider |
| platform/global secrets | `secrets.yaml` in `secrets-file` mode, otherwise configured provider |
| user props | PostgreSQL |
| user secrets | configured secrets provider |

### AWS deployment (`aws-sm`)

| Data class | Authority |
|---|---|
| platform/runtime non-secret config | deployment env plus descriptor-backed runtime snapshots |
| deployment-scoped bundle props | configured bundle descriptor authority plus Redis cache; recommended ECS mode is mounted writable `bundles.yaml` on EFS with `BUNDLES_DESCRIPTOR_PROVIDER=file` |
| deployment-scoped bundle secrets | configured secrets provider; for `aws-sm` this is grouped bundle secret state |
| platform/global secrets | configured secrets provider with current grouped/canonical mixed contract where applicable |
| user props | PostgreSQL |
| user secrets | configured secrets provider under user-scoped keys |

Important:

- `SECRETS_PROVIDER` does not have to own deployment-scoped bundle props
- in recommended ECS deployments, keep secrets in AWS SM
- keep deployment-scoped bundle descriptors and non-secret bundle props in
  mounted writable `bundles.yaml`
- set `BUNDLES_DESCRIPTOR_PROVIDER=file`

## Redis role in bundle props

Redis is the runtime cache for effective deployment-scoped bundle props.

The cache key is:

```text
kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}
```

What Redis does:

- serves effective bundle props at runtime
- receives updates from bundle-admin or bundle-code write paths
- publishes bundle-prop update events for proc reconciliation

What Redis does not do:

- it is not the intended long-term authoritative descriptor store
- it is not the export source of truth by itself

If Redis misses:

- file-backed mode backfills from mounted `bundles.yaml`
- `aws-sm` mode backfills from the grouped bundle descriptor authority

## Current write behavior for deployment-scoped bundle props

`set_bundle_prop(...)` is not “write one Redis key and stop”.

Current behavior:

1. write the effective prop update into Redis
2. persist the same change into the configured bundle descriptor authority
3. publish `bundles.props.update` on Redis
4. proc listens to that channel and reconciles scheduler-driven runtime state

That is why `self.bundle_prop(...)` must be treated as effective deployment
config, not as a raw file read.

## Current persistence target for deployment-scoped bundle secrets

`set_bundle_secret(...)` persists into the configured secrets provider.

That means:

- local `secrets-file` mode persists into `bundles.secrets.yaml`
- provider-backed modes persist into the configured provider state

Bundle secrets are not meant to become plain non-secret descriptor data.

## User-scoped state storage

User-scoped bundle state is intentionally outside descriptors.

| Data class | Store |
|---|---|
| user-scoped bundle props | PostgreSQL `<SCHEMA>.user_bundle_props` |
| user-scoped bundle secrets | configured secrets provider |

That is why:

- user state is not exported by `kdcube export`
- user state is not reconstructed into `bundles.yaml`
- user state is not reconstructed into `bundles.secrets.yaml`

If a bundle wants import/export for user-scoped state, the bundle must provide
its own API or operational workflow.

## Current grouped AWS SM layout

For bundle deployment state in `aws-sm`, the grouped documents are:

| Document | Contents |
|---|---|
| `<prefix>/bundles-meta` | bundle registry inventory |
| `<prefix>/bundles/<bundle_id>/descriptor` | bundle registry entry and non-secret `config` |
| `<prefix>/bundles/<bundle_id>/secrets` | bundle-level secrets only |

Platform/global secret reads may still resolve through grouped documents or
canonical per-key fallback paths depending on the secret class.

So `aws-sm` is not “one single JSON blob for everything”.

## Export and ejection behavior

`kdcube export` is bundle-state export only.

It reconstructs:

- `bundles.yaml`
- `bundles.secrets.yaml`

It does not reconstruct:

- `assembly.yaml`
- `gateway.yaml`
- `secrets.yaml`
- user-scoped bundle props
- user-scoped bundle secrets

The operational interpretation is:

- deployment-scoped bundle state is ejectable back into bundle descriptors
- platform/global deployment state remains deployment-owned
- user-scoped state remains operational state

## Reserved platform-owned bundle props

Reserved bundle prop paths such as:

- `role_models`
- `embedding`
- `economics.reservation_amount_dollars`
- `execution.runtime`
- `mcp.services`

are still stored as deployment-scoped bundle props.

They do not create a new store class.

The special part is interpretation, not storage.

Use the author-facing page for those paths:

- [bundle-reserved-platform-properties-README.md](../../sdk/bundle/bundle-reserved-platform-properties-README.md)
