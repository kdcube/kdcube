---
id: kdcube-services@1-0/interface
title: "KDCube Services Interface"
summary: "Public contract for KDCube-owned managed service MCP surfaces."
status: active
tags: ["interface", "mcp", "delegated-credentials", "connection-hub"]
---

# KDCube Services — Interface

## MCP Endpoint: Conversations

```text
POST /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/conversations
```

Transport: `streamable-http`

Auth: platform-managed delegated credential, configured at:

```text
surfaces.as_provider.mcp.conversations.auth
```

Default policy:

```yaml
mode: managed
authority_id: delegated_client
tools:
  conversations_export:
    grants:
      - conversations:read
selected_tool_grants: true
```

`auth_config` in `entrypoint.py` is only the pointer to this policy path.
Descriptors own the actual grants and tool allowlist.

## Tool: `conversations_export`

Purpose: read-only conversation transcript export for feedback triage and
operational review.

Arguments:

| Name | Type | Description |
| --- | --- | --- |
| `since` | string | Optional ISO timestamp. Limits to conversations started at or after this time. |
| `tenant` | string | Optional tenant id. Must be supplied together with `project`. |
| `project` | string | Optional project id. Must be supplied together with `tenant`. |
| `limit` | integer | Maximum returned conversation records. Clamped to `1..500`. |

Result:

```json
{
  "ok": true,
  "count": 10,
  "total_available": 10,
  "limited": false,
  "conversations": []
}
```

Each conversation record contains:

```text
conversation_id
tenant
project
user_id
source
started_at
title
turns[]
```

Each turn contains:

```text
turn_id
ts
user
assistant
attachments[]
citations[]
```

## Consent And Resource Metadata

Connection Hub must include a resource entry matching the endpoint URL:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/conversations*"
          label: "KDCube conversations MCP"
          tools:
            conversations_export:
              label: "Export conversations"
              description: "Read conversation transcripts for feedback triage."
              grants:
                - conversations:read
```

The capability grant remains separately configured:

```yaml
capabilities:
  - grant: conversations:read
    delegable_roles:
      - kdcube:role:super-admin
```

This split lets Connection Hub show concrete tools for the requested resource
while still checking whether the approving user may delegate each grant.

## Dataflow

```text
Claude / external MCP client
  -> resource URL: /public/mcp/conversations
  -> discovers Connection Hub OAuth metadata
  -> user signs in to KDCube
  -> consent screen shows conversations_export
  -> access token is issued with selected tool + conversations:read
  -> MCP tools/list / tools/call
  -> proc managed MCP guard validates token/resource/tool/grant
  -> kdcube-services FastMCP tool
  -> ConversationExportService
  -> control-plane conversation store
```
