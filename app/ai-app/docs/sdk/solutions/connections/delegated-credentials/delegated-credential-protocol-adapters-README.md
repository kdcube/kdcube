---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
title: "Delegated Credential Protocol Adapters"
summary: "Shared diagram explaining protocol adapters such as OAuth delegated credential as delegated-credential implementations within the Connection Hub model."
status: active
tags: ["service", "auth", "oauth", "mcp", "connection-hub", "identity", "diagram"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
---
# Delegated Credential Protocol Adapters

Protocol adapters issue, store, or verify credentials for delegated Connection
Hub connections. OAuth delegated credential is the current adapter for one inbound external
client shape. Its token is not special at the Connection Hub layer: it is
another credential that a registered authenticator must verify and a
linker/grant resolver must attach to a principal, resource, and allowed
actions.

Implementation status: OAuth delegated credential registers `delegated_client` as a local authority
provider when the feature is mounted. Issued access tokens and grant records
carry the standard `kdcube.credential.v1` envelope:

```text
credential_kind         = delegated_client_access
issuer_authority_id     = delegated_client
issuer_authenticator_id = delegated_client.bearer
audience                = kdcube:delegated_client
```

Federated Data Bus session tokens carry the same envelope vocabulary with
`issuer_authority_id = kdcube.ingress_session` and
`audience = kdcube:data_bus`.

The common abstraction is not "MCP" and not "client access". It is
authenticator-driven delegation:

```text
proof / credential / token
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
authority or capability
  UserSession / provider capability / integration principal
      |
      v
allowed actions
  scopes / tools / provider permissions / operation allowlist
```

## Same Pipeline, Different Modules

```text
Telegram request:
  initData
    -> telegram authenticator
    -> connection edge
    -> projected UserSession


Gmail delegated account:
  OAuth account credential
    -> google/provider authenticator
    -> delegated account grant
    -> Gmail capability


OAuth delegated credential delegated connection:
  KDCube-issued token
    -> delegated_client authenticator
    -> grant registry
    -> integration principal + selected tools
```

## Two Phases

```text
Provisioning / consent
  grantor proves authority
    -> user login / channel proof / user or admin consent
    -> connection edge is written
    -> protocol-specific credential/grant state is stored if needed
    -> credential is issued or stored

Runtime use
  credential/proof arrives later
    -> selected authenticator verifies it
    -> linker/grant resolver finds stored meaning
    -> authority or capability is produced
    -> allowed actions are enforced
```

OAuth delegated credential follows the same lifecycle:

```text
connection-hub@1-0/public/oauth/authorize + /public/oauth/token
  -> writes delegated connection grant
  -> issues KDCube credential

concrete bundle MCP tools/call
  -> delegated_client authenticator verifies credential
  -> grant registry resolves representative + actions
  -> tool policy enforces selected tools
```

OAuth delegated credential delegated clients also carry a delegation edge: the token subject is
the representative, while `grantor_subject` and `identity_scope` say which
grantor-owned identities product reads may use. A user-memories MCP token, for
example, can explicitly allow `grantor_identity_family` so reads include the
grantor's linked Telegram/platform memory rows.

The consent model has two layers. First, the target resource contributes
service grants and concrete tools/actions. Connection Hub shows only grants the
signed-in KDCube user is allowed to delegate for that resource. Second,
Connection Hub records delegation edges, such as the platform identity edge that
allows the external client to represent `platform:<user-id>` with the selected
grants if a downstream boundary requires platform authority. Future
Google/Gmail, Slack, Cognito, or custom-authority edges use the same shape. Each
authority resolver exposes the grant inventory for identities it owns, and the
delegation edge must be bounded by that inventory.

## Where OAuth delegated credential Fits

```text
External client
  Claude Code / MCP connector
      |
      | discovers protected KDCube resource
      v
OAuth delegated credential protocol adapter
  connection-hub@1-0/public/oauth/register
  connection-hub@1-0/public/oauth/authorize
  connection-hub@1-0/public/oauth/token
      |
      | requires grantor authority
      v
Platform auth / Connection Hub authority projection
  browser session / linked Telegram authority / customer role provider
      |
      | consent
      v
Delegated connection grant
  representative = integration:claude:<grantor>
  resource       = concrete bundle MCP resource
  actions        = selected resource tools
  credential     = KDCube access/refresh token
      |
      v
External client calls KDCube as delegated representative
```

OAuth delegated credential owns protocol mechanics for this delegated connection authenticator:

- OAuth metadata and dynamic client registration.
- Authorization code + PKCE.
- Consent screen and CSRF.
- Access and refresh token issue/rotation.
- Selected-tool grants for `/mcp`.

Connection Hub owns the broader model:

- Who the grantor is.
- How non-platform actor identities link to a platform principal.
- Which request authenticators can prove external identities.
- How delegated credentials are selected, verified, linked, represented, and
  revoked.
- How allowed actions are projected into a `UserSession`, provider capability,
  or integration principal.

## Boundary Rules

- Do not verify Telegram/Slack/webhook/API-key provider proof inside the
  OAuth delegated credential protocol adapter. That belongs to Connection Hub request
  authenticators.
- Do not treat a delegated connection credential as the grantor's full platform
  session. It is a representative credential with selected actions.
- Do not derive platform roles from provider-local delegated account roles.
  Platform authority comes from platform auth or Connection Hub authority
  projection.
- Do not split the product model into unrelated "external client access" and
  "delegated account" systems. They are both delegated connections implemented
  by different authenticator and linker modules.
