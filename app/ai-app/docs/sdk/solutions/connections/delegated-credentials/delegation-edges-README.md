---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
title: "Delegation Edges"
summary: "Connection Hub model for external clients that act for a grantor without becoming the same identity as the grantor."
status: active
tags: ["sdk", "solutions", "connections", "delegated-credentials", "identity-family", "mcp", "memory"]
updated_at: 2026-06-29
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
  telegram:434804821 == platform:02e...
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
    "identity_ref": "platform:02e53484-0081-70ce-11c1-e96706b1a182",
    "user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
    "grants": ["memories:read"],
    "roles": ["kdcube:role:chat-user"],
    "permissions": ["memories:read"],
    "economics_budget_bypass": false
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
  "subject": "integration:claude:02e53484-0081-70ce-11c1-e96706b1a182",
  "audience": "kdcube:delegated_client",
  "attrs": {
    "grantor_subject": "02e53484-0081-70ce-11c1-e96706b1a182",
    "client_id": "claude",
    "resource": "https://runtime/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
    "scopes": ["memories:read"],
    "tools": ["memory_search", "memory_get"],
    "identity_scope": "grantor_identity_family"
  }
}
```

The credential envelope identifies the delegated client and target resource.
The delegation edge, grantor authority facts, and resource-specific catalogs
such as nested named-service namespace/tool boundaries live in the grant store, not in
the token body.

Product surfaces must not hand-code `grantor_subject` interpretation. They ask
Connection Hub to resolve the delegated identity scope:

```text
delegated credential envelope
  -> delegated_identity_scope_resolve
  -> memory_user_ids / identities allowed by this delegation
```

The same resolver also returns an economics projection. Grantor roles,
permissions, and budget-bypass facts come from server-side grant metadata
captured at consent time, not from the token body or the minimal credential
envelope. The delegate remains the actor; the grantor is the platform economics
subject:

```json
{
  "economics": {
    "schema": "connection_hub.economics_projection.v1",
    "authority_id": "platform",
    "user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
    "charge_to": "grantor",
    "actor_identity": "integration:claude:02e53484-0081-70ce-11c1-e96706b1a182",
    "roles": ["kdcube:role:super-admin"],
    "permissions": ["memories:read"],
    "budget_bypass": true,
    "provenance": {
      "schema": "connection_hub.delegated_actor_provenance.v1",
      "delegate_identity": "integration:claude:02e53484-0081-70ce-11c1-e96706b1a182",
      "grantor_user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
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
- Cross-authority projection is explicit and future-scoped. A delegated token
  should not silently borrow platform roles at a different authority boundary.
- Economics projection is explicit. Product code should charge the projected
  platform grantor while preserving delegate provenance in accounting metadata.

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
authority facts, and any nested namespace catalogs. Clients may use metadata to
group tools visually, but the managed guard must enforce the stored edge on
every `tools/call`.
