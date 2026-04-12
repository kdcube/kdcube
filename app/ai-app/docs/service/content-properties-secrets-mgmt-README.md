---
id: ks:docs/service/content-properties-secrets-mgmt-README.md
title: "Content Properties and Secrets Management"
summary: "Concrete storage and export model for deployment-scoped bundle descriptors, bundle props, bundle secrets, and user secrets across local and ECS deployments."
tags: ["service", "props", "secrets", "aws-sm", "bundles", "deployment"]
keywords: ["bundle props", "bundle secrets", "bundles-meta", "bundles descriptor", "aws secrets manager", "redis cache", "bundles.yaml", "bundles.secrets.yaml"]
see_also:
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
  - ks:docs/service/secrets/secrets-service-README.md
---
# Content Properties and Secrets Management

## 1. Scope

This document defines the current storage model for:

- deployment-scoped bundle descriptors
- deployment-scoped bundle props
- deployment-scoped bundle secrets
- user-scoped non-secret bundle props
- user-scoped secrets

## 2. Problem being solved

The problem was drift between:

- live bundle admin changes made during system operation
- deployment descriptors used later for ECS redeploy
- Redis-only bundle props
- AWS SM bundle secrets

The system now moves to one authoritative per-bundle document model for ECS with `aws-sm`.

## 3. Design decision

For `aws-sm`, the source of truth is **not** one giant `bundles.yaml` or `bundles.secrets.yaml` snapshot in Secrets Manager.

The source of truth is:

- one small shared metadata document
- one descriptor document per bundle
- one secrets document per bundle

Redis is cache only.

This avoids:

- 64 KB whole-snapshot limits
- whole-system read-modify-write for every small change
- duplicate authority between Redis and a giant exported snapshot

## 4. Exact storage model

### 4.1 AWS SM, deployment-scoped bundle state

If `secrets.provider = aws-sm`, the authoritative documents are:

- `<prefix>/bundles-meta`
- `<prefix>/bundles/<bundle_id>/descriptor`
- `<prefix>/bundles/<bundle_id>/secrets`

Where:

- `<prefix>` is usually `kdcube/<tenant>/<project>`

### 4.2 Meaning of each document

`<prefix>/bundles-meta`

- tenant/project-level bundle metadata that does not belong to one bundle
- currently:
  - `default_bundle_id`
  - `bundle_ids`

Example:

```json
{
  "default_bundle_id": "my.bundle@1-0",
  "bundle_ids": ["my.bundle@1-0", "other.bundle@2-1"]
}
```

`<prefix>/bundles/<bundle_id>/descriptor`

- one effective deployment-scoped bundle item
- includes both:
  - bundle origin/wiring
  - deploy-scoped bundle props

Typical fields:

- `path` or `repo/ref/subdir`
- `module`
- `singleton`
- `name`
- `description`
- `props`

Example:

```json
{
  "repo": "https://github.com/example/my-bundle.git",
  "ref": "main",
  "subdir": "bundle",
  "module": "entrypoint",
  "props": {
    "feature_flags": {
      "use_fast_path": true
    }
  }
}
```

`<prefix>/bundles/<bundle_id>/secrets`

- one effective deployment-scoped bundle secret document

Example:

```json
{
  "user_management": {
    "cognito_user_pool_id": "eu-west-1_abc123",
    "sheets_integration_credentials_file_content": "{...}"
  }
}
```

### 4.3 Platform and user secrets

These remain grouped separately:

- platform/global secrets:
  - `<prefix>/platform/secrets`
- user global secrets:
  - `<prefix>/users/<user_id>/secrets`
- user bundle secrets:
  - `<prefix>/users/<user_id>/bundles/<bundle_id>/secrets`

User secrets are **not** deployment descriptors and must never be exported into `bundles.secrets.yaml`.

### 4.4 User-scoped non-secret bundle props

These are stored in PostgreSQL project schema, not in descriptors and not in AWS SM.

Table:

- `<SCHEMA>.user_bundle_props`

Scope:

- current user
- current bundle
- logical prop key

This is the persistent backing store for:

- `get_user_prop(...)`
- `get_user_props(...)`
- `set_user_prop(...)`
- `delete_user_prop(...)`

## 5. Local `secrets-file` model

If `secrets.provider = secrets-file`, the source of truth remains:

- `bundles.yaml`
- `bundles.secrets.yaml`
- `secrets.yaml`

So:

- single-node `secrets-file`: YAML files are authoritative
- ECS `aws-sm`: grouped per-bundle docs are authoritative

The logical interface is the same. Only the backend differs.

