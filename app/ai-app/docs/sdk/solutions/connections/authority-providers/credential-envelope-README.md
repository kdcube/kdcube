---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
title: "Authority Credential Envelope"
summary: "Canonical kdcube.credential.v1 shape used to route tokens and proofs to reachable authority providers."
status: active
tags: ["sdk", "solutions", "connections", "authority-provider", "credential", "delegated-connections", "data-bus", "oauth"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
---
# Authority Credential Envelope

`kdcube.credential.v1` is the common self-description carried by KDCube-issued
credentials and stored with delegated grants. It is not authorization by
itself. It tells the Connection Hub authority SDK which authority provider and
authenticator can attempt verification.

```json
{
  "schema": "kdcube.credential.v1",
  "credential_id": "cred_or_jti",
  "credential_kind": "derived_session",
  "issuer_authority_id": "kdcube.ingress_session",
  "issuer_authenticator_id": "kdcube.signed_active_record",
  "subject": "session:sess_123",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "audience": "kdcube:data_bus",
  "session_id": "sess_123",
  "verified_authority": {
    "authority_id": "telegram.kdcube_ref",
    "authenticator_id": "telegram.kdcube_ref.init_data",
    "identity": "telegram:434804821",
    "actor_user_id": "telegram_434804821"
  },
  "attrs": {},
  "iat": 1780000000,
  "exp": 1780000900
}
```

## Required Routing Fields

| Field | Purpose |
| --- | --- |
| `schema` | Must be `kdcube.credential.v1`. |
| `credential_kind` | What kind of credential this is, for example `derived_session`, `delegated_client_access`, or `authority_access`. |
| `issuer_authority_id` | Authority that issued or owns verification for this credential. |
| `issuer_authenticator_id` | Concrete authenticator/verifier inside that authority. |
| `subject` | Subject in the issuing authority. |
| `audience` | Surface family the credential targets, for example `kdcube:data_bus`, `kdcube:mcp`, or `bundle:<id>`. |
| `tenant` / `project` | Runtime namespace for storage and lookup. |
| `iat` / `exp` | Issuance and expiry hints. Verifiers still enforce their authoritative state. |

`verified_authority` is used when the credential is derived from an upstream
proof, such as a Telegram actor that already passed request authentication.
`attrs` carries non-secret verifier metadata such as client id, scopes, or
selected tools when the issuing authority needs it.

## Runtime Rule

```text
credential/proof arrives
      |
      v
read untrusted envelope hints
  issuer_authority_id
  issuer_authenticator_id
  audience
      |
      v
local authority registry
      |
      +-- provider reachable here -> verify
      |
      +-- provider not reachable here -> unresolved/fail closed
```

Reachability is intentional:

- `kdcube.ingress_session` is built in and can be verified on ingress and proc.
- Bundle-declared custom authorities are reachable only where the declaring
  bundle is loaded, normally proc.
- Redis discovery records can expose authority metadata across runtimes without
  importing bundle verifier code.

## Current Credential Kinds

### Data Bus Federated Session

The federated Data Bus token is still a `kft1` token, but its claims include a
nested `kdcube.credential.v1` envelope:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "derived_session",
  "issuer_authority_id": "kdcube.ingress_session",
  "issuer_authenticator_id": "kdcube.signed_active_record",
  "subject": "session:sess_123",
  "audience": "kdcube:data_bus",
  "session_id": "sess_123"
}
```

Ingress verifies the signed token and active Redis record, then joins the
stored session. It does not run Telegram, Slack, or bundle-local custom
authority code.

### OAuth/MCP Delegated Client Access

The OAuth/MCP access token is a `kst1` bundle-session token for an integration
representative. Its session claim and grant record include:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "delegated_client_access",
  "issuer_authority_id": "oauth_mcp",
  "issuer_authenticator_id": "oauth_mcp.bearer",
  "subject": "integration:claude:<grantor-sub>",
  "audience": "kdcube:mcp",
  "attrs": {
    "client_id": "claude",
    "scopes": ["conversations:read"],
    "tools": ["conversations_export"]
  }
}
```

The MCP resource server still enforces the grant record. The envelope only
standardizes how this credential is discovered and explained to the authority
runtime.

### Bundle Custom Authority

A bundle can declare a custom authority provider in its entrypoint:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import authority_provider

class NavigatorBundle:
    @authority_provider(
        authority_id="yay.identity",
        authenticator_id="yay.identity.oauth",
        credential_kinds=["authority_access"],
        audiences=["bundle:navigator-tg-bot@1-0"],
        label="Yay Identity",
    )
    async def yay_identity_provider(self):
        return self.yay_authority_provider
```

On proc load, the manifest declaration is registered into Redis authority
discovery. Runtime verification still requires the declaring bundle to be
reachable in the process.
