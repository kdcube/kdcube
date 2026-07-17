---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
title: "Create Delegated Automation Access"
summary: "Configure and use Connection Hub delegated access tokens for scripts, agents, and DevOps automation that represent a KDCube platform user."
status: active
tags: ["connection-hub", "delegated-credentials", "automation", "resources", "roles", "mcp", "named-services", "least-privilege"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-rest-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
---
# Create Delegated Automation Access

Use this recipe when a signed-in KDCube user wants to create a short-lived bearer
token for an automation, script, or external agent that will act on that user's
behalf.

This is not a provider account connection such as Gmail or Slack. It is a
KDCube-issued delegated-client credential for entering KDCube resources.

## Concepts

```text
platform role/grant
  -> authority value the user may delegate

resource
  -> protected KDCube surface where the token may be used

operation
  -> concrete action inside that resource

delegated access token
  -> bearer credential representing an automation for the grantor user

resource_grants
  -> the stored resource-to-grants map for one issued credential

named_service_operations
  -> exact existing namespace operations selected inside a named-services MCP
     resource; resource -> namespace -> operation[]
```

Platform roles are grants in this model. For example,
`kdcube:role:super-admin` is the platform authority grant that allows an admin
to delegate admin authority.

## Configure Grants And Resources

Connection Hub owns the grant and resource registry:

```yaml
bundles:
  items:
    - id: connection-hub@1-0
      config:
        connections:
          delegated_credentials:
            oauth:
              enabled: true
              capabilities:
                - grant: kdcube:role:super-admin
                  label: Use all platform and application APIs
                  description: Admin-only delegated automation access to platform and application APIs.
                  delegable_roles:
                    - kdcube:role:super-admin
                - grant: records:read
                  label: Read records
                  delegable_roles:
                    - kdcube:role:registered
                    - kdcube:role:super-admin
              resources:
                - resource: "*"
                  label: All platform and application APIs
                  admin_only: true
                  grants:
                    - kdcube:role:super-admin
                - resource: "*/api/integrations/bundles/*/*/records@1-0/public/records_export*"
                  label: Records REST API
                  grants:
                    - records:read
                  operations:
                    records_export:
                      label: Export records
                      grants:
                        - records:read
```

`resources` are not arbitrary UI labels. They are the resource scopes that
runtime guards match against incoming request URLs.

`resource: "*"` is the all-resource scope. It is admin-only and should require
the `kdcube:role:super-admin` grant. Non-admin users do not see this resource in
the Connection Hub widget and cannot mint it.

## User Flow

```text
User signs into KDCube
  -> opens Connection Hub
  -> opens Delegated by KDCube
  -> opens Create automation access
  -> chooses grants inside one or more resources
  -> for a named-services MCP resource, chooses exact namespace operations
  -> completes any shown provider-account prerequisite in Delegated to KDCube
  -> creates a token
  -> copies the Bearer header once
```

Connection Hub stores token metadata in the delegated credential grant store.
The UI does not keep showing the raw token after creation.

The issued credential does not store a separate resource list. The boundary is
stored as a resource-to-grants map:

```json
{
  "resource_grants": {
    "*": ["kdcube:role:super-admin"],
    "*/api/integrations/bundles/*/*/records@1-0/public/records_export*": ["records:read"]
  }
}
```

Any displayed list of resources is derived from the keys of this map. Runtime
guards also derive the matchable resource set from this map, so the resource and
its delegated grants cannot drift apart.

Issued access records shown by Connection Hub expose this map, not a standalone
`resources` field or standalone `grants` field.

### Named-service resources are selected at operation level

When a resource config contains `named_services`, Connection Hub renders the
descriptor's existing namespace/operation tree under that resource. The rows
are real selectors, not read-only documentation:

```text
KDCube named services MCP
  [x] User memories
      [x] object.search    memories:read
      [ ] object.upsert    memories:write
  [x] Slack
      [x] object.search    slack:read
      [ ] object.action    slack:write
          Requires connected Slack claim: slack:post
```

Selecting an operation also selects its declared KDCube grants and the common
MCP entry grant required by the resource. Removing a required grant clears the
affected operation. Connection Hub sends only existing descriptor operation
identifiers:

```json
{
  "resource_grants": {
    "*/kdcube-services@1-0/public/mcp/named_services*": [
      "named_services:use",
      "memories:read",
      "slack:read"
    ]
  },
  "named_service_operations": {
    "*/kdcube-services@1-0/public/mcp/named_services*": {
      "mem": ["object.search"],
      "slack": ["object.search"]
    }
  }
}
```

The backend validates the resource, namespace, operation, and required grants
against the live Connection Hub descriptor. It then stores a narrowed copy of
that resource's existing `named_services` policy in `GrantStore`. The KDCube
Services named-service bridge reads that stored policy, so `mem.object.upsert`,
`slack.object.action`, and every unselected namespace fail through the ordinary
runtime boundary.

Do not invent action-specific grant ids such as
`object.action.post_message`. If a provider publishes nested operations, those
exact operation ids appear nested. If it publishes one `object.action`, that is
the selectable operation.

### Provider-account consent is a separate boundary

A Slack, Gmail, or other provider-backed namespace may publish
`connected_accounts` requirements. Connection Hub shows those requirements
beside the affected namespace operation and links to **Delegated to KDCube**.
They answer a different question:

```text
Delegated by KDCube
  may this automation enter this KDCube resource/namespace/operation?

Delegated to KDCube
  may KDCube use this user's connected provider account with these claims?
```

The automation token never contains the Slack/Gmail credential. Provider
discovery metadata is presentation-only and is not copied into the stored
delegated policy. At call time, the named-service provider resolves the
grantor's eligible connected account and checks its provider claim before
calling the upstream API.

The Granted Access list updates live: grants landing out-of-band (an OAuth
consent completing in another tab or client) and revocations push to every
open hub over the widget's federated Data Bus session — see
[Delegated Connections → Live Delivery](../../sdk/solutions/connections/delegated-connections/delegated-connections-README.md#live-delivery-to-open-hubs).

## Hosted-Agent Grants Arrive In The Same Registry

Manual creation is one of three issuance paths into this registry. A hosted
agent (an agent running inside a KDCube app) raises its own consent demand in
chat when it needs a delegated resource; the user's one-click grant creates a
record here keyed to the agent's client identity
(`kdcube-agent:<app>:<agent>`, `source: agent`, shown with an *agent* badge).
One record per (user, agent, resources) — re-consent updates it — and revoking
it in this tab is what unbinds the agent's tool. External OAuth clients (Claude
Code) are the third path. Identity model and the chat grant flow:
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

## Runtime Use

An automation calls KDCube with the issued bearer token:

```bash
curl -sS \
  -H "Authorization: Bearer ${KDCUBE_DELEGATED_TOKEN}" \
  "https://runtime.example/api/profile"
```

For configured REST and MCP resources, the managed guard checks:

- token validity;
- resource match against the keys of the server-side `resource_grants` map;
- grants from `resource_grants` entries that match the current request resource;
- selected operation where applicable;
- projected grantor identity.

For the generic named-services MCP bridge there is one additional, inner check:

```text
outer managed MCP guard
  resource + generic MCP tool + named_services:use
        |
        v
named-service bridge
  stored namespace + selected operation + namespace grants
        |
        v
provider adapter
  grantor's connected-account claim, when the provider requires one
```

Grant checks are resource-scoped. A token with:

```json
{
  "resource_grants": {
    "https://runtime.example/A": ["records:read"],
    "https://runtime.example/B": ["records:write"]
  }
}
```

cannot use `records:write` on resource `A`. Wildcard entries are real matching
entries, so this token can use admin authority on any matching request:

```json
{
  "resource_grants": {
    "*": ["kdcube:role:super-admin"],
    "https://runtime.example/B": ["records:read"]
  }
}
```

Issued records store selected top-level `operations`. Named-service resources
also expose `named_service_operations` and persist the matching narrowed
`named_services` boundary. MCP surfaces may present operations as tools at the
protocol edge, but the delegated access model remains resource/operation based.

For the all-resource admin scope, the shared Connection Hub authentication
surface accepts the token only when:

- the configured resource scope is `*`;
- the token carries the configured `kdcube:role:super-admin` grant;
- the grantor authority projects `kdcube:role:super-admin` as a platform role.

The route then receives a normal projected platform `UserSession` for the
grantor, with delegated provenance in `identity_authority`.

## Testing

1. Sign in as a regular user and open Connection Hub -> Delegated by KDCube.
   Confirm all-resource admin scope is absent.
2. Sign in as a platform admin and open Connection Hub -> Delegated Access.
   Confirm `All platform and application APIs` is visible and marked `admin`.
3. Create a short-lived token for a concrete resource and call that resource
   with `Authorization: Bearer ...`.
4. Create an admin all-resource token and call a platform or application API
   that normally requires the admin role.
5. Confirm logs show delegated runtime projection and that
   `identity_authority.delegate_identity` records the automation actor.
6. For the named-services MCP resource, select only `mem.object.search` and
   create a token. Confirm memory search succeeds while memory upsert, action,
   delete, and every unselected namespace fail closed.
7. Attempt to submit an operation without its declared grant. Confirm creation
   fails instead of widening the token.
8. For a provider-backed namespace, leave the provider account unconnected and
   confirm the UI shows the existing provider/connector/claim prerequisite.
   Complete it through Delegated to KDCube, retry, and confirm the provider
   token never appears in the automation record or response.

Revoke the token in Connection Hub after testing.
