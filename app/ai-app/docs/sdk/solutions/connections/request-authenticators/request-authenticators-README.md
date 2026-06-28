---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
title: "Request Authenticators"
summary: "Connection Hub role: verify request proof through authenticator modules and return linked authority material to the Connection Hub authentication surface."
status: active
tags: ["sdk", "connections", "connection-hub", "authenticators", "request-auth", "gateway"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
---
# Request Authenticators

Request authenticators verify proof carried by incoming requests. The
authenticator selector selects authenticators, not authorities. Each
authenticator is registered under an `authority_id`.

```text
Telegram initData
Slack signature
webhook HMAC
API key
OIDC claim
        |
        v
Connection Hub authenticator module
        |
        v
verified identity + authority_id
```

The gateway does not parse Telegram, Slack, API-key, or webhook proof itself.
It passes a request envelope to Connection Hub and receives an authenticated
result when one authenticator module accepts it.

## Selector Shape

```text
raw request
   |
   v
Connection Hub SDK RequestAuthResolver
   |
   +-- platform token/cookie/session authenticator
   |
   +-- ConnectionHubAuthenticationSurface
         |
         v
       request_authenticate(RequestEnvelope)
         |
         v
       authenticator verifies proof
         |
         v
       verified identity + authority_id
         |
         v
       linker/grant resolver if the surface requires another authority
         |
         v
       AuthenticatedRequest
   |
   v
UserSession
```

Service-level resolver behavior is documented in
[Auth Selector](../../../../service/auth/auth-selector-README.md). This document
describes the Connection Hub side of that authentication surface.

## Request Envelope

The SDK contract passes a JSON-safe view of the request:

```json
{
  "method": "POST",
  "path": "/api/...",
  "url": "https://...",
  "headers": {
    "x-telegram-init-data": "...",
    "x-kdcube-auth-authority-id": "telegram.kdcube_ref",
    "x-kdcube-auth-authenticator-id": "telegram.kdcube_ref.init_data"
  },
  "query": {},
  "cookies": {}
}
```

Controlled KDCube surfaces should include:

```http
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
```

For third-party callbacks that cannot send custom headers, put the
authenticator id in the callback URL:

```text
/public/telegram_webhook?authenticator_id=telegram.kdcube_ref.webhook
```

## Hints

`authority_id` and `authenticator_id` are non-secret selector hints. They
narrow which authenticators may be tried. They are not trusted facts. Legacy
`integration_id`/`connection_id` names are accepted only while older surfaces
migrate to the authority/authenticator names.

```text
authenticator_id = telegram.kdcube_ref.init_data
  -> Connection Hub authenticator row
  -> authority_id=telegram.kdcube_ref
  -> secret_ref=identity.authenticators.telegram_kdcube_ref.bot_token
```

These hints are not:

- a bot token;
- a Telegram bot id;
- a platform user id;
- an identity link.

If an explicit authenticator id is present, Connection Hub should try that row
only and fail closed if it is missing, disabled, or rejects the proof.

## Authenticator Modules

Authenticator modules live inside Connection Hub because they need
access to:

- Connection Hub authenticator metadata;
- bundle secret references;
- identity-link resolution;
- provider-specific verifier code.

```text
Connection Hub
  authenticator modules:
    telegram.init_data
    telegram.webhook
    slack.signature
    oidc.claim
    api-key
    webhook-hmac
```

Apps and gateway code should not duplicate this verifier logic.

## Output

A successful Connection Hub authentication surface returns authority material:

```json
{
  "ok": true,
  "authenticated": true,
  "authority_id": "telegram.kdcube_ref",
  "identity_subject": "434804821",
  "provider": "telegram",
  "provider_subject": "434804821",
  "selected_authenticator": "telegram.kdcube_ref.init_data",
  "actor_user_id": "telegram_434804821",
  "platform_user_id": "02e53484-...",
  "identity_authority": {
    "actor_user_id": "telegram_434804821",
    "platform_user_id": "02e53484-...",
    "economics_user_id": "02e53484-...",
    "user_type": "privileged",
    "platform_roles": ["kdcube:role:super-admin"]
  }
}
```

The gateway turns this into a normal `UserSession`.
