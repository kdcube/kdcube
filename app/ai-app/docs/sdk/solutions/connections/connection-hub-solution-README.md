---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
title: "Connection Hub Solution"
summary: "Canonical map of Connection Hub roles: connection edges, identity-family resolution, request authenticators, authority projection, delegated connections, link flows, and widget auth-context transport."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "identity", "auth", "authority", "delegated-connections"]
updated_at: 2026-07-01
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/channel-first-connection-edge-flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/platform-first-connection-edge-flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/widget-auth-context/widget-auth-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
---
# Connection Hub Solution

Connection Hub is the solution component that lets KDCube connect identities,
request proofs, platform authority, and delegated connections without
collapsing those concepts into scattered auth tricks.

It has eight roles:

```text
Connection Hub
  |
  +-- Connection Edges
  |     identity -> identity delegation, same graph for links and delegates
  |
  +-- Identity Family Resolver
  |     current actor/platform user -> runtime user ids from connection edges
  |
  +-- Request Authenticators
  |     request proof -> verified external identity
  |
  +-- Authority Providers
  |     authority_id -> authenticators + grant resolver + linkers
  |
  +-- Authority Projection
  |     actor identity + resolved connection edge -> UserSession authority
  |     short-lived Data Bus tokens use that UserSession
  |
  +-- Delegated Connections
  |     credential/proof -> authenticator -> linker/grant -> allowed actions
  |
  +-- Link Flows
  |     platform-first and channel-first linking
  |
  +-- Widget/Auth Context Transport
        host iframe/server config -> promoted auth headers
```

## Descriptor Shape

Connection Hub has two related but different descriptor branches:

```yaml
config:
  identity:
    # Current request-identity mechanics.
    # This branch is intentionally narrow today.
    authenticator_selector_cache: ...
    authenticators: ...
    role_resolver: ...
    link_flows: ...

  authority_registry:
    # Canonical registry of authority realms.
    authorities:
      kdcube.platform:
        label: KDCube platform authority
        platform: true
        providers: ...
```

`identity` is the existing operational branch for request authenticators,
selector cache, role-resolution hooks, and link-flow UX. It is not the full
authority registry.

`authority_registry.authorities` is the canonical place for declaring authority
realms and provider instances. A platform authority is not a separate
descriptor kind. It is an authority with `platform: true`.

This lets one deployment register multiple platform-capable authorities without
creating one-off config keys:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      platform: true
      label: KDCube platform authority
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

    telegram.kdcube_ref:
      platform: false
      label: KDCube Ref Telegram identity
      providers:
        telegram_bot_init_data:
          type: telegram_init_data
          enabled: true
          authenticator:
            secret_ref: identity.authenticators.telegram_kdcube_ref.bot_token

    google.accounts:
      platform: false
      label: Google Accounts
      providers:
        google_oidc:
          type: google_id_token
          enabled: true
          authenticator:
            client_id: <google-client-id>.apps.googleusercontent.com
```

`entrypoints.login`
: Browser page for signing in through this provider. The hosting bundle can own
  this UI and styling.

`entrypoints.session_issue`
: Action/callback that validates the upstream proof and asks the Connection Hub
  SDK to issue the configured KDCube session credential.

`entrypoints.consent`
: Optional bundle-hosted delegated credential consent renderer for this
  authority provider. Connection Hub still owns CSRF validation, grant
  narrowing, authorization-code creation, and token issuance.

The consent renderer is presentation only. Connection Hub calls the configured
bundle operation with a payload that includes `form_action`, `csrf_token`,
`request.client_id`, `request.redirect_uri`, `request.scope`, `request.resource`,
PKCE fields, selectable grants, and selectable tools. The rendered page should
POST approve/deny to `form_action` and include those request fields as hidden
inputs. Connection Hub re-validates the client, redirect, scopes, CSRF, selected
grants, and selected tools before issuing anything.

For resilience across custom renderers and browser/proxy behavior, Connection
Hub can recover missing non-secret authorize parameters from the same-origin
`/oauth/authorize?...` referrer on consent POST. That recovery does not relax
client/redirect validation or CSRF. If consent fails, look for
`[connection-hub.oauth] authorize_consent rejected`,
`dynamic_client_missing`, or `consent params recovered from authorize referrer`
in the proc logs.

The delegated credential adapter references the provider instead of repeating
bundle URLs:

```yaml
connections:
  delegated_credentials:
    oauth:
      enabled: true
      consent_ui:
        authority_ref:
          authority_id: kdcube.platform
          provider_id: versatile_google_session
          entrypoint: consent
