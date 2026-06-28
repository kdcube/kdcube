---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
title: "Connection Hub Storage Model"
summary: "Storage map for Connection Hub data: descriptors, secrets, request-authenticator metadata, identity links, link challenges, delegated account tokens, and runtime caches."
status: active
tags: ["sdk", "connections", "connection-hub", "storage", "postgres", "secrets", "identity-links"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
---
# Connection Hub Storage Model

Connection Hub uses multiple storage surfaces. They are separate on purpose.

```text
descriptors / bundles.yaml
  non-secret deployment config

bundles.secrets.yaml / secrets service
  verifier secrets and OAuth client secrets

Postgres
  request-authenticator metadata

bundle-local state today
  identity links
  identity-link challenges
  delegated account connection state

Redis/cache
  discovery, event delivery, runtime caches
```

## Matrix

| Object | Current storage | Contains secrets? | Notes |
| --- | --- | ---: | --- |
| Provider/app config | `bundles.yaml` app props | no | Authority ids, authenticator ids, OAuth app definitions, descriptor authenticators. |
| Bot token / OAuth client secret | `bundles.secrets.yaml` or secrets service | yes | Read through bundle secret lifecycle using `secret_ref`. |
| Request-authenticator metadata | Postgres | no | Stores provider, authority id, authenticator id, `secret_ref`, verifier metadata. |
| Identity link | bundle-local JSON today | no | Maps provider subject to platform user id. |
| Identity family | derived from identity links | no | Resolver output for product aggregation; not persisted separately. |
| Identity-link challenge | bundle-local JSON today | no | Short-lived link proof state. |
| Delegated account token | connections/email stores | yes, user token | OAuth token or app password for automation. |
| Live link update | Data Bus / event delivery | no | Signals original iframe after browser claim completes. |

## Request-Authenticator Metadata

Request-authenticator metadata belongs in Postgres because it is admin/widget
managed operational metadata:

```text
connection_hub_request_authenticators
  authenticator_id
  authority_id
  provider
  enabled
  role_providing
  subject_namespace
  secret_ref
  selector
  verifier
  properties
```

Secret values must not be stored here. Only `secret_ref` is stored.

## Identity Links Today

The current playground implementation stores identity links in bundle-local JSON:

```text
<bundle_storage_root>/identity/identity-links.json
<bundle_storage_root>/identity/identity-link-challenges.json
```

This is why links do not appear in Postgres.

For the local demo runtime, the path is:

```text
~/.kdcube/kdcube-runtime/<tenant>__<project>/data/bundle-storage/<tenant>/<project>/connection-hub-1-0/identity/
```

## Target Storage

For production shape, identity links and challenges should move to a durable
database or platform identity service:

```text
connection_hub_identity_links
  provider
  provider_subject
  platform_user_id
  label
  status
  metadata

connection_hub_identity_link_challenges
  challenge_id
  provider
  provider_subject
  platform_user_id
  live_event_session_id
  status
  expires_at
  metadata
```

The API and logical flow should not change when storage moves.

## Secret Boundary

Verifier secrets stay in descriptor-backed bundle secrets or the configured
secrets service:

```yaml
secrets:
  identity:
    authenticators:
      telegram_kdcube_ref:
        bot_token: "..."
```

Metadata rows reference secrets:

```yaml
secret_ref: identity.authenticators.telegram_kdcube_ref.bot_token
```

No widget/admin API should accept raw fields such as `bot_token`,
`client_secret`, `signing_secret`, `api_key`, or `secret_value`.
