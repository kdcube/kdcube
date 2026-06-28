---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-mcp-protocol-adapter-README.md
title: "OAuth/MCP Protocol Adapter"
summary: "How the current OAuth2 + MCP protocol adapter issues and verifies delegated Connection Hub credentials for least-privilege external client access."
tags: ["sdk", "solutions", "connections", "delegated-credentials", "oauth", "mcp", "descriptor"]
keywords: ["OAuth2 authorization server", "MCP protected resource", "Claude Code", "PKCE", "dynamic client registration", "tool consent", "feedback reader", "descriptor configuration"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
---
# OAuth/MCP Protocol Adapter

OAuth/MCP is the current protocol adapter for one Connection Hub delegated
credential shape: an external tool, such as Claude Code, calls a narrow KDCube
MCP surface after a human platform admin consents. The product concept is
delegated credentials under Connection Hub; OAuth/MCP is only this adapter's
wire protocol and implementation name.

At the Connection Hub authority layer this feature is:

```text
authority_id       = oauth_mcp
authenticator_id   = oauth_mcp.bearer
credential_kind    = delegated_client_access
audience           = kdcube:mcp
representative     = integration:claude:<grantor-sub>
grant resolver     = OAuth/MCP grant store
```

KDCube is the OAuth2 Authorization Server for this integration flow. It does
not delegate this integration authorization step to an external identity
provider. External identity providers may still be used earlier by the normal
platform login path, such as Cognito or app session auth.

The key split is:

| Layer | Owner | Result |
|---|---|---|
| Human authentication | Existing platform auth/session provider | A browser request proves a platform user, usually an admin. |
| Integration authorization | KDCube OAuth2 AS | Admin consents to scopes and MCP tools. |
| Integration execution | KDCube MCP resource server | External client calls allowed tools with a least-privilege token. |

## Runtime Shape

```
Claude Code / external MCP client
  |
  | 1. Discover MCP resource
  |    GET /mcp or /.well-known/oauth-protected-resource
  v
KDCube ingress OAuth/MCP routes
  |
  | returns RFC 9728 / RFC 8414 metadata
  v
Client learns:
  authorization_endpoint = /oauth/authorize
  token_endpoint         = /oauth/token
  registration_endpoint  = /oauth/register
  resource               = /mcp


Client dynamic registration, when used
  |
  | 2. POST /oauth/register
  |    redirect_uris = client callback URIs
  v
KDCube DCR allowlist check
  |
  | only descriptor-allowed redirect URIs are accepted
  v
Public OAuth client record


Human consent
  |
  | 3. Browser opens /oauth/authorize
  |    response_type=code
  |    code_challenge=<PKCE S256>
  |    scope=conversations:read
  |    client_id=<public client>
  |    redirect_uri=<validated callback>
  v
KDCube ingress
  |
  | 4. Validate existing platform session cookie
  |    cookie name comes from assembly.yaml auth.auth_token_cookie_name
  |    user and roles come from platform auth/session resolver
  v
Admin consent page
  |
  | 5. Admin approves selected scope/tool set
  |    CSRF token is single-use and bound to admin subject
  v
Authorization code
  |
  | 6. Redirect back to client with code + state + iss
  v
External client callback


Token issue
  |
  | 7. POST /oauth/token
  |    grant_type=authorization_code
  |    code=<auth code>
  |    code_verifier=<PKCE verifier>
  v
KDCube token endpoint
  |
  | verifies code, client, redirect URI, and PKCE
  | mints a short-lived kst1 integration session
  | stores refresh token, selected tool allowlist, and authority envelope
  v
External client holds:
  access_token  = least-privilege integration token
  refresh_token = rotating integration refresh token


MCP execution
  |
  | 8. POST /mcp
  |    Authorization: Bearer <access_token>
  |    JSON-RPC tools/list or tools/call
  v
KDCube MCP resource server
  |
  | authenticates kst1 token
  | checks role permission
  | checks grant-level selected-tool allowlist
  v
Allowed MCP tool result
```

## Authorization Model

The consenting admin does not hand their full admin session to the external
client. The token endpoint mints a separate integration identity derived from
the consenting admin, for example:

```text
integration:claude:<admin-sub>
```

That integration identity receives only the role and permissions required by
the approved integration, such as:

```text
kdcube:role:feedback-reader
kdcube:*:conversations:*;read
```

Admin roles remain platform roles resolved by the existing platform session and
role resolver. OAuth tokens do not invent admin privileges.

The access token is a normal `kst1` session token for the integration
representative, but it carries a nested `kdcube.credential.v1` envelope. The
same envelope is stored in the access-grant and refresh-token records so refresh
rotation keeps the delegated-client authority provenance:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "delegated_client_access",
  "issuer_authority_id": "oauth_mcp",
  "issuer_authenticator_id": "oauth_mcp.bearer",
  "subject": "integration:claude:<admin-sub>",
  "audience": "kdcube:mcp",
  "attrs": {
    "client_id": "claude",
    "scopes": ["conversations:read"],
    "tools": ["conversations_export"]
  }
}
```

See [Authority Credential Envelope](../../sdk/solutions/connections/authority-providers/credential-envelope-README.md).

## Consent And Tool Enforcement

Scopes describe broad integration capability. MCP tools are the concrete
operations exposed through `/mcp`.

The consent page may show selected tools for the requested scopes. Approval must
bind that selected tool list into the issued grant:

```
consent POST
  -> authorization code stores scopes + selected tools
  -> token endpoint binds selected tools to access token
  -> refresh token record stores selected tools
  -> refresh rotation preserves selected tools
  -> /mcp tools/call checks role permission AND selected-tool grant
