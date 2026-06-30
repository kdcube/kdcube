---
id: kdcube-services@1-0
title: "KDCube Services App"
summary: "Built-in KDCube service surfaces for delegated external clients. The first service exposes a managed MCP conversation export tool for Claude/Yay-style feedback triage."
status: active
tags: ["app", "bundle", "mcp", "connection-hub", "delegated-credentials", "conversations"]
module: entrypoint
singleton: false
primary_surfaces:
  - "MCP endpoint `conversations` — delegated access to conversations_export"
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

## Current Service

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

## Shape

```text
kdcube-services@1-0/
  entrypoint.py                  # thin surface adapter
  surfaces/
    mcp/
      conversations.py           # FastMCP tool registration
  services/
    conversations/
      export.py                  # conversation export product logic
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
