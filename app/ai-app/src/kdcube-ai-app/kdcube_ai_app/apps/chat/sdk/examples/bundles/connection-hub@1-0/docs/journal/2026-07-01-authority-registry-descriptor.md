---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/journal/2026-07-01-authority-registry-descriptor.md
title: "Authority Registry Descriptor"
summary: "Decision note: keep request identity mechanics separate from the canonical authority registry; represent platform authorities with platform: true."
status: active
tags: ["connection-hub", "authority-registry", "platform-authority", "bundle-session", "descriptor"]
---

# Authority Registry Descriptor

Date: 2026-07-01

## Problem

Connection Hub already has `config.identity`, but that branch currently means
request-identity mechanics:

- authenticator selector cache;
- request authenticators;
- role resolver hook;
- link-flow UX.

It is too narrow to become the full authority registry. We also need to support
more than one platform-capable authority, similar to multi-Cognito deployments
and bundle-session deployments.

## Decision

Add a separate canonical branch:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      label: KDCube platform authority
      platform: true
      grants:
        subjects:
          google:<verified_sub>:
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
      providers:
        versatile_google_session:
          type: bundle_session_login
          enabled: true
          label: Versatile Google platform session
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
              roles:
                - kdcube:role:registered
              permissions: []
            assignable:
              roles:
                - kdcube:role:registered
                - kdcube:role:super-admin
              permissions:
                - kdcube:*:chat:*;read;write
                - kdcube:*:*:*
    google.accounts:
      label: Google Accounts
      platform: false
      providers:
        google_oidc:
          type: google_id_token
          enabled: true
          authenticator:
            client_id: <google-client-id>.apps.googleusercontent.com
```

`platform` is a property of an authority:

```yaml
platform: true
```

It is not a separate descriptor kind and not a one-off config branch.

## Bundle-Hosted Provider Rule

A bundle may host the proof exchange and session issuance endpoint, but the
bundle must not own the authority policy.

The provider instance is registered under the target authority:

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
              roles:
                - kdcube:role:registered
              permissions: []
            assignable:
              roles:
                - kdcube:role:registered
              permissions:
                - kdcube:*:chat:*;read;write
```

Connection Hub owns:

- authority id;
- platform-ness;
- provider instance id and type;
- provider `grants.default` and `grants.assignable`;
- platform-subject grants for descriptor-backed bundle-session demos;
- TTL;
- browser/runtime entrypoints.

`entrypoints.login` is the provider's browser sign-in page.
`entrypoints.session_issue` is the proof exchange and KDCube session issuance
action. `entrypoints.consent` is an optional bundle-hosted delegated credential
consent renderer. The custom consent renderer owns presentation only; Connection
Hub still owns the approve/deny POST, CSRF validation, grant narrowing,
authorization-code creation, and token issuance.

Per-user role assignment is not attached to a raw channel proof. It belongs to
the platform authority's user/role store for a concrete platform subject. In the
channel-to-platform flow, a Telegram identity becomes platform-authorized only
through an explicit connection edge to the Google-backed platform subject.

The hosting bundle owns:

- provider-specific proof exchange;
- calling the platform bundle-session authority;
- failing closed if the registered provider is missing, disabled, not
  platform-capable, or not hosted by that bundle operation.

## Google Platform Login Provider

Google is an upstream authority, not the platform authority:

```yaml
google.accounts:
  platform: false
  providers:
    google_oidc:
      type: google_id_token
      authenticator:
        client_id: <google-client-id>.apps.googleusercontent.com
```

The platform provider lives under `kdcube.platform`:

```yaml
kdcube.platform:
  platform: true
  grants:
    subjects:
      google:<verified_sub>:
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
          roles: [kdcube:role:registered, kdcube:role:super-admin]
          permissions: [kdcube:*:chat:*;read;write, kdcube:*:*:*]
```

Versatile can host the UI (`platform_login`) and exchange endpoint
(`auth_google_session`), but Connection Hub remains the owner of the authority
registry and role policy.

Role source must be explicit:

- Cognito platform login reads roles from Cognito ID token claims
  (`cognito:groups`, `custom:roles`, or `roles`). A Cognito user without any
  platform role claim is authenticated but not authorized for `RequireUser`.
- Bundle-session platform login reads roles from Connection Hub
  `authority_registry` provider policy. A Google bundle-session provider can
  grant the baseline platform role in `grants.default.roles`.
- Raw Telegram proof should normally keep `grants.default.roles: []`. Telegram
  auth proves the channel actor; it does not by itself create a platform user.

No middleware should silently convert "authenticated but no roles" into a
platform user. If a provider should create a normal platform user, the
authority/provider config must say so.

The provider runtime is now SDK-owned:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

