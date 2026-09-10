---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
title: "Create Delegated Automation Access"
summary: "Configure and use Connection Hub delegated access tokens for scripts, agents, and DevOps automation that represent a KDCube platform user."
status: active
tags: ["connection-hub", "delegated-credentials", "automation", "resources", "roles", "mcp", "named-services", "least-privilege"]
updated_at: 2026-08-17
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
  -> opens Access cards
  -> opens Create automation access
  -> chooses grants inside one or more resources
  -> for a named-services MCP resource, chooses exact namespace operations
  -> completes any shown provider-account prerequisite in Accounts
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

### Renew a credential, two ways

A token has a lifetime; the grants do not. When the token expires, the card
stays on **Access cards** with an `expired` badge and every grant, operation
selection, account binding and policy it held, and it can still be edited.
Two ways bring the credential back:

- **Reissue** (manual tokens): a new token on the same card and `access_id`.

  ```text
  User opens Access cards
    -> the card shows "expired" and Reissue token
    -> Reissue, then confirm
    -> copies the new Bearer header once (the previous token no longer works)
    -> the automation runs again with the same access
  ```

  Scripts that reference the card by id, and the once-or-always policies on
  its operations, need no change. Reissuing before expiry is allowed and
  rotates the token. A manual token carries its own end date inside it, so
  it is never prolonged.
- **Prolong** (connected apps such as Claude Code or Claude Desktop): the
  client keeps the token it has, and the card's expiry, the refresh token and
  the current access binding are extended. Offered while the credential has
  not ended yet ("expires soon"); after that the client reconnects, onto the
  same card.

By default the new lifetime equals the previous one; the operation
`delegated_access_renew` also takes `ttl_seconds` and `mode`
(`reissue` or `prolong`). A card that was revoked cannot be renewed. A
hosted agent's card renews itself the next time the agent is granted from
the chat.

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
      [x] object.search    slack:search
      [ ] object.action    slack:post
          (the connected Slack account must have approved slack:post too)
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
      "slack:search"
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
against the live Connection Hub descriptor, then narrows that resource's
existing `named_services` policy to the selection and stores the result on the
access record. Only Connection Hub carries the descriptor, so the boundary is
computed there; the managed guard copies it from the card onto each request,
and the KDCube Services named-service bridge reads it from there. So
`mem.object.upsert`, `slack.object.action`, and every unselected namespace fail
through the ordinary runtime boundary.

Because the card carries the boundary, editing the selection later applies to
the token already issued — see
[Editing a manual token in place](../../sdk/solutions/connections/connection-hub-token-storage-README.md#editing-a-manual-token-in-place).
A descriptor change does not reach a card written earlier; the card is
recomputed on its next save.

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
it in this tab is what unbinds the agent's tool. The record extends the same
demand-driven way it was born: an operation needing claims outside it raises a
scoped demand naming exactly the missing grants, and the one-click approval
MERGES them in; editing in this tab is the narrowing direction — the submitted
claim set replaces the record. External OAuth clients (Claude Code) are the
third path. Identity model and the chat grant flow:
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

For hosted agents that consume this named-services MCP bridge, the connection
declaration should ask only for the door grant, usually `named_services:use`.
Conversation operation grants such as `conversations:read` and provider claims
such as `slack:post` are selected or demanded from the Connection Hub catalog at
operation time; they are not static MCP connection scopes.

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
also expose `named_service_operations` — the selection — and persist the
matching narrowed `named_services` boundary the guard carries to the bridge. MCP surfaces may present operations as tools at the
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
7a. Edit that card: swap the selected operation for another one in the same
   namespace and save. Confirm the SAME bearer now reaches the new operation
   and is refused on the old one, with no token re-issue. Rename the card
   without touching the picker and confirm the narrowing survives; clear every
   operation in a namespace and confirm the namespace fails closed.
8. For a provider-backed namespace, leave the provider account unconnected and
   confirm the UI shows the existing provider/connector/claim prerequisite.
   Complete it through Delegated to KDCube, retry, and confirm the provider
   token never appears in the automation record or response.

Revoke the token in Connection Hub after testing.
