---
id: ks:docs/service/secrets/secrets-service-README.md
title: "Secrets Manager Implementations"
summary: "Runtime secrets providers, persistence model, and user-scoped secret behavior."
tags: ["service", "secrets", "configuration", "aws", "runtime"]
keywords: ["SECRETS_PROVIDER", "secrets-service", "aws-sm", "secrets-file", "in-memory", "user secrets", "bundle secrets"]
see_also:
  - ks:docs/service/configuration/service-config-README.md
  - ks:docs/service/configuration/descriptor-plain-config-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
  - ks:docs/service/environment/service-dev-env-README.md
  - ks:docs/service/environment/service-ecs-env-README.md
---
# Secrets Manager Implementations

This document describes the runtime secrets manager implementations currently
supported by KDCube services and how they behave for:

- global service secrets
- bundle-shared secrets
- user-scoped bundle secrets

It also explains what survives service restart and what does not.

This document is only about secrets. For non-secret descriptor-backed runtime
reads from mounted `assembly.yaml` and `bundles.yaml`, see:
[docs/service/configuration/descriptor-plain-config-README.md](../configuration/descriptor-plain-config-README.md)

## 1. Runtime contract

The runtime chooses a secrets backend from `SECRETS_PROVIDER`.

Supported providers:

- `secrets-service`
- `aws-sm`
- `secrets-file`
- `in-memory`

Legacy aliases:

- `local` -> `secrets-service`
- `service` -> `secrets-service`
- `file` / `yaml` -> `secrets-file`

The runtime entrypoint is the secrets manager in
[manager.py](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py).

## 2. Supported secret scopes

### Global service secrets

Examples:

- `services.openai.api_key`
- `services.anthropic.api_key`
- `services.git.http_token`

These are used as shared service-wide defaults.

### Bundle-shared secrets

Examples:

- `bundles.rms@06-04-26-156.secrets.git.http_token`
- `bundles.rms@06-04-26-156.secrets.anthropic.api_key`

These are shared by all users of the same bundle within the same tenant/project.

### User-scoped bundle secrets

Examples:

- `users.alice.bundles.rms@06-04-26-156.secrets.git.http_token`
- `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`

These are intended for current-user credentials such as:

- per-user Claude / Anthropic keys
- per-user Git PATs

Bundles should not build these flat keys manually. Runtime now provides:

- `get_user_secret(...)`
- `set_user_secret(...)`
- `delete_user_secret(...)`

in
[config.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py).

## 3. Provider behaviors

### `in-memory`

Implementation:

