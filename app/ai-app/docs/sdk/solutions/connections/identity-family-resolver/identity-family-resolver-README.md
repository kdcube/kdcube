---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
title: "Identity Family Resolver"
summary: "Connection Hub role: resolve the current actor or platform user to the linked identity family that product features can aggregate over."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "identity-family", "identity-links", "memory"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-links/identity-links-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
---
# Identity Family Resolver

The Identity Family Resolver answers a product-level question:

```text
Given this authenticated actor or platform user, which linked runtime user ids
belong to the same person?
```

It is SDK-owned code in
`kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.resolver`. It is not a
request authenticator. It does not verify Telegram, Slack, OAuth, or webhook
proof. It runs after the request already has an authenticated actor or a platform
session and uses Connection Hub identity links to return the safe identity family
for that actor.

## Position

```text
request/session/proof already authenticated
        |
        v
current actor user_id
  telegram_434804821 / platform uuid / provider:subject
        |
        v
Identity Family Resolver
        |
        +-- identity-link store
        +-- platform authority identity
        +-- provider/integration identities
        v
product aggregation scope
  memory_user_ids / user_ids / identities
```

## Why This Exists

Product features often store records under the runtime actor that produced
them. For example, memories may exist under:

```text
02e53484-0081-70ce-11c1-e96706b1a182
telegram_434804821
google:person@example.com
```

If those identities are linked, the user should be able to see one coherent
memory set. Every product should not reimplement provider parsing or link-store
lookups. The resolver centralizes that logic.

## Output Shape

The SDK resolver is exposed by the current Connection Hub bundle as:

```text
identity_family_resolve
```

The logical response uses schema `connection_hub.identity_family.v1`:

```json
{
  "ok": true,
  "schema": "connection_hub.identity_family.v1",
  "linked": true,
  "platform_user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
  "authority": {
    "kind": "authority",
    "authority_id": "platform",
    "provider": "platform",
    "user_id": "02e53484-0081-70ce-11c1-e96706b1a182"
  },
  "identities": [
    {
      "kind": "authority",
      "authority_id": "platform",
      "provider": "platform",
      "user_id": "02e53484-0081-70ce-11c1-e96706b1a182"
    },
    {
      "kind": "integration",
      "provider": "telegram",
      "provider_subject": "434804821",
      "identity_ref": "telegram:434804821",
      "user_id": "telegram_434804821",
      "integration_id": "telegram.kdcube_ref",
      "authenticator_id": "telegram.kdcube_ref"
    }
  ],
  "user_ids": [
    "02e53484-0081-70ce-11c1-e96706b1a182",
    "telegram_434804821"
  ],
  "memory_user_ids": [
    "02e53484-0081-70ce-11c1-e96706b1a182",
    "telegram_434804821"
  ]
}
```

`memory_user_ids` is intentionally explicit. Memory implementations can use it
directly for backend-side queries without knowing provider-specific runtime user
id conventions.

## Access Rule

The resolver must not become a directory of other users' links.

```text
platform session present
  -> may resolve only that platform user's family

actor session only
  -> may resolve only that actor's family

cross-user request
  -> deny
```

If a Telegram actor is unlinked, the resolver may return a one-item family for
the actor:

```text
telegram_434804821
```

That is enough for low-authority actor-local features. It does not grant platform
roles or economics bypass.

## Memory Integration

Memory backends should use the resolver on the server side:

```text
incoming memory request
        |
        v
resolve authenticated actor/session
        |
        v
Connection Hub bundle identity_family_resolve
        |
        v
memory store query:
  WHERE user_id IN memory_user_ids
```

The widget should not merge identities client-side. The backend must decide the
allowed identity family and then query storage.

## Boundary With Authority Projection

Identity Family Resolver returns aggregation scope. Authority Projection returns
role/economics authority for execution.

They often use the same identity links, but they answer different questions:

```text
Identity Family Resolver:
  "which user ids belong to this person for product data?"

Authority Projection:
  "which roles/permissions/economics authority may this execution use?"
```

Do not use `memory_user_ids` as authorization grants. Product records still need
their normal access checks.
