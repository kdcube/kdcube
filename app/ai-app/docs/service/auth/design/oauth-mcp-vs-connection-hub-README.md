---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/design/oauth-mcp-vs-connection-hub-README.md
title: "OAuth MCP And Connection Hub Delegation"
summary: "Shared diagram explaining OAuth/MCP as a delegated-connection protocol adapter within the Connection Hub model."
status: active
tags: ["service", "auth", "oauth", "mcp", "connection-hub", "identity", "diagram"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
---
# OAuth MCP And Connection Hub Delegation

OAuth MCP integration access is the current service/protocol adapter for one
Connection Hub delegated-connection shape. Its token is not special at the
Connection Hub layer: it is another credential that a registered authenticator
must verify and a linker/grant resolver must attach to a principal, resource,
and allowed actions.

Implementation status: OAuth/MCP registers `oauth_mcp` as a local authority
provider when the feature is mounted. Issued access tokens and grant records
carry the standard `kdcube.credential.v1` envelope:

```text
credential_kind         = delegated_client_access
issuer_authority_id     = oauth_mcp
issuer_authenticator_id = oauth_mcp.bearer
audience                = kdcube:mcp
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
  telegram / gmail / slack / icloud / oauth_mcp / future provider
      |
      v
linker / grant resolver
  identity link / delegated account grant / delegated client grant
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
    -> identity link
    -> projected UserSession


Gmail delegated account:
  OAuth account credential
    -> google/provider authenticator
    -> delegated account grant
    -> Gmail capability


OAuth/MCP delegated connection:
  KDCube-issued token
    -> oauth_mcp authenticator
    -> grant registry
    -> integration principal + selected tools
```

## Two Phases

```text
Provisioning / consent
  grantor proves authority
    -> user login / channel proof / admin consent
    -> identity link or delegated grant is written
    -> credential is issued or stored

Runtime use
  credential/proof arrives later
    -> selected authenticator verifies it
    -> linker/grant resolver finds stored meaning
    -> authority or capability is produced
    -> allowed actions are enforced
```

OAuth/MCP follows the same lifecycle:

```text
/oauth/authorize + /oauth/token
  -> writes delegated connection grant
  -> issues KDCube credential

/mcp tools/call
  -> oauth_mcp authenticator verifies credential
  -> grant registry resolves representative + actions
  -> tool policy enforces selected tools
```

## Where OAuth/MCP Fits

```text
External client
  Claude Code / MCP connector
      |
      | discovers protected KDCube resource
      v
OAuth/MCP protocol adapter
  /oauth/register
  /oauth/authorize
  /oauth/token
  /mcp
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
  resource       = kdcube:mcp
  actions        = conversations_export
  credential     = KDCube access/refresh token
      |
      v
External client calls KDCube as delegated representative
```

OAuth/MCP owns protocol mechanics for this delegated connection authenticator:

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
  OAuth/MCP protocol adapter. That belongs to Connection Hub request
  authenticators.
- Do not treat a delegated connection credential as the grantor's full platform
  session. It is a representative credential with selected actions.
- Do not derive platform roles from provider-local delegated account roles.
  Platform authority comes from platform auth or Connection Hub authority
  projection.
- Do not split the product model into unrelated "external client access" and
  "delegated account" systems. They are both delegated connections implemented
  by different authenticator and linker modules.
