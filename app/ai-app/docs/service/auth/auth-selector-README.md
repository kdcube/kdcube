---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
title: "Auth Selector And Connection Hub Surface"
summary: "Gateway request-authentication boundary: platform auth first, then the Connection Hub authentication surface, complete UserSession out."
status: active
tags: ["service", "auth", "gateway", "connections", "authenticators", "sessions"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Auth Selector And Connection Hub Surface

The gateway request-auth boundary is intentionally small. Middleware uses the
Connection Hub SDK's `RequestAuthResolver`. The resolver first accepts the
configured platform authenticator when platform credentials are present. If that
does not establish a session, it asks one configured Connection Hub
`ConnectionHubAuthenticationSurface` whether the request proves an external
actor.

Connection Hub owns the selector. It decides which Telegram/Slack/OIDC/API-key
or custom authority authenticator should inspect the request, verifies the
proof, links or projects authority, and returns one complete `UserSession`.

```text
HTTP / SSE / Socket.IO / app API request
        |
        v
FastAPIGatewayAdapter
        |
        v
RequestAuthResolver
   |
   +-- descriptor-registered platform authenticator
   |     kdcube.cognito / kdcube.multi-cognito / kdcube.bundle-session / kdcube.simple-idp
   |
   +-- ConnectionHubAuthenticationSurface
         |
         v
      Connection Hub selector + authenticator modules
        Telegram initData / webhook HMAC / API key / Slack signature / custom authority / ...
        |
        v
verified identity + authority_id
        |
        v
Surface Guard + Linker + Grant Resolver
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
- OAuth delegated credential delegated credential token.

Without Connection Hub's selector, every app/surface repeats "how do I
authenticate this request, link it to a platform user, and turn that into
roles?" Connection Hub makes that one platform mechanism.

## Connection Hub Authentication Surface

Connection Hub is installed as the gateway's external-auth surface through
`assembly.yaml`:

```yaml
auth:
  authenticators:
    connection_hub:
      enabled: true
      app_id: "connection-hub@1-0"
      operation: "request_authenticate"
```

When enabled, the gateway first accepts a valid platform token/cookie session
when one is present. That path is role-providing and wins. If no platform
session is established, the gateway asks the Connection Hub authentication
surface. Connection Hub receives a `RequestEnvelope` and dispatches to its own
authenticator modules. Those modules verify proof, read Connection Hub identity
links and secrets, resolve authority, and return authority material. The
surface converts that material into a normal `UserSession`.

The Connection Hub surface does a cheap local prefilter before it calls the
Connection Hub bundle operation. It only invokes Connection Hub when the
request carries external auth material or a selector hint, such as:

- `X-KDCube-Auth-*` authority/authenticator/provider/integration headers;
- equivalent query parameters for provider-controlled callback URLs;
- Telegram Mini App `initData`;
- provider signature/API-key headers.

If no such material is present, the Connection Hub surface declines without
calling the bundle operation. That does not reject the request. The normal gateway policy
continues with an anonymous or platform-authenticated session, so public bundle
APIs, MCP surfaces, widgets, and static assets remain public when their route
contract says they are public.

Once called, Connection Hub owns authenticator selection, Redis-backed selector
caches, provider fallbacks, and the final decision to authenticate or decline.
The gateway does not inspect provider secrets or run provider-specific verifier
logic.

Connection Hub stores request-authenticator metadata in its own app store
(Postgres for widget-managed rows) and reads secret values only through bundle
secrets. The gateway sees neither bot tokens nor provider-specific verifier
configuration.

## Controlled Surfaces Carry Selector Hints

For surfaces KDCube controls, request-auth should not depend on guessing. The
surface sends either platform auth material or external proof plus a stable
non-secret authority/authenticator selector:

```http
X-Telegram-Init-Data: <Telegram.WebApp.initData>
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
```

For provider callbacks where the provider controls the request headers, put a
stable non-secret integration selector into the callback URL. Telegram webhooks
should be registered as:

```text
/public/telegram_webhook?integration_id=telegram.kdcube_ref
```

The integration id is configured in app props. It names the non-secret
Telegram integration row used by the app for this callback. It is not a bot id
and not a secret. The Telegram Mini App host reads equivalent auth context
from server config, forwards it to hosted iframes through the standard
`CONFIG_RESPONSE`, and attaches it on its own app API calls.

Uncontrolled third-party hooks are the only place where an authenticator module may
need to infer from raw request shape alone. All new
controlled webhook examples should include `integration_id`; the fallback
exists for uncontrolled provider callbacks, not as the preferred setup. If a
controlled request supplies `integration_id`, the Telegram SDK tries that row
only and fails closed when no enabled row matches or the proof is rejected.

## Header-Only Auth Paths

Some platform surfaces intentionally accept only header credentials. MCP and
other machine-to-machine paths use this mode so cookies and request bodies do
not participate in authentication.

```text
header_only_auth=True
  -> skip Connection Hub authentication surface
  -> extract Authorization / ID-token headers only
  -> run standard platform token authenticator
```

This preserves the existing strict behavior for header-only routes while still
allowing browser, Mini App, webhook, and app API routes to use Connection Hub's
authenticator modules.

## Cognito Is Also An Authenticator

The current Cognito/session/simple auth managers are active through the selector
as the descriptor-registered platform authenticator. They preserve existing
token/cookie behavior but now produce normal selector provenance:

```text
authenticator: kdcube.multi-cognito
  input: Authorization + ID token
  output: identity under kdcube.platform

authenticator: kdcube.bundle-session
  input: kst1 session cookie
  output: identity under kdcube.platform

authentication surface: connection-hub
  input: raw request envelope
  internal authenticator modules: telegram, slack, api-key, oidc, ...
  output: identity under module authority; linker/grant resolver produce session
```

The gateway derives the platform authenticator from
`auth.authenticators.platform` when present, otherwise from existing
`auth.idp`/`auth.providers` descriptors. If no descriptor declares platform
auth, the development default is `simple`. Runtime code should not introduce
new service-local `AUTH_PROVIDER` switches.

## Multiple Bots And Providers

Provider families can have multiple configured authenticator rows. These rows
belong to Connection Hub. The gateway does not know them. For Telegram,
Connection Hub recognizes Telegram request shape and tries its configured
authenticator rows:

```text
request has x-telegram-init-data
  -> gateway asks Connection Hub authentication surface
  -> Connection Hub module family = telegram
  -> if x-kdcube-auth-authenticator-id=telegram.support.init_data:
       try telegram.support.init_data only
     else:
       use provider fallback order for uncontrolled hooks
  -> selected_authenticator = telegram.support.init_data
```

This is bounded selection, not a blind broadcast to every app in the system.

## Current Limits

Implemented now:

- the authenticator selector contract and SDK primitives;
- descriptor-registered platform token/cookie auth as the role-providing first
  path;
- Connection Hub authentication surface;
- `UserSession.identity_authority`;
- Connection Hub `request_authenticate` operation;
- Connection Hub authenticator metadata widget/API backed by Postgres rows and
  bundle-secret references;
- Redis-backed Connection Hub authenticator metadata selector cache;
- Telegram authenticator module with `initData` verification and identity-link
  authority projection.

Not complete yet:

- Slack/webhook/API-key authenticator modules;
- custom-authority surface guards for non-platform required authorities.
