---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
title: "Protect Bundle MCP With Managed Credentials"
summary: "Recipe for exposing a bundle MCP surface protected by Connection Hub delegated credentials, with tool-centric grants and descriptor-owned policy."
status: active
tags: ["recipes", "connections", "connection-hub", "delegated-credentials", "mcp", "managed-auth", "bundle-surfaces"]
updated_at: 2026-06-30
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/delegated-credential-protocol-adapters-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
---
# Protect Bundle MCP With Managed Credentials

Use this recipe when a bundle exposes MCP tools and wants KDCube/Connection Hub
to manage external-client delegation, consent, token issuance, and tool
enforcement.

This is for cases like:

```text
Claude connects to user memories
Claude connects to KDCube knowledge
another external client connects to a narrow bundle MCP service
```

## Vocabulary

```text
mode: bundle
  Bundle owns auth itself, for example a custom shared header token.

mode: managed
  Platform/Connection Hub owns delegated credential auth.
  The bundle declares the protected surface and tool policy in descriptors.
```

MCP is the resource family, not the auth mechanism. The managed authority is:

```text
authority_id = delegated_client
authenticator_id = delegated_client.bearer
```

## Bundle Entry Point

Expose the MCP endpoint normally from the bundle:

```python
@mcp(
    alias="memories",
    route="public",
    transport="streamable-http",
    auth_config="surfaces.as_provider.mcp.memories.auth",
)
async def memories_mcp(self, request: Request, **kwargs):
    return build_memory_mcp_app(...)
```

The handler should not parse OAuth tokens itself. Managed auth is applied by the
bundle integration bridge and delegated credential guard.

For proc-served bundle MCP, build the FastMCP app as stateless:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("KDCube Memories", stateless_http=True)
```

KDCube's current bundle MCP route is dispatched request-by-request through proc.
FastMCP's stateful streamable HTTP session manager stores `mcp-session-id`
transports in local Python memory, not in Redis/Postgres. That is not stable for
fresh app instances, multiple proc workers, container restarts, or calls routed
to a different machine. Keep durable state in KDCube stores, Connection Hub
credential records, and product storage, not in bundle-local FastMCP session
memory.

## Descriptor Surface

Put the surface under `surfaces.as_provider.mcp`:

```yaml
surfaces:
  as_provider:
    mcp:
      memories:
        route: public
        auth:
          mode: managed
          authority_id: delegated_client
          identity_scope: grantor_identity_family
          tools:
            memory_search:
              grants:
                - memories:read
            memory_get:
              grants:
                - memories:read
```

Each tool declares its required grants. If one tool needs multiple grants, put
the full set on that tool:

```yaml
tools:
  admin_reindex:
    grants:
      - knowledge:read
      - knowledge:maintain
```

Do not model multi-grant tools by duplicating the tool under several grant
rows.

## Connection Hub Delegated Credential Config

Connection Hub defines which grants are delegable and which concrete resources
use them:

```yaml
connections:
  delegated_credentials:
    oauth:
      enabled: true
      public_clients:
        - client_id: claude
          redirect_uris:
            - https://claude.ai/api/mcp/auth_callback
            - http://localhost/callback
      capabilities:
        - grant: memories:read
          label: Read KDCube memories
          description: Read memory notes through delegated MCP tools.
          delegable_roles:
            - kdcube:role:chat-user
            - kdcube:role:paid
            - kdcube:role:privileged
            - kdcube:role:super-admin
      resources:
        - resource: "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*"
          identity_scope: grantor_identity_family
          tools:
            memory_search:
              label: Search memories
              description: Search memory notes visible to the connected user.
              grants:
                - memories:read
            memory_get:
              label: Read one memory
              description: Read a memory note by id.
              grants:
                - memories:read
```

The capability row answers:

```text
who may delegate memories:read?
```

The resource row answers:

```text
which tools exist on this concrete MCP resource,
and which grants does each tool need?
```

## External Client Flow

```text
External client calls MCP resource
  without a delegated credential
        |
        v
MCP bridge returns protected-resource challenge
        |
        v
Client follows Connection Hub OAuth metadata
        |
        v
User signs in to KDCube and consents
        |
        v
Connection Hub issues delegated_client credential
        |
        v
Client calls MCP tools/list or tools/call with Bearer token
        |
        v
Managed guard checks:
  token valid
  resource matches
  selected tool allowed
  tool grants are present