```

This makes the tool-selection UI meaningful. A token with the right role but no
matching selected-tool grant must fail closed for non-admin integration calls.

## Descriptor Contract

This is a Connection Hub delegated-credential protocol adapter. OAuth metadata,
authorization, token, refresh, and dynamic-client-registration routes are served
by chat-ingress because they are platform auth protocol routes. The delegated
credential they issue should then guard concrete bundle/proc surfaces such as
bundle-provided MCP endpoints.

Its non-secret protocol configuration belongs in
`assembly.yaml` under `auth.connection_hub.delegated_credentials.oauth_mcp`.

Reference shape:

```yaml
auth:
  connection_hub:
    delegated_credentials:
      oauth_mcp:
        enabled: false
        issuer: ""
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

The bundle surface that consumes this credential is configured in
`bundles.yaml`, not in `assembly.yaml`. For a proc-served bundle MCP endpoint:

```yaml
bundles:
  items:
    - id: "feedback@1-0"
      config:
        enabled:
          mcp:
            feedback: true
        mcp:
          feedback:
            auth:
              mode: managed
              authority_id: oauth_mcp
              grants: [conversations:read]
              selected_tool_grants: true
```

`mode: managed` means the proc MCP bridge owns the credential and grant check
before dispatching into the bundle MCP app. If `mode` is absent, the MCP auth
block is bundle-owned metadata; the knowledge bundle's
`mcp.knowledge.auth.header_name` is one such bundle-owned scheme.

Legacy root `/mcp` remains an adapter while this feature is migrated to
bundle-provided MCP surfaces. It should not be modeled as "platform MCP"; the
normal product shape is:

```text
OAuth/consent/token routes
  -> delegated credential
  -> proc bundle @mcp endpoint with mode: managed
  -> bundle MCP app
```

Old shape to avoid in new descriptors:

```yaml
auth:
  oauth_mcp:
    enabled: false
    issuer: ""
```

Rules:

- `enabled` controls whether the OAuth/MCP routes are mounted.
- `issuer` is the public origin advertised in OAuth metadata. If omitted, local
  development may derive it from the request origin.
- `public_clients[*].redirect_uris` configures known public clients.
- `dynamic_client_registration.allowed_redirect_uris` constrains pre-auth
  dynamic client registration.
- Redirect URI fields are descriptor lists, not comma-separated strings.
- Tenant and project come from `assembly.yaml -> context.tenant` and
  `context.project`.
- Admin session cookie name comes from
  `assembly.yaml -> auth.auth_token_cookie_name`.
- This flow currently uses public clients plus PKCE and does not require a new
  secret in `secrets.yaml`.

If route guarding or bypass policy must be configurable, use the existing
gateway/ingress descriptor model instead of feature-specific hardcoded route
lists.

## Storage

Runtime grant state is tenant/project scoped and lives in the platform runtime
store, normally Redis.

