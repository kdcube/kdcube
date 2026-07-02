---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
title: "OAuth Delegated Credential Protocol Adapter"
summary: "How the OAuth2 protocol adapter issues and verifies delegated Connection Hub credentials for least-privilege external client access."
tags: ["sdk", "solutions", "connections", "delegated-credentials", "oauth", "mcp", "descriptor"]
keywords: ["OAuth2 authorization server", "MCP protected resource", "Claude Code", "PKCE", "dynamic client registration", "tool consent", "feedback reader", "descriptor configuration"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
---
# OAuth Delegated Credential Protocol Adapter

OAuth delegated credential is the current protocol adapter for one Connection Hub delegated
credential shape: an external tool, such as Claude Code, calls a narrow KDCube
MCP surface after an authenticated KDCube user consents. The product concept is
delegated credentials under Connection Hub; OAuth delegated credential is only this adapter's
wire protocol and implementation name.

At the Connection Hub authority layer this feature is:

```text
authority_id       = delegated_client
authenticator_id   = delegated_client.bearer
credential_kind    = delegated_client_access
audience           = kdcube:delegated_client
representative     = integration:claude:<grantor-sub>
grant resolver     = OAuth delegated credential grant store
```

KDCube is the OAuth2 Authorization Server for this integration flow. It does
not delegate this integration authorization step to an external identity
provider. External identity providers may still be used earlier by the normal
platform login path, such as Cognito or app session auth.

The key split is:

| Layer | Owner | Result |
|---|---|---|
| Human authentication | Existing platform auth/session provider | A browser request proves a platform user. |
| Integration authorization | KDCube OAuth2 AS | User consents to descriptor-configured grants and resource tools they are allowed to delegate. |
| Integration execution | KDCube protected resource server | External client calls allowed resource tools with a least-privilege token. |

## Runtime Shape

```
Claude Code / external MCP client
  |
  | 1. Discover protected bundle MCP resource
  |    GET/POST /api/integrations/bundles/{tenant}/{project}/{bundle}/public/mcp/{alias}
  |    without a valid delegated credential
  v
Proc bundle MCP bridge
  |
  | returns RFC 9728 WWW-Authenticate challenge for that concrete resource
  v
Connection Hub delegated credential OAuth adapter
  |
  | returns RFC 9728 / RFC 8414 metadata
  v
Client learns:
  authorization_endpoint = /api/.../connection-hub@1-0/public/oauth/authorize
  token_endpoint         = /api/.../connection-hub@1-0/public/oauth/token
  registration_endpoint  = /api/.../connection-hub@1-0/public/oauth/register
  resource               = concrete bundle MCP URL


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
  | 3. Browser opens Connection Hub /public/oauth/authorize
  |    response_type=code
  |    code_challenge=<PKCE S256>
  |    scope=<resource grant, e.g. memories:read>
  |    client_id=<public client>
  |    redirect_uri=<validated callback>
  v
Connection Hub OAuth adapter
  |
  | 4. Validate existing platform session cookie
  |    cookie name comes from the selected platform authority provider
  |    user and roles come from platform auth/session resolver
  v
User consent page
  |
  | 5. User approves platform delegation grants and selected tool set
  |    CSRF token is single-use and bound to grantor subject
  v
Authorization code
  |
  | 6. Redirect back to client with code + state + iss
  v
External client callback


Token issue
  |
  | 7. POST Connection Hub /public/oauth/token
  |    grant_type=authorization_code
  |    code=<auth code>
  |    code_verifier=<PKCE verifier>
  v
KDCube token endpoint
  |
  | verifies code, client, redirect URI, and PKCE
  | mints a short-lived kst1 integration session
  | stores refresh token, selected tool allowlist, credential envelope,
  | and explicit delegation edge(s)
  v
External client holds:
  access_token  = least-privilege integration token
  refresh_token = rotating integration refresh token


MCP execution
  |
  | 8. POST concrete bundle MCP URL
  |    Authorization: Bearer <access_token>
  |    JSON-RPC tools/list or tools/call
  v
Proc bundle MCP bridge
  |
  | authenticates kst1 token
  | checks role permission
  | checks grant-level selected-tool allowlist
  v
Allowed MCP tool result
```

## Authorization Model

The consenting user does not hand their full platform session to the external
client. The token endpoint mints a separate integration identity derived from
the consenting user, for example:

```text
integration:claude:<grantor-sub>
```

That integration identity receives only the role and permissions required by
the approved integration. For the legacy conversation export grant this includes:

```text
kdcube:role:feedback-reader
kdcube:*:conversations:*;read
```

Admin roles remain platform roles resolved by the existing platform session and
role resolver. OAuth tokens do not invent admin privileges. For ordinary user
resources, such as `user-memories@2026-06-26/public/mcp/memories`, the token is
issued with generic delegated-client authority and the concrete approved grant,
for example `memories:read`.

The access token is a normal `kst1` session token for the integration
representative. The server-side access-grant and refresh-token records bind it
to a `kdcube.credential.v1` envelope, so refresh rotation keeps the
delegated-client authority provenance and identity-scope policy without
requiring product code to decode grantor facts from the token body:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "delegated_client_access",
  "issuer_authority_id": "delegated_client",
  "issuer_authenticator_id": "delegated_client.bearer",
  "subject": "integration:claude:<grantor-sub>",
  "audience": "kdcube:delegated_client",
  "attrs": {
    "client_id": "claude",
    "resource": "https://runtime.example/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
    "scopes": ["memories:read"],
    "tools": ["memory_search", "memory_get"],
    "identity_scope": "grantor_identity_family"
  }
}
```

See [Authority Credential Envelope](../../sdk/solutions/connections/authority-providers/credential-envelope-README.md).
For the relationship between the delegate and the approving user, see
[Delegation Edges](delegation-edges-README.md).

## Consent, Delegation Edges, And Tool Enforcement

Scopes describe broad integration capability. MCP tools are the concrete
operations exposed through a concrete bundle MCP endpoint.

Grant capability rows define who may delegate a grant. Resource tool rows define
what each concrete MCP tool requires. Do not model a multi-grant tool by listing
the same tool under multiple grant rows; put the complete grant set on the tool:

```yaml
resources:
  - resource: "*/knowledge@1-0/public/mcp/knowledge_managed*"
    label: "KDCube knowledge MCP"
    identity_scope: "grantor"
    tools:
      search:
        label: "Search knowledge"
        grants: ["knowledge:read"]
      admin_reindex:
        label: "Rebuild knowledge index"
        grants: ["knowledge:read", "knowledge:maintain"]
