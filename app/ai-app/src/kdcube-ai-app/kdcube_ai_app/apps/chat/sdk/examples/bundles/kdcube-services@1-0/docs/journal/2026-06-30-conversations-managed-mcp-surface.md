---
id: kdcube-services@1-0/docs/journal/2026-06-30-conversations-managed-mcp-surface.md
title: "Conversations Managed MCP Surface"
summary: "Introduces the neutral KDCube services bundle and its first managed MCP surface, conversations_export."
status: active
tags: ["kdcube-services", "mcp", "conversations", "delegated-credentials"]
---

# Conversations Managed MCP Surface

## Decision

Create `kdcube-services@1-0` as a neutral KDCube-owned service bundle.

The bundle is not an admin bundle. It can contain user-level and admin-level
service families. Admin behavior is expressed by descriptor delegability and
tool grants, not by the bundle name.

## First Service

```text
kdcube-services@1-0/public/mcp/conversations
  -> conversations_export
  -> ConversationExportService
```

`conversations_export` requires `conversations:read` through the managed MCP
policy:

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

Connection Hub controls whether that grant is delegable by a given signed-in
user. The default policy makes it super-admin-only.

## Why This Exists

The previous Yay/Claude experiment used a root `/mcp` endpoint that combined
OAuth, consent, token storage, and tool execution in one place. KDCube's
product model is different:

```text
Connection Hub OAuth / consent
  -> delegated credential
  -> concrete bundle MCP resource
  -> proc managed MCP guard
  -> service tool
```

This bundle provides the concrete resource that replaces the root `/mcp`
conversation export shortcut.

## Module Shape

```text
entrypoint.py
  thin bundle and surface wiring

surfaces/mcp/conversations.py
  FastMCP tool registration and tool argument schema

services/conversations/export.py
  reusable conversation export product logic
```

Future services should follow the same split.
