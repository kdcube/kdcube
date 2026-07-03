---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
title: "Set Up A Platform Authority Provider"
summary: "Recipe for selecting and configuring the KDCube platform authority provider: Cognito/multi-Cognito, SimpleIDP, or a bundle-hosted platform session."
status: draft
tags: ["recipes", "connections", "connection-hub", "platform-authority", "cognito", "multi-cognito", "simple-idp", "bundle-session"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/host-platform-authority-in-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
---
# Set Up A Platform Authority Provider

Use this recipe when a deployment needs to decide how users become KDCube
platform users.

The platform authority is the authority that answers:

```text
who is the platform subject?
which platform roles and permissions does this subject have?
which browser credential proves this platform session?
which subject owns platform/economics activity?
```

Connection Hub owns the platform authority registry. `assembly.yaml` selects
which registered provider is active for the runtime. Browser clients consume
the generated `/api/cp-frontend-config` contract and should not hardcode
provider internals.

## Provider Methods

| Method | Use when | Browser driver | Credential transport |
| --- | --- | --- | --- |
| Cognito | One Cognito pool/client is the platform login provider. | OIDC authorization-code flow. | Access token in `AUTH_TOKEN_COOKIE_NAME`, ID token in `ID_TOKEN_COOKIE_NAME`. |
| Multi-Cognito | A runtime trusts more than one Cognito pool/client pair. | Browser still logs into one configured OIDC provider. | Access token + ID token; server verifies both against the trusted provider list. |
| SimpleIDP | Local/dev or controlled test deployment needs simple users without an external IdP. | Simple token issue/login flow. | Simple platform token in `AUTH_TOKEN_COOKIE_NAME` or Authorization header. |
| Bundle-hosted session | An app bundle owns the login UI/upstream proof and KDCube should accept the result as a platform session. | Browser follows `auth.loginUrl`. | KDCube bundle-session token in `AUTH_TOKEN_COOKIE_NAME`; ID token cookie is not required. |

All methods should produce a `kdcube.platform` subject for normal platform
surfaces.

## Select The Provider In Assembly

`assembly.yaml` should select the active Connection Hub platform provider. It
should not duplicate the provider implementation details.

```yaml
auth:
  type: cognito
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito
```

For a bundle-hosted platform session:

```yaml
auth:
  type: bundle
  idp: session
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: product_google_session
    entrypoint: login
```

For SimpleIDP:

```yaml
auth:
  type: simple
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: simple
```

The selected provider id must exist in the Connection Hub authority registry.

## Register Cognito / Multi-Cognito

Place Cognito provider details under
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

For a single Cognito deployment, the `trusted_providers` list may contain only
the primary provider. Multi-Cognito is server-side trust expansion; it does not
mean the browser logs into multiple providers at once.

Expected browser config:

```json
{
  "auth": {
    "authType": "cognito",
    "oidcConfig": {
      "authority": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_PRIMARY",
      "client_id": "primary-client"
    },
    "authTokenCookieName": "__Secure-LATC",
    "idTokenCookieName": "__Secure-LITC",
    "profileUrl": "/profile",
    "logoutUrl": "/api/platform/logout"
  }
}
```

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
registered as a platform provider so the rest of the runtime can use the same
provider-selection path.

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
                authenticator:
                  type: simple_idp_token
                  cookie:
                    auth_token_cookie_name: __Secure-LATC
```

Expected browser/server behavior:

```text
simple login/token issue
  -> token is carried in Authorization or LATC
  -> SimpleIDP verifies local user registry
  -> /profile verifies a non-anonymous platform user
```

SimpleIDP does not use a browser OIDC callback and does not require an ID token
cookie.

## Register Bundle-Hosted Session

Use this when a bundle hosts the login page and upstream proof flow, but
Connection Hub owns platform authority registration and policy.

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
                type: bundle_session_login
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
                type: google_id_token
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
        -> bundle login page
        -> upstream proof, for example Google ID token
        -> bundle operation calls Connection Hub SDK runtime
        -> runtime verifies upstream proof and resolves grants
        -> runtime issues KDCube bundle-session token
        -> server sets LATC
        -> /profile verifies a non-anonymous platform user
```

The bundle-hosted flow does not require `ID_TOKEN_COOKIE_NAME`; that cookie is
for Cognito/OIDC browser auth. See
[Host A Platform Authority Flow In A Bundle](host-platform-authority-in-bundle-README.md)
for the full bundle-side recipe.

## Secrets

Keep sensitive values in the secrets lifecycle, not in public descriptors.

| Secret | Used by |
| --- | --- |
| Cognito app/client secret, if applicable | Cognito provider/runtime. |
| `services.session_token.secret` | Bundle-session token signing and verification. |
| Upstream provider secrets | Bundle-hosted login operations or request authenticators. |
| Bot/webhook/OAuth client secrets | Connection Hub authenticators and integration providers. |

Every ingress/proc worker that validates bundle-session tokens must read the
same `services.session_token.secret`.

## Switching Providers

Switching platform providers on the same browser origin is an operational test
step. Browser cookies are scoped by origin, path, and cookie name. They are not
scoped by tenant/project or active KDCube descriptor.

Before switching a local or shared-origin environment:

1. If the old runtime is still available, call its `auth.logoutUrl`.
2. Stop or refresh the old runtime.
3. Clear site data for the origin if the old provider used HttpOnly cookies and
   logout is not available or not routed.
4. Start the new runtime.
5. Open `/api/cp-frontend-config` and verify `auth.authType`, provider urls, and
   cookie names.
6. Complete login for the selected provider.
7. Open `/profile` and verify it returns a non-anonymous platform user.
8. Open one registered-user surface, for example bundles list or chat.

Expected cookie state after login:

| Provider | Expected cookies |
| --- | --- |
| Cognito / multi-Cognito | `AUTH_TOKEN_COOKIE_NAME` with access token and `ID_TOKEN_COOKIE_NAME` with ID token. |
| SimpleIDP | `AUTH_TOKEN_COOKIE_NAME` or Authorization header with simple token. |
| Bundle-hosted session | `AUTH_TOKEN_COOKIE_NAME` with KDCube bundle-session token. `ID_TOKEN_COOKIE_NAME` is not required. |

If `/profile` remains anonymous, do not trust visual login state in the client.
Debug from the server contract:

- `/api/cp-frontend-config` came from the intended runtime;
- `/profile` is routed to ingress;
- `auth.logoutUrl` is routed if the shell exposes logout;
- expected cookies are present for the selected provider;
- stale cookies from another provider are not still present on the same origin;
- Cognito callback completed before `getUser()` fallback;
- bundle-session provider wrote the platform auth/session cookie.

## Minimal Verification Checklist

For every provider method, verify:

- `assembly.yaml` selects the intended Connection Hub provider.
- `bundles.yaml` has the selected provider under
  `connection-hub@1-0.config.authority_registry.authorities.kdcube.platform`.
- `/api/cp-frontend-config` returns the expected `authType`.
- `/profile` returns anonymous before login and a platform user after login.
- Registered-user surfaces reject anonymous and work after login.
- Logout or site-data clearing returns `/profile` to anonymous.