```

In this shape:

```text
authority -> provider instance
```

`providers.<id>` is a configured provider instance. `providers.<id>.type` is the
implementation type/enum. The id and type may match for the normal single
instance case, such as `providers.cognito.type: cognito`. They diverge only
when a deployment registers multiple instances of the same provider type, for
example `cognito_admin` and `cognito_customer`. A provider instance may have:

- `authenticator`: verifies incoming auth material;
- `issuer`: mints auth material;
- `input.authenticator_ref`: points to another authenticator used as input;
- `entrypoints`: names browser/runtime bundle operations for login, session
  issuance, and optional consent rendering;
- `grants.default`: roles/permissions issued to every successful session from
  this provider.
- `grants.assignable`: the maximum roles/permissions this provider may assign
  through authority-level subject grants.

For example, `versatile_google_session` consumes a Google ID token verified by
`google.accounts.providers.google_oidc`, then issues a KDCube bundle-session
token for subject `google:<sub>`. The hosted UI may live in Versatile, but
authority ids, grants, TTL, and subject assignments remain in Connection Hub.

Telegram Mini App `initData` remains a channel/request authenticator in this
model. It is linked to the Google-backed platform identity through a Connection
Hub connection edge; it is not configured as a `kdcube.platform` session
provider.

The hosting bundle does not need a local platform-session config branch. It is
registered by provider entrypoints:

```yaml
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
```

Frontend platform login uses the same registry instead of a materialized URL in
the assembly descriptor:

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: versatile_google_session
    entrypoint: login
```

The web app asks Connection Hub to resolve that provider entrypoint. Connection
Hub returns the concrete bundle public URL for the current tenant/project.

If the same deployment later adds another platform login flow, it adds another
provider instance:

```yaml
authority_registry:
  authorities:
    kdcube.platform:
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
                - kdcube:role:super-admin
              permissions:
                - kdcube:*:chat:*;read;write
                - kdcube:*:*:*
```

The role policy for bundle-session subjects stays in the platform authority:

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
```

For Cognito-backed platform login, this provider `grants.default` branch is not
used. Cognito roles are read from the verified ID token claims. A Cognito user
without any platform role/group claim is still a platform-authenticated user and
receives the baseline `kdcube:role:registered` role at the platform auth
boundary.

Provider `grants.assignable` bounds what authority-level subject grants may
issue through that hosted provider.

Subject grants use stable authority subjects. Email can be used only in
`grants.bootstrap_rules` as a verified bootstrap claim; it is not the identity
key. For Google, a rule matching email must require/prove
`email_verified: true`.

Legacy or intermediate descriptors may still keep request authenticators under
`identity.authenticators` until that branch is migrated into
`authority_registry.authorities.*.providers.*.authenticator`.

The bundle remains the hosted UI/API surface. The reusable provider runtime is
SDK-owned:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

Connection Hub remains the registry owner for authority ids, platform-ness,
provider instances, grants, TTL, and host metadata.

## One Mental Model

```text
incoming world event/request/widget call
       |
       | proof material
       |   browser cookies / Telegram initData / Slack signature / API key
       v
Connection Hub Authenticator Selector
       |
       | candidate authenticators
       v
Request Authenticator
       |
       | verified identity + authority_id
       v
Surface Guard
       |
       | same authority -> Grant Resolver
       | different authority -> Connection Edge Resolver -> Grant Resolver
       v
KDCube app/API/runtime/ReAct/economics
```

Delegated connections are the common abstraction for consented capability
relationships. The token or proof is not meaningful by itself. A registered
authenticator interprets it, and a linker/grant resolver decides which
principal, representative, resource, and actions it carries:

```text
credential / proof / token
  Telegram initData / Gmail OAuth token / iCloud password / KDCube OAuth token
       |
       v
registered authenticator
  telegram / gmail / slack / icloud / delegated_client / future provider
       |
       v
linker / grant resolver
  connection edge / delegated account grant / delegated client grant
       |
       v
