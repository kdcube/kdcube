---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
title: "OAuth Delegated Credential Protocol Adapter"
summary: "How the OAuth2 protocol adapter resolves pre-registered, Client ID Metadata Document, and DCR clients, then issues and verifies least-privilege Connection Hub credentials."
tags: ["sdk", "solutions", "connections", "delegated-credentials", "oauth", "mcp", "descriptor"]
keywords: ["OAuth2 authorization server", "MCP protected resource", "Claude Code", "PKCE", "Client ID Metadata Document", "CIMD", "dynamic client registration", "tool consent", "live grant lookup", "operation csrf protection", "descriptor configuration"]
updated_at: 2026-08-07
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

## Three Client Registration Paths

KDCube resolves an OAuth client in this order:

1. **Pre-registered client.** `public_clients` in the Connection Hub descriptor
   supplies a stable client id and callback set.
2. **Client ID Metadata Document (CIMD).** An HTTPS URL used as `client_id`
   identifies and hosts the public client's metadata. This is the current MCP
   registration path for clients that support URL-based registration.
3. **Dynamic Client Registration (DCR).** A client first calls
   `/oauth/register`; KDCube stores the generated `dcr-...` client id. This path
   remains available for existing clients and can be disabled independently.

All three paths lead into the same PKCE, consent, grant, token, refresh, and
revocation machinery. Registration does not grant authority. It establishes
the client identity and valid callback URIs that the user sees before deciding
what to delegate.

CIMD resolution is an SSRF-sensitive server operation. KDCube accepts HTTPS
metadata URLs with a path, resolves every address before connecting, rejects
non-public addresses and redirects, pins the approved addresses for the
request, sends no ambient credentials or cookies, and limits the decoded JSON
document to 5 KiB by default. The document's `client_id` must exactly equal its
URL, only public clients using PKCE are accepted, and callback matching is
exact. Valid documents may be cached according to response cache headers;
network errors and malformed documents are never cached.

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


Client registration resolution
  |
  | 2a. match a descriptor pre-registration
  | 2b. resolve an HTTPS Client ID Metadata Document
  | 2c. or, for a DCR client, POST /oauth/register first
  v
Validated public OAuth client
  |
  | client identity + callback set; no resource authority yet
  v


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
  | 5. User approves platform delegation grants and selected operation set
  |    CSRF token is single-use and bound to grantor subject
  |    displayed client metadata is fingerprinted and rechecked on submit
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
  | stores refresh token, selected operation allowlist, credential envelope,
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
  | checks grant-level selected-operation allowlist
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

That integration identity receives the generic delegated-client role plus the
approved grants from the concrete protected-resource descriptor. OAuth does not
hardcode service-specific permissions:

```text
kdcube:role:delegated-client
<approved resource grants, for example memories:read>
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
    "scopes": ["memories:read"],
    "operations": ["memory_search", "memory_get"],
    "resource_grants": {
      "https://runtime.example/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories": ["memories:read"]
    },
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
The selected operations are then filtered against that final grant set. Approval must
bind both the selected operation list and the resulting delegation edge into the
issued grant:

```
consent POST
  -> authorization code stores final scopes + selected operations + delegation_edges
  -> token endpoint binds selected operations + delegation_edges to access token
  -> refresh token record stores selected operations + delegation_edges
  -> refresh rotation preserves selected operations + delegation_edges
  -> managed bundle MCP tools/call checks role permission AND selected-operation grant
```

This makes the tool-selection UI meaningful. A token with the right grant but
no matching selected operation must fail closed.

## Descriptor Contract

This is a Connection Hub delegated-credential protocol adapter. OAuth metadata,
authorization, token, refresh, revocation, and optional DCR routes are served by
the `connection-hub@1-0` bundle public operation. CIMD uses the authorization
route and the client-provided HTTPS metadata URL; it does not add a KDCube
registration endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/.well-known/oauth-authorization-server
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/.well-known/openid-configuration
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/.well-known/oauth-protected-resource?resource=<bundle-mcp-url>
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/authorize
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/authorize/consent
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/register
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/token
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/oauth/jwks
```

