---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-simple-idp-bridge-README.md
title: "Bundle SimpleIDP Bridge"
summary: "How a bundle can validate an external sign-in flow and issue platform-recognized SimpleIDP cookies."
tags: ["service", "auth", "simple-idp", "bundle", "sso"]
keywords: ["SimpleIDP", "bundle auth", "external identity", "SSO bridge", "cookie auth", "idp_users.json"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-firewall-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Bundle SimpleIDP Bridge

SimpleIDP can be used as a platform-level identity bridge in local and embedded
deployments. In this pattern, a bundle owns one or more external sign-in flows,
validates the external identity, registers a platform token in the SimpleIDP
registry, and sets the platform auth cookies for the browser.

For new bundle-owned browser login sessions that need logout, delete, and
cluster-wide invalidation, use [Bundle Session Auth](bundle-session-auth-README.md).
This bridge remains the SimpleIDP-specific path.

The browser then reaches platform routes as a normal authenticated platform
session. The gateway still performs platform authentication; the bundle only
creates the SimpleIDP registry record and cookie handoff.

## Runtime Shape

```
Browser / front shell
  |
  | POST /api/integrations/bundles/{tenant}/{project}/{bundle}/public/auth_<provider>
  v
Bundle public auth endpoint
  |
  | validate external credential
  |   - Telegram initData
  |   - Google credential
  |   - OAuth/OIDC code exchange
  |   - another bundle-owned provider
  v
External identity is accepted
  |
  | register SimpleIDP token
  v
SimpleIDP registry utility
  |
  | atomic write + cluster lock + cache reset
  v
idp_users.json
  |
  | response sets platform cookies
  v
Browser sends platform cookies to /profile, /api/integrations/*, /sse, /socket.io
  |
  v
Gateway -> SimpleIDP -> UserSession
```

## Deployment Configuration

The platform auth provider must be SimpleIDP:

`assembly.yaml` selects the provider:

```yaml
auth:
  type: simple
  idp: simple
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: simple
```

`bundles.yaml` carries the provider under
`connection-hub@1-0.config.authority_registry.authorities.kdcube.platform.providers`:

```yaml
simple:
  type: simple_idp
  enabled: true
  authenticator:
    cookie:
      auth_token_cookie_name: __Secure-LATC
      id_token_cookie_name: __Secure-LITC
```

## User Store

The store is `/config/idp_users.json` for every service and every bundle runtime
that registers SimpleIDP users. The path is pinned by the runtime: neither the
Connection Hub provider nor `platform.services.<service>.idp.idp_db_path`
configures it, and the CLI removes the latter when it is present.

The JSON file holds the users. Redis holds only a cache-version counter keyed by
a hash of the path, so a service reading a different file would not see users
registered elsewhere even while observing the same version counter.

`register_simple_idp_user()` writes to this store when called without an explicit
path.

For SimpleIDP, the auth token and ID token cookies can carry the same opaque
platform token. The cookie names come from the selected platform authority
provider:

| Provider field | Runtime purpose |
|---|---|
| `authenticator.cookie.auth_token_cookie_name` | Access/auth token cookie consumed by the gateway. |
| `authenticator.cookie.id_token_cookie_name` | Identity token cookie consumed by the gateway. |

## Bundle Endpoint

Expose the bundle sign-in endpoint on the public bundle route:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import api
from kdcube_ai_app.apps.middleware.simple_idp_registry import register_simple_idp_user


@api(method="POST", alias="auth_external", route="public")
async def auth_external(self, request=None, **payload):
    external_user = await validate_external_identity(payload)
    platform_token = mint_stable_platform_token(external_user)

    await register_simple_idp_user(
        platform_token,
        {
            "sub": external_user.subject,
            "username": external_user.username,
            "email": external_user.email,
            "name": external_user.name,
            "roles": ["kdcube:role:registered"],
            "permissions": ["kdcube:*:chat:*;read;write"],
        },
    )

    response = make_json_response({"ok": True})
    response.set_cookie("__Secure-LATC", platform_token, path="/", secure=True, samesite="lax")
    response.set_cookie("__Secure-LITC", platform_token, path="/", secure=True, samesite="lax")
    return response
```

Use the descriptor-configured cookie names in production code instead of hard
coded names. Admin users are represented by platform roles such as
`kdcube:role:super-admin`; regular authenticated users normally carry
`kdcube:role:registered`.

## Registry Utility

Use `register_simple_idp_user()` instead of direct JSON writes:

```python
from kdcube_ai_app.apps.middleware.simple_idp_registry import (
    register_simple_idp_user,
    get_simple_idp_registry,
)

await register_simple_idp_user(token, user_payload)
```

The utility provides:

| Capability | Behavior |
|---|---|
| Process cache | Auth reads use a process-local cache instead of reading the JSON file on every request. |
| Cache reset | Registration updates the local cache immediately. |
| Cluster invalidation | When Redis is available, registration increments a registry version key so other workers reload. |
| Cluster write lock | Writes use a Redis `SET NX` lock when available. |
| Local fallback | Writes use an advisory file lock and atomic replace when Redis is unavailable. |

Cache refresh path:

```
SimpleIDP.authenticate(token)
  |
  v
SimpleIDPRegistry.get_user(token)
  |
  +-- cache hit + Redis version unchanged --> return user
  |
  +-- cache expired or version changed ----> read idp_users.json once
                                             refresh cache
                                             return user
```

Registration path:

```
register_simple_idp_user(token, user)
  |
  +-- acquire Redis write lock, or file lock fallback
  |
  +-- read current idp_users.json
  |
  +-- upsert token -> user payload
  |
  +-- atomic replace idp_users.json
  |
  +-- bump Redis version key
  |
  +-- refresh local process cache
```

## Token Design

The platform token should be stable for the external subject and should be
derived from a deployment secret, for example:

```
token = "<prefix>-" + hmac_sha256(secret, provider + ":" + external_subject)
```

The token is the lookup key in `idp_users.json`; the value is the platform user
profile and role set:

```json
{
  "generated-token": {
    "sub": "provider:external-subject",
    "username": "User Name",
    "email": "user@example.test",
    "name": "User Name",
    "roles": ["kdcube:role:registered"],
    "permissions": ["kdcube:*:chat:*;read;write"]
  }
}
```

## Verification

After the bundle sign-in response sets cookies, these platform routes should see
the authenticated user:

```bash
curl -i \
  -b '__Secure-LATC=<platform-token>; __Secure-LITC=<platform-token>' \
  http://chat-ingress:8010/profile
```

Expected result:

```json
{
  "user_type": "REGISTERED",
  "username": "User Name"
}
```

Admin users should resolve as `PRIVILEGED`.
