---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
title: "Auth"
summary: "Authentication providers and token transport across REST/SSE/Socket.IO."
tags: ["service", "auth", "security", "tokens"]
keywords: ["delegated auth", "cookie auth", "JWT", "SSE auth", "Socket.IO"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-simple-idp-bridge-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-long.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/README-monitoring-observability.md
---
# Auth Overview

This module supports multiple authentication providers and several token transport
options across REST, SSE, and Socket.IO.

## How auth works (current)

1) **Request-auth resolver**
The gateway runs the request through the Connection Hub SDK request-auth
resolver. A valid platform token/cookie session wins first because it directly
provides platform authority. If no platform session is established, the
resolver can ask the Connection Hub authentication surface. Provider-specific
selector and authenticator modules live inside Connection Hub.

2) **Authentication**
The accepted surface returns a complete `UserSession`. For classic platform
requests this is Cognito/session/simple auth. For channel/provider requests this
may be Connection Hub after one of its authenticator modules verifies proof and
resolves linked authority.

3) **User type classification**  
User type is derived from roles:
- **Privileged**: any role in `PRIVILEGED_ROLES` (`kdcube:role:super-admin`, `kdcube:role:admin`)
- **Paid**: any role in `PAID_ROLES` (`kdcube:role:paid`)
- **Registered**: authenticated user with any other role
- **External**: authenticated/proven identity with no platform roles
- **Anonymous**: no valid token

4) **Requirements enforcement**  
`RequireUser()` now means:
- user is **non‑anonymous** and not merely an unprivileged external actor
- user has **at least one role**

5) **Session ownership enforcement**  
If a request includes `User-Session-ID` (header) or `user_session_id` (query param),
the gateway verifies that this session belongs to the authenticated user. Unknown or
mismatched sessions are rejected (401/403).

## Boundary With Connections

Service auth validates platform credentials and produces a `UserSession` for
platform REST/SSE/Socket.IO/MCP/API requests. It should not contain every
provider-specific connection-edge rule.

When a request starts from another channel, such as Telegram init data, webhook
signature, API key, or an app-owned proof, KDCube-controlled callers should
send the external proof plus stable non-secret authority/authenticator selector
hints that name the authority realm and verifier row being used. API and iframe
calls normally carry:

```http
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
```

Provider
webhooks that cannot add arbitrary selector headers, such as Telegram
`setWebhook`, should put the same selector in the webhook URL query string:
`?authenticator_id=<authenticator-id>`. Uncontrolled hooks may lack that hint
and are handled by provider-specific request-shape matching as a fallback.
Connection Hub should:

1. select authenticators from request hints and request shape;
2. validate the provider/request proof;
3. return verified identity + `authority_id`;
4. link to the required authority when the surface requires a different one;
5. resolve roles/permissions/grants under that authority;
6. stamp the execution context before role checks, economics, ReAct, tools, or
   child runtimes run.

Connection Hub authenticator rows store metadata and `secret_ref` only. Provider
secrets remain in descriptor-backed bundle secrets, not in app tables or widget
state.

That authority projection is documented in
[Auth Selector](auth-selector-README.md),
[Connection Hub Solution](../../sdk/solutions/connections/connection-hub-solution-README.md) and
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

## Supported auth providers

1) Cognito (production)
- Implementation: [Cognito auth](../../../src/kdcube-ai-app/kdcube_ai_app/auth/implementations/cognito.py)
- Uses access token (Bearer) and optional ID token for profile/roles.

2) Multi-Cognito (mixed trusted runtimes)
- Implementation: [Multi Cognito auth](../../../src/kdcube-ai-app/kdcube_ai_app/auth/implementations/multi_cognito.py)
- Selected by the Connection Hub platform authority provider when the selected
  provider has more than one trusted Cognito user-pool/client pair.
- Accepts JWTs from every trusted provider declared under
  `connection-hub@1-0.config.authority_registry`. Provider selection is based
  on token claims: `iss` plus `client_id` for access tokens or `aud` for ID
  tokens.
- The access token and ID token must resolve to the same provider and subject.
- The browser still logs into one configured OIDC provider. Multi-Cognito is a
  server-side trust-list mode for mixed scenes where one authenticated shell can
  call apps hosted by another trusted runtime.