The authorization-server document is served at both well-known locations. MCP
clients fetch the OIDC one and treat a 404 there as fatal, so it stays and the
document carries the fields OIDC discovery requires:

- `jwks_uri` points at `/oauth/jwks`, which returns `{"keys": []}` and always
  will. Access tokens are opaque (`kst1`), so there is no public key and no
  client verifies a signature — the resource validates the token.
- `subject_types_supported` is `["public"]`.
- `id_token_signing_alg_values_supported` is present because the schema
  requires it. No `id_token` is ever issued: `openid` is absent from
  `scopes_supported`, so no client can request one.

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
                  provider_id: "workspace_google_session"
                  entrypoint: "consent"
              issuer: ""
              public_clients:
                - client_id: "claude"
                  client_name: "Claude"
                  application_type: "native"
                  redirect_uris:
                    - "https://claude.ai/api/mcp/auth_callback"
                    - "http://localhost/callback"
                    - "http://127.0.0.1/callback"
              dynamic_client_registration:
                enabled: true
                default_application_type: "native"
                allowed_redirect_uris:
                  - "https://claude.ai/api/mcp/auth_callback"
                  - "http://localhost/callback"
                  - "http://127.0.0.1/callback"
              client_id_metadata_documents:
                enabled: true
                # Empty means any public HTTPS metadata host may identify a
                # client. Set domains to narrow publishers for this deployment.
                allowed_domains: []
                allow_subdomains: true
                fetch_timeout_seconds: 5.0
                max_document_bytes: 5120
                cache_ttl_seconds: 3600
                cache_max_ttl_seconds: 86400
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
`bundles.yaml`, not in `assembly.yaml`. For a proc-served bundle MCP endpoint,
the surface declares only the managed boundary. The concrete tool/grant catalog
for delegated OAuth lives in Connection Hub `resources` above:

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
                  selected_tool_grants: true
```

`mode: managed` means the proc MCP bridge owns credential validation before
dispatching into the bundle MCP app. It resolves the resource catalog from
Connection Hub and uses that catalog for tool/grant enforcement. If `mode` is
absent, the MCP auth block is bundle-owned metadata. The knowledge bundle's
shared-token surface uses
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
- `public_clients[*]` configures known public clients. Their
  `redirect_uris`, `application_type`, and optional display metadata are owned
  by the descriptor.
- `client_id_metadata_documents.enabled` advertises and accepts HTTPS URL
  client ids. `allowed_domains` is an optional publisher allowlist; an empty
  list keeps the open CIMD model while the resolver still enforces public
  addresses, HTTPS, no redirects, bounded bodies, and exact client-id and
  callback matching. Cache settings apply only to valid documents. Errors and
  malformed documents are fetched again on the next authorization attempt.
- `dynamic_client_registration.enabled` controls whether `/oauth/register` is
  advertised and served. Keep it enabled while clients still use DCR; disable
  it only after those clients have migrated to pre-registration or CIMD.
- `dynamic_client_registration.allowed_redirect_uris` constrains pre-auth
  dynamic client registration. Registration runs before any user has
  authenticated, so this allowlist is the defense that keeps an attacker from
  registering a "client" whose redirect points at their own server: a stolen
  authorization code can only be delivered to a known app callback or to the
  user's own machine.
- Pre-registered and DCR native-client redirect matching follows RFC 8252:
  loopback redirects (`localhost`,
  `127.0.0.1`, `::1`) match on **any port**, because a native client binds a
  dynamic local port for its callback — but scheme, host, and path must match
  an allowlisted entry exactly. All non-loopback redirects must match exactly,
  including the port. Implementation: `redirect_uri_allowed()` in
  `kdcube_ai_app/apps/chat/sdk/solutions/connections/delegated_credentials/oauth/clients.py`.
- CIMD callback matching honours what the document states. A published entry
  that **names a port** must be matched exactly, including that port — the
  document asserted a concrete callback, so it is held to it. A **portless**
  loopback entry admits any port, because a native client cannot publish the
  port it will bind at runtime; scheme, host, and path are still matched
  exactly. Web-client callbacks must use HTTPS. Native-client callbacks may use
  HTTPS or HTTP on `localhost`, `127.0.0.1`, or `::1`; other schemes and
  duplicate callback entries are rejected before consent.
- A CIMD document that omits `application_type` is read as a native client when
  **every** redirect it publishes is an HTTP loopback URI. The OIDC default of
  `web` would refuse those entries outright, and published documents of real
  native clients omit the field. A document carrying any non-loopback HTTP
  redirect still resolves to `web` and is still refused.
- Practical consequence for native MCP clients: an entry like
  `http://localhost/callback` admits `http://localhost:52791/callback`, but not
  `http://localhost:52791/auth/callback` — a client whose callback uses a
  different loopback *path* needs its own allowlist entry with that exact path.
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

