---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-links/identity-links-README.md
title: "Identity Links"
summary: "Connection Hub role: map verified external identities to KDCube platform principals without storing roles or delegated account tokens."
status: active
tags: ["sdk", "connections", "connection-hub", "identity-links", "platform-principal"]
updated_at: 2026-06-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/channel-first-identity-linking-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/platform-first-identity-linking-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
---
# Identity Links

Identity links answer one question:

```text
Which KDCube platform principal owns this verified external identity?
```

When a product needs all runtime user ids that belong to the same person, use
the [Identity Family Resolver](../identity-family-resolver/identity-family-resolver-README.md)
instead of reading this store directly.

Examples:

```text
telegram:434804821          -> 02e53484-0081-70ce-11c1-e96706b1a182
google:person@example.com   -> 02e53484-0081-70ce-11c1-e96706b1a182
bundle:crm:user-77          -> 02e53484-0081-70ce-11c1-e96706b1a182
```

## Boundary

Identity links store identity routing only.

```text
Identity Link
  provider
  provider_subject
  platform_user_id
  label
  status
  metadata
```

They do not store:

- platform roles;
- platform subscriptions/budgets;
- Telegram-local admin roles;
- OAuth access/refresh tokens;
- bot tokens or signing secrets.

## Flow Position

```text
provider proof
  "Telegram says this is user 434804821"
        |
        v
Identity Link
  "telegram:434804821 belongs to platform user 02e..."
        |
        v
Authority Projection
  "this execution may use platform roles/economics of 02e..."
```

## Writes Require Proof

An identity link should be written only after both sides of the link are proven.

```text
external identity proof
  Telegram initData / Slack OAuth profile / OIDC claim / signed webhook
        +
platform identity proof
  current KDCube browser session or trusted platform-owned operation
        |
        v
write provider:<subject> -> platform_user_id
```

Manual link creation is a development/onboarding helper only. Production flows
should use provider-specific proof flows.

## Current Implementation

In the current `connection-hub@1-0` playground app, identity links are
bundle-local JSON state:

```text
<bundle_storage_root>/identity/identity-links.json
<bundle_storage_root>/identity/identity-link-challenges.json
```

For the local demo runtime this resolves to:

```text
~/.kdcube/kdcube-runtime/<tenant>__<project>/data/bundle-storage/<tenant>/<project>/connection-hub-1-0/identity/
```

This is intentionally called out because request-authenticator metadata is in
Postgres, but identity links are not yet in Postgres.

## Production Direction

Identity links and link challenges should move to a durable Connection Hub or
platform-owned store:

```text
connection_hub_identity_links
connection_hub_identity_link_challenges
```

The logical contract should stay the same. The storage move must not introduce
secret values into identity-link rows.
