---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
title: "Set Up Platform Sign-In"
summary: "Recipe for selecting and configuring the KDCube platform authenticator: Cognito, SimpleIDP, or server-side login with a KDCube platform session."
status: draft
tags: ["recipes", "connections", "connection-hub", "platform-authority", "cognito", "multi-cognito", "simple-idp", "bundle-login"]
keywords: ["platform authority", "server-side login", "Cognito", "SimpleIDP", "bundle"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/host-platform-login-in-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/server-side-login-and-platform-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
---
# Set Up Platform Sign-In

Use this recipe when a deployment needs to decide how users become KDCube
platform users.

The `kdcube.platform` authority is the realm that groups the authenticators
allowed to establish a platform subject and the grants associated with that
subject. The sign-in configuration answers:

```text
who is the platform subject?
which platform roles and permissions does this subject have?
which browser credential proves this platform session?
which subject owns platform/economics activity?
```

Connection Hub owns the platform authority registry. `assembly.yaml` selects
one registered authenticator or server-side login lane for the runtime.
Browser clients consume the generated `/api/cp-frontend-config` contract and
should not hardcode authenticator internals.

## Sign-In Choices

| Choice | Use when | Browser driver | Credential transport |
| --- | --- | --- | --- |
| Cognito | One Cognito pool/client is the platform authenticator. | OIDC authorization-code flow. | Access token in `AUTH_TOKEN_COOKIE_NAME`, ID token in `ID_TOKEN_COOKIE_NAME`. |
| Multi-Cognito | One Cognito authenticator trusts more than one pool/client pair. | Browser still logs into its primary Cognito pool. | Access token + ID token; server verifies both against the trusted pool list. |
| SimpleIDP | Local/dev or controlled test deployment needs simple users without an external IdP. | Simple token issue/login flow. | Simple platform token in `AUTH_TOKEN_COOKIE_NAME` or Authorization header. |
| App-defined server-side login | An app owns the login UI and validates the authenticator proof; KDCube owns the resulting platform session. | Browser follows `auth.loginUrl`. | KDCube `kst1` platform-session token in `AUTH_TOKEN_COOKIE_NAME`; ID token cookie is not required. |
| Platform-hosted server-side login | The platform hosts sign-in through the Cognito or OIDC authenticator named by the `bundle` entry; the browser runs no identity client. | Browser follows `auth.loginUrl` (`/api/platform/session/login`); the platform callback sets the cookie. | KDCube `kst1` platform-session token in `AUTH_TOKEN_COOKIE_NAME`, HttpOnly, sliding lifetime. See [Platform-Hosted Server-Side Login](../../../service/auth/server-side-login-and-platform-session-README.md#platform-hosted-server-side-login). |

Every successful choice should produce a `kdcube.platform` subject for normal
platform surfaces. The callback and sign-out URLs needed on the identity
provider, per origin and app client, are documented in:
[Register KDCube On Your Identity Provider](identity-provider-urls-README.md).

## Select The Sign-In Entry In Assembly

`assembly.yaml` should select the active Connection Hub sign-in entry. It
should not duplicate the authenticator or lane implementation details.

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito_demo
```

For app-defined server-side login:

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: product_google_session
    entrypoint: login
```

For an app-defined SimpleIDP authenticator:

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: simple
```

The selected `provider_id` must exist in the Connection Hub authority registry.

## Register Cognito / Multi-Cognito Authenticators

Place Cognito authenticator details under
`connection-hub@1-0.config.authority_registry`.

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            label: KDCube platform authority
            platform: true
            providers:
              cognito_demo:
                type: multi_cognito
                enabled: true
                authenticator:
                  type: cognito_id_token
                  id_token_header_name: X-ID-Token
                  region: eu-west-1
                  user_pool_id: eu-west-1_PRIMARY
                  app_client_id: primary-client
                  hosted_ui_domain: https://auth.example.com
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

For a single Cognito deployment, the `trusted_providers` list may contain only
the primary pool. Multi-Cognito expands the pools trusted by one authenticator;
the browser still signs into one pool at a time.

Expected browser config:

```json
{
  "auth": {
    "authType": "cognito",
    "oidcConfig": {
      "authority": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_PRIMARY",
      "client_id": "primary-client",
      "end_session_endpoint": "https://auth.example.com/logout"
    },
    "authTokenCookieName": "__Secure-LATC",
    "idTokenCookieName": "__Secure-LITC",
    "profileUrl": "/profile",
    "logoutUrl": "/api/platform/logout"
  }
}
```

The selected authenticator supplies all three OIDC values. See
[Authority Provider Runtime](../../../sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md#browser-auth-contract)
for the distinction between KDCube platform logout and Cognito sign-out.

Expected browser flow:

```text
browser -> Cognito Hosted UI / OIDC
        -> /platform/callback?code=...&state=...
        -> browser OIDC client receives access token + ID token
        -> browser writes LATC + LITC
        -> /profile verifies a non-anonymous platform user
```

The `/platform/callback?...code...state...` URL is normal for the Cognito
authorization-code flow.

## Register SimpleIDP

SimpleIDP is intended for local/dev and controlled tests. It should still be
registered as a platform authenticator so the rest of the runtime can use the
same sign-in selection path.

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            label: KDCube platform authority
            platform: true
            providers:
              simple:
                type: simple_idp
                enabled: true
                label: Local SimpleIDP platform identity
                authenticator:
                  id_token_header_name: X-ID-Token
                  cookie:
                    auth_token_cookie_name: __Secure-LATC
                    id_token_cookie_name: __Secure-LITC
                    masqueraded_token_cookie_name: __Secure-LMTC
```

The user store is not declared here. Every service reads
`/config/idp_users.json`, pinned by the runtime. The browser credential is
declared in `assembly.yaml` under `frontend.config.auth.token` and must exist in
that store.

Expected browser/server behavior:

```text
simple login/token issue
  -> token is carried in Authorization or LATC
  -> SimpleIDP verifies local user registry
  -> /profile verifies a non-anonymous platform user
```

SimpleIDP does not use a browser OIDC callback and does not require an ID token
cookie.

## Register App-Defined Server-Side Login

Use this when an application bundle hosts the login page and authenticator proof
flow, while Connection Hub owns the authority registration and policy and
KDCube owns the resulting session.

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            label: KDCube platform authority
            platform: true
            providers:
              product_google_session:
                type: bundle
                enabled: true
                entrypoints:
                  login:
                    bundle_id: product-app@1-0
                    route: public
                    operation: platform_login
                  session_issue:
                    bundle_id: product-app@1-0
                    route: public
                    operation: auth_google_session
                input:
                  authenticator_ref:
                    authority_id: google.accounts
                    provider_id: google_oidc
                issuer:
                  type: kdcube_session_token
                  ttl_seconds: 43200
                  cookie:
                    auth_token_cookie_name: __Secure-LATC
                    secure: true
                    same_site: lax
                grants:
                  default:
                    roles:
                      - kdcube:role:registered
                    permissions: []
                  assignable:
                    roles:
                      - kdcube:role:registered
                      - kdcube:role:super-admin
                    permissions:
                      - kdcube:*:*:*

          google.accounts:
            platform: false
            providers:
              google_oidc:
                type: google
                enabled: true
                authenticator:
                  client_id: "<google-client-id>.apps.googleusercontent.com"
```

Expected browser config:

```json
{
  "auth": {
    "authType": "bundle",
    "loginUrl": "/api/integrations/bundles/.../public/platform_login",
    "profileUrl": "/profile",
    "logoutUrl": "/api/platform/logout",
    "authTokenCookieName": "__Secure-LATC"
  }
}
```

Expected browser flow:

```text
browser -> auth.loginUrl
        -> login page hosted by the app
        -> authenticator proof, for example Google ID token
        -> application operation calls Connection Hub SDK runtime
        -> runtime verifies the authenticator proof and resolves grants
        -> runtime issues KDCube kst1 platform-session token
        -> server sets LATC
        -> /profile verifies a non-anonymous platform user
```

The server-side flow does not require `ID_TOKEN_COOKIE_NAME`; that cookie is
for Cognito/OIDC browser auth. See
[Host A Platform Login Flow In An App](host-platform-login-in-app-README.md)
for the full application-side recipe.

## Secrets

Keep sensitive values in the secrets lifecycle, not in public descriptors.

| Secret | Used by |
| --- | --- |
| Cognito app/client secret, if applicable | Cognito authenticator/runtime. |
| `platform.services.session_token.secret` | KDCube `kst1` platform-session signing and verification. |
| Identity-provider secrets | Server-side login operations or request authenticators. |
| Bot/webhook/OAuth client secrets | Connection Hub authenticators and integration providers. |

Every ingress/proc worker that validates `kst1` platform-session tokens must
read the same `platform.services.session_token.secret`.

## Switching Sign-In Choices

Switching platform sign-in on the same browser origin is an operational test
step. Browser cookies are scoped by origin, path, and cookie name. They are not
scoped by tenant/project or active KDCube descriptor.

Before switching a local or shared-origin environment:

1. If the old runtime is still available, call its `auth.logoutUrl`.
2. Stop or refresh the old runtime.
3. Clear site data for the origin if the old provider used HttpOnly cookies and
   logout is not available or not routed.
4. Start the new runtime.
5. Open `/api/cp-frontend-config` and verify `auth.authType`, provider URLs, and
   cookie names.
6. Complete login for the selected sign-in choice.
7. Open `/profile` and verify it returns a non-anonymous platform user.
8. Open one registered-user surface, for example bundles list or chat.

Expected cookie state after login:

| Sign-in choice | Expected cookies |
| --- | --- |
| Cognito / multi-Cognito | `AUTH_TOKEN_COOKIE_NAME` with access token and `ID_TOKEN_COOKIE_NAME` with ID token. |
| SimpleIDP | `AUTH_TOKEN_COOKIE_NAME` or Authorization header with simple token. |
| Server-side login | `AUTH_TOKEN_COOKIE_NAME` with a KDCube `kst1` platform-session token. `ID_TOKEN_COOKIE_NAME` is not required. |

If `/profile` remains anonymous, do not trust visual login state in the client.
Debug from the server contract:

- `/api/cp-frontend-config` came from the intended runtime;
- `/profile` is routed to ingress;
- `auth.logoutUrl` is routed if the shell exposes logout;
- expected cookies are present for the selected sign-in choice;
- stale cookies from the previous sign-in choice are not still present on the same origin;
- Cognito callback completed before `getUser()` fallback;
- the server-side login wrote the platform auth/session cookie.

## Minimal Verification Checklist

For every sign-in choice, verify:

- `assembly.yaml` selects the intended Connection Hub entry.
- `bundles.yaml` has the selected entry under
  `connection-hub@1-0.config.authority_registry.authorities.kdcube.platform`.
- `/api/cp-frontend-config` returns the expected `authType`.
- `/profile` returns anonymous before login and a platform user after login.
- Registered-user surfaces reject anonymous and work after login.
- Logout or site-data clearing returns `/profile` to anonymous.