## Consent Screen: Accounts This Connection Needs

Before the per-account picker, the authorize page resolves the **requested scope
to the provider accounts that back it**, so a required-but-unconnected provider
is visible and connectable HERE — not discovered when the first tool call fails
at the Delegated-to gate (the external-client consent page must never dead-end).
A requested claim reaches a provider two ways:

- **Hard (AND)** — a provider-claim token in a provider's claim vocabulary
  (`sheets:read` → Google). Every provider a scope names this way is required;
  each renders one row with `not connected` / `needs more access` / `connected`
  status and a connect / approve-access deep-link.
- **Any-of (OR)** — a provider-neutral **door claim** (`mail:read`) whose backing
  provider claim differs from the door token (`gmail:read` on Google,
  `email:read` on iCloud) and which several providers can satisfy. Connecting ANY
  ONE satisfies it, so the page renders a single "connect one of" choice, never a
  "connect them all" list. A door claim declares its options on its capability:

  ```yaml
  capabilities:
    - grant: "mail:read"
      label: "Read connected mail"
      delegable_roles: ["kdcube:role:registered", "kdcube:role:super-admin"]
      delegable_permissions: ["mail:read"]
      connected_accounts:
        - provider_id: "google"        # gmail:read backs the mail:read door
          claims: ["gmail:read"]
        # add iCloud/Yahoo options as they ship — any one satisfies mail:read
  ```

Each door group resolves to: **satisfied** (an option already connected and
holding its claims → no action, and no nag to connect the others); **folded**
into a provider required anyway for a hard reason (Google for `sheets:read`
already → one connect covers `mail:read` too, no separate choice); or a
**choice** with a connect deep-link per option. Door claims that offer the same
providers (`mail:read` + `mail:send`) coalesce into one group. Every connect link
opens the *Delegated to KDCube* connect panel in a NEW tab (this page is
mid-OAuth), pre-selected with the provider, the connector app chosen broker-style
(the enabled app whose `allowed_claims` cover the need), and least-privilege
claims — the same claim→provider and app selection the credential broker uses at
call time, so what the page offers is exactly what a later call resolves.

The door claim → provider mapping declared here mirrors the tool's own runtime
`connected_accounts` contract; the browser consent page cannot see into the
provider bundle's tool metadata, so the capability declaration is how it reaches
the page. Keep the two in step. Resolution is best-effort and fail-open: if the
provider config is unavailable the panel simply does not render, never an error.

## Consent Screen: Per-Account Binding Picker

The authorize page shows the consenting user's connected provider accounts,
grouped per provider, each account with its own approved claims as checkboxes.
The picks travel through the authorization code into the client's registry
card as its per-account binding (`account_scope`). Nothing is pre-checked on a
first connect — granting is always the user's explicit action; a re-consent
pre-fills only what this client was already granted before. The details block
summarizes the requested scope ("N capabilities (families)") instead of
printing raw scope tokens: every scope is presented below as a labeled,
narrowable row.

Enforcement is default-closed for delegated callers: an account left unticked
is not usable by this client even though the connection ceiling names the
claim. The semantics live in
[Configure Agent → Service Access](../configuring-agent-service-access/configuring-agent-service-access-README.md);
a call that needs more raises `agent_grant_required` in the delegated-account
broker. When the operation names a concrete account, the client-facing adapter
can specialize that result to `agent_account_binding_required`, preserving the
exact account and claim. Both reasons deep-link the client's own grant card
(Delegated by KDCube), never the provider-connect tab whose state is not the
problem. The URL is recovery data for the host or external client to present;
returning it does not open Connection Hub or replay the failed operation.