```

Consent has two visible layers:

```text
Service/resource grants
  -> grants required by the selected MCP resource and tool set
  -> shown only when the signed-in KDCube user may delegate them

Platform delegation edge
  -> grants the external client may assume when a downstream boundary needs
     the grantor's platform authority
  -> must be selected by the user and must be a subset of the requested,
     delegable service grants
```

Approval may narrow the requested scopes to the selected platform-edge grants.
The selected tools are then filtered against that final grant set. Approval must
bind both the selected tool list and the resulting delegation edge into the
issued grant:

```
consent POST
  -> authorization code stores final scopes + selected tools + delegation_edges
  -> token endpoint binds selected tools + delegation_edges to access token
  -> refresh token record stores selected tools + delegation_edges
  -> refresh rotation preserves selected tools + delegation_edges
  -> managed bundle MCP tools/call checks role permission AND selected-tool grant
```

This makes the tool-selection UI meaningful. A token with the right grant but
no matching selected-tool grant must fail closed.

## Descriptor Contract

This is a Connection Hub delegated-credential protocol adapter. OAuth metadata,
authorization, token, refresh, and dynamic-client-registration routes are served
by the `connection-hub@1-0` bundle public operation:

```text
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/.well-known/oauth-authorization-server
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/.well-known/oauth-protected-resource?resource=<bundle-mcp-url>
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/authorize
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/authorize/consent
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/register
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/token
```

Stable root aliases such as `/.well-known/...` or `/oauth/...` may be added by
gateway routing later, but those aliases route to Connection Hub. They do not
make the OAuth adapter an ingress-owned feature.

Its non-secret protocol configuration belongs in `bundles.yaml` under the
Connection Hub bundle config:

Reference shape:

```yaml
bundles:
  items:
    - id: "connection-hub@1-0"
      config:
        connections:
          delegated_credentials:
            oauth:
              enabled: true
              brand: "KDCube"
              consent_ui:
                authority_ref:
                  authority_id: "kdcube.platform"
                  provider_id: "versatile_google_session"
                  entrypoint: "consent"
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
              capabilities:
                - grant: "memories:read"
                  label: "Read memories"
                  description: "Read memory notes visible to the KDCube user who approves the connection."
                  delegable_roles:
                    - "kdcube:role:registered"
                    - "kdcube:role:paid"
                    - "kdcube:role:privileged"
                    - "kdcube:role:super-admin"
                  delegable_permissions:
                    - "memories:read"
                - grant: "memories:write"
                  label: "Write memories"
                  description: "Create or update memory notes visible to the KDCube user who approves the connection."
                  delegable_roles:
                    - "kdcube:role:registered"
                    - "kdcube:role:paid"
                    - "kdcube:role:privileged"
                    - "kdcube:role:super-admin"
                  delegable_permissions:
                    - "memories:write"
              resources:
                - resource: "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*"
                  label: "User memories MCP"
                  identity_scope: "grantor_identity_family"
                  tools:
                    memory_search:
                      label: "Search memories"
                      grants: ["memories:read"]
                    memory_get:
                      label: "Read memory"
                      grants: ["memories:read"]
                - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
                  label: "KDCube named services MCP"
                  tools:
                    named_services_search:
                      label: "Named service search"
                      grants: ["named_services:use"]
                    named_services_upsert:
                      label: "Named service upsert"
                      grants: ["named_services:use"]
                    named_services_action:
                      label: "Named service action"
                      grants: ["named_services:use"]
                    named_services_delete:
                      label: "Named service delete"
                      grants: ["named_services:use"]
                  named_services:
                    namespaces:
                      mem:
                        label: "User memories"
                        authority_id: delegated_client
                        tools:
                          search:
                            operation: object.search
                            grants: ["memories:read"]
                          get:
                            operation: object.get
                            grants: ["memories:read"]
                          upsert:
                            operation: object.upsert
                            label: "Write memory"
                            grants: ["memories:write"]
                          action:
                            operation: object.action
                            label: "Memory action"
                            grants: ["memories:read"]
                          delete:
                            operation: object.delete
                            label: "Delete memory"
                            grants: ["memories:write"]
