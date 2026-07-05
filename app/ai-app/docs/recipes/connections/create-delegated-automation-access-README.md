---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/create-delegated-automation-access-README.md
title: "Create Delegated Automation Access"
summary: "Configure and use Connection Hub delegated access tokens for scripts, agents, and DevOps automation that represent a KDCube platform user."
status: active
tags: ["connection-hub", "delegated-credentials", "automation", "resources", "roles"]
updated_at: 2026-07-05
see_also:
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
  -> opens Delegated Access
  -> chooses grants inside one or more resources
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

Issued records store selected `operations`. MCP surfaces may present those
operations as MCP tools at the protocol edge, but the delegated access model is
resource/operation based.

For the all-resource admin scope, the shared Connection Hub authentication
surface accepts the token only when:

- the configured resource scope is `*`;
- the token carries the configured `kdcube:role:super-admin` grant;
- the grantor authority projects `kdcube:role:super-admin` as a platform role.

The route then receives a normal projected platform `UserSession` for the
grantor, with delegated provenance in `identity_authority`.

## Testing

1. Sign in as a regular user and open Connection Hub -> Delegated Access.
   Confirm all-resource admin scope is absent.
2. Sign in as a platform admin and open Connection Hub -> Delegated Access.
   Confirm `All platform and application APIs` is visible and marked `admin`.
3. Create a short-lived token for a concrete resource and call that resource
   with `Authorization: Bearer ...`.
4. Create an admin all-resource token and call a platform or application API
   that normally requires the admin role.
5. Confirm logs show delegated runtime projection and that
   `identity_authority.delegate_identity` records the automation actor.

Revoke the token in Connection Hub after testing.
