---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/use-connected-identities-in-product-feature-README.md
title: "Use Connected Identities In A Product Feature"
summary: "Recipe for product features that need to read data across the current user's connected identities without turning identity family into an authorization shortcut."
status: active
tags: ["recipes", "connections", "connection-hub", "identity-family", "connection-edges", "memory"]
updated_at: 2026-06-29
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_memory.py
---
# Use Connected Identities In A Product Feature

Use this recipe when a product feature stores records by the runtime actor that
created them, but the user expects one coherent view across connected accounts.

Current example:

```text
User memories created from:
  platform user a1b2c3d4-...
  telegram user telegram_100200300

Connection Hub edge:
  telegram.kdcube_ref:100200300 -> platform:a1b2c3d4-...
  grants include identity:family

Memory widget:
  shows both records when "all my memories" is selected
```

## What This Solves

Runtime identity and product storage identity are not always the platform user:

```text
Telegram Mini App writes memory as telegram_100200300
Browser chat writes memory as a1b2c3d4-...
Delegated client may read as integration:claude:...
```

The feature should not hardcode Telegram, Claude, Gmail, or platform ids. It
should ask Connection Hub:

```text
given this current actor/session,
which product user ids are in the allowed connected identity family?
```

## Flow

```text
Product request
  already authenticated as actor/session
        |
        v
Product backend calls Connection Hub
  operation = identity_family_resolve
        |
        v
Connection Hub
  checks current actor/platform user
  reads connection edges
  verifies the edge grants allow identity family aggregation
        |
        v
Product backend receives:
  memory_user_ids / user_ids / identities
        |
        v
Product store query:
  WHERE user_id IN memory_user_ids
```

## Do This In The Backend

The backend owns the aggregation decision. Do not merge identity families in the
browser.

```text
widget/client request
  -> product backend
  -> Connection Hub identity_family_resolve
  -> store query with returned user ids
```

For a bundle feature, call the Connection Hub operation from server-side code:

```text
call_bundle_operation(
  bundle_id = "connection-hub@1-0",
  operation = "identity_family_resolve",
  payload = {
    "user_id": current_actor_user_id,
    "scope": "memory"
  }
)
```

Use the normalized result:

```json
{
  "ok": true,
  "schema": "connection_hub.identity_family.v1",
  "platform_user_id": "a1b2c3d4-...",
  "memory_user_ids": [
    "a1b2c3d4-...",
    "telegram_100200300"
  ],
  "identities": [
    {
      "authority_id": "platform",
      "user_id": "a1b2c3d4-..."
    },
    {
      "provider": "telegram",
      "integration_id": "telegram.kdcube_ref",
      "user_id": "telegram_100200300"
    }
  ]
}
```

## Read Policy

Identity family is a read/visibility scope. It is not a role.

```text
good:
  read records whose owner user_id is in memory_user_ids

bad:
  treat memory_user_ids as platform permissions
  bypass economics because identity family exists
  let a client provide user_ids directly
```

If no connection edge exists, or the edge does not delegate identity-family
reads, the resolver returns only the current actor:

```text
memory_user_ids = ["telegram_100200300"]
```

That is expected. The product should still work in actor-local mode.

## Write Policy

Writes should usually stay actor/provenance scoped:

```text
Telegram-originated write:
  user_id = telegram_100200300

Browser-originated write:
  user_id = a1b2c3d4-...

Delegated-client write:
  user_id = integration/client actor, or deny writes unless explicitly delegated
```

Do not silently rewrite all writes to the platform user. That destroys
provenance and makes debugging impossible.

## Delete Policy

Deletes must use the same visibility family as reads, then delete the actual
owner record.

```text
1. Resolve memory_user_ids.
2. Load the requested memory with user_id IN memory_user_ids.
3. If not found, return not found.
4. Delete using the loaded record's real owner/scope.
```

This matters when the current actor is Telegram and the selected record belongs
to the platform user, or when the current actor is Telegram and the record was
created under another Telegram-visible bundle scope.

## Logging

Product features should log both the actor and the resolved family:

```text
[memory.identity_family]
  actor_user_id=telegram_100200300
  family_user_ids=['a1b2c3d4-...', 'telegram_100200300']
  family_size=2
```

Connection Hub should log the resolver decision:

```text
[connection-hub.identity_family_resolve]
  requested_user=telegram_100200300
  projected_platform_user=a1b2c3d4-...
  projected_grants=['identity:family', ...]
  memory_user_ids=['a1b2c3d4-...', 'telegram_100200300']
```

If Connection Hub returns two ids but the product logs one id, the product is
dropping or misreading the resolver payload.

## Minimal Test

```text
1. Create one memory from platform chat.
2. Create one memory from Telegram Mini App.
3. Link Telegram to the platform account with identity:family consent.
4. Open memories from Telegram.
5. Select "all my memories".
6. Confirm records from both user ids are visible.
7. Delete a Telegram-owned record.
8. Delete a platform-owned record.
9. Confirm both deletes use backend family resolution, not client-supplied ids.
```

## What Not To Do

- Do not let the client send arbitrary `user_ids`.
- Do not make provider-specific code in the product feature.
- Do not use identity family as authorization for protected operations.
- Do not write all external-channel records as the platform user.
- Do not hide resolver failures; log and fall back to actor-local reads.