```

For generic named-service MCP resources, keep grants two-layered:
`kdcube_tools` advertises the generic MCP bridge tools and
`kdcube_named_services` advertises namespace/tool boundaries. The OAuth adapter
derives supported scopes from both layers, but it persists the nested
`named_services` catalog separately into the auth code, refresh token, and
access-grant record. The hosting bundle then enforces the catalog that was
actually granted instead of reading namespace policy from its own descriptor.

The bundle surface that consumes this credential is configured in
`bundles.yaml`, not in `assembly.yaml`. For a proc-served bundle MCP endpoint:

```yaml
bundles:
  items:
    - id: "user-memories@2026-06-26"
      config:
        surfaces:
          as_provider:
            mcp:
              memories:
                auth:
                  mode: managed
                  authority_id: delegated_client
                  tools:
                    memory_search:
                      grants: [memories:read]
                    memory_get:
                      grants: [memories:read]
                  selected_tool_grants: true
```

`mode: managed` means the proc MCP bridge owns the credential and grant check
before dispatching into the bundle MCP app. If `mode` is absent, the MCP auth
block is bundle-owned metadata. The knowledge bundle's shared-token surface uses
`surfaces.as_provider.mcp.knowledge.auth.mode: bundle` and reads
`surfaces.as_provider.mcp.knowledge.auth.header_name` before returning its MCP
app.

There is no platform-level `/mcp`. MCP is exposed by bundles and served by proc.
The normal product shape is:

```text
Connection Hub OAuth/consent/token routes
  -> delegated credential
  -> proc bundle @mcp endpoint with mode: managed
  -> bundle MCP app
