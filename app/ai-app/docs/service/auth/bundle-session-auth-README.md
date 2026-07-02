---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
title: "Bundle Session Auth"
summary: "Platform-recognized login sessions issued by a bundle-owned sign-in flow."
tags: ["service", "auth", "bundle", "session", "sso"]
keywords: ["bundle auth", "session token", "front shell", "login", "logout", "register", "invalidate"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-simple-idp-bridge-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-firewall-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
---
# Bundle Session Auth

Bundle session auth is the platform provider for deployments where a bundle or
front shell owns the external sign-in flow and then issues platform-recognized
browser cookies.

The bundle validates the external identity. The platform owns the session token,
session registry, revocation, role lookup, and gateway authentication.

Use this when a bundle needs to accept identities from Telegram, Google,
another OAuth/OIDC provider, a front shell, or an embedded app and then make the
browser authenticated for normal platform routes such as `/profile`,
`/api/integrations/*`, `/sse`, and `/socket.io`.

## Runtime Shape

```
Browser / front shell
  |
  | POST bundle public login endpoint
  v
Bundle sign-in handler
  |
  | validate external identity
  | call platform bundle-session authority
  v
Redis-backed platform session registry
  |
  | returns kst1 signed session token
  v
Bundle response sets auth cookie
  |
  | browser sends cookie to platform routes
  v
Ingress/proc gateway
  |
  | effective auth provider: session
  | validate kst1 token + Redis session + current user record
  v
Platform UserSession
```

## Implementation Surface

| Surface | Owner | Purpose |
|---|---|---|
| Bundle public endpoint | Bundle | Hosts login/issuer UI or operations and validates upstream identity. Normal browser logout uses the platform logout endpoint. |
| `kdcube_ai_app.auth.bundle` | Platform | Async API used by the bundle to register/login/logout/delete/invalidate sessions. |
| Browser cookies | Connection Hub provider config | Carry the `kst1.*` auth token under configured cookie names. |
| Gateway auth manager | Platform | Validates token, Redis session, user record, and roles on each request. |
| Redis | Platform | Stores active session records, user records, token versions, and session indexes. |
| `secrets.yaml` / secret provider | Deployment | Stores `services.session_token.secret` shared by all validating services. |

## Descriptor Contract

Use `auth.idp: session` for this provider:

```yaml
auth:
  type: "bundle"
  idp: "session"
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: versatile_google_session
    entrypoint: login
```

When `frontend.config.auth.authType` is omitted, `auth.type: bundle` or
`auth.idp: session` derives browser `authType: "bundle"`. That tells the
control-plane client that login is owned by a bundle/front shell and that
platform requests should use the descriptor-configured cookies already present
in the browser.

The browser-facing auth contract is still provider-neutral. A host or scene
should use the URLs returned by `/api/cp-frontend-config`:

| Field | Meaning |
|---|---|
| `auth.loginUrl` | Optional browser login entrypoint resolved from Connection Hub provider metadata. |
| `auth.profileUrl` | Server auth-state endpoint. Default: `/profile`. This is the source of truth for "is the browser logged in?". |
| `auth.logoutUrl` | Server logout endpoint. Default: `/api/platform/logout`. |

The client should not inspect bundle-session cookies directly. Those cookies
are HTTP-only in normal deployments. The client asks `profileUrl`; the platform
gateway resolves the configured authority and returns the current session.

The signing secret is stored in `secrets.yaml`:

```yaml
services:
  session_token:
    secret: "<generated shared signing secret>"
```

The canonical secret lookup key is `services.session_token.secret`. CLI-managed
local runtimes generate this value when absent during init/refresh. Managed
deployments must materialize the same key through their configured secrets
provider. Every ingress/proc worker must read the same value. Secret rotation
is an operational restart boundary: rotate the secret, invalidate active bundle
sessions if needed, and restart workers so all processes verify with one value.

Cookie names are provider-driven. They live on the selected Connection Hub
platform provider, normally under `provider.issuer.cookie` for bundle-session
providers:

| Descriptor field | Browser credential |
|---|---|
| `issuer.cookie.auth_token_cookie_name` | Auth/access cookie consumed by the gateway. |
| `issuer.cookie.id_token_cookie_name` | Optional identity cookie. For bundle session auth it can carry the same `kst1.*` token when a frontend expects both cookies. |

In bundle code, read these names from settings:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_settings


auth_cookie = get_settings().AUTH.AUTH_TOKEN_COOKIE_NAME
id_cookie = get_settings().AUTH.ID_TOKEN_COOKIE_NAME
```

## Storage Surfaces

Bundle session auth uses Redis as mutable runtime storage. Key names are
tenant/project namespaced.

| Storage | Shape | Lifetime | Used by |
|---|---|---|---|
| User record | `{tenant}:{project}:kdcube:auth:bundle-session:user:{sub}` | Until delete | Gateway validation and role freshness. |
| Session record | `{tenant}:{project}:kdcube:auth:bundle-session:session:{sid}` | Session TTL | Token activation, logout, and token hash match. |
| User sessions set | `{tenant}:{project}:kdcube:auth:bundle-session:user-sessions:{sub}` | Session TTL window | Invalidate/delete all sessions for a subject. |
| User version | `{tenant}:{project}:kdcube:auth:bundle-session:user-version:{sub}` | Until delete | Role/session revocation boundary. |
| Signing secret | `services.session_token.secret` | Deployment secret lifecycle | HMAC signature verification. |
| Browser auth cookie | descriptor-configured name | Cookie lifecycle | Transport from browser to gateway. |

Validation reads the current user record. A role update made through
`register_user(...)` is reflected on the next request without waiting for the
browser cookie to expire.

## Bundle API

Import the authority from `kdcube_ai_app.auth.bundle`:

```python
from kdcube_ai_app.auth.bundle import get_bundle_session_authority


authority = get_bundle_session_authority()
```

The API is fully async.

## Public Bundle Endpoint Pattern

Expose the login endpoint on a public bundle route. The endpoint is public
because the user is not authenticated by the platform before the external
identity has been validated.

```python
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.bundle import get_bundle_session_authority
from kdcube_ai_app.infra.plugin.bundle_loader import api


@api(method="POST", alias="auth_external", route="public")
async def auth_external(self, request=None, **payload):
    external_user = await validate_external_identity(payload)

    authority = get_bundle_session_authority()
    grant = await authority.login_or_register(
        sub=f"{external_user.provider}:{external_user.subject}",
        username=external_user.username,
        email=external_user.email,
        name=external_user.name,
        roles=["kdcube:role:registered"],
        permissions=["kdcube:*:chat:*;read;write"],
        provider=external_user.provider,
        provider_subject=external_user.subject,
    )

    auth_cfg = get_settings().AUTH
    response = JSONResponse(
        {
            "ok": True,
            "session_id": grant.session_id,
            "expires_at": grant.expires_at,
        }
    )
    response.set_cookie(
        auth_cfg.AUTH_TOKEN_COOKIE_NAME,
        grant.token,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        auth_cfg.ID_TOKEN_COOKIE_NAME,
        grant.token,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response
```

### Register Or Update User

```python
user = await authority.register_user(
    sub="google:123",
    username="Alice",
    email="alice@example.test",
    roles=["kdcube:role:registered"],
    permissions=["kdcube:*:chat:*;read;write"],
    provider="google",
    provider_subject="123",
)
```

`sub` is the canonical platform subject. Keep it stable. Accounting,
conversation ownership, and rate-limit identity are derived from this value.

For providers with stable external subjects, use a deterministic subject shape:

| Provider | Example `sub` |
|---|---|
| Telegram | `telegram:123456789` |
| Google | `google:10987654321` |
| OIDC provider | `oidc:<issuer-host>:<subject>` |
| Front shell local account | `front-shell:<account-id>` |

### Login

```python
grant = await authority.login(
    sub="google:123",
    provider="google",
    provider_subject="123",
    ttl_seconds=12 * 3600,
)
```

`grant.token` is the browser auth token. Set it in the descriptor-configured
auth cookie:

```python
response.set_cookie(
    "__Secure-LATC",
    grant.token,
    path="/",
    secure=True,
    httponly=True,
    samesite="lax",
)
```

Use the descriptor-configured cookie name in production code instead of a hard
coded value. When a separate ID cookie is required by a frontend integration, it
can carry the same token for this provider.

### Login Or Register

```python
grant = await authority.login_or_register(
    sub="telegram:42",
    username="Alice",
    roles=["kdcube:role:registered"],
    provider="telegram",
    provider_subject="42",
)
```

Use this when the external provider already proved the identity and the bundle
wants a single call for first login and subsequent login.

## Handshakes

### First Login

```
1. Browser submits external credential
     POST bundle public auth endpoint

2. Bundle validates the credential
     Telegram initData / OAuth code / provider JWT / front-shell session

3. Bundle calls:
     await authority.login_or_register(...)

4. Platform writes:
     user record
     user version
     session record
     user sessions set

5. Bundle response sets:
     auth auth-token cookie = kst1.*
     optional id-token cookie = kst1.*

6. Browser calls:
     GET /profile
     GET /api/integrations/bundles

7. Gateway resolves:
     effective auth provider session
     token -> Redis session -> current user record -> UserSession
```

### Subsequent Request

```
Browser request with auth cookie
  |
  v
token extractor
  |
  v
BundleSessionAuthManager
  |
  +-- verify signature
  +-- read active Redis session
  +-- read current user
  +-- derive roles/user type
  v
route handler / bundle API / SSE / Socket.IO
```

### Logout

```
Browser calls auth.logoutUrl from frontend config
  |
  v
POST /api/platform/logout
  |
  v
Platform reads configured auth cookie
  |
  +-- if provider is bundle-session:
  |     await logout_bundle_session(token=token)
  |
  +-- delete Redis session record
  |
  v
Response clears platform cookies
```

The standard browser logout endpoint is:

```text
POST /api/platform/logout
```

It clears `AUTH_TOKEN_COOKIE_NAME`, `ID_TOKEN_COOKIE_NAME`, and
`MASQUERADED_TOKEN_COOKIE_NAME`. For `auth.idp: session` it also invalidates the
active bundle-session record in Redis. This endpoint is intentionally platform
generic: the browser does not need to know whether the configured authority is
Cognito, bundle-session, or another platform provider.

Bundles do not need to implement logout for the normal browser shell. A bundle
may still expose a branded "signed out" page or an upstream-provider sign-out
flow, but that is UI/provider cleanup. The KDCube platform session must still
end through the generic platform logout endpoint or the same SDK authority
logout primitive.

If a bundle needs a custom logout operation for a non-browser surface, use the
same underlying authority primitive:

```python
@api(method="POST", alias="auth_logout", route="public")
async def auth_logout(self, request=None, **payload):
    auth_cfg = get_settings().AUTH
    token = request.cookies.get(auth_cfg.AUTH_TOKEN_COOKIE_NAME) if request else None
    await get_bundle_session_authority().logout(token=token)

    response = JSONResponse({"ok": True})
    response.delete_cookie(auth_cfg.AUTH_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(auth_cfg.ID_TOKEN_COOKIE_NAME, path="/")
    return response
```

### Role Change Or Admin Promotion

```
Admin action / bundle account operation
  |
  v
await authority.register_user(
    sub=existing_sub,
    roles=[...new roles...],
    permissions=[...new permissions...],
)
  |
  v
Next request reads current user record and uses new roles
```

Use `await authority.invalidate_user(sub)` when existing browser sessions should
be forced to log in again after the role change.

### Delete User

```
Account deletion
  |
  v
await authority.delete_user(sub)
  |
  +-- invalidate active sessions
  +-- remove user record
  |
  v
Existing browser cookies no longer authenticate
```

## Lifecycle API Summary

### Logout API

```python
await authority.logout(token=token_from_cookie)
```

Logout deletes the backing Redis session. The signed cookie is no longer enough
to authenticate.

### Invalidate User API

```python
await authority.invalidate_user("google:123")
```

This increments the user's token version and removes active sessions. Existing
cookies stop working.

### Delete User API

```python
await authority.delete_user("google:123")
```

Delete invalidates sessions and removes the platform user record.

## Validation Path

When `auth.idp: session`, the gateway uses
`BundleSessionAuthManager`.

```
Cookie / header token
  |
  v
BundleSessionAuthManager.authenticate()
  |
  +-- verify kst1 signature with services.session_token.secret
  +-- check exp
  +-- read Redis session by sid
  +-- compare token hash
  +-- compare current user token version
  +-- read current user record
  +-- reject disabled/deleted users
  v
User(username, email, roles, permissions, sub)
```

The token is signed, but Redis is the mutable source of truth. That gives these
properties:

| Operation | Behavior |
|---|---|
| Concurrent login | Safe. Each login writes a separate session key. |
| Concurrent registration | Safe. User upsert is per canonical subject. |
| Logout | Deletes one active session. |
| Invalidate | Revokes all known sessions for the subject and bumps version. |
| Delete | Revokes sessions and removes the user record. |
| Role change | Validation reads the current user record, so roles are not only taken from the cookie. |

## Concurrency Model

All public operations are async and use Redis as the shared coordination
surface.

| Operation | Concurrency behavior |
|---|---|
| `login(...)` | Creates a new independent session id and session record. Parallel logins for the same `sub` are allowed. |
| `register_user(...)` | Upserts the current user record for one canonical `sub`. Parallel updates converge on the last written profile for that subject. |
| `logout(...)` | Deletes one session record by token/session id. Repeated logout calls are safe. |
| `invalidate_user(...)` | Bumps the user version and removes known active session records for the subject. |
| `delete_user(...)` | Runs invalidation and then removes the user record. |

Validation checks the token signature, active session record, token hash,
current user version, and current user record. A stale cookie cannot authenticate
after logout, invalidate, delete, or signing-secret rotation.

## Data Bus Relationship

Bundle session auth is browser/platform authentication. It makes the browser a
known platform user for platform routes.

Data Bus federated tokens are short-lived transport capability tokens for
Socket.IO Data Bus publishing. A public mini app can use both flows:

```
Public mini app
  |
  | login/claim endpoint validates external identity
  v
Bundle
  |
  +-- issue bundle session cookie for platform routes
  |
  +-- issue federated Data Bus token for Socket.IO publish
```

Use [Bundle Federated Auth](../../sdk/bundle/auth-bundle-federated-README.md)
for the Data Bus token claim flow.

## Token Shape

Bundle session cookies use:

```
kst1.<b64url-json-claims>.<b64url-hmac-sha256>
```

Claims include:

| Claim | Purpose |
|---|---|
| `schema` | `kdcube.session_token.v1` |
| `sid` | Redis session id |
| `sub` | Canonical platform subject |
| `provider` / `provider_subject` | External identity source that produced the session |
| `ver` | User token version for revocation |
| `iat` / `exp` | Issue and expiry time |

Bundles should call the platform API instead of minting this token themselves.

## Relationship To Other Auth Flows

| Flow | Purpose |
|---|---|
| Cognito | Platform owns login, registration, MFA, and JWT validation. |
| SimpleIDP bridge | Bundle registers an opaque token in `idp_users.json`; useful for local/embedded simple auth. |
| Bundle session auth | Bundle owns external identity validation; platform owns session tokens and Redis-backed revocation. |
| Federated Data Bus token | Short-lived capability token for Socket.IO Data Bus after an identity is already accepted. |

## Verification

After login, these checks should succeed from the browser or from a container on
the same network:

```bash
curl -i \
  -b '__Secure-LATC=<kst1-token>; __Secure-LITC=<kst1-token>' \
  http://chat-ingress:8010/profile
```

Expected profile shape:

```json
{
  "user_type": "REGISTERED",
  "username": "Alice",
  "email": "alice@example.test"
}
```

Admin users should resolve as `PRIVILEGED`.

If `/profile` is anonymous, check these items in order:

| Check | Expected |
|---|---|
| Descriptor | `auth.idp: session` in `assembly.yaml`. |
| Secret | `services.session_token.secret` exists and is identical for ingress/proc. |
| Cookie name | Browser sends the selected provider auth cookie to the platform origin. |
| Token prefix | Cookie value starts with `kst1.`. |
| Redis session | The backing session key exists until logout/expiry. |
| User record | The user record exists and is not disabled. |
