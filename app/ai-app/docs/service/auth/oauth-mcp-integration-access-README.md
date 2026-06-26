---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
title: "OAuth MCP Integration Access"
summary: "How KDCube exposes a descriptor-configured OAuth2 authorization server and MCP protected resource for least-privilege external integration access."
tags: ["service", "auth", "oauth", "mcp", "integration-access", "descriptor"]
keywords: ["OAuth2 authorization server", "MCP protected resource", "Claude Code", "PKCE", "dynamic client registration", "tool consent", "feedback reader", "descriptor configuration"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
---
# OAuth MCP Integration Access

OAuth MCP integration access is the platform service mechanism for allowing an
external tool, such as Claude Code, to call a narrow KDCube MCP surface after a
human platform admin consents.

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
  | stores refresh token and selected tool allowlist
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

This is a platform auth/MCP integration access feature served by chat-ingress.
It is an auth capability, not an ingress-service descriptor setting and not an
app configuration field. Its non-secret configuration belongs in
`assembly.yaml` under `auth.oauth_mcp`.

Reference shape:

```yaml
auth:
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
| Access grant | Binds an access token to selected MCP tools. | Same TTL as access token. |
| Refresh token | Stores client, admin subject, scopes, selected tools, and rotation state. | Long-lived, rotating. |
| Bundle session record | The issued access token is a `kst1` session for the integration identity. | Access-token TTL. |

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

It is a descriptor-configured bridge from an already-authenticated platform
admin consent flow to a least-privilege MCP integration token.

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
    `auth.oauth_mcp.enabled: false`.
