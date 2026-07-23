---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
title: "Delegate A KDCube Service To An External Client"
summary: "User-facing recipe for connecting Claude or another external client to a KDCube service through Connection Hub delegated credentials."
status: active
tags: ["recipes", "connections", "connection-hub", "delegated-credentials", "oauth", "claude", "mcp", "consent"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegation-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
---
# Delegate A KDCube Service To An External Client

Use this recipe to understand the user journey when a person connects an
external client, such as Claude, to a KDCube service.

Example:

```text
Connect Claude to KDCube memories.
Claude may search/read memories.
Claude may not receive the user's full KDCube session.
Claude may not call tools that were not consented.
```

## User Journey

```text
1. User opens external client settings
   Example: Claude -> Connectors -> Add local/remote MCP connector

2. User enters a KDCube MCP service URL
   Example:
   https://runtime.example/api/integrations/bundles/demo/demo/
     user-memories@2026-06-26/public/mcp/memories

3. External client probes the service
   KDCube replies that the service requires delegated credentials.
   A client not pre-listed in public_clients registers itself via dynamic
   client registration (DCR); registration is accepted only if the client's
   redirect URI is on the DCR redirect allowlist
   (dynamic_client_registration.allowed_redirect_uris).

4. External client opens the KDCube consent URL
   User signs in to KDCube if needed.

5. Connection Hub shows consent for this concrete service
   Service: KDCube memories
   Grants: memories:read
   Tools:
     - memory_search
     - memory_get
   Identity scope:
     - grantor only, or
     - grantor identity family

6. User approves selected grants/tools

7. Connection Hub returns an OAuth code to the external client

8. External client exchanges the code for a delegated credential

9. External client calls the KDCube service with:
   Authorization: Bearer <delegated credential>

10. KDCube enforces:
    selected resource
    selected tools
    selected grants
    selected identity scope
```

This OAuth connector journey, **Create automation access**, and the hosted-agent
chat consent are three issuance paths over the same Connection Hub resource
catalog, but they are not the same request flow:

```text
external MCP connector
  -> OAuth authorize + consent
  -> selected resource/tools/grants in the OAuth grant record

manual script token
  -> Connection Hub / Delegated by KDCube / Create automation access
  -> resource_grants + exact named_service_operations selection

hosted agent (an agent inside a KDCube app)
  -> consent demand in chat -> one-click grant, per agent
  -> record keyed to the agent's client identity (kdcube-agent:<app>:<agent>)
```

The hosted-agent path is documented in
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

Do not document the manual `named_service_operations` payload as an OAuth
protocol field. OAuth consent renders the descriptor-backed catalog within the
OAuth flow; manual automation creation sends that explicit selector to
`delegated_access_create`.

## What Identity Does The Client Get?

The external client gets its own delegated identity:

```text
authority_id = delegated_client
subject      = integration:claude:<grantor>
```

It does not become the platform browser session.

The delegated credential carries a server-side delegation record:

```text
delegate:
  integration:claude:<grantor>

grantor:
  platform user a1b2c3d4-...

allowed:
  resource = user-memories MCP
  grants   = memories:read
  tools    = memory_search, memory_get
  identity_scope = grantor_identity_family
```

When the target service needs product data owned by connected identities, it
asks Connection Hub to resolve that delegated identity scope. For memories, that
can yield:

```text
memory_user_ids:
  a1b2c3d4-...
  telegram_100200300
```

Only descriptor and consent allow this. The external client does not choose
extra user ids.

## Consent Rules

Connection Hub should show only grants that the signed-in KDCube user may
delegate.

```text
descriptor says:
  memories:read can be delegated by registered, paid, privileged, super-admin

signed-in user has:
  kdcube:role:registered

consent can show:
  memories:read

consent must not show:
  grants that require roles/permissions this user does not have
```

Tool selection is narrower than grant selection:

```text
grant:
  memories:read

tools under that grant:
  memory_search
  memory_get

user selects:
  memory_search only

result:
  token cannot call memory_get
```

For the generic `kdcube-services@1-0/public/mcp/named_services` bridge, there is
one more level because one generic MCP tool accepts a namespace argument:

```text
outer consent
  named_services_search + named_services:use

inner named-service boundary
  mem.object.search    + memories:read
  task.object.search   + tasks:read
  slack.object.action  + slack:post
```

The OAuth consent view displays the configured namespace/operation rows. The
stored named-service policy is enforced again by the bridge at every call; a
generic `named_services_action` tool does not authorize actions in every
namespace.

If the namespace uses Slack, Gmail, or another connected provider, its
provider-account claims remain a second prerequisite under **Delegated to
KDCube** — and the external client additionally needs a per-account binding
(`account_scope`) on its own grant card. That binding is default-closed: the
authorize screen offers the user's connected accounts with per-claim
checkboxes (nothing pre-checked), and an account left unticked yields
`agent_grant_required` at call time, deep-linking the client's card for the
user to grant. The external MCP client gets a KDCube delegated credential,
never the provider token.

## Identity Scope Choices

Services can define the identity scope they support.

```text
grantor
  The external client can see only records owned by the consenting platform user.

grantor_identity_family
  The external client can see records owned by the consenting user's connected
  identities, if the relevant connection edges grant identity:family.
```

For user memories, `grantor_identity_family` is usually the useful scope:

```text
platform memory notes
telegram memory notes
future linked-channel memory notes
```

## End-To-End Diagram

```text
External client
  "I want to use this MCP resource"
        |
        v
KDCube MCP resource
  "Protected. Use Connection Hub OAuth metadata."
        |
        v
Connection Hub OAuth adapter
  browser sign-in + consent
        |
        v
Connection Hub grant store
  access token -> delegated_client credential
  selected resource/tools/grants
  delegation edge to grantor
        |
        v
External client
  calls MCP with Bearer token
        |
        v
Managed surface guard
  verifies token, resource, tool, grant
        |
        v
Product service
  optionally resolves delegated identity scope
        |
        v
Result
```

## What The User Should See

A good consent screen should be concrete:

```text
Connect Claude to KDCube memories

Claude will be allowed to:
  [x] Search memory notes
  [x] Read one memory note

Memory scope:
  [x] Include my connected identities

Claude will not be allowed to:
  - edit memories
  - delete memories
  - access unrelated KDCube services
```

Avoid generic text like "approve OAuth access". The user needs to understand the
service, tools, and identity scope.

## Minimal Test

```text
1. Configure a managed memories MCP resource.
2. Add it as a connector in the external client.
3. Complete KDCube sign-in and consent.
4. In the external client, list tools.
5. Confirm only consented tools are available/usable.
6. Ask the external client to search memories.
7. Confirm it sees only the consented identity scope.
8. Revoke or narrow the grant, then confirm calls fail closed.
9. For the generic named-services bridge, approve one namespace operation and
   confirm a different operation in the same namespace fails closed.
10. For a provider-backed namespace, revoke the user's connected-account claim
    while leaving the MCP grant active; confirm the provider call fails without
    exposing the upstream credential.
```

## What Not To Do

- Do not give the external client the user's browser cookie.
- Do not issue admin/super-admin authority just because the grantor is admin.
- Do not let the client select arbitrary grants or tools.
- Do not accept dynamic client registration with un-allowlisted redirect URIs;
  DCR runs before any user authenticates, so
  `dynamic_client_registration.allowed_redirect_uris` is what keeps an
  authorization code deliverable only to a known app callback or loopback.
  Matching rules (RFC 8252 loopback any-port) are in
  [OAuth Delegated Credential Protocol Adapter](../../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).
- Do not bypass Connection Hub identity-scope resolution in product services.
- Do not describe this as "MCP auth"; it is delegated credentials for a
  protected resource, and MCP is only the first resource family.
