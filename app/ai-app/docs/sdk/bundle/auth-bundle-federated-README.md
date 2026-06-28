---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
title: "Federated Data Bus Session Tokens"
summary: "How a non-browser client claims a short-lived Data Bus token backed by a KDCube UserSession, usually through Connection Hub but also through a bundle-owned issuer when the bundle owns the authority."
status: active
tags: ["sdk", "connections", "connection-hub", "auth", "federated", "telegram", "socketio", "data-bus"]
keywords:
  [
    "federated data bus token",
    "telegram mini app data bus",
    "connection hub claim",
    "socket.io federated token",
    "authority projection",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/channel-first-identity-linking-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
---
# Federated Data Bus Session Tokens

A client can have valid upstream identity material without having a KDCube
browser session. Telegram Mini App is the current example: Telegram supplies
`Telegram.WebApp.initData`, but Socket.IO Data Bus requires a KDCube
`UserSession`.

The standard product flow is Connection Hub-owned:

```text
client with upstream proof
  -> Connection Hub federated_data_bus_claim
Connection Hub
  -> selects configured request authenticator
  -> verifies upstream proof
  -> resolves identity link when present
  -> creates/refreshes UserSession for the actor
  -> stores projected authority on session.identity_authority
  -> issues short-lived Data Bus token
client
  -> Socket.IO connect with auth.federated_token
ingress
  -> verifies token, Redis registration, bundle scope, and backing session
  -> admits Data Bus for that session
```

This is the preferred flow for shared identity providers such as Telegram,
Slack, Google, or OIDC because Connection Hub owns the request-authenticator
registry, identity-link lookup, and authority projection.

The lower-level primitive is still generic: any proc-side code can call
`issue_federated_data_bus_token(...)` after it has verified the upstream proof
and built the correct actor session authority. A direct bundle-owned claim
endpoint is valid when that bundle owns a custom authority. It must emit the
same standardized token/session shape described here; it must not revive the
old token body that duplicated provider identity, roles, permissions, or
subject allowlists inside the signed token.

## Connection Hub Claim Request

The host or iframe calls the Connection Hub public operation with promoted auth
context headers:

```http
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/federated_data_bus_claim
X-Telegram-Init-Data: <Telegram.WebApp.initData>
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
Content-Type: application/json

{ "data": {} }
```

Example response:

```json
{
  "ok": true,
  "schema": "kdcube.federated_token_claim.v1",
  "federated_token": "kst-fed...",
  "session_id": "session-...",
  "expires_at": 1780000000,
  "bundle_id": "connection-hub@1-0"
}
```

`bundle_id` is the Data Bus bundle scope for the live channel. For Connection
Hub link flows, that is `connection-hub@1-0`.

## Direct Bundle-Owned Claim

A bundle-owned issuer uses the same helper and the same token shape:

```python
grant = await issue_federated_data_bus_token(
    request=request,
    tenant=settings.TENANT,
    project=settings.PROJECT,
    bundle_id="custom-authority-app@1-0",
    user_id=actor_user_id,
    user_type=user_type,
    username=actor_user_id,
    roles=roles,
    permissions=permissions,
    identity_authority=identity_authority,
)
```

The bundle must validate the upstream proof before calling the helper. If it
needs linked platform authority, it must resolve and pass that authority into
`identity_authority`; ingress will not redo provider verification or linkage.

## Session Shape

For an unlinked Telegram user:

```text
UserSession
  user_id     = telegram_434804821
  user_type   = registered
  roles       = []
  permissions = []
```

This is enough to open a low-authority live channel for the link flow. It is
not enough for privileged platform behavior or economics bypass.

After the Telegram identity is linked to a platform user, a new claim projects
platform authority onto the Telegram actor session:

```text
UserSession
  user_id     = telegram_434804821
  user_type   = privileged
  roles       = platform roles
  permissions = platform permissions
  identity_authority
    actor_user_id       = telegram_434804821
    platform_user_id    = 02e53484-...
    economics_user_id   = 02e53484-...
    identity_provider   = telegram
    identity_provider_subject = 434804821
```

The session does not become the browser platform session. It remains the actor
session, with platform authority carried explicitly for role/economics checks.

## Token Shape

The signed token body is intentionally minimal. It carries the Data Bus session
scope plus a nested `kdcube.credential.v1` envelope for authority-runtime
routing:

| Claim | Meaning |
| --- | --- |
| `schema` | Federated token schema id. |
| `jti` | Redis registration id. |
| `sub` | Tenant/project/bundle token subject. |
| `tenant` / `project` | Runtime scope. |
| `bundle_id` | Data Bus bundle scope. |
| `session_id` | Backing `UserSession` id. |
| `allowed_transports` | Contains `data_bus`. |
| `credential` | Standard authority credential envelope. |
| `iat` / `exp` | Issue and expiry timestamps. |

Provider identity, roles, permissions, and linked platform provenance are not
duplicated into the signed token body. They live on the backing `UserSession`.
The only provenance copied into the token is inside `credential.verified_authority`
so the authority SDK can explain what upstream proof produced the derived
session.

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "derived_session",
  "issuer_authority_id": "kdcube.ingress_session",
  "issuer_authenticator_id": "kdcube.signed_active_record",
  "subject": "session:<session_id>",
  "audience": "kdcube:data_bus",
  "session_id": "<session_id>",
  "verified_authority": {
    "actor_user_id": "telegram_434804821",
    "platform_user_id": "02e53484-..."
  }
}
```

See [Authority Credential Envelope](../solutions/connections/authority-providers/credential-envelope-README.md).

## Socket.IO Connection

The client opens Socket.IO with the returned token:

```ts
const socket = io(platformBaseUrl, {
  path: "/socket.io",
  transports: ["websocket"],
  auth: {
    tenant,
    project,
    bundle_id: claim.bundle_id,
    federated_token: claim.federated_token,
  },
});
```

Socket.IO verifies token integrity, Redis registration, scope, and backing
session before accepting the connection. After that, `data_bus.publish` uses
normal actor/reply metadata from the admitted session.

## Link Flow Reconnect

When a Telegram identity becomes linked or unlinked, the widget should reclaim
and reconnect its Data Bus channel. The existing session may have been created
before the link existed, so it may not carry the new projected authority.

```text
connection_hub.identity.link_changed
  -> widget calls federated_data_bus_claim again
  -> widget reconnects Socket.IO with the new token/session
  -> widget refreshes link status
```

## Runtime Secret

The token issuer and Socket.IO verifier use the descriptor-backed service
secret:

```yaml
services:
  federated_token:
    secret: "<generated shared signing secret>"
```

The canonical lookup key is `services.federated_token.secret`. Local CLI
runtimes persist it through the runtime secret path; managed deployments
materialize it through the configured secrets provider.
