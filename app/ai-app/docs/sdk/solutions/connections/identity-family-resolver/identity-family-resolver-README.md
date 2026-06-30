---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
title: "Identity Family Resolver"
summary: "Connection Hub role: resolve the current actor or platform user through connection edges to the identity family that product features can aggregate over."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "identity-family", "connection-edges", "memory"]
updated_at: 2026-06-29
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
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
session and uses Connection Hub edges to return the safe identity family
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
        +-- connection-edge store
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

If those identities are connected, the user should be able to see one coherent
memory set. Every product should not reimplement provider parsing or edge-store
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

## Delegated Identity Scope

Delegated external clients use the same edge vocabulary. A Claude connector is
a delegate of a grantor, not the same person as the grantor, but the delegation
edge can still authorize grantor-scoped or grantor-family reads when the user
consented to that scope.

For delegated credentials, Connection Hub exposes:

```text
delegated_identity_scope_resolve
```

Input is a verified `kdcube.credential.v1` envelope. Output uses schema
`connection_hub.delegated_identity_scope.v1`:

```json
{
  "ok": true,
  "schema": "connection_hub.delegated_identity_scope.v1",
  "delegate_identity": "integration:claude:02e...",
  "grantor_user_id": "02e...",
  "identity_scope": "grantor_identity_family",
  "memory_user_ids": ["02e...", "telegram_434804821"]
}
```

Product surfaces should use this operation instead of parsing
`grantor_subject` themselves. The delegated credential's descriptor-owned
`identity_scope` decides whether reads stay on the grantor or expand to connected
identities.

## Access Rule

The resolver must not become a directory of other users' edges.

```text
platform session present
  -> may resolve only that platform user's family

actor session only
  -> may resolve only that actor's family

cross-user request
  -> deny
```

If a Telegram actor has no edge, the resolver may return a one-item family for
the actor:

```text
telegram_434804821
```

That is enough for low-authority actor-local features. It does not grant platform
roles or economics bypass.

If a Telegram actor has an edge but that edge does not include
`identity:family`, the resolver still returns only the actor-local family:

```text
telegram_434804821
```

That is deliberate. A connection edge means “these identities were proven and
connected”; the `identity:family` grant means “this source identity may use the
target platform user's identity family for product reads.” Memories and other
features that aggregate records across identities must require the grant, not
only the existence of an edge.

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

For debugging, the memory backend logs the actor and resolved family when it
fetches data. A healthy Telegram-linked request with `identity:family` should
show both the Telegram runtime user id and the platform user id:

```text
[memory.identity_family] ... actor_user_id=telegram_434804821
  family_user_ids=['02e53484-0081-70ce-11c1-e96706b1a182', 'telegram_434804821']
```

If `family_user_ids` contains only `telegram_434804821`, inspect the Connection
Hub edge record. The usual cause is an old or low-authority edge with
`"grants": []`.

The same resolver is used by delegated-client MCP reads. In the current
`kdcube-services@1-0/public/mcp/named_services` flow:

```text
Claude calls named_services_search(namespace="mem")
      |
      v
managed MCP guard validates delegated_client credential
      |
      v
runtime projection sets the grantor as the effective product user
      |
      v
memory named-service provider resolves identity family
      |
      v
memory search runs over allowed memory_user_ids
```

This has been tested for memory search through the generic named-services MCP
surface. The external client does not supply `memory_user_ids`; it only supplies
the delegated credential and the namespace/tool arguments.

## Boundary With Authority Projection

Identity Family Resolver returns aggregation scope. Authority Projection returns
role/economics authority for execution.

They often use the same connection edges, but they answer different questions:

```text
Identity Family Resolver:
  "which user ids belong to this person for product data?"

Authority Projection:
  "which roles/permissions/economics authority may this execution use?"
```

Do not use `memory_user_ids` as authorization grants. Product records still need
their normal access checks.
