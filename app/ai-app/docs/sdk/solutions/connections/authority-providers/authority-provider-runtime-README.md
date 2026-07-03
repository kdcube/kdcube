---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
title: "Authority Provider Runtime"
summary: "Canonical Connection Hub runtime contract for authenticator selection, authority-scoped identities, linkers, grant resolvers, and surface guards."
status: design
tags: ["sdk", "solutions", "connections", "connection-hub", "authority-provider", "authenticator-selector", "surface-guard", "grants"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/host-platform-authority-in-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
---
# Authority Provider Runtime

This document defines the canonical runtime model for Connection Hub request
auth, custom authorities, delegated connections, and protected surfaces.

The selector selects **authenticators**, not whole authorities. Each
authenticator belongs to an authority provider.

```text
request auth material
  token / cookie / header / signature / Telegram initData / API key
        |
        v
Connection Hub Authenticator Selector
  uses non-trusted hints and request shape to choose verifier candidates
        |
        v
Authenticator
  verifies one concrete proof/credential shape
        |
        v
verified identity under authority_id
        |
        v
Surface Guard
  compares resolved authority with required authority
        |
        +-- same authority -> Grant Resolver
        |
        +-- different authority -> Authority Linker -> Grant Resolver
        |
        v
authorize / reject
```

## Core Terms

| Term | Meaning |
| --- | --- |
| `authority_id` | The identity/grant realm. Examples: `kdcube.platform`, `custom.identity`, `telegram.kdcube_ref`, `delegated_client`. |
| `authenticator_id` | One verifier for one proof shape. Examples: `kdcube.cognito`, `custom.google_oidc`, `telegram.kdcube_ref.init_data`, `delegated_client.bearer`. |
| Authority Provider | Owns an `authority_id`, identity namespace, grant resolver, linkers, and registered authenticators. |
| Authenticator | Verifies auth material and returns a verified identity under its authority. |
| Connection Hub Authenticator Selector | Chooses authenticator candidates inside Connection Hub. It does not authorize and it does not trust hints as facts. |
| Authority Linker | Maps an identity from one authority to another, or returns null. |
| Grant Resolver | Loads roles, permissions, scopes, tools, or operation grants for an identity under one authority. |
| Surface Guard | Declares required authority/grants and asks the runtime to authorize the request. |

## Authority Registry Descriptor

Connection Hub keeps the authority registry separate from the older
`identity.*` request-authenticator branch.

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      label: KDCube platform authority
      platform: true
      providers:
        cognito:
          type: cognito
          enabled: true
          authenticator: ...
        versatile_google_session:
          type: bundle_session_login
          enabled: true
          input: ...
          issuer: ...
          grants: ...

    telegram.kdcube_ref:
      label: KDCube Ref Telegram bot identity
      platform: false
      providers:
        telegram_bot_init_data:
          type: telegram_init_data
          authenticator: ...
```

`platform: true` means identities from that authority can be used as the
platform subject for platform surfaces, economics, and ownership projection.
There can be more than one platform-capable authority in a deployment. For
example, a deployment may use Cognito and a bundle-session issuer during a
custom-authority migration.

Provider instances live under the authority they operate for. `providers.<id>`
is a configured provider instance; `providers.<id>.type` is the implementation
type/enum. The id and type may match for the normal single-instance case, such
as `providers.cognito.type: cognito`. They diverge only when there are multiple
instances of the same provider type. A provider instance can have one optional
authenticator, one optional issuer, one optional input authenticator reference,
and browser/runtime entrypoints.

For Cognito-backed platform login, Connection Hub owns the Cognito provider
metadata:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      platform: true
      providers:
        cognito:
          type: multi_cognito
          enabled: true
          label: KDCube Cognito platform session
          authenticator:
            type: cognito_id_token
            id_token_header_name: X-ID-Token
            region: eu-west-1
            user_pool_id: eu-west-1_PRIMARY
            app_client_id: primary-client
            service_client_id: primary-client
            jwks_cache_ttl_seconds: 86400
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

For a bundle-hosted platform session login provider, Connection Hub owns the
provider metadata and the hosting bundle owns only the UI/operation:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      platform: true
      providers:
        versatile_google_session:
          type: bundle_session_login
          entrypoints:
            login:
              bundle_id: versatile@2026-03-31-13-36
              route: public
              operation: platform_login
            session_issue:
              bundle_id: versatile@2026-03-31-13-36
              route: public
              operation: auth_google_session
            consent:
              bundle_id: versatile@2026-03-31-13-36
              route: public
              operation: delegated_consent
          input:
            authenticator_ref:
              authority_id: google.accounts
              provider_id: google_oidc
          issuer:
            type: kdcube_session_token
            ttl_seconds: 43200
          grants:
            default:
              roles: [kdcube:role:registered]
              permissions: []
            assignable:
              roles: [kdcube:role:registered]
              permissions: [kdcube:*:chat:*;read;write]

    google.accounts:
      platform: false
      providers:
        google_oidc:
          type: google_id_token
          authenticator:
            client_id: <google-client-id>.apps.googleusercontent.com
```

`entrypoints.consent` is not an authorization hook. It is a UI hook. The bundle
receives a Connection Hub payload and renders the product-specific consent
screen, then posts back to the supplied `form_action`. Connection Hub remains
the protocol owner: it validates CSRF, dynamic/public client registration,
redirect URI, requested scopes, selected grants, selected tools, and only then
creates the authorization code or delegated credential.

Bundle renderers should preserve the payload's OAuth request fields as hidden
form inputs. Connection Hub also has a defensive fallback that can recover
missing non-secret authorize fields from a same-origin authorize referrer, but
that is protocol hardening, not the renderer contract.

This keeps authority semantics registered once in Connection Hub while allowing
provider engines to live in bundles, SDK modules, or platform auth managers.
The hosting bundle is resolved by its provider `entrypoints` and does not carry
a local platform-session policy branch.

## Platform Authority Provider Methods

KDCube currently supports two main ways to provide the platform authority:

1. **Platform-managed token authority**: Cognito, multi-Cognito, or local
   SimpleIDP. The platform verifier validates the browser's platform tokens
   directly.
2. **Bundle-hosted platform session authority**: a bundle hosts the login UI and
   upstream proof flow, then asks the Connection Hub SDK runtime to issue a
   standard KDCube bundle-session token.

Both methods produce a `kdcube.platform` subject. They differ in how the browser
gets the session and which cookie slots are meaningful.

### Cognito / Multi-Cognito Platform Authority

```text
Browser
  -> /api/cp-frontend-config
  -> authType=cognito + oidcConfig
  -> Cognito Hosted UI / OIDC code flow
  -> /platform/callback?code=...&state=...
  -> browser OIDC client receives access token + id token
  -> browser writes platform cookies
  -> /profile verifies the platform session server-side
```

Server configuration:

- `assembly.yaml` selects the platform provider:

  ```yaml
  auth:
    type: cognito
    connection_hub:
      bundle_id: connection-hub@1-0
      authority_id: kdcube.platform
      provider_id: cognito
  ```

- Connection Hub owns provider details under
  `authority_registry.authorities.kdcube.platform.providers.cognito`.
- The runtime maps that provider into `MultiCognitoAuthManager`.
- The verifier expects an access token and, when available, an ID token.

Browser configuration:

- `/api/cp-frontend-config` returns `auth.authType: cognito`,
  `auth.oidcConfig.authority`, `auth.oidcConfig.client_id`,
  `auth.authTokenCookieName`, and `auth.idTokenCookieName`.
- Browser clients run the OIDC callback flow and write:

  | Cookie | Meaning in Cognito mode |
  | --- | --- |
  | `AUTH_TOKEN_COOKIE_NAME` / `__Secure-LATC` | Cognito/OIDC access token. |
  | `ID_TOKEN_COOKIE_NAME` / `__Secure-LITC` | Cognito/OIDC ID token. |
  | `MASQUERADED_TOKEN_COOKIE_NAME` / `__Secure-LMTC` | Optional masquerade token. |

The callback URL looking like `/platform/callback?code=...&state=...` is normal
for Cognito authorization-code flow because Cognito redirects back to the
configured KDCube `redirect_uri`.

### SimpleIDP Platform Authority

Simple auth is the local/development platform-token path. It does not involve a
browser OIDC callback.

```text
Configured SimpleIDP user/token
  -> browser or host receives a platform token
  -> token is carried in Authorization or AUTH_TOKEN_COOKIE_NAME
  -> SimpleIDP verifier resolves kdcube.platform subject
  -> /profile verifies the platform session server-side
```

Use this only when the deployment intentionally wants a simple platform identity
registry, usually local development, demos, or embedded test surfaces. Production
browser deployments should prefer Cognito/multi-Cognito or a registered
bundle-session platform provider.

### Bundle-Hosted Platform Session Authority

```text
Browser
  -> /api/cp-frontend-config
  -> authType=bundle + loginUrl/profileUrl/logoutUrl
  -> bundle-hosted login page
  -> upstream proof, for example Google ID token
  -> bundle public operation calls Connection Hub SDK runtime
  -> runtime verifies upstream proof and resolves grants
  -> runtime issues KDCube bundle-session token
  -> server sets platform auth cookie
  -> /profile verifies the platform session server-side
```

Server configuration:

- `assembly.yaml` selects the Connection Hub provider:

  ```yaml
  auth:
    type: bundle
    connection_hub:
      bundle_id: connection-hub@1-0
      authority_id: kdcube.platform
      provider_id: product_google_session
      entrypoint: login
  ```

- Connection Hub owns provider details under
  `authority_registry.authorities.kdcube.platform.providers.<provider_id>`.
- The provider type is `bundle_session_login`.
- The bundle owns only the registered UI/operations, for example
  `entrypoints.login`, `entrypoints.session_issue`, and optionally
  `entrypoints.consent`.

Browser configuration:

- `/api/cp-frontend-config` returns `auth.authType: bundle`, `auth.loginUrl`,
  `auth.profileUrl`, and `auth.logoutUrl`.
- There is no browser OIDC token-writing flow unless the hosted bundle page
  internally uses an upstream OIDC library.
- The issued KDCube session token is carried in:

  | Cookie | Meaning in bundle-session mode |
  | --- | --- |
  | `AUTH_TOKEN_COOKIE_NAME` / `__Secure-LATC` | KDCube bundle-session token. |
  | `ID_TOKEN_COOKIE_NAME` / `__Secure-LITC` | Not required for bundle-session auth; Cognito-specific clients may not see it. |
  | `MASQUERADED_TOKEN_COOKIE_NAME` / `__Secure-LMTC` | Optional masquerade token. |

The bundle-session verifier uses the platform auth token. It does not need a
Cognito-style ID token.

## Browser Auth Contract

Browser clients should consume the platform auth contract returned by
`/api/cp-frontend-config`; they should not construct provider URLs from bundle
ids, tenant/project ids, or descriptor internals.

| Frontend field | Source | Purpose |
| --- | --- | --- |
| `auth.authType` | Selected platform provider. | Selects the browser auth driver, for example `cognito`, `bundle`, or `simple`. |
| `auth.oidcConfig` | Cognito/multi-Cognito provider. | Browser OIDC driver config. Present only for OIDC-backed browser auth. |
| `auth.loginUrl` | Connection Hub provider `entrypoints.login`, when the provider hosts browser login. | Where the browser navigates to create a platform session. |
| `auth.profileUrl` | Platform default `/profile` unless overridden. | Server-side current-session probe after the browser has established auth cookies. |
| `auth.logoutUrl` | Platform default `/api/platform/logout` unless overridden. | Generic platform logout. It clears platform cookies and invalidates bundle-session storage when applicable. |

OIDC metadata, when present, is a browser login driver. The browser completes
the OIDC callback and writes the platform cookies from the OIDC tokens. After
that, `auth.profileUrl` is the server-side confirmation that the platform
gateway accepts the current request as an authenticated platform session.

`/api/platform/logout` is intentionally provider-neutral where it is routed by
the deployment proxy. For bundle-session providers it reads the configured
platform auth cookie, calls the bundle-session authority logout primitive, and
clears the platform cookies. A provider may still define a branded sign-out page
or upstream-provider cleanup flow, but the KDCube platform session is ended by
the platform logout contract.

## Switching Platform Authority Providers

Switching between Cognito and bundle-session on the same browser origin is a
deployment/test operation, not a normal user flow. Browser cookies are scoped by
origin, path, and cookie name. They are not scoped by tenant, project, or active
KDCube descriptor.

Before switching a local environment from one platform provider to another:

1. Stop or refresh the old environment.
2. If possible, call the old environment's `auth.logoutUrl` while it is still
   running.
3. Clear site data for the shared origin if the previous provider set HttpOnly
   cookies and the logout route is not available.
4. Start the new environment.
5. Check `/api/cp-frontend-config`.
6. Check that the server-side auth settings match the same cookie names and
   provider type.
7. Complete login.
8. Check `/profile`.

Expected checks:

| Selected provider | `/api/cp-frontend-config` | Browser cookies after login | `/profile` |
| --- | --- | --- | --- |
| Cognito / multi-Cognito | `authType: cognito`, `oidcConfig` present | `LATC` access token and `LITC` ID token | Non-anonymous platform user. |
| Bundle-session | `authType: bundle`, `loginUrl` present | `LATC` bundle-session token; `LITC` is not required | Non-anonymous platform user. |
| SimpleIDP | `authType: simple` or local simple config | `LATC` simple platform token or Authorization header | Non-anonymous platform user. |

If `/profile` remains anonymous after a successful browser login:

- verify the active `/api/cp-frontend-config` came from the intended runtime;
- verify the proxy routes `/profile` and, if used, `/api/platform/logout` to
  ingress;
- verify old HttpOnly cookies from another provider are not still present on the
  same origin;
- verify Cognito login wrote both access and ID token cookies;
- verify bundle-session login wrote the platform auth/session cookie.

Google in this model is an upstream authority/provider, not the platform
authority. The platform subject is created under `kdcube.platform` by the
registered hosted provider, typically as `google:<sub>` for bundle-session auth.

For platform login, a successfully authenticated platform subject is always a
platform user. If the authority/provider returns no roles, the platform auth
boundary normalizes the subject to `kdcube:role:registered`. Provider
`grants.default.roles` may still grant a more specific default role set, and
subject assignments/bootstrap rules may grant admin or app-specific roles.
Telegram channel proof should normally keep an empty default role set; raw
Telegram proof is channel authentication, not platform login.

Cognito is already a platform authenticator. Its roles come from token claims
such as `cognito:groups`, `custom:roles`, or `roles`. If those claims are
absent, the same platform-auth rule applies and the user receives
`kdcube:role:registered` at the platform boundary.

For descriptor-backed bundle-session demos, role policy can be attached to the
platform authority:

```yaml
kdcube.platform:
  grants:
    subjects:
      google:123:
        roles: [kdcube:role:super-admin]
        permissions: [kdcube:*:*:*]
    bootstrap_rules:
      - id: bootstrap_admin_by_google_email
        when:
          provider: google
          claims:
            email: owner@example.com
            email_verified: true
        roles: [kdcube:role:super-admin]
        permissions: [kdcube:*:*:*]
```

Assignments are bounded by the provider's `grants.assignable` block. If
a subject assignment or bootstrap rule asks for something outside that
assignable set, the hosted provider must fail closed.

`grants.subjects` is the canonical long-lived assignment surface. The
key is the authority subject, for example `google:<verified_sub>`, not email.
`grants.bootstrap_rules` is only a bootstrap mechanism for cases where an admin
knows a verified upstream claim before they know the stable subject. For Google,
an email rule must also require/prove `email_verified: true`; after login the
issued platform subject remains `google:<sub>`.

The hosted bundle does not implement this policy. The reusable runtime lives in:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

The bundle route/UI calls that runtime. The runtime resolves the provider
instance from Connection Hub, verifies the upstream proof, resolves grants, and
issues the bundle-session token.

## Request Hints Are Not Truth

Controlled surfaces may include hints to avoid slow or broad selection:

```http
X-KDCube-Auth-Authority-ID: custom.identity
X-KDCube-Auth-Authenticator-ID: custom.google_oidc
```

These are non-secret selector hints. They narrow candidate authenticators, but
identity is proven only when the selected authenticator verifies the
provider-specific proof.

Provider callbacks can carry the same information in query params:

```text
/public/telegram_webhook?integration_id=telegram.kdcube_ref
```

These hints only narrow the candidate list. Truth is produced only by a
successful authenticator verification result.

```text
hint says authority_id=custom.identity
        |
        v
Connection Hub selector tries candidate authenticators under custom.identity
        |
        v
authenticator verifies token/signature
        |
        v
truth = verified identity + verified authority_id
```

If a hinted authenticator is missing, disabled, or rejects the material, the
request fails closed unless the surface explicitly allows fallback selection.

## Surface Guard Contract

A protected surface should be able to declare:

```yaml
surface_guard:
  required_authority: kdcube.platform
  required_grants:
    - kdcube:role:feedback-reader
  accepted_auth:
    authority_ids:
      - kdcube.platform
      - custom.identity
    authenticator_ids:
      - kdcube.cognito
      - custom.google_oidc
      - delegated_client.bearer
```

Current platform surfaces implicitly require `kdcube.platform`. Custom
authority support becomes real when surfaces can declare another
`required_authority`.

## Runtime Authorization

```text
Surface Guard:
  required_authority = kdcube.platform
  required_grants    = [kdcube:role:feedback-reader]

Request:
  Authorization: Bearer <token>
  X-KDCube-Auth-Authenticator-ID: delegated_client.bearer

Runtime:
  selector -> delegated_client.bearer
  authenticator -> identity=integration:claude:<grantor>, authority_id=delegated_client
  linker delegated_client -> kdcube.platform if needed
  grant resolver for kdcube.platform
  authorize if required grant is present
```

For a custom authority surface:

```text
Surface Guard:
  required_authority = custom.identity
  required_grants    = [custom:role:admin]

Runtime:
  selector -> custom.google_oidc
  authenticator -> identity=custom:user:123, authority_id=custom.identity
  no platform link required
  grant resolver for custom.identity
  authorize if custom:role:admin is present
```

For a platform surface reached by a custom identity:

```text
Surface Guard:
  required_authority = kdcube.platform

Runtime:
  selector -> custom.google_oidc
  authenticator -> identity=custom:user:123, authority_id=custom.identity
  linker custom.identity -> kdcube.platform
  grant resolver for kdcube.platform
  authorize if platform grant is present
```

## Authority Provider Contract

An authority provider should expose:

```text
AuthorityProvider
  authority_id
  authenticators[]
  grant_resolver(identity, requested_grants)
  linkers[to_authority_id]
optional credential/grant provisioning operations
```

Bundle-local custom authorities declare this through the bundle manifest:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import authority_provider

class MyBundle:
    @authority_provider(
        authority_id="custom.identity",
        authenticator_id="custom.identity.oauth",
        credential_kinds=["authority_access"],
        audiences=["bundle:custom-app@1-0"],
    )
    async def custom_identity_provider(self):
        return self.custom_authority_provider
```

On proc load, the declaration is published to Redis authority discovery as
metadata. The verifier itself remains reachable only where the bundle is
loaded. Ingress may use the metadata for diagnostics/selection, but it must not
import bundle-local verifier code.

An authenticator result should include:

```json
{
  "authenticated": true,
  "authority_id": "custom.identity",
  "authenticator_id": "custom.google_oidc",
  "identity": {
    "subject": "user:123",
    "ref": "custom.identity:user:123",
    "label": "Sofia"
  },
  "auth_material_type": "google_oidc"
}
```

The grant resolver is authority-owned:

```text
grant_resolver("custom.identity", "user:123")
  -> roles / permissions / scopes / tools
```

The linker never invents grants. It only maps identity across authorities:

```text
linker("custom.identity:user:123", to="kdcube.platform")
  -> "kdcube.platform:a1b2c3d4-..."
  -> or null
```

## Credential Envelope

KDCube-issued credentials and delegated grant records use
`kdcube.credential.v1` as their routing envelope. The runtime reads the
untrusted envelope to choose a reachable authority provider, then the provider
performs real verification.

```text
credential envelope
  issuer_authority_id
  issuer_authenticator_id
  credential_kind
  audience
      |
      v
AuthorityRegistry
      |
      +-- reachable provider -> verify
      |
      +-- not reachable -> unresolved/fail closed
```

The canonical schema and examples are in
[Authority Credential Envelope](credential-envelope-README.md).

## Provisioning And Runtime Use

The same model has two lifecycle phases.

```text
Provisioning / consent
  grantor proves authority
    -> user login / channel proof / user or admin consent
    -> connection edge or delegated grant is written
    -> credential/capability is issued or stored

Runtime use
  credential/proof arrives later
    -> authenticator verifies it
    -> linker/grant resolver finds stored meaning
    -> authority or capability is produced
    -> allowed actions are enforced
```

## Migration Target

Current state:

- platform auth managers are registered selector authenticators for
  `kdcube.platform`;
- Connection Hub Telegram rows are request authenticators;
- Connection Hub caches authenticator metadata in Redis and still resolves proof,
  links, and grants on each request;
- OAuth delegated credential is the current protocol adapter that registers the
  `delegated_client` authority and `delegated_client.bearer` authenticator for
  managed MCP surfaces;
- `kdcube-services@1-0` uses that authority for the managed `conversations` and
  `named_services` MCP surfaces;
- most surfaces implicitly require `kdcube.platform`.

Target:

- all authenticators declare an `authority_id`;
- surface guards declare required authority and grants;
- delegated credential protocol adapters register authority providers and
  authenticators with the same descriptor/registry shape as request
  authenticators;
- custom deployments such as custom register `custom.identity` as an authority provider;
- platform APIs require `kdcube.platform` only when they truly require platform
  authority.

For MCP clients, authority-provider metadata is not enough for a good UX. The
MCP server should also advertise connector metadata:

```text
FastMCP(..., stateless_http=True, icons=[...], website_url=..., instructions=...)
tool annotations: readOnlyHint / destructiveHint / idempotentHint
```

Those hints help clients such as Claude render icons and group tools, while the
authority provider and grant resolver remain the enforcement path.