```

Old shape to avoid in new descriptors:

```yaml
auth:
  delegated_client: ...
  connection_hub:
    delegated_credentials: ...
```

Rules:

- `enabled` controls whether the Connection Hub public OAuth operation accepts
  requests.
- `issuer` is the public origin advertised in OAuth metadata. If omitted, local
  development derives it from the mounted Connection Hub public operation URL.
- `public_clients[*].redirect_uris` configures known public clients.
- `dynamic_client_registration.allowed_redirect_uris` constrains pre-auth
  dynamic client registration.
- Redirect URI fields are descriptor lists, not comma-separated strings.
- Tenant and project come from `assembly.yaml -> context.tenant` and
  `context.project`.
- Platform session cookie name comes from the selected platform authority
  provider in `connection-hub@1-0.config.authority_registry`.
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
| CSRF token | Single-use consent POST protection bound to grantor subject. | Short TTL. |
| Authorization code | Stores client, redirect URI, PKCE challenge, grantor subject, resource, final scopes, selected tools, delegation edges, and grantor authority facts captured at consent. | Short TTL, single use. |
| Access grant | Binds an access token to selected MCP tools, the `delegated_client` credential envelope, delegation edges, and server-side grantor authority facts. | Same TTL as access token. |
| Refresh token | Stores client, grantor subject, resource, scopes, selected tools, credential envelope, delegation edges, grantor authority facts, and rotation state. | Long-lived, rotating. |
| Bundle session record | The issued access token is a `kst1` session for the integration identity. | Access-token TTL. |

Redis loss is safe but product-visible: missing records fail closed, but
long-lived connectors can require re-consent if dynamic client or refresh-token
records disappear. The solution-level durability design note is
[Grant Storage Durability](../../sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md).

## Failure Modes

| Situation | Expected behavior |
|---|---|
| No platform session on Connection Hub `/public/oauth/authorize` | `login_required`; client must start from an authenticated browser session. |
| Authenticated user lacks the configured delegable role/permission for a requested grant | `forbidden`; the user can only delegate grants allowed by descriptor policy. |
| DCR redirect URI is not allowlisted | `invalid_redirect_uri`; client is not registered. |
| Bad redirect URI on authorize/token | Request fails; codes are not delivered to unvalidated redirects. |
| Missing or invalid PKCE verifier | Token request fails with `invalid_grant`. |
| Token has grant but no selected-tool grant | Bundle MCP `tools/call` fails closed. |
| Tool is not listed by endpoint policy or not selected during consent | Bundle MCP `tools/call` returns an MCP tool authorization error. |
| Refresh token is invalid or rotated | Token request fails with `invalid_grant`. |

## What This Is Not

This mechanism is not:

- a replacement for browser login;
- a way for an app to mint platform admin sessions;
- an app-level named service;
- a place for operator-facing environment variables;
- a broad "admin API" token.

It is a descriptor-configured protocol adapter from an already-authenticated
platform consent flow to a least-privilege delegated credential.

## Relationship To Connection Hub

OAuth delegated credential is one delegated-connection authenticator/protocol adapter under the
Connection Hub concept. Its HTTP protocol surface is hosted by the Connection
Hub bundle public `oauth` operation. Concrete MCP resources remain bundle/proc
surfaces and use the delegated credential produced by Connection Hub. The shared
diagram lives in
[Delegated Credential Protocol Adapters](delegated-credential-protocol-adapters-README.md).

At the Connection Hub layer, OAuth delegated credential is not conceptually different from other
credential-bearing integrations. It provides one authenticator and one grant
registry:

```text
KDCube-issued integration token
  -> delegated_client authenticator
  -> delegated_client grant registry
  -> delegated representative principal
  -> selected tools / allowed actions
