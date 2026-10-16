---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
title: "Delegation Edges"
summary: "Connection Hub model for external clients that act for a grantor without becoming the same identity as the grantor."
status: active
tags: ["sdk", "solutions", "connections", "delegated-credentials", "identity-family", "mcp", "memory"]
updated_at: 2026-10-16
keywords: [delegation edge, native authority cast, current roles, grantor, economics, provenance]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
---
# Delegation Edges

A delegated external client is not the same identity as the user who approved
it. It is a representative. Connection Hub models that relationship as a
delegation edge:

```text
delegate identity:
  delegated_client:claude:<delegation-id>

grantor identity:
  platform:<user-id>

allowed:
  resource = exact KDCube surface URL
  grants   = selected delegated grants
  tools    = selected concrete tools/actions
  identity_scope = grantor | grantor_identity_family | selected_identities
```

This is the same connection-edge primitive with a different relationship scope
and stricter grants. A Telegram channel edge and a delegated Claude edge differ
in what the edge permits, not in where the relationship is modeled.

```text
Channel/account edge:
  telegram:100200300 == platform:02e...
  "these identities are the same person"

Delegation edge:
  delegated_client:claude:abc -> platform:02e...
  "this external client may act for this user, only within these constraints"
```

## Runtime Use

There are two different grant sets in a delegated client consent.

```text
Selected service/resource grants
  authority = authority that guards the target service
  example   = memories:read on user-memories MCP
  source    = connections.delegated_credentials.oauth.capabilities
              + resources[*].tools[*].grants
  rule      = show only grants the signed-in KDCube user may delegate

Delegation edges
  authority = authority whose identity the delegate may represent later
  example   = platform:<user-id> with memories:read
  rule      = edge grants must be <= grants available to that identity
```

The current OAuth delegated credential adapter writes the platform edge. During consent the
page shows the selected platform-authority grants explicitly. The authorization
code, access-grant record, and refresh-token record store a server-side
`delegation_edges` list:

```json
[
  {
    "schema": "connection_hub.delegation_edge.v1",
    "authority_id": "platform",
    "identity_ref": "platform:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "grants": ["memories:read"]
  }
]
```

Other authority edges use the same model. Their grants come from that
authority's resolver. For built-in provider authorities, such as Google/Gmail or
Slack, the resolver reads the connected account and returns the grants/scopes
already available to that account. For custom authorities, the bundle-registered
authority module returns the grant inventory. For example:

```json
{
  "schema": "connection_hub.delegation_edge.v1",
  "authority_id": "google.gmail",
  "identity_ref": "google:gmail:account-id",
  "provider": "google",
  "integration_id": "gmail",
  "grants": ["gmail.readonly"],
  "account_label": "user@example.com"
}
```

Those authority-edge grants must be a subset of the grants already available to
the grantor identity at that authority. Connection Hub should not special-case
"external accounts"; platform, Cognito, Google, Slack, and custom bundle
authorities all expose delegable grants through the same authority inventory
contract.

For OAuth delegated credential delegated credentials, the server-side access-grant / refresh
record stores a `kdcube.credential.v1` envelope. The access token is a presented
credential handle/session token; product code must not decode grantor authority
from the token body. The envelope subject is the delegate. The grantor and
identity scope are envelope attributes:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "delegated_client_access",
  "issuer_authority_id": "delegated_client",
  "issuer_authenticator_id": "delegated_client.bearer",
  "subject": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
  "audience": "kdcube:delegated_client",
  "attrs": {
    "grantor_subject": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "client_id": "claude",
    "resource": "https://runtime/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
    "scopes": ["memories:read"],
    "tools": ["memory_search", "memory_get"],
    "identity_scope": "grantor_identity_family"
  }
}
```

The credential envelope identifies the delegated client and target resource.
The delegation edge, target identity mapping, and resource-specific catalogs
such as nested named-service namespace/tool boundaries live in the grant store, not in
the token body.

Product surfaces must not hand-code `grantor_subject` interpretation. They ask
Connection Hub to resolve the delegated identity scope:

```text
delegated credential envelope
  -> delegated_identity_scope_resolve
  -> memory_user_ids / identities allowed by this delegation