3) SimpleIDP (dev / local)
- Implementation: [SimpleIDP](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/simple_idp.py)
- Token-to-user mapping stored in `idp_users.json` (or `IDP_DB_PATH`).
- Bundle-owned sign-in flows can register users through the cached registry
  utility documented in [Bundle SimpleIDP Bridge](bundle-simple-idp-bridge-README.md).

4) Bundle session auth
- Implementation: [Bundle session auth](../../../src/kdcube-ai-app/kdcube_ai_app/auth/bundle/sessions.py)
- `auth.idp: session`.
- A bundle/front shell validates an external identity and calls the async
  platform session authority to register/login/logout/delete/invalidate users.
- Session cookies carry a signed `kst1.*` token. Redis stores the active
  session record, user record, and revocation/version state.
- Details: [Bundle Session Auth](bundle-session-auth-README.md).

5) Delegated auth (proxy login service)
- Proxy service build: [ProxyLogin Dockerfile](../../../deployment/docker/custom-ui-managed-infra/Dockerfile_ProxyLogin)
- Proxy service wiring: [custom-ui-managed compose](../../../deployment/docker/custom-ui-managed-infra/docker-compose.yaml)
- The proxy service exchanges credentials and returns tokens; the UI stores
  access + refresh + id tokens and forwards them to the API.
- The delegated web-proxy supports both the existing masquerade cookie flow and
  a non-masquerade flow where the real auth and identity cookies are already
  present on the request.

6) OAuth delegated credential protocol adapter for delegated credentials
- Implementation: `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth`,
  mounted by the `connection-hub@1-0` bundle public `oauth` operation.
- Connection Hub hosts an OAuth2 authorization server for external integration
  clients such as Claude Code. MCP protected resources remain concrete
  bundle/proc MCP endpoints.
- Existing platform session auth proves the grantor before consent. The grantor
  can be a regular user when the requested capability is descriptor-delegable by
  that user's roles/permissions, or an admin for admin-only capabilities.
- Consent issues a least-privilege integration session and binds the selected
  MCP tool allowlist to the issued grant.
- Details: [OAuth delegated credential Protocol Adapter](../../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).

### Cognito Platform Authority Descriptor Shape

`assembly.yaml` selects the platform authority provider. Cognito details live
in the Connection Hub authority registry, not in `assembly.yaml`.

```yaml
# assembly.yaml
auth:
  type: cognito
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito
```

```yaml
# bundles.yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            label: KDCube platform authority
            platform: true
            providers:
              cognito:
                type: multi_cognito
                enabled: true
                authenticator:
                  type: cognito_id_token
                  id_token_header_name: X-ID-Token
                  region: eu-west-1
                  user_pool_id: eu-west-1_PRIMARY
                  app_client_id: primary-client
                  service_client_id: primary-client
                  cookie:
                    auth_token_cookie_name: __Secure-LATC
                    id_token_cookie_name: __Secure-LITC
                    masqueraded_token_cookie_name: __Secure-LMTC
                  trusted_providers:
                    - alias: primary
                      kind: cognito
                      region: eu-west-1
                      user_pool_id: eu-west-1_PRIMARY
                      app_client_id: primary-client
                    - alias: peer
                      kind: cognito
                      region: eu-west-1
                      user_pool_id: eu-west-1_PEER
                      app_client_id: peer-client
```

The control-plane frontend config, ingress, proc, and deployment descriptors
all resolve Cognito runtime fields from the selected Connection Hub provider.
Environment variables may still override runtime values for deployment
mechanics, but the descriptor owner is Connection Hub.

## Bundle Builder Reading Path

| Goal | Read |
|---|---|
| Understand how requests choose Cognito/session/Connection Hub authenticators | [Auth Selector](auth-selector-README.md) |
| Bundle/front shell performs login and browser should become a platform user | [Bundle Session Auth](bundle-session-auth-README.md) |
| Bundle writes a SimpleIDP token for local/embedded simple auth | [Bundle SimpleIDP Bridge](bundle-simple-idp-bridge-README.md) |
| External tool should access a narrow MCP integration surface after descriptor-governed user/admin consent | [OAuth delegated credential Protocol Adapter](../../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md) |
| Public mini app needs Socket.IO Data Bus publish rights | [Bundle Federated Auth](../../sdk/bundle/auth-bundle-federated-README.md) |
| Bundle endpoint should be public or role-protected | [Bundle Firewall](../../sdk/bundle/bundle-firewall-README.md) |