- [InMemorySecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- stores all secrets in process memory only
- supports writes
- does not synchronize across replicas
- does not survive service restart

Use only for:

- tests
- very local temporary runs

Do not use for:

- persistent environments
- multi-worker correctness

### `secrets-service`

Implementation:

- [SecretsServiceSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes secrets through the configured `SECRETS_URL`
- uses `SECRETS_TOKEN` for reads
- uses `SECRETS_ADMIN_TOKEN` for writes
- the proc/ingress service itself is not the storage of record
- persistence depends on the backing store used by the secrets service

Restart behavior:

- service restart does not lose secrets
- values are reloaded from the remote secrets service

User-scoped secrets:

- stored under the same canonical flat key namespace
- for example:
  - `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`

### `aws-sm`

Implementation:

- [AwsSecretsManagerSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes to AWS Secrets Manager
- `SECRETS_AWS_SM_PREFIX` or `SECRETS_SM_PREFIX` defines the namespace root
- if no explicit prefix is set, runtime derives:
  - `kdcube/<tenant>/<project>`

Secret id mapping examples:

- `services.openai.api_key`
  - `kdcube/<tenant>/<project>/services/openai/api_key`
- `bundles.rms@06-04-26-156.secrets.git.http_token`
  - `kdcube/<tenant>/<project>/bundles/rms@06-04-26-156/secrets/git/http_token`
- `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`
  - `kdcube/<tenant>/<project>/users/alice/bundles/rms@06-04-26-156/secrets/anthropic/api_key`

Restart behavior:

- fully persistent
- service restart has no effect on stored values

### `secrets-file`

Implementation:

- [SecretsFileSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes YAML descriptors through the storage abstraction in
  [storage.py](../../../src/kdcube-ai-app/kdcube_ai_app/storage/storage.py)
- supports:
  - `file://...`
  - `s3://...`

Configured URIs:

- `GLOBAL_SECRETS_YAML`
- `BUNDLE_SECRETS_YAML`

Current important implementation detail:

- user-scoped secrets are currently persisted into `GLOBAL_SECRETS_YAML`
- there is not yet a separate `USER_SECRETS_YAML`

Restart behavior:

- persistent if the configured YAML location is persistent
- `file://...` survives restart if the file is on durable local/EFS storage
- `s3://...` survives restart because the source of truth is S3

Read behavior:

- rereads YAML on every `get_secret()`
- no in-memory secret-value cache

Write behavior:

- writes are serialized with a distributed Redis lock when Redis is configured
- reads do not rely on Redis

So after restart:

- the service simply rereads the YAML descriptor again
- values remain as long as the file/object still exists

## 4. `secrets-file` YAML layouts

### Global service secrets

Example:

```yaml
services:
  openai:
    api_key: sk-openai
  anthropic:
    api_key: sk-anthropic
```

### Bundle-shared secrets

Example:

```yaml
bundles:
  version: "1"
  items:
    - id: "rms@06-04-26-156"
      secrets:
        git:
          http_token: ghp_xxx
          http_user: x-access-token
        anthropic:
          api_key: sk-ant-xxx
```

### User-scoped bundle secrets

Current `secrets-file` implementation stores them in `GLOBAL_SECRETS_YAML`.

After one RMS user saves:

- Anthropic API key
- Git PAT

for bundle `rms@06-04-26-156`, the YAML will look like:

```yaml
users:
  alice:
    bundles:
      rms@06-04-26-156:
        secrets:
          anthropic:
            api_key: sk-ant-user
          git:
            http_token: ghp_user_pat
            http_user: x-access-token
```

That is the state that survives restart.

## 5. Multiple workers / replicas

### `in-memory`

- each worker has its own copy
- no cross-worker visibility
- no persistence

### `secrets-service`

- source of truth is remote
- all workers read the same backing store
- persistence depends on that remote service

### `aws-sm`

- source of truth is AWS Secrets Manager
- all workers read the same remote store
- fully persistent

### `secrets-file`

- source of truth is the YAML descriptor
- all workers see the same values if they point to the same file/object
- reads reread YAML directly, so restart is not special
- write races are serialized by Redis lock when Redis is configured

Redis is not the value store here. It is only:

- write coordination
- metadata/key tracking

## 6. REST API exposure rules

### Bundle-shared secrets

Admin UI/API may manage bundle-shared secrets.

### User-scoped secrets

Current rule:

- user secrets are write-only over REST
- runtime can list internal metadata, but user-facing REST does not return values
- current user write route does not return key names either

This is intentional.

We do not expose current-user secret values back to the browser.

## 7. RMS bundle behavior

RMS now prefers credentials in this order:

### Git

1. `users.<user_id>.bundles.rms@06-04-26-156.secrets.git.http_token`
2. `bundles.rms@06-04-26-156.secrets.git.http_token`
3. `services.git.http_token`
4. process / machine git auth

### Claude

1. `users.<user_id>.bundles.rms@06-04-26-156.secrets.anthropic.api_key`
2. `bundles.rms@06-04-26-156.secrets.anthropic.api_key`
3. `services.anthropic.api_key`
4. process / machine Claude auth

This means:

- per-user override is possible
- shared team default is still possible
- existing env-based deployments still work as fallback

## 8. Choosing a provider

Recommended:

- local compose / local multi-service dev:
  - `secrets-service`
- ECS / AWS:
  - `aws-sm`
- local IntelliJ / direct proc debugging against descriptors:
  - `secrets-file`
- tests only:
  - `in-memory`

## 9. Summary

Persistence across service restart depends entirely on the chosen provider:

- `in-memory`: no
- `secrets-service`: yes, if its backing store persists
- `aws-sm`: yes
- `secrets-file`: yes, if the referenced YAML location persists

For `secrets-file`, user-scoped secrets currently survive restart by being written
into `GLOBAL_SECRETS_YAML` under the `users:` tree.
