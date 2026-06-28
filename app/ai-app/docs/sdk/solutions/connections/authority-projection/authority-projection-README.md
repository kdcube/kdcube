---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
title: "Authority Projection"
summary: "Connection Hub role: project a verified actor identity plus linked platform principal into UserSession authority and portable identity_authority context."
status: active
tags: ["sdk", "connections", "connection-hub", "authority", "roles", "economics", "runtime-context"]
updated_at: 2026-06-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/automations/automations-sdk-solution-README.md
---
# Authority Projection

Authority projection is the step that turns a linked external identity into
runtime authority.

```text
actor identity
  telegram_434804821
        +
linked platform principal
  02e53484-...
        |
        v
UserSession + identity_authority
```

## Why It Exists

The actor and the authority source can be different.

```text
actor/storage identity:
  telegram_434804821

platform authority identity:
  02e53484-0081-70ce-11c1-e96706b1a182
```

This lets an app keep Telegram-scoped storage or product behavior while platform
role checks and economics use the linked platform principal.

## Runtime Shape

```text
UserSession
  user_id     = telegram_434804821
  user_type   = privileged
  roles       = ["kdcube:role:super-admin"]
  permissions = [...]
  identity_authority
        |
        v
identity_authority
  actor_user_id       = telegram_434804821
  storage_user_id     = telegram_434804821
  platform_user_id    = 02e53484-...
  economics_user_id   = 02e53484-...
  user_type           = privileged
  economics_user_type = privileged
  platform_roles      = ["kdcube:role:super-admin"]
  platform_permissions = [...]
  identity_provider   = telegram
  identity_provider_subject = 434804821
```

After projection, downstream code should see a normal authorized session. It
should not re-check Telegram or parse provider proof.

## Role Rule

Provider-local roles are not platform roles.

```text
Telegram local role = admin
  -> can authorize Telegram app/admin behavior
  -> must not become kdcube:role:admin

Platform principal roles
  -> kdcube:role:super-admin
  -> kdcube:role:admin
  -> kdcube:role:paid
  -> used for platform requirements and economics
```

## Runtime Boundaries

Projected authority must cross detached work boundaries:

```text
request
  -> queue/schedule job
  -> later worker
  -> ReAct/runtime/tools/economics
```

For scheduled automations, the job source should carry normalized
`identity_authority` so the later run can restore the same effective authority.
Do not make each surface redo provider-specific auth or role mapping.

## Federated Data Bus Sessions

Non-browser clients use the same projection model. For example, a Telegram Mini
App calls Connection Hub `federated_data_bus_claim` with Telegram proof
headers. Connection Hub creates a Data Bus `UserSession` whose actor remains the
Telegram user:

```text
user_id = telegram_434804821
```

If no identity link exists, the session stays low authority:

```text
user_type   = registered
roles       = []
permissions = []
```

If `telegram:434804821` is linked to a platform user, the next claim keeps the
same actor id and projects the platform authority into that session. The
federated token itself only points at the session; it does not duplicate roles,
permissions, or provider identity in the signed token body.

## SDK Helpers

The platform SDK exposes helpers for normalizing/applying this envelope:

```text
kdcube_ai_app.apps.chat.sdk.identity_authority
```

Main responsibilities:

- resolve platform authority for a linked platform user;
- normalize authority before storing it in a queued job/source;
- apply authority to the communication/request context before role checks,
  economics, ReAct, tools, or child runtimes run.