Grant-card updates distinguish omission from an explicit empty map:
`account_scope` omitted preserves existing account bindings, while
`account_scope: {}` intentionally clears them. This lets an unrelated resource
or claim edit leave account authority unchanged and still gives the user an
explicit revoke-all operation.

## Revocation And Card Lifecycle

- **RFC 7009 revocation.** `POST /oauth/revoke` accepts the client's refresh
  or access token (`token`, optional `token_type_hint`), revokes it, and
  retires the pointed-to Connection Hub card together with its other live
  token material. Unknown tokens still return 200 (idempotent, non-probing).
  The `revocation_endpoint` is advertised in the RFC 8414 metadata, so a
  disconnecting client that honors it leaves no orphan card.
- **DCR sibling supersession.** A dynamically-registered client gets a new
  `dcr-…` id on every reconnect, so its previous card could never be used
  again. A fresh consent therefore supersedes sibling cards — same grantor and
  resource, a different `dcr-…` client whose registered redirect ORIGIN
  matches (the app's stable identity across re-registrations). The sibling
  donates its per-account binding to the new card, then is revoked. Statically
  registered client ids are keyed stably and never pile up.
- **CIMD identity is stable at the URL.** The client id is the metadata URL,
  so reconnects address the same client identity. The consent POST re-resolves
  the client and compares a digest of the metadata shown to the user; a change
  requires a fresh authorization page instead of silently approving different
  callbacks or display metadata.
- **Refresh rotations preserve the card.** Re-registration on token issuance
  merges the card's existing grants and per-account binding (a rotation never
  wipes the user's ticks), and rotated refresh records keep the registry-card
  pointer. The replacement refresh record receives the card's current scopes
  and operations rather than the token's older snapshot. Cards stamp
  `last_issued_at` on every issuance — a stale value marks a disconnect
  orphan; cards also expire with the refresh-token TTL.
- **Single-use state is consumed atomically.** Authorization-code exchange and
  consent-CSRF validation use an awaited Redis Lua `GET`+`DEL` transition, so
  concurrent workers cannot both accept the same record. Refresh first reads
  one exact token snapshot, validates current live authority, and then passes
  that snapshot to an awaited Lua compare-and-rotate transition. The script
  deletes the old record and creates one replacement in the same Redis action;
  a stale or concurrent consumer receives `invalid_grant`.
- **A card pointer makes the card authoritative.** Every managed MCP/REST call
  and refresh resolves the current pointed-to card before using delegated
  authority. A missing or expired card is revoked authority. An unavailable
  store, malformed record, unsupported schema, invalid structure, or binding
  mismatch denies the request; the runtime never falls back to the access or
  refresh token's older grant snapshot. Legacy records without a card pointer
  retain their snapshot contract.

## External URLs Behind A Proxy

OAuth callback, consent, upload, and recovery links must reflect the scheme the
client used at the trusted edge.

`X-Forwarded-Proto` is a trail rather than a single value: each proxy appends
its own observation, and repeated headers arrive joined with `, `. The bundled
OpenResty proxy therefore reads the **rightmost** element first — written by the
proxy closest to it, the only one it can treat as trusted — and accepts that
element only when it is exactly `http` or `https`. Anything else, including an
empty or malformed list, falls back to the scheme that proxy received.

An edge that appends rather than overwrites is handled by this rule, which
matters because appending is the common behaviour: with a terminator in front,
a client that sends `X-Forwarded-Proto` at all — even with the correct value —
would otherwise produce a list that matches nothing, and generated links would
silently drop to `http://`, signed upload URLs among them. Whatever the client
writes lands to the left of the trusted proxy's value and is never read, so a
client can neither inject a scheme nor raise one; the worst it can do is
degrade its own links.

Overwriting the header at the trusted edge remains the cleaner deployment, but
it is no longer a precondition for correct links. Validation prevents malformed
schemes from entering generated links; it is not trusted-proxy authentication by
itself.

## Storage

Runtime grant state is tenant/project scoped and lives in the platform runtime
store, normally Redis.

| Record | Purpose | Lifetime |
|---|---|---|
| Dynamic client record | Stores registered public client metadata and redirect URIs. | Until registration expiry or cleanup policy. |
| Valid CIMD cache entry | Stores one validated public client snapshot. Errors and malformed documents are not cached. | Response cache policy, capped by descriptor TTL. |
| CSRF token | Single-use consent POST protection bound to grantor subject plus client metadata digest. | Short TTL. |
| Bundle operation CSRF token | Protects cookie-authenticated state-changing bundle operations; binds subject, tenant, project, bundle, operation, and method. Connection Hub keeps an exhaustive protected-or-exempt inventory of every effective POST surface. | Ten minutes, single use. |
| Authorization code | Stores client, redirect URI, PKCE challenge, grantor subject, resource, final scopes, selected operations, delegation edges, and grantor authority facts captured at consent. | Short TTL, single use. |
| Access grant | Binds an access token to selected operations, the `delegated_client` credential envelope, delegation edges, and server-side grantor authority facts. | Same TTL as access token. |
| Refresh token | Stores client, grantor subject, resource, scopes, selected operations, credential envelope, delegation edges, grantor authority facts, and rotation state. | Long-lived, rotating. |
| Bundle session record | The issued access token is a `kst1` session for the integration identity. | Access-token TTL. |

Redis loss is safe but product-visible: missing records fail closed, but
long-lived connectors can require re-consent if dynamic client or refresh-token
records disappear. State-store resolution belongs to the delegated-credential
adapter, below MCP and REST dispatch. It reuses a request application's shared
async client when available and otherwise uses the platform's shared async
client factory. Resolution and I/O failures are logged with the failed
operation and returned as `503 temporarily_unavailable` by OAuth routes and
managed MCP/REST authorization guards; raw storage errors are not returned to
clients. The operation CSRF service uses the same shared state infrastructure:
the local CLI derives the `chat-proc` connection from descriptor-owned
`infra.redis`, and the ECS task receives the corresponding logical secret
through Secrets Manager. `REDIS_URL` is internal service wiring, not an
operator configuration surface. No separate descriptor field or
deployment-specific store is required. The solution-level durability design note is
[Grant Storage Durability](../../sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md).

## Failure Modes

| Situation | Expected behavior |
|---|---|
| No platform session on Connection Hub `/public/oauth/authorize` | `login_required`; client must start from an authenticated browser session. |
| Authenticated user lacks the configured delegable role/permission for a requested grant | `forbidden`; the user can only delegate grants allowed by descriptor policy. |
| DCR is disabled | `/oauth/register` is not advertised and returns `404`; pre-registered and CIMD clients continue to work. |
| DCR redirect URI is not allowlisted | `invalid_redirect_uri`; client is not registered. |
| CIMD URL resolves to a private address, redirects, exceeds the size cap, or returns malformed metadata | Authorization fails closed; the failed document is not cached. |
| CIMD metadata changes after the consent page is shown | Consent submit fails and the client must restart authorization. |
| Bad redirect URI on authorize/token | Request fails; codes are not delivered to unvalidated redirects. |
| Missing or invalid PKCE verifier | Token request fails with `invalid_grant`. |
| Token has grant but no selected operation | Bundle MCP `tools/call` fails closed. |
| Tool is not listed by endpoint policy or not selected during consent | Bundle MCP `tools/call` returns an MCP tool authorization error. |
| Pointed-to live grant card is absent or expired | The delegated credential is treated as revoked. |
| Pointed-to live grant card cannot be read, decoded, structurally validated, or bound to this credential | Managed MCP/REST calls fail closed with `503`; refresh does not rotate and returns `temporarily_unavailable` for store failure or `invalid_grant` for invalid authority state. |
| Cookie-authenticated grant mutation omits, reuses, or changes the operation CSRF token context | Proc rejects the mutation with `403`; Redis failure returns `503`. |
| Two workers exchange one authorization code or consume one consent-CSRF token concurrently | One Lua transition wins; the other request sees the record as absent and cannot replay it. |
| Two workers rotate one refresh token concurrently | Current live authority is checked before rotation; one compare-and-rotate script wins and creates one replacement, while the stale request receives `invalid_grant`. |
| OAuth shared state cannot be read or changed | OAuth routes and managed MCP/REST guards log the store operation and return `503 temporarily_unavailable`; they do not continue from guessed state. |
| Refresh token is invalid or rotated | Token request fails with `invalid_grant`. |
| Forwarded scheme is absent or not exactly `http`/`https` at the bundled proxy | Generated links use the scheme received by the proxy; arbitrary forwarded values are ignored. |

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
  -> selected operations / allowed actions
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
descriptor-allowed scopes + selected operations
      |
      v
auth code + PKCE
      |
      v
integration token + refresh token + selected-operation grant
```

The OAuth delegated credential authenticator validates only OAuth delegated credential tokens and grant records.
It should not learn Telegram, Slack, webhook, Gmail, or customer directory proof
formats. Those are other authenticator modules. Likewise, connection edges should
not issue OAuth codes or refresh tokens; those records belong to the
OAuth delegated credential grant registry.

## Current Managed MCP Connector Shape

The live example implementation is `kdcube-services@1-0`. It exposes managed
MCP resources configured outside the OAuth adapter, for example:

```text
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

MCP apps return `KDCubeMCPServer`, whose proc-serving default is
`stateless_http=True`. Requests are dispatched independently through proc
workers, so protocol session state is not stored in one bundle-local server
object. The SDK v2 server accepts both the MCP 2026-07-28 discovery flow and
legacy `initialize` clients; this wire negotiation is independent of whether
the OAuth client was pre-registered, resolved through CIMD, or registered by
DCR.

### MCP 2026-07-28 support boundary

The modern Streamable HTTP path targets the MCP `2026-07-28` core through the
official Python SDK v2. KDCube's wire regression records the actual exchange
through `KDCubeMCPServer` and the proc bridge and checks:

- `server/discover` replaces the modern initialize handshake;
- every request carries protocol version, client capabilities, and client
  identity metadata;
- `MCP-Protocol-Version`, `Mcp-Method`, and operation-specific `Mcp-Name`
  headers agree with the body;
- modern requests carry no `Mcp-Session-Id`;
- ordinary results carry `resultType: complete` and server identity;
- cacheable list results carry `ttlMs` and `cacheScope`;
- a legacy client can still negotiate the retained initialize path.

The authorization adapter implements the corresponding registration and OAuth
changes: CIMD is supported, DCR remains as a separately configurable
compatibility path, DCR records carry `application_type`, authorization
responses carry `iss`, and persisted delegated state remains issuer- and
resource-bound.

This is a support statement for the capabilities KDCube exposes, backed by the
focused wire and OAuth suites. It is not a blanket claim that every optional
MCP extension is implemented. KDCube does not currently claim the Tasks
extension, and a distributable full-conformance claim additionally requires a
green report from the official MCP conformance runner plus live external-client
DCR and CIMD journeys.

For connector UX, MCP apps should advertise:

- server `icons` and `website_url` from
  `kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata`;
- `ToolAnnotations` such as `readOnlyHint` and `destructiveHint`.

Connection Hub consent uses descriptor labels/grants. Claude's post-connection
tool grouping uses MCP `ToolAnnotations`. They are related UX surfaces, but they
are not the same enforcement boundary.

## Managed REST Surface Shape

Managed REST uses the same delegated credential and grant store as managed MCP,
but REST has two enforcement locations:

- application REST operations are guarded by the proc application REST bridge;
- platform REST resources are guarded by the shared request-auth layer before
  the platform route handler runs.

Do not route REST authorization through generic platform cookies, and do not
call MCP guard code from REST handlers.

An application exposes a normal REST operation. The operation may live on
`public` or `operations`; `auth.mode: managed` is what lets a delegated bearer
token replace a browser cookie session for that operation.

```python
@api(method="POST", alias="records_export", route="public")
async def records_export(self, **params):
    ...
```

The operation becomes delegated-credential protected only through descriptor
configuration:

```yaml
surfaces:
  as_provider:
    api:
      public:
        records_export:
          POST:
            auth:
              mode: managed
              authority_id: delegated_client
              selected_operation_grants: true
              operations:
                records_export:
                  grants:
                    - records:read
```

The corresponding Connection Hub delegated resource describes the same concrete
resource and operation catalog:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: records:read
          label: Read records
          delegable_roles:
            - kdcube:role:registered
            - kdcube:role:super-admin
      resources:
        - resource: "*/api/integrations/bundles/*/*/records@1-0/public/records_export*"
          label: Records REST API
          operations:
            records_export:
              label: Export records
              description: Export records visible to the approving user.
              grants:
                - records:read
```

At request time:

```text
Authorization: Bearer <delegated-client access token>
  -> managed REST guard validates token, resource, authority, grants, operation consent
  -> proc projects grantor_user_id into UserSession and ExternalEventPayload
  -> application operation receives the delegated platform-user context
```

This flow is orthogonal to the platform authority provider. The approving user
may have signed in through Cognito, multi-Cognito, or an application-hosted
platform authority. The REST guard only consumes the already-issued delegated
credential record.

For platform APIs, there is no application operation descriptor. Connection Hub
still owns the resource and operation catalog:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: devops:deploy
          label: Deploy runtime
          delegable_roles:
            - kdcube:role:super-admin
      resources:
        - resource: "*/api/platform/admin/redeploy*"
          label: Platform redeploy API
          operations:
            platform_admin_redeploy:
              label: Redeploy runtime
              grants:
                - devops:deploy
```

When a request carries `Authorization: Bearer <delegated-client access token>`,
the Connection Hub authentication surface checks whether the URL matches a
configured delegated resource. If it does, it validates the token, grant,
resource, and selected operation and returns a projected `UserSession` for the
grantor. Existing platform route dependencies then see the same roles and
permissions they would see from a normal platform session.

If the URL is not configured as a delegated resource, the Connection Hub
delegated bearer path is ignored and normal platform authentication rules apply.
Keep platform resource patterns one-operation-wide until the route has an
explicit operation selector.

For admin-created automation that may enter any KDCube API, Connection Hub uses
the platform role itself as the delegable grant:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: kdcube:role:super-admin
          label: Use all platform and application APIs
          delegable_roles:
            - kdcube:role:super-admin
      resources:
        - resource: "*"
          label: All platform and application APIs
          admin_only: true
          grants:
            - kdcube:role:super-admin
```

`resource: "*"` is deliberately special: non-admin users do not see it in
Connection Hub, and the request-auth surface accepts it only when the
server-side resource-grant map assigns `kdcube:role:super-admin` to `*` and the
stored grantor authority projects the grantor with platform admin privilege.
The role is the authority grant.

Issued automation credentials store the boundary as `resource_grants`, not as a
separate resource list plus a separate grant list. The Connection Hub access
record exposes this same map:

```json
{
  "resource_grants": {
    "*": ["kdcube:role:super-admin"],
    "*/api/integrations/bundles/*/*/records@1-0/public/records_export*": ["records:read"]
  }
}
```

The bearer token is not the authority source for this decision. Managed guards
read the server-side grant record by access-token hash and derive matchable
resources from the keys of `resource_grants`.

For a manual automation bearer targeting a resource with a `named_services`
catalog, the request may additionally contain the exact selection:

```json
{
  "named_service_operations": {
    "*/kdcube-services@1-0/public/mcp/named_services*": {
      "mem": ["object.search"]
    }
  }
}
```

This field belongs to the manual `delegated_access_create` operation, not the
OAuth token endpoint. Connection Hub validates it against the same descriptor
catalog OAuth consent uses and stores a narrowed `named_services` tree in the
server-side grant. Ordinary REST and product-specific MCP resources still
derive compatible top-level operations from `resource_grants`.

Provider-account requirements are not embedded into this selector. They are
resolved through the grantor's separate **Delegated to KDCube** connection, and
the provider token never enters the delegated-client bearer.

## Regression Checklist

Use focused tests and one live connector test.

1. Connection Hub OAuth metadata routes return issuer, authorization endpoint,
   token endpoint, conditional registration endpoint, CIMD capability flag,
   and concrete protected-resource metadata.
2. Pre-registered clients resolve without a network metadata fetch.
3. A valid CIMD client resolves only from a public HTTPS endpoint, uses exact
   callback matching, and is cached only when its response permits caching.
4. CIMD redirects, private addresses, oversized or malformed documents, and
   metadata changes between display and approval fail closed; failures are not
   cached.
5. DCR accepts only descriptor-allowed redirect URIs and remains usable when
   explicitly enabled.
6. Authorization requires an authenticated platform session.
7. Consent POST validates CSRF and re-validates client, redirect URI, PKCE, and
   the client metadata snapshot shown to the user.
8. Token issue stores selected operations and nested named-service catalogs on both
   access grant and refresh record.
9. Refresh rotation preserves selected operations and nested named-service catalogs.
10. Integration token without a selected-operation grant fails closed at the managed
   bundle MCP guard.
11. Users can consent only to grants permitted by the Connection Hub descriptor
   (`delegable_roles` / `delegable_permissions`).
12. Bundle MCP modern discovery and legacy initialization both reach the same
    stateless server and tool catalog.
13. Bundle MCP `tools/list` and `tools/call` return MCP-shaped responses, not
   unhandled HTTP 500s for authorization failures.
14. `named_services` advertises server instructions that tell clients to call
    `named_services_list` first and then inspect capabilities/schema.
15. MCP server icon metadata resolves to the KDCube favicon, and
    `ToolAnnotations` split read-only tools from write/action/delete tools in
    clients that honor MCP annotations.
16. Manual automation access to a named-services resource stores only selected
    namespace operations; sibling operations and namespaces fail at the bridge.
17. Removing a required domain or MCP-entry grant clears/rejects its selected
    namespace operation.
18. A missing provider-account claim fails independently without exposing the
    provider credential to the delegated client.
19. The feature is disabled when
    `connection-hub@1-0.config.connections.delegated_credentials.oauth.enabled: false`.
20. The "Accounts this connection needs" panel resolves a hard provider claim
    (`sheets:read` → a Google row) and an any-of door claim (`mail:read` → one
    "connect one of" choice over its `connected_accounts` options), folds a door
    claim into a hard-required provider, and treats a door claim already backed
    by one connected account as satisfied (no "connect the others").
21. Concurrent authorization-code and consent-CSRF consumers produce exactly
    one successful result under real Redis.
22. Concurrent refresh requests using the same exact token snapshot create
    exactly one replacement token; the other request cannot rotate stale state.
23. Every effective Connection Hub operation POST is classified as either
    cookie-CSRF protected or explicitly read-only; every public POST is listed
    under its protocol-specific exemption.
24. Redis failures on OAuth state operations are logged and returned as
    `503 temporarily_unavailable` rather than an unstructured `500`.
25. The recorded modern Streamable HTTP exchange contains the required
    per-request metadata and routing headers, contains no session header, and
    returns `resultType`, server identity, and cache hints.
26. The official MCP conformance runner is executed for the exact public
    surface before publishing an unqualified conformance claim; unsupported
    optional capabilities remain outside the claim.
27. A provider tool's `account_id` reaches both requirement preflight and the
    provider operation; two eligible accounts return `account_required`, and
    resending with one candidate resolves that same account end to end.
28. A named capable account outside the caller's binding returns
    `agent_account_binding_required` with the account, claim, caller card, and
    KDCube resource surface identified before provider I/O.
29. Updating a grant without `account_scope` preserves existing bindings;
    updating with `account_scope: {}` clears them.
30. A manual automation recovery URL focuses the existing access card and the
    affected resource/account/claim; it does not invoke a hosted-agent create
    operation or change the grant automatically.
31. Bundled proxy configuration accepts only `http` and `https` as forwarded
    schemes and falls back to its received scheme for any other value.