allowed actions on resource surface
  Gmail / Slack / iCloud / KDCube MCP / future KDCube API
```

The visible use cases differ only by authenticator and linker:

```text
Telegram:
  initData -> telegram authenticator -> connection edge -> projected UserSession

Gmail:
  OAuth account credential -> google authenticator -> account grant -> Gmail capability

OAuth delegated credential:
  KDCube-issued token -> delegated_client authenticator -> grant registry -> integration principal + tools

Managed KDCube Services MCP:
  delegated_client credential -> kdcube-services@1-0 -> conversations / named_services tools
  named_services_list -> namespace capabilities/schema -> namespace operation
```

Connection edges prove which identity may represent which other identity, and
which grants are delegated on that relationship. Delegated connection
credentials are one edge-producing/edge-consuming use case; Telegram account
linking is another. Do not derive platform roles from provider-local roles. The
delegated representative receives only the allowed capability, not the
grantor's full platform session.

## Reading Order

| Need | Read |
| --- | --- |
| Understand the complete map | This document |
| Store and resolve identity delegation edges | [Connection Edges](connection-edges/connection-edges-README.md) |
| Resolve all linked runtime user ids for product aggregation | [Identity Family Resolver](identity-family-resolver/identity-family-resolver-README.md) |
| User starts in Telegram/Slack/etc. and then attaches to KDCube | [Channel-First Connection Edge Flow](link-flows/channel-first-connection-edge-flow-README.md) |
| User starts in KDCube and then proves a provider identity | [Platform-First Connection Edge Flow](link-flows/platform-first-connection-edge-flow-README.md) |
| Authenticate a request with provider proof | [Request Authenticators](request-authenticators/request-authenticators-README.md) |
| Understand selector/authenticator/authority/grant contracts | [Authority Provider Runtime](authority-providers/authority-provider-runtime-README.md) |
| Understand token/proof routing metadata | [Authority Credential Envelope](authority-providers/credential-envelope-README.md) |
| Carry roles/economics across runtime boundaries | [Authority Projection](authority-projection/authority-projection-README.md) |
| Open Data Bus from Telegram or another non-browser actor | [Federated Data Bus Session Tokens](../../bundle/auth-bundle-federated-README.md) |
| Understand delegated representatives and grants | [Delegated Connections](delegated-connections/delegated-connections-README.md) |
| Connect Gmail/Slack/iCloud as delegated provider accounts | [Delegated Accounts](delegated-accounts/delegated-accounts-README.md) |
| Host Connection Hub or another widget in an iframe | [Widget Auth Context](widget-auth-context/widget-auth-context-README.md) |
| Know where data/secrets live | [Storage Model](storage-model/storage-model-README.md) |
| Understand gateway-level auth selection | [Auth Selector](../../../service/auth/auth-selector-README.md) |

## Main Roundtrips

### Channel-First Connection Edge Flow

The user starts in a context where provider auth material is already present.
Telegram Mini App is the current implemented example.

```text
Telegram Mini App has Telegram.WebApp.initData
       |
       v
host embeds Connection Hub iframe
       |
       | CONFIG_RESPONSE.authContext.headers
       |   X-Telegram-Init-Data
       |   X-KDCube-Auth-Authority-ID
       |   X-KDCube-Auth-Authenticator-ID
       v
Connection Hub iframe calls Connection Hub
       |
       v
Connection Hub authenticator selector picks the Telegram verifier
       |
       v
Connection Hub validates Telegram proof
       |
       v
creates provider-proof claim challenge
       |
       v
user opens platform claim URL in browser
       |
       v
KDCube platform sign-in if needed
       |
       v
claim endpoint writes telegram:<id> -> platform_user_id connection edge
       |
       v
Connection Hub emits Data Bus update to original iframe
```

### Platform-First Connection Edge Flow

The user starts from a normal KDCube browser/platform session.

```text
KDCube browser session
       |
       v
Connection Hub creates challenge for current platform_user_id
       |
       v
user opens provider proof surface
       |
       v
provider proof completes challenge
       |
       v
Connection Hub writes provider:<subject> -> platform_user_id
```

### Request Authentication

The gateway/service auth side asks for a complete `UserSession`.

```text
HTTP / SSE / Socket.IO / app API request
       |
       v
