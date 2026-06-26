---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
title: "Auth Selector"
summary: "Gateway request-authentication stack: request in, selected authenticator, complete UserSession out."
status: active
tags: ["service", "auth", "gateway", "connections", "authenticators", "sessions"]
updated_at: 2026-06-26
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Auth Selector

The gateway authenticates requests through a selector stack. The selector gets
the raw request/context and returns one complete `UserSession`.

```text
HTTP / SSE / Socket.IO / app API request
        |
        v
FastAPIGatewayAdapter
        |
        v
RequestAuthSelector
   |
   +-- platform token authenticators
   |     Cognito / multi-Cognito / bundle-session / simple-idp
   |
   +-- Connection Hub request-auth bridge
         Connection Hub provider modules:
           Telegram initData / webhook HMAC / API key / Slack signature / ...
        |
        v
UserSession
        |
        v
requirements -> rate limits -> backpressure -> economics/runtime
```

There is no special "Telegram role check" in the downstream code. If Telegram
proof is accepted and linked to a platform principal, the resulting session
already carries the effective roles and `identity_authority`.

## Session Is The Boundary

The selector result is a session, not a provider-specific object.

```text
UserSession
  user_id             actor/storage identity
  user_type           effective platform user type
  roles               effective platform roles
  permissions         effective platform permissions
  identity_authority  actor/platform/economics provenance
```

After this point, normal gateway code handles:

- `RequireUser` / `RequireRoles`;
- throttling;
- backpressure;
- economics role/funding;
- app API visibility checks;
- downstream runtime context.

## Why This Exists

KDCube has more than one way a user can arrive:

- browser session with Cognito or bundle-session cookies;
- Telegram Mini App `initData`;
- Telegram webhook signed by a bot;
- Slack request signature;
- app-specific HMAC webhook;
- API key;
- OAuth MCP integration token.

Without a selector, every app/surface repeats "how do I authenticate this
request, link it to a platform user, and turn that into roles?" The selector
makes that one platform mechanism.

## Connection Hub Candidate

Connection Hub is a selector candidate configured in `assembly.yaml`:

```yaml
auth:
  authenticators:
    connection_hub:
      enabled: true
      app_id: "connection-hub@1-0"
      operation: "request_authenticate"
```

When enabled, the gateway first accepts a valid platform token/cookie session
when one is present. That path is role-providing and should win. If no platform
session is established, the gateway can ask Connection Hub. Connection Hub
receives a `RequestEnvelope` and dispatches to its own provider modules. Those
modules verify proof, read Connection Hub identity links and secrets, resolve
platform authority, and return authority material. The gateway adapter converts
that material into a normal `UserSession`.

Connection Hub stores request-authenticator metadata in its own app store
(Postgres for widget-managed rows) and reads secret values only through bundle
secrets. The gateway sees neither bot tokens nor provider-specific verifier
configuration.

## Controlled Surfaces Carry A Connection Id

For surfaces KDCube controls, request-auth should not depend on guessing. The
surface sends either platform auth material or external proof plus a stable
non-secret connection selector:

```http
X-Telegram-Init-Data: <Telegram.WebApp.initData>
X-KDCube-Auth-Provider: telegram
X-KDCube-Auth-Connection-ID: telegram.default
```

`connection_id` is configured in app props and in Connection Hub authenticator
metadata. It is not a bot id and not a secret. The Telegram Mini App host reads
it from server config, forwards it to hosted iframes through the standard
`CONFIG_RESPONSE`, and attaches it on its own app API calls.

Uncontrolled third-party hooks are the only place where a provider module may
need to infer from raw request shape alone. If a controlled request supplies
`connection_id`, Connection Hub tries that row only and fails closed when no
enabled row matches.

## Header-Only Auth Paths

Some platform surfaces intentionally accept only header credentials. MCP and
other machine-to-machine paths use this mode so cookies and request bodies do
not participate in authentication.

```text
header_only_auth=True
  -> skip Connection Hub request-auth bridge
  -> extract Authorization / ID-token headers only
  -> run standard platform token candidate
```

This preserves the existing strict behavior for header-only routes while still
allowing browser, Mini App, webhook, and app API routes to use Connection Hub's
provider modules.

## Cognito Is Also An Authenticator

The current Cognito/session/simple auth managers are still active and preserve
their behavior. Conceptually they are selector candidates too:

```text
selector candidate: cognito-token
  input: Authorization + ID token
  output: UserSession(platform_user_id, roles)

selector candidate: bundle-session-cookie
  input: kst1 session cookie
  output: UserSession(platform_user_id, roles)

selector candidate: connection-hub
  input: raw request envelope
  internal modules: telegram, slack, api-key, oidc, ...
  output: UserSession(actor=<provider subject>, platform authority if linked)
```

The migration target is to register all auth managers through the same selector
surface. The initial implementation keeps the existing `AuthManager` as the
default selector candidate to avoid changing Cognito/session behavior.

## Multiple Bots And Providers

Provider families can have multiple configured authenticator rows. These rows
belong to Connection Hub. The gateway does not know them. For Telegram,
Connection Hub recognizes Telegram request shape and tries its configured bot
verifiers:

```text
request has x-telegram-init-data
  -> gateway calls Connection Hub bridge
  -> Connection Hub module family = telegram
  -> if x-kdcube-auth-connection-id=telegram.support:
       try telegram.support only
     else:
       use provider fallback order for compatibility/uncontrolled hooks
  -> selected_authenticator = telegram.support
```

This is bounded selection, not a blind broadcast to every app in the system.

## Current Limits

Implemented now:

- `RequestAuthSelector`;
- standard platform token/cookie auth as the role-providing first path;
- optional Connection Hub bridge candidate;
- `UserSession.identity_authority`;
- Connection Hub `request_authenticate` operation;
- Connection Hub authenticator metadata widget/API backed by Postgres rows and
  bundle-secret references;
- Telegram provider module with `initData` verification and identity-link
  authority projection.

Not complete yet:

- Redis selector cache;
- Slack/webhook/API-key provider modules;
- full replacement of the legacy `AUTH_PROVIDER` switch with descriptor-defined
  selector registrations.