```

The feature registers a `delegated_client` authority provider in the Connection Hub
authority registry. That makes the implementation visible to code using the
authority SDK and keeps the protocol mechanics under the same service that owns
delegated credentials.

The consent roundtrip is how that credential and grant registry entry are
created:

```text
grantor authority
  platform browser session / projected platform principal
      |
      v
connection-hub@1-0/public/oauth/authorize
      |
      v
descriptor-allowed scopes + selected MCP tools
      |
      v
auth code + PKCE
      |
      v
integration token + refresh token + selected-tool grant
```

The OAuth delegated credential authenticator validates only OAuth delegated credential tokens and grant records.
It should not learn Telegram, Slack, webhook, Gmail, or customer directory proof
formats. Those are other authenticator modules. Likewise, connection edges should
not issue OAuth codes or refresh tokens; those records belong to the
OAuth delegated credential grant registry.

## Current Managed MCP Connector Shape

The live example implementation is `kdcube-services@1-0`. It exposes managed
MCP resources such as:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/conversations
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

The `named_services` MCP surface is intentionally generic: namespaces such as
`mem`, `task`, and `cnv` are tool arguments, while namespace-specific grants are
kept in the Connection Hub protected-resource catalog. The MCP server advertises
server-level instructions so clients know the intended order:

```text
named_services_list
  -> named_services_capabilities / named_services_schema
  -> named_services_search / named_services_get / named_services_upsert / ...
```

The FastMCP apps are built with `stateless_http=True`, because current bundle
MCP requests are dispatched request-by-request through proc workers. Protocol
session state is not stored in a bundle-local FastMCP object.

For connector UX, MCP apps should advertise:

- server `icons` and `website_url` from
  `kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata`;
- `ToolAnnotations` such as `readOnlyHint` and `destructiveHint`.

Connection Hub consent uses descriptor labels/grants. Claude's post-connection
tool grouping uses MCP `ToolAnnotations`. They are related UX surfaces, but they
are not the same enforcement boundary.

## Regression Checklist

Use focused tests and one live connector test.

1. Connection Hub OAuth metadata routes return issuer, authorization endpoint,
   token endpoint, registration endpoint, and concrete protected-resource
   metadata.
2. DCR accepts only descriptor-allowed redirect URIs.
3. Authorization requires an authenticated platform session.
4. Consent POST validates CSRF and re-validates client, redirect URI, and PKCE.
5. Token issue stores selected tools and nested named-service catalogs on both
   access grant and refresh record.
6. Refresh rotation preserves selected tools and nested named-service catalogs.
7. Integration token without a selected-tool grant fails closed at the managed
   bundle MCP guard.
8. Users can consent only to grants permitted by the Connection Hub descriptor
   (`delegable_roles` / `delegable_permissions`).
9. Bundle MCP `tools/list` and `tools/call` return MCP-shaped responses, not
   unhandled HTTP 500s for authorization failures.
10. `named_services` advertises server instructions that tell clients to call
    `named_services_list` first and then inspect capabilities/schema.
11. MCP server icon metadata resolves to the KDCube favicon, and
    `ToolAnnotations` split read-only tools from write/action/delete tools in
    clients that honor MCP annotations.
12. The feature is disabled when
    `connection-hub@1-0.config.connections.delegated_credentials.oauth.enabled: false`.