Connection Hub SDK RequestAuthResolver
       |
       +-- platform token/cookie auth
       |
       +-- ConnectionHubAuthenticationSurface
             |
             v
           selected authenticator verifies request proof
             |
             v
           verified identity + authority_id
             |
             v
           connection edge resolver resolves required authority if needed
             |
             v
           grant resolver returns roles/economics/access grants
       |
       v
UserSession
```

## Data Sources

| Data | Source | Used by |
| --- | --- | --- |
| Provider proof | Incoming request or host widget config, for example Telegram `initData` | Request authenticator module |
| Authority/authenticator hints | App/server config for the surface that owns the provider integration | Connection Hub authenticator selector |
| Verifier secret | Bundle secrets / secrets service via `secret_ref` | Provider verifier only |
| Connection edge | Connection Hub edge store | Request auth, authority projection, link status, delegation |
| Identity family | Connection Hub edge resolver | Product aggregation such as memories across connected identities |
| Platform roles | Platform principal/role resolver | Authority projection |
| Delegated connection grant | Connection Hub grant/account store | Automation/app tools or inbound protocol adapters |
| Provider account token | Connections framework user-scoped store | Automation/app tools |
| Widget auth context | Host `CONFIG_RESPONSE` | Child iframe API calls |

## Current Implementation Boundary

Current implementation:

- Telegram provider proof is implemented.
- Request-authenticator metadata is Postgres-backed.
- Connection edges and edge challenges are currently bundle-local JSON in the
  example app.
- Identity-family resolution is implemented as Connection Hub resolver logic
  over the same edge store.
- Delegated Gmail/Slack/iCloud provider account storage uses the existing
  connections and email integration stores.
- OAuth delegated credential is the current inbound delegated-connection
  protocol adapter.
- `kdcube-services@1-0` is the current built-in managed MCP example. It exposes
  `conversations` and `named_services` MCP surfaces protected by
  `delegated_client`.
- Application REST operations can also be protected by managed delegated
  credentials through `surfaces.as_provider.api...auth`. This is a REST guard,
  not generic browser/platform auth and not MCP guard reuse. It validates the
  delegated bearer, resource, grant set, and selected operation, then projects
  the grantor user into the runtime request context.
- Platform REST APIs can opt into the same delegated credential model by
  registering their resource pattern and one operation boundary in Connection
  Hub's delegated credential resource catalog. The shared request-auth layer
  validates the delegated bearer before the route handler runs and returns a
  projected `UserSession` for the approving platform user. This is the
  "delegated devops automation" shape and is independent of whether the
  approving user signed in through Cognito or an application-hosted platform
  authority.
- Admin-created delegated automation can also use a configured all-resource
  scope, `resource: "*"`, guarded by `admin_only: true` and the
  `kdcube:role:super-admin` grant. Platform roles are grants in this authority
  inventory.
- `named_services` is the current generic MCP bridge over configured
  named-service namespaces such as `mem`, `task`, and `cnv`. Its MCP server
  advertises instructions to call `named_services_list` first, then inspect
  capabilities/schema, then call search/get/write/action tools.
- MCP connector presentation is metadata, not authorization: KDCube server icons
  and `ToolAnnotations` are provided for clients such as Claude, while
  Connection Hub still enforces resource/tool/grant/identity-scope from stored
  delegated credential records.
- Gateway auth selection is documented in service auth.

Production direction:

- move connection edges and challenges to durable Postgres or a platform-owned
  identity service;
- keep verifier secrets only in descriptor-backed bundle secrets/secrets
  service;
- keep Connection Hub authenticators as the only place for Telegram/Slack/OIDC
  request-proof verification.

## Boundary With Delegated Credential Protocol Adapters

OAuth delegated credential is the current service/protocol implementation of one
delegated-connection authenticator and grant registry. It verifies KDCube-issued
integration credentials, resolves the delegated representative, and enforces
selected actions. The shared diagram lives in
[Delegated Credential Protocol Adapters](delegated-credentials/delegated-credential-protocol-adapters-README.md).

Do not put Telegram/Slack/webhook proof verification into OAuth delegated
credential. That belongs to Connection Hub request authenticators. Do not put
OAuth-code issuance, refresh-token rotation, or selected-tool grant state into
the generic edge store. That belongs to delegated connection protocol adapters
such as [OAuth delegated credential Protocol Adapter](delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).