| Record | Purpose | Lifetime |
|---|---|---|
| Dynamic client record | Stores registered public client metadata and redirect URIs. | Until registration expiry or cleanup policy. |
| CSRF token | Single-use consent POST protection bound to admin subject. | Short TTL. |
| Authorization code | Stores client, redirect URI, PKCE challenge, admin subject, scopes, and selected tools. | Short TTL, single use. |
| Access grant | Binds an access token to selected MCP tools and the `oauth_mcp` credential envelope. | Same TTL as access token. |
| Refresh token | Stores client, admin subject, scopes, selected tools, authority envelope, and rotation state. | Long-lived, rotating. |
| Bundle session record | The issued access token is a `kst1` session for the integration identity. | Access-token TTL. |

Redis loss is safe but product-visible: missing records fail closed, but
long-lived connectors can require re-consent if dynamic client or refresh-token
records disappear. The solution-level durability design note is
[Grant Storage Durability](../../sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md).

## Failure Modes

| Situation | Expected behavior |
|---|---|
| No platform session on `/oauth/authorize` | `login_required`; client must start from an authenticated admin browser session. |
| Authenticated user is not admin | `forbidden`; only configured admin roles may consent. |
| DCR redirect URI is not allowlisted | `invalid_redirect_uri`; client is not registered. |
| Bad redirect URI on authorize/token | Request fails; codes are not delivered to unvalidated redirects. |
| Missing or invalid PKCE verifier | Token request fails with `invalid_grant`. |
| Token has role but no selected-tool grant | Non-admin `/mcp tools/call` fails closed. |
| Tool is not allowed by role | `/mcp tools/call` returns an MCP tool authorization error. |
| Refresh token is invalid or rotated | Token request fails with `invalid_grant`. |

## What This Is Not

This mechanism is not:

- a replacement for browser login;
- a way for an app to mint platform admin sessions;
- an app-level named service;
- a place for operator-facing environment variables;
- a broad "admin API" token.

It is a descriptor-configured protocol adapter from an already-authenticated
platform admin consent flow to a least-privilege delegated credential.

## Relationship To Connection Hub

OAuth/MCP is one delegated-connection authenticator/protocol adapter under the
Connection Hub concept. It is still implemented as service auth endpoints
because `/oauth/*` and `/mcp` are platform ingress surfaces. The shared diagram
lives in
[Delegated Credential Protocol Adapters](delegated-credential-protocol-adapters-README.md).

At the Connection Hub layer, OAuth/MCP is not conceptually different from other
credential-bearing integrations. It provides one authenticator and one grant
registry:

```text
KDCube-issued integration token
  -> oauth_mcp authenticator
  -> oauth_mcp grant registry
  -> delegated representative principal
  -> selected tools / allowed actions
```

The feature registers an `oauth_mcp` authority provider in the local Connection
Hub authority registry when mounted. That makes the implementation visible to
code using the authority SDK without turning `/oauth/*` into a bundle-local
surface.

The consent roundtrip is how that credential and grant registry entry are
created:

```text
grantor authority
  platform browser session / projected platform principal
      |
      v
/oauth/authorize
      |
      v
consent scopes + selected MCP tools
      |
      v
auth code + PKCE
      |
      v
integration token + refresh token + selected-tool grant
```

The OAuth/MCP authenticator validates only OAuth/MCP tokens and grant records.
It should not learn Telegram, Slack, webhook, Gmail, or customer directory proof
formats. Those are other authenticator modules. Likewise, identity links should
not issue OAuth codes or refresh tokens; those records belong to the
OAuth/MCP grant registry.

## Regression Checklist

Use focused tests and one live connector test.

1. OAuth metadata routes return issuer, authorization endpoint, token endpoint,
   registration endpoint, and `/mcp` resource metadata.
2. DCR accepts only descriptor-allowed redirect URIs.
3. Authorization requires an authenticated platform admin session.
4. Consent POST validates CSRF and re-validates client, redirect URI, and PKCE.
5. Token issue stores selected tools on both access grant and refresh record.
6. Refresh rotation preserves selected tools.
7. Non-admin integration token without a selected-tool grant fails closed.
8. Admin tokens bypass selected-tool grants only where intentional.
9. `/mcp tools/list` and `/mcp tools/call` return MCP-shaped responses, not
   unhandled HTTP 500s for authorization failures.
10. The feature is disabled when
    `auth.connection_hub.delegated_credentials.oauth_mcp.enabled: false`.
