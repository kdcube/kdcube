# Delegated Access For Automation

Date: 2026-07-05

Status: implemented

## Summary

Connection Hub now exposes a user-facing **Delegated Access** section. A signed-in
platform user can create a short-lived bearer credential for automation. The
credential is bounded to selected configured grants and the matching delegated
operations.

This is for automation representing the KDCube user at KDCube boundaries. It is
not the same as connecting an external provider account such as Gmail or Slack.

## Ownership

The backend mechanism is SDK-owned:

- `apps/chat/sdk/solutions/connections/delegated_credentials/automation_access.py`
- `apps/chat/sdk/solutions/connections/delegated_credentials/oauth/grants.py`
- `apps/chat/sdk/solutions/connections/delegated_credentials/oauth/store.py`

The Connection Hub bundle remains a thin adapter:

- `delegated_access_list`
- `delegated_access_create`
- `delegated_access_revoke`
- `ui/widgets/connections/src/features/delegatedAccess`

## Flow

```text
User opens Connection Hub -> Delegated Access
  -> chooses grants inside configured resources and a TTL
  -> Connection Hub calls SDK AutomationAccessService
  -> SDK checks grant inventory for the current platform user
  -> SDK narrows operations from selected grants
  -> SDK mints delegated-client bearer token
  -> SDK binds token metadata in GrantStore for managed surfaces
  -> widget shows the token once
```

Revocation deletes the metadata record and logs out the underlying bundle
session by `session_id`.

## Notes

- Token policy, grant narrowing, credential metadata, storage, and revocation
  are not implemented in the bundle.
- The feature reuses delegated credential infrastructure, so existing managed
  MCP and delegated-client guards can enforce the issued credentials.
- Issued automation records and delegated credential metadata use
  `resource_grants` as the canonical boundary: `resource -> grants[]`.
  There is no separately persisted resource list for issued access.
- Managed guards derive the matchable resource set from the keys of
  `resource_grants` and read the full record from the server-side `GrantStore`
  by access-token hash.
- Managed guards compute available grants from the `resource_grants` entries
  that match the current request resource. A token with `{A: read, B: write}`
  cannot use `write` on `A`.
- `resource: "*"` is a real matching resource entry. A token with
  `{"*": ["kdcube:role:super-admin"], "B": ["records:read"]}` can still use
  the admin grant on `B`, because both `*` and `B` match that request.
- Issued records use `operations`, not `tools`. MCP remains a tool protocol at
  the edge, but Connection Hub's delegated access model is resource and
  operation based.
- Resources are protected KDCube surfaces. The all-resource scope is expressed
  as `resource: "*"`, is visible only to platform admins, and requires the
  platform role grant `kdcube:role:super-admin`.
- Platform roles are authority grants for delegated access.
- Future user-connected provider integrations, such as Gmail or Slack, remain a
  separate subsystem.