## Token transport (server-side)

The gateway/auth adapters accept tokens from multiple sources. The intent is to
support:
- REST (headers, cookies)
- SSE (query params, headers, cookies)
- Socket.IO (auth payload, cookies)

### Access token (auth)
- Header: `Authorization: Bearer <access_token>`
- Cookie: `__Secure-LATC` (configurable via `AUTH_TOKEN_COOKIE_NAME`)
- SSE query param: `bearer_token`
- Socket.IO auth payload: `bearer_token`

### ID token
- Header: `X-ID-Token` (configurable via `ID_TOKEN_HEADER_NAME`)
- Cookie: `__Secure-LITC` (configurable via `ID_TOKEN_COOKIE_NAME`)
- SSE query param: `id_token`
- Socket.IO auth payload: `id_token`

### Precedence
1) Explicit transport payload (headers or auth payload for Socket.IO)
2) Query params (SSE only)
3) Cookies (fallback)

Notes:
- For SSE, query param tokens are injected into headers in
  [chat web app](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py) before gateway processing.
- For Socket.IO, the gateway session upgrade uses auth payload first, then cookies
  in [ingress chat core](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/chat_core.py).
- If cookies are present, they are treated as valid credentials (same as headers).

## Delegated proxy cookie flows

Delegated auth has two proxy-level request shapes:

| Shape | Browser request contains | Proxy action |
|---|---|---|
| Masquerade cookie flow | proxylogin masquerade cookie only | call internal `/auth/unmask`, receive real token cookies, inject them into the upstream request |
| Non-masquerade cookie flow | configured auth cookie and configured ID cookie | forward the request with those cookies unchanged |

The current web-proxy templates include a commented future validation hook in
the non-masquerade branch:

```nginx
# local validation = ngx.location.capture("/auth/validate", {
#     method = ngx.HTTP_GET,
# })
```

Leave this call disabled until proxylogin exposes the validation endpoint. The
gateway/backend still validates the resulting JWTs through the configured auth
provider; the future proxylogin validation hook is an extra proxy-side gate for
non-masquerade browser cookie sessions.

## Configuration

Common environment variables:
- `ID_TOKEN_HEADER_NAME` (default `X-ID-Token`)
- `AUTH_TOKEN_COOKIE_NAME` (default `__Secure-LATC`)
- `ID_TOKEN_COOKIE_NAME` (default `__Secure-LITC`)
- `IDP_DB_PATH` (SimpleIDP user token map)
- `services.session_token.secret` (descriptor secret used by bundle session auth)

In descriptor-driven deployments these values come from the selected platform
authority provider in Connection Hub:

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            providers:
              cognito:
                authenticator:
                  id_token_header_name: X-ID-Token
                  cookie:
                    auth_token_cookie_name: __Secure-LATC
                    id_token_cookie_name: __Secure-LITC
                  jwks_cache_ttl_seconds: 86400
```

## Roles (current)

Common role names in this codebase (non‑exhaustive):
- `kdcube:role:super-admin` → privileged
- `kdcube:role:admin` → privileged
- `kdcube:role:paid` → paid
- `kdcube:role:registered` → baseline platform user
- `kdcube:role:service` → service accounts (registered unless explicitly privileged)

Role sets are defined in `kdcube_ai_app/auth/AuthManager.py`.

Central platform-auth rule:

- if a configured platform authority successfully authenticates a user and
  returns no roles, platform auth normalizes that user to
  `kdcube:role:registered`;
- this applies to platform authorities such as Cognito, SimpleIDP, and
  configured bundle-session platform providers;
- this does not apply to raw external channel proofs such as Telegram initData
  until they resolve/project to a platform authority.

## References
- Gateway/auth adapters:
  [gateway adapter](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/gateway.py),
  [auth adapter](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/auth.py)
- SSE + Socket.IO ingress:
  [SSE chat](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py),
  [Socket.IO chat](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py),
  [ingress chat core](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/chat_core.py)