```

## MCP Transport State

Managed credentials do not require a stateful MCP transport. The delegated
credential, consent record, resource match, selected tools, selected grants, and
identity scope are stored by Connection Hub and are checked on every request.
That makes `tools/list` and `tools/call` work even when proc dispatches through
fresh FastMCP app instances.

Stateful MCP would need a different platform component:

```text
external MCP client
  -> KDCube MCP ingress/broker owns mcp-session-id
  -> broker stores/routes protocol session state
  -> proc dispatches stateless tool calls to bundles
```

That broker is not the current bundle MCP route. Until such an ingress-owned MCP
session layer exists, proc-served bundle MCP surfaces should use
`stateless_http=True`.

## What The Bundle Receives

The bundle should receive a request whose delegated credential has already been
validated for the managed surface. Product code may still ask Connection Hub for
identity scope if it needs product aggregation:

```text
delegated_identity_scope_resolve
  credential envelope -> grantor identity / memory_user_ids
```

For example, the memories MCP resource can read the grantor identity family
when `identity_scope = grantor_identity_family`.

## Named-Service MCP Bridge

`kdcube-services@1-0` also exposes a generic MCP surface for configured named
services:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

The MCP service name is `named_services`, and the named-service namespace is a
tool argument:

```text
named_services_schema(namespace="mem")
named_services_search(namespace="mem", query="...")
named_services_get(namespace="mem", object_ref="mem:<id>")
named_services_upsert(namespace="mem", object_json="{...}")
named_services_host_file(namespace="task", file_ref="fi:...")
named_services_action(namespace="mem", object_ref="mem:<id>", action="preview")
named_services_delete(namespace="task", object_ref="task:<id>")
```

The generic named-services MCP server also advertises server-level instructions
to MCP clients. Those instructions tell clients to start with
`named_services_list`, use returned namespaces exactly, inspect
`named_services_capabilities` and `named_services_schema` for unfamiliar
namespaces, and avoid write/action/delete tools unless the user explicitly asks
for them. This matters because generic tools such as `named_services_search`
need a namespace argument; without instructions, a client may guess instead of
discovering the configured namespace catalog.

Claude's own post-connection "Tool permissions" UI is driven by the MCP tools
returned by the MCP server. The read-only/write/delete grouping comes from MCP
`ToolAnnotations`, not from Connection Hub consent. KDCube MCP surfaces should
set `readOnlyHint`, `destructiveHint`, server `icons`, and `website_url` when
building the FastMCP app; the SDK helper
`kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata` provides the
standard KDCube favicon and annotation helpers.

With this generic bridge Claude will show tools such as
`named_services_search` and `named_services_upsert`. Connection Hub consent is
where namespace labels and grants such as "User memories / Write memory" are
shown. To make Claude show namespace-specific tool rows, expose generated tools
such as `mem_search` or `task_upsert`; the generic bridge does not do that yet.

Configure two layers in two owners. The hosting bundle only configures the
generic MCP entry boundary:

```yaml
surfaces:
  as_provider:
    mcp:
      named_services:
        auth:
          mode: managed
          authority_id: delegated_client
          tools:
            named_services_schema:
              grants: [named_services:use]
            named_services_capabilities:
              grants: [named_services:use]
            named_services_host_file:
              grants: [named_services:use]
            named_services_action:
              grants: [named_services:use]
            named_services_delete:
              grants: [named_services:use]
            named_services_upsert:
              grants: [named_services:use]
```

Connection Hub owns the namespace boundary catalog for consent and delegation:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          named_services:
            namespaces:
              mem:
                authority_id: delegated_client
                tools:
                  schema:
                    operation: object.schema
                    grants: [memories:read]
                  search:
                    label: Search memories
                    operation: object.search
                    grants: [memories:read]
                  upsert:
                    label: Write memory
                    description: Create or update a memory note.
                    operation: object.upsert
                    grants: [memories:write]
                  action:
                    label: Memory action
                    operation: object.action
                    grants: [memories:read]
                  delete:
                    label: Delete memory
                    operation: object.delete
                    grants: [memories:write]
              task:
                label: Tasks
                authority_id: delegated_client
                tools:
                  search:
                    label: Search tasks
                    operation: object.search
                    grants: [tasks:read]
                  upsert:
                    label: Write task
                    operation: object.upsert
                    grants: [tasks:write]
                  host_file:
                    label: Host task file
                    operation: object.host_file
                    grants: [tasks:write]
                  delete:
                    label: Delete task
                    operation: object.delete
                    grants: [tasks:write]
              cnv:
                label: Canvas
                authority_id: delegated_client
                tools:
                  search:
                    label: Search canvas
                    operation: object.search
                    grants: [canvas:read]
                  upsert:
                    label: Write canvas
                    operation: object.upsert
                    grants: [canvas:write]
```

