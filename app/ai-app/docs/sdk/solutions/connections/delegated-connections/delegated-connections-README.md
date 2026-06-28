---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
title: "Delegated Connections"
summary: "Connection Hub role for consented connections where a credential/proof is verified by an authenticator, linked to a principal or grant, and constrained by allowed actions."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "delegated-connections", "oauth", "mcp", "consent", "grants"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-mcp-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-mcp-consent-branding-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md
---
# Delegated Connections

Delegated Connections is the Connection Hub role for consented relationships
where a grantor authorizes a delegated representative to perform limited
actions against a resource surface.

This is one abstraction:

```text
credential / proof / token
  provider OAuth token / app password / KDCube-issued token / signed request
      |
      v
registered authenticator
  telegram / gmail / slack / icloud / oauth_mcp / future provider
      |
      v
linker / grant resolver
  resolves grantor principal, delegated representative, resource surface
      |
      v
allowed actions
  scopes / tools / provider permissions / operation allowlist
      |
      v
execution principal or capability
  UserSession authority / provider capability / integration principal
```

Provider accounts and external clients are not different conceptual systems.
They are different authenticator/linker implementations over the same delegated
connection model:

| Case | Authenticator verifies | Linker/grant resolver returns |
| --- | --- | --- |
| Gmail delegated account | Gmail OAuth credential/account state. | Platform grantor + Gmail capability + allowed provider actions. |
| iCloud delegated account | App-specific password/account state. | Platform grantor + iCloud capability + allowed provider actions. |
| Claude OAuth/MCP connection | KDCube-issued access/refresh token through the OAuth/MCP registry. | Integration principal + KDCube resource + selected tools. |
| Telegram-linked request | Telegram initData or webhook proof. | Actor identity + linked platform principal + projected authority. |

Provider-account details are covered by
[Delegated Accounts](../delegated-accounts/delegated-accounts-README.md).
OAuth/MCP details are covered by
[OAuth/MCP Protocol Adapter](../delegated-credentials/oauth-mcp-protocol-adapter-README.md).

OAuth/MCP is the current authenticator/protocol adapter for a delegated
representative that calls KDCube:

```text
KDCube-issued integration token
  -> oauth_mcp authenticator validates token/grant
  -> grant resolver returns integration principal + selected tools
  -> MCP operation runs only if allowed
```

The product concept is the delegated connection. A grant is the stored consent
record inside that connection: representative identity, resource surface,
allowed actions, credential state, expiry, and revocation state.

## Two Phases

The lifecycle has two phases. This is the real boundary to preserve.

```text
1. Provisioning / consent

   grantor proves authority
     -> user logs in / channel proof is verified / admin consents
     -> Connection Hub writes identity link or delegated grant
     -> credential/capability is issued or stored


2. Runtime use

   credential/proof arrives later
     -> registered authenticator verifies it
     -> linker/grant resolver finds the stored meaning
     -> authority or capability is produced
     -> allowed actions are enforced
```

Examples:

| Provisioning / consent | Runtime use |
| --- | --- |
| Telegram user starts link, KDCube user claims it, identity link is written. | Telegram `initData` arrives, Telegram authenticator verifies it, identity link projects platform authority. |
| User connects Gmail, OAuth callback stores provider grant. | Automation asks for Gmail capability, provider adapter resolves token and allowed provider actions. |
| Admin approves Claude MCP access, OAuth/MCP grant is written. | Claude sends KDCube token, `oauth_mcp` authenticator resolves integration principal and selected tools. |

## Relationship To Identity Links

Delegated Connections is separate from identity linking, but they compose:

```text
provider/channel proof
  -> request authenticator
  -> identity link
  -> platform authority projection
  -> platform user can approve delegated connections
```

For example, a user may arrive through Telegram, prove `telegram:<id>`, and be
projected to platform user `02e...`. That projected authority can then decide
whether the user may approve a delegated connection. The delegated connection
itself is still a separate consent/grant record.

## OAuth/MCP Runtime Map

```text
External Client
  Claude Code / Claude connector / customer integration / future client
        |
        | discovers protected resource/protocol
        | GET /mcp
        | GET /.well-known/oauth-protected-resource
        v
KDCube Delegated Connection Protocol Adapter
  served by chat-ingress when auth.connection_hub.delegated_credentials.oauth_mcp.enabled=true
        |
        | returns authorization endpoint, token endpoint,
        | registration endpoint, scopes, and resource metadata
        v
OAuth Client Registration
  static public client or dynamic client registration
        |
        | redirect URIs checked against descriptor allowlist
        v
Human Admin Browser
        |
        | opens /oauth/authorize
        | carries existing platform session cookie
        v
Platform Auth Resolver
        |
        | validates session and returns platform roles
        | e.g. kdcube:role:super-admin
        v
Consent Screen
        |
        | user approves scopes and selected tools
        | e.g. conversations:read / conversations_export
        v
Authorization Code + PKCE
        |
        | client exchanges code at /oauth/token
        v
Integration Token
        |
        | access token carries least-privilege integration role
        | e.g. kdcube:role:feedback-reader
        v
/mcp
        |
        | checks token role + selected tool grant
        v
KDCube MCP Tool
  conversations_export today; additional tools later
```

OAuth/MCP is one authenticator/protocol implementation. The solution concept is
wider: any future provider or client protocol should plug in as an
authenticator/linker over the same credential, principal, resource,
allowed-action, and revocation boundaries instead of inventing a separate auth
stack.

## Roles Involved

There are two role moments.

