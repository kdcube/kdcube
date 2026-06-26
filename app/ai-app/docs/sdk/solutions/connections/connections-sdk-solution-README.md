---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connections-sdk-solution-README.md
title: "Connections SDK Solution"
summary: "Identity links, connected external accounts, and authority projection from channel identities into KDCube execution context."
tags: ["sdk", "solutions", "connections", "identity", "auth", "authority", "telegram", "automations"]
updated_at: 2026-06-26
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/automations/automations-sdk-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
---
# Connections SDK Solution

Connections is the solution layer for joining identities and accounts without
collapsing them into one thing.

It covers two related but different stories:

- identity links: `telegram:434804821` is the same human/platform principal as
  `02e53484-...`;
- connected accounts: the user grants automation access to a remote account,
  such as Gmail, Slack, iCloud, or another service.

The first story is about **who is acting and which platform authority they can
use**. The second story is about **which external resources an app/agent may
operate on**.

## Terminology

| Term | Meaning |
| --- | --- |
| Channel identity | Identity proven by the current surface or transport, for example `telegram:434804821`, `google:user@example.com`, `api-key:<fingerprint>`. |
| Actor identity | The identity that initiated the current execution. For Telegram-owned storage this may be `telegram_434804821`. |
| Platform principal | KDCube platform user id that owns platform roles, permissions, subscriptions, budgets, and admin authority. |
| Connected account | A delegated external resource account, such as Gmail or Slack, that an app/agent may use with consent. |
| Authority projection | The one-time conversion from actor/channel identity to effective platform roles and economics identity for this execution. |
| Channel authorizer | The ingress component that validates a channel proof, resolves links, asks platform authority for roles, and stamps execution context. |

## The Three Layers

```text
1. Authentication / proof
   "This request really came from Telegram user 434804821."

2. Link resolution
   "telegram:434804821 is linked to platform user 02e53484-..."

3. Authority projection
   "This execution acts as telegram_434804821, but role/economics authority is
    platform user 02e53484-... with roles [kdcube:role:super-admin]."
```

These layers should not be mixed.

Surface-local roles are not platform roles. For example, a Telegram-local
`admin` row can authorize a Telegram Mini App action, but it must not become
`kdcube:role:super-admin`. Platform authority must come from the linked platform
principal and the platform authority resolver.

## Runtime Shape

When a channel identity is linked to a platform principal, execution context
should carry both identities:

```text
REQUEST_CONTEXT.user
  user_id     = telegram_434804821       # actor/storage identity
  user_type   = privileged               # effective platform authority
  roles       = ["kdcube:role:super-admin"]
  permissions = [...]

BUNDLE_CALL_CONTEXT.identity_authority
  actor_user_id       = telegram_434804821
  storage_user_id     = telegram_434804821
  platform_user_id    = 02e53484-0081-70ce-11c1-e96706b1a182
  economics_user_id   = 02e53484-0081-70ce-11c1-e96706b1a182
  user_type           = privileged
  economics_user_type = privileged
  platform_roles      = ["kdcube:role:super-admin"]
  platform_permissions = [...]
  identity_provider   = telegram
  identity_provider_subject = 434804821
```

`REQUEST_CONTEXT.user` is what generic role validators see. `BUNDLE_CALL_CONTEXT.identity_authority`
preserves provenance and the actor/economics split across runtime boundaries.

This is documented as part of the portable room in
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

## Current SDK Mechanism

The current implementation lives in:

```text
kdcube_ai_app.apps.chat.sdk.identity_authority
```

Important helpers:

| Helper | Purpose |
| --- | --- |
| `resolve_platform_authority(...)` | Given an actor identity and linked platform user id, return a JSON-safe authority envelope. |
| `normalize_execution_authority(...)` | Normalize an authority envelope before storing it in a queued job source or `bundle_call_context`. |
| `apply_authority_to_comm_context(...)` | Stamp effective role/permissions into `REQUEST_CONTEXT.user` before tools/ReAct run. |

Automations use this through:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations import due

due.configure_due_automations(
    storage_root_or_error=storage_root_or_error,
    automation_operations_module=automation_operations,
    scheduler_identity_resolver=my_identity_context_resolver,
)
```

The resolver is app/channel-specific only at the proof/link layer. It should
answer: "for this actor, which platform principal is linked?" It should not
invent platform roles.

## Surfaces

| Surface | How it should work |
| --- | --- |
| Browser REST/SSE/Socket.IO | Normal platform auth creates `UserSession`; roles are already present in request context. |
| Telegram Mini App public routes | Telegram init data proves Telegram actor. A channel authorizer should resolve the linked platform principal and stamp authority before protected app work. |
| Telegram webhook | Webhook signature/bot token proves the channel event. A channel authorizer should resolve the Telegram identity to platform authority if platform roles/economics are needed. |
| Scheduled automations | Supported now through `scheduler_identity_resolver`; authority is stored in durable job source and rebound when the job runs. |
| Manual queued jobs | If the request starts from a platform browser session, roles are already present. If it queues detached work for a surface actor, the queue source should carry `identity_authority`. |
| Data Bus | Messages must carry enough actor/auth metadata for a channel authorizer or provider to stamp authority before trusted work. |
| MCP/API integration tokens | Integration tokens should authenticate as a platform principal or be resolved through the same channel-authorizer path before role checks. |

## Target Channel Authorizer

We still need a generic authorizer component at ingress/channel boundaries.

Target flow:

```text
incoming request/event/job
  |
  | validate channel proof
  |   browser cookie / Telegram initData / webhook signature / API key / MCP token
  v
channel authorizer
  |
  | actor identity = provider:subject
  | platform principal = Connections.resolve(actor identity)
  | roles/permissions = PlatformAuthority.resolve(platform principal)
  v
execution context
  |
  | REQUEST_CONTEXT.user carries effective roles
  | BUNDLE_CALL_CONTEXT.identity_authority carries actor/economics split
  v
app code, role checks, economics, ReAct, tools, child runtimes
```

This should be platform/SDK-owned, not repeated by every app. Apps should only
declare which channel proofs they accept and how their local storage scope maps
to actor identity.

## Relationship To Service Auth

`docs/service/auth` describes platform authentication providers and token
transport. Connections is higher-level:

- service auth answers "is this platform request authenticated?";
- Connections answers "which identities/accounts belong together?";
- authority projection answers "what effective platform roles/funding does this
  execution carry after the channel identity is linked?"

Do not put channel-specific identity-link policy into generic token extraction.
Token extraction belongs to service auth. Link resolution and authority
projection belong to Connections / channel authorizers.
