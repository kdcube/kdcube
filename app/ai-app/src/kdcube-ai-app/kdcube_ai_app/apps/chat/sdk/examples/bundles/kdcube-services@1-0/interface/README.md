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

## MCP Endpoint: Named Services

```text
POST /api/integrations/bundles/{tenant}/{project}/kdcube-services@1-0/public/mcp/named_services
```

Transport: `streamable-http`

Auth: platform-managed delegated credential, configured at:

```text
surfaces.as_provider.mcp.named_services.auth
```

Default outer policy:

```yaml
mode: managed
authority_id: delegated_client
tools:
  named_services_list:
    grants: [named_services:use]
  named_services_about:
    grants: [named_services:use]
  named_services_schema:
    grants: [named_services:use]
  named_services_search:
    grants: [named_services:use]
  named_services_get:
    grants: [named_services:use]
  named_services_call:
    grants: [named_services:use]
selected_tool_grants: true
```

Namespace boundary policy lives in Connection Hub resource metadata, not in the
hosting bundle MCP auth section:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
          named_services:
            namespaces:
              mem:
                label: User memories
                authority_id: delegated_client
                tools:
                  about:
                    operation: provider.about
                    grants: [memories:read]
                  schema:
                    operation: object.schema
                    grants: [memories:read]
                  search:
                    operation: object.search
                    grants: [memories:read]
                  get:
                    operation: object.get
                    grants: [memories:read]
```

Tools:

| Tool | Description |
| --- | --- |
| `named_services_list` | List configured namespaces and per-operation grants. |
| `named_services_about(namespace)` | Read provider about metadata. |
| `named_services_capabilities(namespace)` | Read provider capabilities. |
| `named_services_schema(namespace, object_kind?)` | Read provider object schema. |
| `named_services_search(namespace, query?, limit?, filters_json?)` | Search namespace objects. |
| `named_services_get(namespace, object_ref)` | Read one object by ref. |
| `named_services_upsert(namespace, object_json, ...)` | Create or update one object when `object.upsert` is allowed. |
| `named_services_host_file(namespace, file_ref, ...)` | Host/register one file ref when `object.host_file` is allowed. |
| `named_services_action(namespace, object_ref, action, ...)` | Run a bounded object action when `object.action` is allowed. |
| `named_services_delete(namespace, object_ref, ...)` | Delete/archive one object when `object.delete` is allowed. |
| `named_services_call(operation, namespace, ...)` | Generic operation wrapper. |

When a namespace operation needs a grant that the delegated credential lacks,
the tool returns:

```json
{
  "ok": false,
  "error": "delegated_consent_required",
  "namespace": "mem",
  "operation": "object.schema",
  "required_grants": ["memories:read"],
  "missing_grants": ["memories:read"]
}
```

That result is the provider-boundary signal. It does not guarantee that every
MCP client will automatically open an incremental OAuth flow. For current
Claude-facing resources, include likely namespace grants in the initial
Connection Hub resource metadata as a nested namespace/tool catalog when a
one-step user experience is required:

```yaml
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
```

The protected-resource discovery document exposes this nested catalog as
`kdcube_named_services`, next to the generic `kdcube_tools` list. The OAuth
authorization code, refresh token, and access-grant record then preserve the
same catalog for runtime enforcement.

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
