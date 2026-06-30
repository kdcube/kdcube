---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
title: "Authority Provider Runtime"
summary: "Canonical Connection Hub runtime contract for authenticator selection, authority-scoped identities, linkers, grant resolvers, and surface guards."
status: design
tags: ["sdk", "solutions", "connections", "connection-hub", "authority-provider", "authenticator-selector", "surface-guard", "grants"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
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
Connection Hub Authenticator Selector
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
| `authority_id` | The identity/grant realm. Examples: `kdcube.platform`, `custom.identity`, `telegram.kdcube_ref`, `delegated_client`. |
| `authenticator_id` | One verifier for one proof shape. Examples: `kdcube.cognito`, `custom.google_oidc`, `telegram.kdcube_ref.init_data`, `delegated_client.bearer`. |
| Authority Provider | Owns an `authority_id`, identity namespace, grant resolver, linkers, and registered authenticators. |
| Authenticator | Verifies auth material and returns a verified identity under its authority. |
| Connection Hub Authenticator Selector | Chooses authenticator candidates inside Connection Hub. It does not authorize and it does not trust hints as facts. |
| Authority Linker | Maps an identity from one authority to another, or returns null. |
| Grant Resolver | Loads roles, permissions, scopes, tools, or operation grants for an identity under one authority. |
| Surface Guard | Declares required authority/grants and asks the runtime to authorize the request. |

## Request Hints Are Not Truth

Controlled surfaces may include hints to avoid slow or broad selection:

```http
X-KDCube-Auth-Authority-ID: custom.identity
X-KDCube-Auth-Authenticator-ID: custom.google_oidc
```

These are non-secret selector hints. They narrow candidate authenticators, but
identity is proven only when the selected authenticator verifies the
provider-specific proof.

Provider callbacks can carry the same information in query params:

```text
/public/telegram_webhook?integration_id=telegram.kdcube_ref
```

These hints only narrow the candidate list. Truth is produced only by a
successful authenticator verification result.

```text
hint says authority_id=custom.identity
        |
        v
Connection Hub selector tries candidate authenticators under custom.identity
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
      - custom.identity
    authenticator_ids:
      - kdcube.cognito
      - custom.google_oidc
      - delegated_client.bearer
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
  X-KDCube-Auth-Authenticator-ID: delegated_client.bearer

Runtime:
  selector -> delegated_client.bearer
  authenticator -> identity=integration:claude:<grantor>, authority_id=delegated_client
  linker delegated_client -> kdcube.platform if needed
  grant resolver for kdcube.platform
  authorize if required grant is present
```

For a custom authority surface:

```text
Surface Guard:
  required_authority = custom.identity
  required_grants    = [custom:role:admin]

Runtime:
  selector -> custom.google_oidc
  authenticator -> identity=custom:user:123, authority_id=custom.identity
  no platform link required
  grant resolver for custom.identity
  authorize if custom:role:admin is present
```

For a platform surface reached by a custom identity:

```text
Surface Guard:
  required_authority = kdcube.platform

Runtime:
  selector -> custom.google_oidc
  authenticator -> identity=custom:user:123, authority_id=custom.identity
  linker custom.identity -> kdcube.platform
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

Bundle-local custom authorities declare this through the bundle manifest:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import authority_provider

class MyBundle:
    @authority_provider(
        authority_id="custom.identity",
        authenticator_id="custom.identity.oauth",
        credential_kinds=["authority_access"],
        audiences=["bundle:navigator-tg-bot@1-0"],
    )
    async def custom_identity_provider(self):
        return self.custom_authority_provider
```

On proc load, the declaration is published to Redis authority discovery as
metadata. The verifier itself remains reachable only where the bundle is
loaded. Ingress may use the metadata for diagnostics/selection, but it must not
import bundle-local verifier code.

An authenticator result should include:

```json
{
  "authenticated": true,
  "authority_id": "custom.identity",
  "authenticator_id": "custom.google_oidc",
  "identity": {
    "subject": "user:123",
    "ref": "custom.identity:user:123",
    "label": "Sofia"
  },
  "auth_material_type": "google_oidc"
}
```

The grant resolver is authority-owned:

```text
grant_resolver("custom.identity", "user:123")
  -> roles / permissions / scopes / tools
```

The linker never invents grants. It only maps identity across authorities:

```text
linker("custom.identity:user:123", to="kdcube.platform")
  -> "kdcube.platform:02e53484-..."
  -> or null
```

## Credential Envelope

KDCube-issued credentials and delegated grant records use
`kdcube.credential.v1` as their routing envelope. The runtime reads the
untrusted envelope to choose a reachable authority provider, then the provider
performs real verification.

```text
credential envelope
  issuer_authority_id
  issuer_authenticator_id
  credential_kind
  audience
      |
      v
AuthorityRegistry
      |
      +-- reachable provider -> verify
      |
      +-- not reachable -> unresolved/fail closed
```

The canonical schema and examples are in
[Authority Credential Envelope](credential-envelope-README.md).

## Provisioning And Runtime Use

The same model has two lifecycle phases.

```text
Provisioning / consent
  grantor proves authority
    -> user login / channel proof / user or admin consent
    -> connection edge or delegated grant is written
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

- platform auth managers are registered selector authenticators for
  `kdcube.platform`;
- Connection Hub Telegram rows are request authenticators;
- Connection Hub caches authenticator metadata in Redis and still resolves proof,
  links, and grants on each request;
- OAuth delegated credential is the current protocol adapter that registers the
  `delegated_client` authority and `delegated_client.bearer` authenticator for
  managed MCP surfaces;
- `kdcube-services@1-0` uses that authority for the managed `conversations` and
  `named_services` MCP surfaces;
- most surfaces implicitly require `kdcube.platform`.

Target:

- all authenticators declare an `authority_id`;
- surface guards declare required authority and grants;
- delegated credential protocol adapters register authority providers and
  authenticators with the same descriptor/registry shape as request
  authenticators;
- custom deployments such as custom register `custom.identity` as an authority provider;
- platform APIs require `kdcube.platform` only when they truly require platform
  authority.

For MCP clients, authority-provider metadata is not enough for a good UX. The
MCP server should also advertise connector metadata:

```text
FastMCP(..., stateless_http=True, icons=[...], website_url=..., instructions=...)
tool annotations: readOnlyHint / destructiveHint / idempotentHint
```

Those hints help clients such as Claude render icons and group tools, while the
authority provider and grant resolver remain the enforcement path.