```

Identity-scope resolution supplies data-owner ids and charging provenance. Its
response carries no entitlement proof, and caller-supplied role/bypass fields
are ignored. Managed admission establishes economics separately through a
typed, request-local **required-authority cast**:

1. Verify the delegated credential and restore its current Card/Control edge.
2. Map the edge's exact target identity into the platform authority required by
   the KDCube boundary. The Card is the edge; no second connection-edge record
   is created for it.
3. Read that authority's native user record once for the invocation. With a
   bundle-session authority this is `BundleSessionAuthority.get_user`, whose
   roles reflect the last native sign-in or trusted admin write. Integration
   user records and derived platform sessions cannot provide these facts.
4. Bound the Card-selected default/operation role by the resolved native roles,
   using the platform hierarchy; custom roles remain exact capabilities. An
   excessive selection is refused with `grantor_role_changed`. Existing
   resource, operation, Control and account constraints still apply.
5. Pass the admitted effective roles, permissions and budget bypass to runtime
   and billing consumers, retaining actor, target, Card revision and native
   source/freshness provenance. Legacy Cards with no selected role retain only
   the target's non-privileged roles; an empty result creates no baseline role.

A missing target or an unwired native provider returns 403
`target_authority_identity_unresolved`; direct Cognito deployments use this
result until a native IdP lookup is wired. A disabled target is denied, and a
native-store outage returns 503 `target_authority_unavailable`. There is no
fallback to consent metadata or a fabricated registered identity. A native
record is not a live upstream IdP lookup or an immutable writer-audit ledger:
trusted sign-in/admin paths and integration-subject separation own its writes.

New codes, access/refresh bindings and selected edges serialize identity,
grants and provenance, without role/permission/bypass snapshots. Refreshing an
old record strips those entitlement fields from the successor; old records
remain readable but their captured entitlements are ignored. No data migration
or silent role backfill is performed.

The following is an admitted billing projection, not the data-scope operation's
output. The delegate remains the actor; the grantor is the economics subject:

```json
{
  "economics": {
    "schema": "connection_hub.economics_projection.v1",
    "authority_id": "platform",
    "user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "charge_to": "grantor",
    "actor_identity": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "roles": ["kdcube:role:registered"],
    "permissions": ["memories:read"],
    "budget_bypass": false,
    "provenance": {
      "schema": "connection_hub.delegated_actor_provenance.v1",
      "delegate_identity": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
      "grantor_user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
      "client_id": "claude",
      "resource": "https://runtime/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
      "grants": ["memories:read"],
      "tools": ["memory_search", "memory_get"],
      "identity_scope": "grantor_identity_family"
    }
  }
}
```

This projection does not carry `user_type`. If old accounting/runtime APIs need
a compact lane label, the runtime derives it from `roles`, `budget_bypass`, and
anonymous state at that boundary.

## Identity Scope

`identity_scope` decides whether a delegated read can cross linked identities.

| Value | Meaning |
|---|---|
| `grantor` | The delegate may act only on the grantor's primary user id. |
| `grantor_identity_family` | The delegate may act on the grantor plus linked runtime identities, such as Telegram or Google identities connected to that platform user. |
| `selected_identities` | Reserved shape for future consent UI where the user chooses individual linked identities. |

Default is `grantor`. A resource must explicitly opt in to
`grantor_identity_family`.

Example for user memories:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*"
          label: "User memories MCP"
          identity_scope: "grantor_identity_family"
          tools:
            memory_search:
              grants: ["memories:read"]
            memory_get:
              grants: ["memories:read"]
```

## Guardrails

- A delegation edge is resource-bound. A token for one MCP URL must not work on
  another MCP URL.
- A delegation edge is tool-bound. Consent-selected tools are stored with the
  token and enforced at `tools/call`.
- A delegation edge is grant-bound. Each tool declares required grants; the
  credential must contain them.
- A generic bridge can have nested boundaries. For `kdcube-services@1-0`
  `named_services`, the selected MCP tool grants allow the generic bridge
  itself (`named_services:use`), while the nested namespace catalog decides
  whether a call to `namespace="mem"` also has `memories:read` or
  `memories:write`.
- Each KDCube boundary requires an explicit cast to its platform authority.
  Captured roles and a derived session never prove that target's authority.
- Economics projection is explicit. Product code should charge the projected
  platform grantor using the admitted cast while preserving delegate provenance.
  User-memories MCP consumes that admission result, never scope-operation
  economics. Without it, embedding is not charged and search remains lexical;
  a failed delegated scope lookup cannot widen into native family aggregation.

## Connector UX Metadata Is Not The Edge

MCP connector metadata helps clients render the service:

```text
server icon / website_url
tool title
ToolAnnotations(readOnlyHint, destructiveHint, ...)
server instructions
```

Those fields do not grant authority. The durable edge remains the server-side
grant record: resource, selected tools, selected grants, identity scope, grantor
identity mapping, and any nested namespace catalogs. Clients may use metadata to
group tools visually, but the managed guard must enforce the stored edge on
every `tools/call`.