The outer managed MCP guard checks the generic MCP tool and
`named_services:use`. The named-services bridge checks the namespace/operation
authority and grant from the delegated credential grant record before calling
the provider. If the delegated credential lacks the inner grant, the tool
returns:

```json
{
  "ok": false,
  "error": "delegated_consent_required",
  "namespace": "mem",
  "operation": "object.schema",
  "missing_grants": ["memories:read"]
}
```

This is the correct lower-boundary signal, but a normal MCP tool result is not
the same as an HTTP OAuth challenge. If the target client does not support
incremental consent from tool results, include likely namespace grants in the
initial Connection Hub resource consent.

For one-step consent, advertise the namespace/tool boundary catalog on the
Connection Hub resource without flattening namespace grants onto the whole
resource:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          tools:
            named_services_schema:
              grants: [named_services:use]
            named_services_search:
              grants: [named_services:use]
            named_services_upsert:
              grants: [named_services:use]
            named_services_host_file:
              grants: [named_services:use]
            named_services_action:
              grants: [named_services:use]
            named_services_delete:
              grants: [named_services:use]
          named_services:
            namespaces:
              mem:
                authority_id: delegated_client
                tools:
                  schema:
                    operation: object.schema
                    grants: [memories:read]
                  search:
                    operation: object.search
                    grants: [memories:read]
                  upsert:
                    operation: object.upsert
                    grants: [memories:write]
                  action:
                    operation: object.action
                    grants: [memories:read]
                  delete:
                    operation: object.delete
                    grants: [memories:write]
              task:
                label: Tasks
                authority_id: delegated_client
                tools:
                  search:
                    operation: object.search
                    grants: [tasks:read]
                  upsert:
                    operation: object.upsert
                    grants: [tasks:write]
                  host_file:
                    operation: object.host_file
                    grants: [tasks:write]
                  delete:
                    operation: object.delete
                    grants: [tasks:write]
              cnv:
                label: Canvas
                authority_id: delegated_client
                tools:
                  search:
                    operation: object.search
                    grants: [canvas:read]
                  upsert:
                    operation: object.upsert
                    grants: [canvas:write]
```

The outer tool grants say what the MCP bridge needs to enter the generic tool.
The nested namespace/tool catalog says which provider boundary consumes
`memories:read`, `memories:write`, `tasks:read`, `tasks:write`, `canvas:read`,
or `canvas:write`. This is what lets `mem`, `task`, and `cnv` require different
grants for the same generic bridge tool.

The protected-resource discovery response keeps these catalogs separate too:

```json
{
  "kdcube_tools": [
    {"name": "named_services_search", "grants": ["named_services:use"]}
  ],
  "kdcube_named_services": {
    "namespaces": {
      "mem": {
        "authority_id": "delegated_client",
        "tools": {
          "search": {"operation": "object.search", "grants": ["memories:read"]}
        }
      }
    }
  }
}
```

## Bundle-Owned MCP Can Still Exist

A bundle can expose a second MCP alias with `mode: bundle`:

```yaml
surfaces:
  as_provider:
    mcp:
      knowledge:
        route: operations
        auth:
          mode: bundle
          header_name: X-Knowledge-MCP-Token
```

Use this when the bundle intentionally owns a private auth contract. Use
`mode: managed` when the surface should participate in Connection Hub delegated
credentials and user consent.

## Minimal Test

```text
1. Configure a managed MCP surface with two read tools.
2. Configure Connection Hub delegated_credentials.oauth capabilities/resources.
3. Call the MCP URL without Authorization.
4. Confirm the response points to Connection Hub OAuth metadata.
5. Complete OAuth from an external client.
6. Consent to only one tool if the UI allows narrowing.
7. Call the selected tool; it succeeds.
8. Call an unselected tool; it fails closed.
9. Confirm logs include delegated_client authority and selected-tool enforcement.
```

## What Not To Do

- Do not put OAuth/delegated-client config under ingress.
- Do not call this mechanism by old branch names; OAuth is only the protocol
  adapter.
- Do not hardcode tool grants in the MCP handler.
- Do not rely on bundle-local stateful FastMCP sessions for proc-served bundle
  MCP. Use `stateless_http=True`.
- Do not let a token with a broad grant call tools the user did not select.
- Do not use `mode: managed` for a bundle-owned shared-secret endpoint.
