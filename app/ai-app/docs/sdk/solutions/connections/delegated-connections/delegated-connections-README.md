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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-consent-branding-README.md
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
  telegram / gmail / slack / icloud / delegated_client / future provider
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
| Claude OAuth delegated credential connection | KDCube-issued access/refresh token through the OAuth delegated credential registry. | Integration principal + KDCube resource + selected tools. |
| Telegram-linked request | Telegram initData or webhook proof. | Actor identity + linked platform principal + projected authority. |

Provider-account details are covered by
[Delegated Accounts](../delegated-accounts/delegated-accounts-README.md).
OAuth delegated credential details are covered by
[OAuth delegated credential Protocol Adapter](../delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).

OAuth delegated credential is the current authenticator/protocol adapter for a delegated
representative that calls KDCube:

```text
KDCube-issued integration token
  -> delegated_client authenticator validates token/grant
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
     -> user logs in / channel proof is verified / user or admin consents
     -> Connection Hub writes a connection edge and any protocol-specific grant state
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
| Telegram user starts link, KDCube user claims it, connection edge is written. | Telegram `initData` arrives, Telegram authenticator verifies it, edge projection provides platform authority. |
| User connects Gmail, OAuth callback stores provider grant. | Automation asks for Gmail capability, provider adapter resolves token and allowed provider actions. |
| User or admin approves Claude MCP access, OAuth delegated credential grant is written according to descriptor delegability. | Claude sends KDCube token, `delegated_client` authenticator resolves integration principal and selected tools. |

## Relationship To Connection Edges

Delegated connections and account linking are both connection-edge views. The common
graph says which identity may represent which other identity and which grants
are delegated. Protocol adapters may also keep protocol-specific state, such as
OAuth refresh tokens or selected MCP tools.

```text
provider/channel proof
  -> request authenticator
  -> connection edge
  -> platform authority projection
  -> platform user can approve delegated connections
```

For example, a user may arrive through Telegram, prove `telegram:<id>`, and be
projected to platform user `02e...`. That projected authority can then decide
whether the user may approve another delegated connection. Both facts are
edges; only protocol-specific credential material lives outside the generic edge
record.

## OAuth delegated credential Runtime Map

```text
External Client
  Claude Code / Claude connector / customer integration / future client
        |
        | discovers protected resource/protocol
        | calls concrete bundle MCP URL without delegated credential
        v
Proc bundle MCP bridge
        |
        | returns RFC 9728 protected-resource challenge
        v
Connection Hub Delegated Connection Protocol Adapter
  served by connection-hub@1-0 public oauth operation
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
        | opens connection-hub@1-0/public/oauth/authorize
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
        | client exchanges code at connection-hub@1-0/public/oauth/token
        v
Integration Token
        |
        | access token carries least-privilege integration role
        | e.g. kdcube:role:feedback-reader
        v
Concrete bundle MCP endpoint
        |
        | checks token role + selected tool grant
        v
KDCube MCP Tool
  conversations_export today; additional tools later
```

OAuth delegated credential is one authenticator/protocol implementation. The solution concept is
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
        |   custom:role:admin
        |   custom:role:user
        v
Authority Provider
        |
        | identity=custom:user:123, authority_id=custom.identity
        v
/oauth/authorize or another protected surface
        |
        | surface guard checks required authority/grants
v
Delegated connection grant
```

If the target surface requires `custom.identity`, no platform mapping is needed. If
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
              Telegram / Slack / API key -> connection edge -> platform authority
```

So delegated connection issuance belongs downstream of the authority system. It
should not learn how Google, Telegram, or a customer directory work.

## Descriptor Contract

The current OAuth delegated credential protocol implementation is configured on the Connection
Hub bundle in `bundles.yaml`.

```yaml
bundles:
  items:
    - id: "connection-hub@1-0"
      config:
        connections:
          delegated_credentials:
            oauth:
              enabled: true
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
[OAuth delegated credential Protocol Adapter](../delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).
Consent-page branding is documented in
[Branding the MCP Authorization Screen](../delegated-credentials/oauth-delegated-credential-consent-branding-README.md).

## Session Provider Compatibility

Native OAuth delegated credential delegated connection access is usable without customer patches when the
platform auth resolver can validate the browser session used at
Connection Hub `/public/oauth/authorize`.

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

The OAuth delegated credential service itself is not tied to one identity provider. It asks the
configured platform auth resolver to authenticate the human browser session. If a
deployment brings a custom role authority, that authority must return normal
KDCube role strings such as `kdcube:role:super-admin`.

Runtime credentials use the shared `kdcube.credential.v1` envelope, whether the
credential is a Data Bus derived session, an OAuth delegated credential delegated client token,
or a future bundle custom authority token. See
[Authority Credential Envelope](../authority-providers/credential-envelope-README.md).

## Storage

Current implementation stores OAuth delegated credential grant state in Redis through
`GrantStore`. Some records are intentionally short-lived; others are product
state and need persistence in production.

Read the durability design note before relying on long-lived external
connectors:

- [Grant Storage Durability](design/grant-storage-durability-README.md)

## Current KDCube Services MCP Example

The built-in example for inbound delegated external clients is
`kdcube-services@1-0`.

```text
kdcube-services@1-0/public/mcp/conversations
  -> one managed MCP tool: conversations_export

kdcube-services@1-0/public/mcp/named_services
  -> generic MCP tools over configured named-service namespaces
  -> current namespaces include mem, task, and cnv when configured
```

The `named_services` surface is intentionally two-layered:

```text
outer MCP tool grant:
  named_services:use

inner namespace grant:
  memories:read / memories:write
  tasks:read / tasks:write
  canvas:read / canvas:write
```

The outer grant lets the generic bridge run. The inner namespace catalog tells
the bridge which authority/grant to enforce when a tool call targets a concrete
namespace. This lets one MCP connector expose several product realms without
making every namespace a separate MCP server.

The MCP server advertises instructions for clients:

```text
1. call named_services_list
2. inspect capabilities/schema for an unfamiliar namespace
3. call search/get/upsert/action/delete only when the namespace permits it
```

The connector icon and read/write grouping are MCP metadata:

- server icon / website URL comes from the shared KDCube MCP metadata helper;
- read-only vs write/action/delete grouping comes from MCP `ToolAnnotations`;
- Connection Hub consent grouping remains descriptor-driven and independent
  from the client UI's post-connection grouping.

## Reading Order

| Need | Read |
| --- | --- |
| Understand delegated connection object model | This document |
| Understand authority provider and custom-authority registration | [Authority Provider Runtime](../authority-providers/authority-provider-runtime-README.md) |
| Understand credential routing fields | [Authority Credential Envelope](../authority-providers/credential-envelope-README.md) |
| Understand KDCube using external provider accounts | [Delegated Accounts](../delegated-accounts/delegated-accounts-README.md) |
| Configure current OAuth delegated credential protocol endpoints | [OAuth delegated credential Protocol Adapter](../delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md) |
| Brand the consent page | [OAuth delegated credential Consent Branding](../delegated-credentials/oauth-delegated-credential-consent-branding-README.md) |
| Understand delegated credential protocol adapters | [Delegated Credential Protocol Adapters](../delegated-credentials/delegated-credential-protocol-adapters-README.md) |
| Decide Redis vs durable storage | [Grant Storage Durability](design/grant-storage-durability-README.md) |