```text
1. Consent authority

   browser session subject:
     internal:google:...
     roles: [kdcube:role:super-admin]

   Used only to decide whether the human can approve the connection.


2. Delegated representative execution authority

   integration subject:
     integration:claude:<admin-sub>
     roles: [kdcube:role:feedback-reader]
     selected tools: [conversations_export]

   Used by /mcp when the delegated representative calls tools.
```

The delegated representative does not receive the grantor's full session. It
receives a least-privilege credential for a separate representative subject.

## How Custom Authorities Fit

A deployment may use a customer-owned login and role authority before the
delegated connection flow starts. That customer system should be modeled as an
authority provider: it owns an `authority_id`, authenticators, a grant resolver,
and optional linkers to other authorities.

```text
Customer login surface
  e.g. Google OIDC in a branded front shell
        |
        | validates Google credential
        v
Customer role provider
  local user store / directory / role table
        |
        | resolves grants under customer authority
        |   yey:role:admin
        |   yey:role:user
        v
Authority Provider
        |
        | identity=yey:user:123, authority_id=yey.custom
        v
/oauth/authorize or another protected surface
        |
        | surface guard checks required authority/grants
v
Delegated connection grant
```

If the target surface requires `yey.custom`, no platform mapping is needed. If
the target surface requires `kdcube.platform`, the authority linker must map the
customer identity to a platform identity and then the platform grant resolver
loads platform roles.

This is the desired shape for deployments such as YEY Boats: Google proves the
human, the local role store resolves grants under the customer authority, and
surface guards decide whether that authority is sufficient or must be linked to
`kdcube.platform`. The delegated connection flow consumes the resolved
authority; it does not own Google verification or local role storage.

In the fully standardized platform, that customer role provider is selected by
the Connection Hub authentication surface:

```text
Connection Hub SDK RequestAuthResolver
  |
  +-- Cognito / platform browser session
  +-- platform bundle-session
  +-- ConnectionHubAuthenticationSurface
        |
        +-- role-providing authenticator
        |     signed customer session / customer OIDC / enterprise header
        |
        +-- linked-identity authenticator
              Telegram / Slack / API key -> identity link -> platform authority
```

So delegated connection issuance belongs downstream of the authority system. It
should not learn how Google, Telegram, or a customer directory work.

## Descriptor Contract

The current OAuth/MCP protocol implementation is configured in `assembly.yaml`
under `auth.connection_hub.delegated_credentials.oauth_mcp`.

```yaml
auth:
  oauth_mcp:
    enabled: true
    issuer: "https://example.com"
    brand: "Example KDCube"
    public_clients:
      - client_id: "claude"
        redirect_uris:
          - "https://claude.ai/api/mcp/auth_callback"
          - "http://localhost/callback"
          - "http://127.0.0.1/callback"
    dynamic_client_registration:
      allowed_redirect_uris:
        - "https://claude.ai/api/mcp/auth_callback"
        - "http://localhost/callback"
        - "http://127.0.0.1/callback"
```

The service-level descriptor and endpoint contract is documented in
[OAuth/MCP Protocol Adapter](../delegated-credentials/oauth-mcp-protocol-adapter-README.md).
Consent-page branding is documented in
[Branding the MCP Authorization Screen](../delegated-credentials/oauth-mcp-consent-branding-README.md).

## Session Provider Compatibility

Native OAuth/MCP delegated connection access is usable without customer patches when the
platform auth resolver can validate the browser session used at
`/oauth/authorize`.

Works without extra patching:

- Cognito or multi-Cognito platform auth.
- Simple/dev auth.
- Platform bundle-session auth where the browser token is a platform
  `BundleSessionAuthManager` token backed by the bundle-session authority.

Requires a matching auth resolver:

- A deployment that issues its own stateless `kst1` browser token and sets a
  custom `AUTH_PROVIDER=session` must have a platform auth manager that verifies
  that token. If the platform maps `session` to a different manager, the OAuth
  consent step cannot authenticate that browser cookie.

The OAuth/MCP service itself is not tied to one identity provider. It asks the
configured platform auth resolver to authenticate the human browser session. If a
deployment brings a custom role authority, that authority must return normal
KDCube role strings such as `kdcube:role:super-admin`.

Runtime credentials use the shared `kdcube.credential.v1` envelope, whether the
credential is a Data Bus derived session, an OAuth/MCP delegated client token,
or a future bundle custom authority token. See
[Authority Credential Envelope](../authority-providers/credential-envelope-README.md).

## Storage

Current implementation stores OAuth/MCP grant state in Redis through
`GrantStore`. Some records are intentionally short-lived; others are product
state and need persistence in production.

Read the durability design note before relying on long-lived external
connectors:

- [Grant Storage Durability](design/grant-storage-durability-README.md)

## Reading Order

| Need | Read |
| --- | --- |
| Understand delegated connection object model | This document |
| Understand authority provider and custom-authority registration | [Authority Provider Runtime](../authority-providers/authority-provider-runtime-README.md) |
| Understand credential routing fields | [Authority Credential Envelope](../authority-providers/credential-envelope-README.md) |
| Understand KDCube using external provider accounts | [Delegated Accounts](../delegated-accounts/delegated-accounts-README.md) |
| Configure current OAuth/MCP protocol endpoints | [OAuth/MCP Protocol Adapter](../delegated-credentials/oauth-mcp-protocol-adapter-README.md) |
| Brand the consent page | [OAuth/MCP Consent Branding](../delegated-credentials/oauth-mcp-consent-branding-README.md) |
| Understand delegated credential protocol adapters | [Delegated Credential Protocol Adapters](../delegated-credentials/delegated-credential-protocol-adapters-README.md) |
| Decide Redis vs durable storage | [Grant Storage Durability](design/grant-storage-durability-README.md) |
