---
id: kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-rest-with-managed-credentials-README.md
title: "Protect REST With Managed Delegated Credentials"
summary: "Configure application REST operations or platform REST resources so external automation can call them with a Connection Hub delegated bearer token."
status: active
tags: ["connection-hub", "delegated-credentials", "rest", "automation", "oauth"]
---

# Protect REST With Managed Delegated Credentials

Use this recipe when an external automation should call an application REST
operation or a configured platform REST resource on behalf of a signed-in
KDCube user.

This is separate from platform login. The approving user may authenticate with
Cognito, multi-Cognito, or an application-hosted platform authority. The REST
caller presents a delegated-client bearer token issued by Connection Hub.

Related docs:

- [OAuth Delegated Credential Protocol Adapter](../../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md)
- [Protect Application MCP With Managed Credentials](protect-bundle-mcp-with-managed-credentials-README.md)
- [Connection Hub Solution](../../sdk/solutions/connections/connection-hub-solution-README.md)

## Flow

```text
KDCube user signs in
  -> Connection Hub consent grants an external automation selected grants/operations
  -> automation receives a delegated-client bearer token
  -> automation calls REST with Authorization: Bearer ...
  -> managed REST guard validates token, resource, grants, operation consent
  -> proc projects the grantor user into UserSession / ExternalEventPayload
  -> application or platform operation runs with delegated platform-user context
```

## Application REST Operation

The application exposes a normal REST operation. The route can be `public` or
`operations`; the important part is the operation auth config. When `auth.mode`
is `managed`, the proc bridge accepts the delegated bearer token and does not
require the caller to also have a browser/platform cookie session.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import api

@api(method="POST", alias="records_export", route="public")
async def records_export(self, **params):
    ...
```

Do not parse delegated bearer tokens in the application operation. The proc
bridge performs delegated credential validation and projects the runtime user
before the operation is invoked.

## Application Descriptor

Declare managed auth on the REST operation under
`surfaces.as_provider.api.<route>.<operation>.<METHOD>.auth`.

```yaml
bundles:
  items:
    - id: records@1-0
      config:
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
                          grants:
                            - records:read
```

`selected_operation_grants: true` means the concrete operation must be present
in the delegated credential grant record. MCP may expose operations as tools at
the protocol edge, but the stored delegated access boundary is operation-centric.

## Connection Hub Delegated Resource

Connection Hub owns the delegable grant catalog and the concrete protected
resource catalog.

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
                - grant: records:read
                  label: Read records
                  description: Read records visible to the approving user.
                  delegable_roles:
                    - kdcube:role:registered
                    - kdcube:role:paid
                    - kdcube:role:privileged
                    - kdcube:role:super-admin
              resources:
                - resource: "*/api/integrations/bundles/*/*/records@1-0/public/records_export*"
                  label: Records REST API
                  operations:
                    records_export:
                      label: Export records
                      description: Export records visible to the approving user.
                      grants:
                        - records:read
```

The resource URL is matched without query parameters. Use the exact public URL
shape that the automation will call.

## Platform REST Resource

For platform-owned APIs there is no application operation descriptor. Connection
Hub still owns the consent and resource catalog, and the shared request-auth
surface turns an accepted delegated bearer into a normal `UserSession`.

Define a concrete platform resource pattern with exactly one operation boundary:

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
                - grant: devops:deploy
                  label: Deploy runtime
                  description: Run selected devops actions for this project.
                  delegable_roles:
                    - kdcube:role:super-admin
              resources:
                - resource: "*/api/platform/admin/redeploy*"
                  label: Platform redeploy API
                  operations:
                    platform_admin_redeploy:
                      label: Redeploy runtime
                      description: Trigger a configured platform redeploy action.
                      grants:
                        - devops:deploy
```

The request-auth layer checks this catalog before normal route handlers run.
If the delegated token is valid, resource-matched, grant-matched, and consented
for `platform_admin_redeploy`, the platform route sees a projected platform
session for the approving user. If the URL is not in the Connection Hub resource
catalog, the bearer token is ignored by Connection Hub and normal platform auth
rules apply.

Keep one operation per platform resource pattern for now. If a single platform
URL needs multiple separately-consented operations, split the resource pattern
or add an explicit selector before enabling automation.

## All-Resource Admin Automation

Platform roles are also authority grants. When an admin explicitly creates a
delegated automation credential for every KDCube resource, use the platform role
grant itself:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: kdcube:role:super-admin
          label: Use all platform and application APIs
          delegable_roles:
            - kdcube:role:super-admin
      resources:
        - resource: "*"
          label: All platform and application APIs
          admin_only: true
          grants:
            - kdcube:role:super-admin
```

The admin role is the grant for this authority. At runtime the shared
Connection Hub authentication surface accepts this token only when the
server-side delegated credential record carries this resource-to-grants
boundary and the stored grantor authority projects the grantor as a platform
admin:

```json
{
  "resource_grants": {
    "*": ["kdcube:role:super-admin"]
  }
}
```

The credential record does not store a sibling resource list. Runtime resource
matching uses the keys of `resource_grants`, so a resource cannot be separated
from the grants delegated for it.

Grant checks use only the `resource_grants` entries that match the current
request resource. This prevents cross-resource leakage: `{A: read, B: write}`
does not allow `write` on `A`. A wildcard entry is a normal matching entry, so
`{"*": ["kdcube:role:super-admin"], "B": ["records:read"]}` still grants admin
authority on `B` because `*` matches that request.

## Call Shape

```bash
curl -sS \
  -H "Authorization: Bearer ${KDCUBE_DELEGATED_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"limit": 5}' \
  "https://runtime.example/api/integrations/bundles/demo/demo/records@1-0/public/records_export"
```

If the token is missing or invalid, the guard returns `401` with OAuth protected
resource metadata in `WWW-Authenticate` when it can derive it. If the grant or
operation is missing, the guard returns `403`.

## What The Application Receives

After a successful guard check:

- `UserSession.user_id` is the grantor platform user id.
- `UserSession.user_type` is derived from the projected grantor roles.
- `UserSession.identity_authority` contains delegated credential provenance,
  approved grants, selected operations, identity scope, and economics
  projection.
- `ExternalEventPayload.user` carries the same projected identity for runtime
  helpers and nested named-service/application calls.

The application should treat this as delegated platform-user context, not as a
raw external-channel user id.

## Testing

1. Configure the REST operation auth and Connection Hub delegated resource for
   application APIs, or configure only the Connection Hub delegated resource for
   platform APIs.
2. Create a delegated access token in Connection Hub with the resource, grant,
   and operation selected.
3. Call the REST operation with `Authorization: Bearer <token>`.
4. Confirm proc logs contain:

```text
[connection-hub.oauth.rest_guard] accepted ...
Managed REST runtime projection applied ...
```

5. Call with an unselected operation or missing grant and confirm the guard
   fails closed with `403`.
