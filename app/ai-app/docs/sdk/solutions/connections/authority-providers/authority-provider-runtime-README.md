---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
title: "Authority Provider Runtime"
summary: "Canonical Connection Hub runtime contract for authenticator selection, authority-scoped identities, linkers, grant resolvers, and surface guards."
status: design
tags: ["sdk", "solutions", "connections", "connection-hub", "authority-provider", "authenticator-selector", "surface-guard", "grants"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
---
# Authority Provider Runtime

This document defines the canonical runtime model for Connection Hub request
auth, custom authorities, delegated connections, and protected surfaces.

The selector selects **authenticators**, not whole authorities. Each
authenticator belongs to an authority provider.

```text
request auth material
  token / cookie / header / signature / Telegram initData / API key
        |
        v
Authenticator Selector
  uses non-trusted hints and request shape to choose verifier candidates
        |
        v
Authenticator
  verifies one concrete proof/credential shape
        |
        v
verified identity under authority_id
        |
        v
Surface Guard
  compares resolved authority with required authority
        |
        +-- same authority -> Grant Resolver
        |
        +-- different authority -> Authority Linker -> Grant Resolver
        |
        v
authorize / reject
```

## Core Terms

| Term | Meaning |
| --- | --- |
| `authority_id` | The identity/grant realm. Examples: `kdcube.platform`, `yey.custom`, `telegram.kdcube_ref`, `oauth_mcp`. |
| `authenticator_id` | One verifier for one proof shape. Examples: `kdcube.cognito`, `yey.google_oidc`, `telegram.kdcube_ref.init_data`, `oauth_mcp.bearer`. |
| Authority Provider | Owns an `authority_id`, identity namespace, grant resolver, linkers, and registered authenticators. |
| Authenticator | Verifies auth material and returns a verified identity under its authority. |
| Authenticator Selector | Chooses authenticator candidates. It does not authorize and it does not trust hints as facts. |
| Authority Linker | Maps an identity from one authority to another, or returns null. |
| Grant Resolver | Loads roles, permissions, scopes, tools, or operation grants for an identity under one authority. |
| Surface Guard | Declares required authority/grants and asks the runtime to authorize the request. |

## Request Hints Are Not Truth

Controlled surfaces may include hints to avoid slow or broad selection:

```http
X-KDCube-Auth-Authority-ID: yey.custom
X-KDCube-Auth-Authenticator-ID: yey.google_oidc
```

During migration, older surfaces may still send
`X-KDCube-Auth-Integration-ID` or `X-KDCube-Auth-Connection-ID`; the runtime may
treat those as selector aliases only. New surfaces should use authority and
authenticator ids.

Provider callbacks can carry the same information in query params:

```text
/public/telegram_webhook?authenticator_id=telegram.kdcube_ref.webhook
```

These hints only narrow the candidate list. Truth is produced only by a
successful authenticator verification result.

```text
hint says authority_id=yey.custom
        |
        v
selector tries candidate authenticators under yey.custom
        |
        v
authenticator verifies token/signature
        |
        v
truth = verified identity + verified authority_id
```

If a hinted authenticator is missing, disabled, or rejects the material, the
request fails closed unless the surface explicitly allows fallback selection.

## Surface Guard Contract

A protected surface should be able to declare:

```yaml
surface_guard:
  required_authority: kdcube.platform
  required_grants:
    - kdcube:role:feedback-reader
  accepted_auth:
    authority_ids:
      - kdcube.platform
      - yey.custom
    authenticator_ids:
      - kdcube.cognito
      - yey.google_oidc
      - oauth_mcp.bearer
```

Current platform surfaces implicitly require `kdcube.platform`. Custom
authority support becomes real when surfaces can declare another
`required_authority`.

## Runtime Authorization

```text
Surface Guard:
  required_authority = kdcube.platform
  required_grants    = [kdcube:role:feedback-reader]

Request:
  Authorization: Bearer <token>
  X-KDCube-Auth-Authenticator-ID: oauth_mcp.bearer

Runtime:
  selector -> oauth_mcp.bearer
  authenticator -> identity=integration:claude:<grantor>, authority_id=oauth_mcp
  linker oauth_mcp -> kdcube.platform if needed
  grant resolver for kdcube.platform
  authorize if required grant is present
```

For a custom Yey surface:

```text
Surface Guard:
  required_authority = yey.custom
  required_grants    = [yey:role:admin]

Runtime:
  selector -> yey.google_oidc
  authenticator -> identity=yey:user:123, authority_id=yey.custom
  no platform link required
  grant resolver for yey.custom
  authorize if yey:role:admin is present
```

For a platform surface reached by a Yey identity:

```text
Surface Guard:
  required_authority = kdcube.platform

Runtime:
  selector -> yey.google_oidc
  authenticator -> identity=yey:user:123, authority_id=yey.custom
  linker yey.custom -> kdcube.platform
  grant resolver for kdcube.platform
  authorize if platform grant is present
```

## Authority Provider Contract

An authority provider should expose:

```text
AuthorityProvider
  authority_id
  authenticators[]
  grant_resolver(identity, requested_grants)
  linkers[to_authority_id]
  optional credential/grant provisioning operations
```

An authenticator result should include:

```json
{
  "authenticated": true,
  "authority_id": "yey.custom",
  "authenticator_id": "yey.google_oidc",
  "identity": {
    "subject": "user:123",
    "ref": "yey.custom:user:123",
    "label": "Sofia"
  },
  "auth_material_type": "google_oidc"
}
```

The grant resolver is authority-owned:

```text
grant_resolver("yey.custom", "user:123")
  -> roles / permissions / scopes / tools
```

The linker never invents grants. It only maps identity across authorities:

```text
linker("yey.custom:user:123", to="kdcube.platform")
  -> "kdcube.platform:02e53484-..."
  -> or null
```

## Provisioning And Runtime Use

The same model has two lifecycle phases.

```text
Provisioning / consent
  grantor proves authority
    -> user login / channel proof / admin consent
    -> identity link or delegated grant is written
    -> credential/capability is issued or stored

Runtime use
  credential/proof arrives later
    -> authenticator verifies it
    -> linker/grant resolver finds stored meaning
    -> authority or capability is produced
    -> allowed actions are enforced
```

## Migration Target

Current state:

- platform auth managers effectively implement `kdcube.platform`;
- Connection Hub Telegram rows are request authenticators;
- OAuth/MCP is a service auth implementation with its own grant store;
- most surfaces implicitly require `kdcube.platform`.

Target:

- all request auth candidates are registered authenticators;
- all authenticators declare an `authority_id`;
- surface guards declare required authority and grants;
- OAuth/MCP becomes the `oauth_mcp` authenticator + grant resolver;
- custom deployments such as Yey register `yey.custom` as an authority provider;
- platform APIs require `kdcube.platform` only when they truly require platform
  authority.
