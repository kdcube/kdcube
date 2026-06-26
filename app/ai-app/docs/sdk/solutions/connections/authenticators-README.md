---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticators-README.md
title: "Connection Authenticators"
summary: "SDK contract for request authenticators owned by Connection Hub: request envelope in, verified linked authority out."
status: active
tags: ["sdk", "connections", "authenticators", "identity", "telegram", "authority", "gateway"]
updated_at: 2026-06-26
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connections-sdk-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Connection Authenticators

Connection authenticators are provider modules inside Connection Hub. They let
one configured app own provider-specific request proof logic:

```text
Telegram initData
Slack signature
webhook HMAC
API key
        |
        v
request-auth selector
        |
        v
Connection Hub bridge
        |
        v
Connection Hub provider module
        |
        v
verified provider identity
        |
        v
identity link + platform authority
        |
        v
UserSession
```

The caller passes the request to the Connection Hub bridge. Connection Hub then
chooses one of its own provider modules, such as Telegram, Slack, webhook HMAC,
API key, Gmail/OIDC, or another future module. The gateway never owns those
provider modules and never parses provider-specific proof.

Platform token/cookie auth, such as Cognito or bundle-session cookies, remains a
separate selector candidate today. It can be migrated behind the same selector
surface later, but it is not a Telegram/Slack-style Connection Hub provider
module unless we deliberately configure it that way.

## SDK Contract

The SDK module is:

```python
kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators
```

Important types:

| Type | Purpose |
| --- | --- |
| `RequestEnvelope` | JSON-safe view of a request: method, path, URL, headers, query, cookies, optional body. |
| `AuthenticatorRegistration` | Connection Hub module row. Secrets are referenced by key, not stored in metadata. |
| `AuthenticatedRequest` | Connection Hub result before the gateway creates/reuses a session. Carries actor, link, principal, and `identity_authority`. |
| `ConnectionHubAuthenticatorsClient` | App/channel client for calling Connection Hub's `request_authenticate` public operation. |

Example app/channel call:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators import (
    ConnectionHubAuthenticatorsClient,
)

client = ConnectionHubAuthenticatorsClient(connection_hub_bundle_id="connection-hub@1-0")
result = await client.authenticate_request(request)
if result.ok and result.authenticated:
    authority = result.identity_authority
```

Gateway integration uses the same request envelope, but returns a `UserSession`
to the gateway rather than exposing this intermediate result to application
code.

## Selector Rule

The request-auth selector first accepts a valid role-providing platform session
when one is present, such as Cognito, bundle-session, or another platform token
candidate. If no platform session is established, it may call the Connection Hub
bridge. Connection Hub owns the provider choice. Target app operations should
not hardcode Telegram, Slack, Cognito, or API-key logic.

```text
request
  -> gateway selector calls Connection Hub bridge
  -> Connection Hub Telegram module sees initData headers/query/body
  -> one configured Connection Hub bot row validates the proof
  -> Connection Hub resolves telegram:<id>
  -> platform authority resolver supplies roles/permissions
  -> gateway receives UserSession(user_id=telegram_<id>, roles=<platform roles>)
```

If Connection Hub does not authenticate the request, the selector returns the
platform-auth result it already established, or anonymous when no platform auth
was present.

## Connection Id Hint

Controlled surfaces should not make Connection Hub guess. If KDCube serves the
surface or app handler, it should carry either platform auth material or an
external proof plus the stable non-secret connection id:

```http
X-Telegram-Init-Data: <Telegram.WebApp.initData>
X-KDCube-Auth-Provider: telegram
X-KDCube-Auth-Connection-ID: telegram.default
```

`connection_id` is deployment metadata, not a bot id and not a secret. The app
or surface reads it from app props/server config and forwards it with the
request. Connection Hub then tries only that configured authenticator row. If an
explicit `connection_id` is present but no enabled row matches, authentication
fails closed with `auth_connection_not_configured`.

Uncontrolled inbound hooks are the exception. A third-party webhook may not
carry KDCube-specific headers. In that case the provider module may inspect the
raw request shape and use provider-specific selectors, but this is fallback
behavior for unmanaged callers, not the intended path for KDCube-controlled
surfaces.

## Identity And Roles

The actor identity and authority identity are deliberately separate.

```text
UserSession
  user_id = telegram_434804821          # actor/storage identity
  user_type = privileged                # effective platform authority
  roles = ["kdcube:role:super-admin"]   # platform roles
  identity_authority:
    actor_user_id = telegram_434804821
    platform_user_id = 02e53484-...
    economics_user_id = 02e53484-...
    identity_provider = telegram
    identity_provider_subject = 434804821
```

Telegram-local `admin` is not a platform role. It may authorize Telegram app
actions, but platform role checks and economics must use the linked platform
principal.

## Multiple Authenticators

Provider families can have many configured authenticator rows. They are
configured and executed inside Connection Hub. For Telegram this means many
bots.

```yaml
identity:
  authenticators:
    - authenticator_id: telegram.default
      provider: telegram
      connection_id: telegram.default
      label: Default bot
      role_providing: false
      secret_ref: identity.telegram.bot_token
      enabled: true
    - authenticator_id: telegram.support
      provider: telegram
      label: Support bot
      secret_ref: identity.telegram.bot_token_support
      enabled: true
```

Connection Hub first recognizes the provider family from request shape, then
asks the configured module rows in that family. A successful verifier identifies
the selected authenticator. Descriptor rows are deployment config. Widget-managed
rows are Connection Hub Postgres metadata. Both carry only `secret_ref`; secret
values live in `bundles.secrets.yaml` or the configured bundle secrets provider.

`role_providing` is `false` for linked external providers such as Telegram: the
Telegram proof establishes the actor, then the identity link supplies platform
authority. It should be `true` only for authenticators that directly prove a
platform principal/role.

## Current Implementation

Implemented now:

- request envelope and typed SDK result;
- gateway request-auth selector returning `UserSession`;
- current platform token/cookie auth as the role-providing first candidate;
- Connection Hub bridge as an optional selector candidate configured in
  `auth.authenticators.connection_hub`;
- Connection Hub `request_authenticate` public operation;
- Connection Hub authenticator metadata APIs/widget backed by Postgres rows and
  bundle-secret references;
- Telegram Mini App/WebApp `initData` verification through the Connection Hub
  Telegram provider module and one or more configured bot rows;
- identity link resolution and `identity_authority` projection into
  `UserSession`.

Still to standardize:

- Slack/webhook/API-key verifier modules;
- Redis-backed selector cache for high-volume webhook/auth requests;
- moving Cognito/session/simple into the same selector registration surface in
  configuration, while preserving their existing runtime behavior.