## 6. Redis role

Redis is cache only for:

- bundle registry
- bundle props
- bundle secret key metadata used by admin endpoints

Redis is no longer the authoritative storage for deployment-scoped bundle props in `aws-sm` mode.

Redis is also not the authority for user-scoped bundle props. Those live in PostgreSQL.

If Redis is empty, proc can reload:

- live bundle registry from `bundles-meta` + `bundles/<id>/descriptor`
- bundle props from `bundles/<id>/descriptor.props`

## 7. Runtime write paths

### 7.1 Bundle registry updates

These operations update the authoritative descriptor docs in `aws-sm` mode:

- `POST /admin/integrations/bundles`
- `POST /internal/bundles/update`
- `POST /admin/integrations/bundles/reset-env`
- `POST /internal/bundles/reset-env`

What is written:

- `bundles-meta`
- each changed `bundles/<id>/descriptor`
- stale descriptor docs are removed on authoritative replace

### 7.2 Bundle prop updates

These operations update:

- Redis cache
- `bundles/<bundle_id>/descriptor.props`

Operations:

- `POST /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props/reset-code`

### 7.3 Bundle secret updates

These operations update:

- `bundles/<bundle_id>/secrets`

Operation:

- `POST /admin/integrations/bundles/{bundle_id}/secrets`

### 7.4 User secret updates

These operations update:

- `users/<user_id>/secrets`
- or `users/<user_id>/bundles/<bundle_id>/secrets`

They are outside deployment export.

## 8. Export model

The effective deployment export is reconstructed from the live authoritative docs.

### 8.1 Effective `bundles.yaml`

Build from:

- `bundles-meta`
- every `bundles/<bundle_id>/descriptor`

### 8.2 Effective `bundles.secrets.yaml`

Build from:

- `bundles-meta.bundle_ids`
- every `bundles/<bundle_id>/secrets`

### 8.3 CLI export

The CLI can export the current live state with IAM only:

```bash
kdcube \
  --export-live-bundles \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

Optional:

- `--aws-profile <profile>`
- `--aws-sm-prefix <prefix>`

This command reads:

- `bundles-meta`
- `bundles/<id>/descriptor`
- `bundles/<id>/secrets`

and writes:

- `bundles.yaml`
- `bundles.secrets.yaml`

No direct access to proc, Redis, Postgres, or VPC-only endpoints is required.

## 9. Deployment discipline

If operators redeploy with old descriptor files, they can still overwrite current live deployment-scoped state.

So the correct redeploy flow is:

1. export current effective bundle descriptors from the running environment
2. review/edit them if needed
3. use those exported files as deployment input

This is especially important for:

- bundle admin changes to bundle props
- bundle admin changes to deployment-scoped bundle secrets
- bundles added or removed during operational time

## 10. Reserved bundles

Reserved/runtime-only bundles are not exported into deployment state.

Examples:

- `kdcube.admin`
- built-in example bundles

They remain runtime concerns and are excluded from:

- `bundles-meta.bundle_ids`
- `bundles/<id>/descriptor` export set

## 11. Locking

For `aws-sm`, writes use a distributed Redis lock per document.

That means:

- one lock for one bundle descriptor doc
- one lock for one bundle secrets doc
- one lock for `bundles-meta`

There is no whole-system lock.

## 12. Existing ECS environments

An old ECS environment may already have:

- bundle secrets in grouped docs
- but no `bundles-meta`
- and no `bundles/<id>/descriptor` docs yet

To bootstrap the new descriptor model, perform one live authoritative bundle write, for example:

- `reset-env`
- or a bundle registry update through bundle admin

After that:

- `bundles-meta` exists
- per-bundle descriptor docs exist
- CLI export works from live state

## 13. What is solved already

### 13.1 Solved

- deployment-scoped bundle secrets are persistent in `aws-sm`
- deployment-scoped bundle descriptors and bundle props are persistent in `aws-sm`
- bundles added during operational time can be exported later
- effective `bundles.yaml` and `bundles.secrets.yaml` can be reconstructed from live state
- Redis loss does not lose deployment-scoped bundle descriptor/props state

### 13.2 Not solved by this design

- user-scoped props persistence
- assembly/gateway as live mutable authoritative documents
- automatic operator discipline during redeploy
  - operators still need to export current live state before redeploy if they want to preserve operational changes

## 14. Recommended next steps

1. Keep this per-bundle SM model for ECS now.
2. Treat Redis only as cache.
3. Use live export before redeploy.
4. Later, if needed, move deployment-scoped bundle props from SM descriptor docs into Postgres while keeping the same export shape.
