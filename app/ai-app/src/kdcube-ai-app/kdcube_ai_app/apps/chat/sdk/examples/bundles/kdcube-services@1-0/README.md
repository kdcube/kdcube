---
id: kdcube-services@1-0
title: "KDCube Services App"
summary: "Built-in KDCube service surfaces for delegated external clients. It exposes managed MCP tools for KDCube conversations and configured named-service namespaces."
status: active
tags: ["app", "bundle", "mcp", "connection-hub", "delegated-credentials", "conversations"]
module: entrypoint
singleton: false
primary_surfaces:
  - "MCP endpoint `conversations` — delegated access to conversations_export"
  - "MCP endpoint `named_services` — delegated access to configured named-service namespaces"
links:
  config: config/bundles.template.yaml
  interface: interface/README.md
  design: docs/README.md
---

# KDCube Services App

`kdcube-services@1-0` is the built-in app for KDCube-owned service surfaces that
external clients may access through Connection Hub delegated credentials.

It is intentionally neutral: it is not an "admin bundle" as a whole. Some tools
are admin-only because their descriptor grants are delegable only by admins.
Other future tools can be regular user services.

## Current Services

### Conversations

MCP endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/conversations
```

Tool:

| Tool | Grant | Default delegability |
| --- | --- | --- |
| `conversations_export` | `conversations:read` | `kdcube:role:super-admin` |

This is the platform-native replacement for the old root `/mcp`
conversation-export shortcut used by the Yay/Claude experiment. The OAuth
protocol and consent screen remain Connection Hub responsibilities; this bundle
only owns the protected product surface.

### Named Services

MCP endpoint:

```text
/api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

Tools:

| Tool | Outer Grant | Purpose |
| --- | --- | --- |
| `named_services_list` | `named_services:use` | List namespaces exposed by this MCP surface. |
| `named_services_about` | `named_services:use` | Read provider about metadata. |
| `named_services_capabilities` | `named_services:use` | Read provider capabilities for a configured namespace. |
| `named_services_schema` | `named_services:use` | Read object schema metadata. |
| `named_services_search` | `named_services:use` | Search objects in a configured namespace. |
| `named_services_get` | `named_services:use` | Read one object by ref. |
| `named_services_upsert` | `named_services:use` | Create or update one object if the namespace permits `object.upsert`. |
| `named_services_host_file` | `named_services:use` | Host/register a file ref if the namespace permits `object.host_file`. |
| `named_services_action` | `named_services:use` | Run a bounded object action if the namespace permits `object.action`. |
| `named_services_delete` | `named_services:use` | Delete/archive one object if the namespace permits `object.delete`. |
| `named_services_call` | `named_services:use` | Generic named-service operation wrapper. |

Each namespace can require additional grants per operation. Those namespace
boundaries are not configured in this hosting bundle. Connection Hub owns the
resource consent catalog and persists the approved catalog into the delegated
credential grant record:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          tools:
            named_services_schema:
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
                  get:
                    operation: object.get
                    grants: [memories:read]
              task:
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
                authority_id: delegated_client
                tools:
                  search:
                    operation: object.search
                    grants: [canvas:read]
                  upsert:
                    operation: object.upsert
                    grants: [canvas:write]
```

The outer MCP guard checks the selected MCP tool and `named_services:use`.
The named-services bridge then checks the namespace/operation authority and
grant from the delegated credential grant record before it calls the provider.
If the delegated credential lacks a namespace grant, the tool returns a
structured `delegated_consent_required` payload. Current MCP clients do not
reliably convert that tool result into a new OAuth consent flow, so production
resources should advertise likely namespace grants during initial Connection
Hub consent.

## Shape

```text
kdcube-services@1-0/
  entrypoint.py                  # thin surface adapter
  surfaces/
    mcp/
      conversations.py           # FastMCP tool registration
      named_services.py          # FastMCP named-service bridge registration
  services/
    conversations/
      export.py                  # conversation export product logic
    named_services/
      bridge.py                  # grant-record namespace policy + dispatch
  config/
    bundles.template.yaml
    bundles.secrets.template.yaml
  interface/
    README.md
  docs/
    README.md
    journal/
```

## Auth Model

The `@mcp(..., auth_config="surfaces.as_provider.mcp.conversations.auth")`
decorator points the platform to the descriptor path for the auth policy.
The actual policy is descriptor-owned:

```yaml
surfaces:
  as_provider:
    mcp:
      conversations:
        auth:
          mode: managed
          authority_id: delegated_client
          tools:
            conversations_export:
              grants: [conversations:read]
          selected_tool_grants: true
```

When a client calls the MCP endpoint:

```text
Bearer token
  -> proc managed MCP guard
  -> Connection Hub delegated credential grant record
  -> authority_id + resource + selected tool + required grants
  -> FastMCP tool dispatch
```

The tool does not check roles itself. By the time it runs, the surface guard has
validated the delegated credential and selected tool grant.

The FastMCP surface uses stateless streamable HTTP because the proc bridge
dispatches each bundle MCP request independently.

## Extension Rule

Add future KDCube service families as separate modules and separate MCP aliases.
For example:

```text
services/
  conversations/
  usage/
  users/
```

Each family gets its own `surfaces.as_provider.mcp.<alias>.auth` policy and
Connection Hub resource entry, so consent remains concrete and tool-centric.

For named-service republishing, add namespace boundaries under the Connection
Hub delegated credential resource metadata:
`connections.delegated_credentials.oauth.resources[].named_services.namespaces`.
This keeps the MCP service name aligned with the named-service system while
avoiding a separate MCP server per namespace.
