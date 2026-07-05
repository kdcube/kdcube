---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/journal/2026-07-05-managed-rest-guard.md
title: "Managed REST guard"
summary: "Connection Hub delegated credentials now protect application REST operations through a REST-specific guard, not generic platform auth or MCP internals."
status: active
tags: ["connection-hub", "delegated-credentials", "rest", "automation"]
---

# Managed REST Guard

Date: 2026-07-05

## Decision

Managed delegated access is no longer MCP-only. Application REST operations can
now be protected with a REST-specific Connection Hub guard.

The guard is configured on the application REST surface:

```yaml
surfaces:
  as_provider:
    api:
      public:
        records_export:
          POST:
            auth:
              mode: managed
              authority_id: delegated_client
              selected_operation_grants: true
              operations:
                records_export:
                  grants: [records:read]
```

The delegable resource and operation catalog stays in Connection Hub:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: "*/api/integrations/bundles/*/*/records@1-0/public/records_export*"
          operations:
            records_export:
              grants: [records:read]
```

## Boundary

This is not generic platform auth. It is also not MCP auth reused from REST.

At request time:

1. Proc resolves the application REST endpoint and its descriptor `auth`.
2. If `auth.mode: managed`, proc calls the Connection Hub REST guard.
3. The guard validates:
   - bearer token;
   - delegated credential authority;
   - protected resource URL;
   - required grants;
   - selected operation consent when configured.
4. Proc projects the delegated grantor identity into `UserSession` and
   `ExternalEventPayload`.
5. The application operation runs with the projected platform-user context.

## Platform Authority Orthogonality

The managed REST guard does not care whether the approving KDCube user originally
authenticated through Cognito, multi-Cognito, or an application-hosted platform
authority. Those providers establish the grantor platform identity before
consent. The REST guard consumes only the delegated credential record issued by
Connection Hub.

## Implementation

- SDK guard:
  `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.surface_guard`
- Proc bridge:
  `kdcube_ai_app.apps.chat.proc.rest.integrations.integrations`
- Descriptor parser accepts `operations` as a resource catalog alias alongside
  existing `tools`/`actions` for delegated resources.

## Verification

Focused tests cover:

- managed REST guard accepts a consented operation;
- managed REST guard rejects an unconsented operation;
- Connection Hub resource catalog can provide operation grants;
- existing managed MCP behavior remains intact.