Versatile's `services/platform_session_issuer.py` is a thin hosted UI/operation
wrapper. It must not own the provider registry, provisioning rules, or
bundle-session issuance semantics.

## Why This Matters

This keeps roles and permissions defined in one place: the Connection Hub
registry. It also lets us support multiple platform authorities later without
adding incompatible config shapes.

The same registry can describe:

- Cognito-backed platform authorities;
- bundle-session platform authorities;
- custom authority providers loaded from bundles;
- request authenticators and provider instances as the model matures.

## Follow-Up

- Keep adding provider kinds to this registry instead of inventing local bundle
  config branches for authority policy.
- Promote request authenticators/custom authorities into this vocabulary as
  they become first-class provider instances.
- Keep formal docs aligned:
  - `docs/sdk/solutions/connections/connection-hub-solution-README.md`
  - `docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md`
  - `docs/sdk/solutions/connections/storage-model/storage-model-README.md`
  - `versatile@2026-03-31-13-36/docs/integrations/platform-session-issuer.md`

## Implementation Update

Implemented on 2026-07-01:

- `kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config`
  flattens and resolves `authority_registry.authorities.*.providers.*`.
- `AuthorityRegistryClient` resolves provider instances from the SDK-owned
  authority registry. It can use explicit registry data or the shared
  bundle-props store; it does not call the Connection Hub bundle.
- Connection Hub exposes `authority_provider_resolve` as a thin operations API
  facade for callers that only have HTTP/bundle-operation access.
- Versatile's hosted Telegram session login resolves its platform-session
  authority provider from Connection Hub instead of local bundle policy.
- Versatile's hosted Google login resolves its Google upstream authenticator and
  platform provider policy from Connection Hub, then issues a standard
  bundle-session `kst1` token.
- Bundle-session provider runtime moved into the Connection Hub SDK module
  `kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login`.
  Versatile now keeps only hosted UI/operation wrappers.
- SDK Google OIDC verification is available under
  `kdcube_ai_app.apps.chat.sdk.integrations.google`.
- The custom-authority runtime uses bundle/session auth; the normal demo runtime
  remains Cognito/multi-Cognito and must not be mixed into this test setup.

## 2026-07-02: Provider Entrypoint Resolution For Frontend Login

The platform frontend must not need a materialized `auth.login_url` in
assembly. The descriptor should name the Connection Hub provider entrypoint:

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: versatile_google_session
    entrypoint: login
```

The SDK resolver owns this lookup. The platform frontend config endpoint should
resolve the entrypoint through `ConnectionHubClient`, which loads the
Connection Hub registry from the shared bundle-props store and materializes
`auth.loginUrl` in the frontend config response.

Connection Hub also exposes a public facade for browser/external callers that
cannot use the in-process SDK client:

```text
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/authority_provider_entrypoint_resolve
```

Input:

```json
{
  "data": {
    "authority_id": "kdcube.platform",
    "provider_id": "versatile_google_session",
    "entrypoint": "login"
  }
}
```

Output includes the concrete current-runtime URL:

```json
{
  "ok": true,
  "url": "/api/integrations/bundles/demo/custom-authority/versatile@2026-03-31-13-36/public/platform_login"
}
```

The SDK client exposes
`ConnectionHubClient.resolve_authority_provider_entrypoint` and
`AuthorityRegistryClient.resolve_provider_entrypoint` for in-process callers.
The bundle API uses the same SDK client internally.

For product-specific migrations, this is the descriptor contract: register the
platform authority provider in Connection Hub, set the web frontend to
`auth.connection_hub`, and let the Connection Hub SDK client resolve the hosted
login URL.

## 2026-07-02: Reference Bundle-Hosted Consent Renderer

Versatile now includes a reference `delegated_consent` public operation for
`entrypoints.consent`. It renders a bundle-owned consent page using the payload
provided by Connection Hub and posts approve/deny back to Connection Hub's
`form_action`.

The split is:

- bundle owns consent presentation;
- Connection Hub owns CSRF, grant/tool narrowing, authorization-code creation,
  and delegated credential issuance.

This proves the custom consent extension point without requiring a product
bundle to patch platform OAuth routes.

## 2026-07-02: Cognito Is A Platform Authority Provider

Cognito and multi-Cognito are now represented in the same Connection Hub
authority registry as bundle-hosted platform-session providers.

Canonical selector in `assembly.yaml`:

```yaml
auth:
  type: cognito
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito
```

Canonical provider in `connection-hub@1-0.config.authority_registry`:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      label: KDCube platform authority
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
```

Runtime readers must use the Connection Hub SDK registry helpers instead of
reading Cognito fields directly from `assembly.yaml`. Deployment renderers may
still emit env vars such as `COGNITO_REGION` and `AUTH_PROVIDER`, but those are
runtime projections of the selected provider, not the canonical descriptor
home.
