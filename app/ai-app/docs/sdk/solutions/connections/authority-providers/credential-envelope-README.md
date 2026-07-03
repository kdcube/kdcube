---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/credential-envelope-README.md
title: "Authority Credential Envelope"
summary: "Canonical kdcube.credential.v1 shape used to route tokens and proofs to reachable authority providers."
status: active
tags: ["sdk", "solutions", "connections", "authority-provider", "credential", "delegated-connections", "data-bus", "oauth"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
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
    "identity": "telegram:100200300",
    "actor_user_id": "telegram_100200300"
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
| `audience` | Surface family the credential targets, for example `kdcube:data_bus`, `kdcube:delegated_client`, or `bundle:<id>`. |
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

## Platform Browser Sessions And Envelopes

Not every credential used by a platform authority is a
`kdcube.credential.v1` envelope.

`kdcube.credential.v1` is the KDCube-issued self-description used when KDCube
needs to route a credential through Connection Hub authority/provider metadata.
External provider tokens remain in their provider-native shape and are verified
by the selected platform provider.

| Platform provider method | Browser credential | Envelope relationship |
| --- | --- | --- |
| Cognito / multi-Cognito | Cognito/OIDC access token in `AUTH_TOKEN_COOKIE_NAME`; Cognito/OIDC ID token in `ID_TOKEN_COOKIE_NAME`. | External JWTs. They are not rewritten into a KDCube envelope in the browser. The selected Connection Hub provider tells the runtime which Cognito verifier/trust list to use. |
| SimpleIDP | Simple platform token in `AUTH_TOKEN_COOKIE_NAME` or Authorization header. | Local/simple platform credential. It does not require an envelope unless a future issuer explicitly adds one. |
| Bundle-hosted platform session | KDCube `kst1` bundle-session token in `AUTH_TOKEN_COOKIE_NAME`. | KDCube-issued session credential. The bundle-session authority/runtime may include envelope metadata for routing and diagnostics, but browser clients treat it as the platform auth/session token. |
| Delegated external client | KDCube `kst1` delegated-client token in Authorization bearer. | KDCube-issued delegated credential with explicit `delegated_client_access` envelope/grant metadata. |

The browser-facing contract comes from `/api/cp-frontend-config`. Clients should
use its `authType`, `oidcConfig`, `loginUrl`, `profileUrl`, `logoutUrl`, and
cookie names rather than attempting to infer credential kind from token shape.

Switching platform providers on the same origin can leave stale cookies from the
previous provider. Always verify the expected cookie set and `/profile` after a
switch:

- Cognito/multi-Cognito: access token cookie and ID token cookie;
- SimpleIDP: simple platform token cookie or Authorization header;
- bundle-session: platform auth/session cookie only; ID token cookie is not
  required.

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

### Delegated Client Access

The delegated client access token is a `kst1` bundle-session token for an integration
representative. Its session claim and grant record include:

```json
{
  "schema": "kdcube.credential.v1",
  "credential_kind": "delegated_client_access",
  "issuer_authority_id": "delegated_client",
  "issuer_authenticator_id": "delegated_client.bearer",
  "subject": "integration:claude:<grantor-sub>",
  "audience": "kdcube:delegated_client",
  "attrs": {
    "client_id": "claude",
    "scopes": ["conversations:read"],
    "tools": ["conversations_export"],
    "resource": "https://runtime.example/api/integrations/bundles/demo/demo/kdcube-services@1-0/public/mcp/conversations"
  }
}
```

The MCP resource server still enforces the grant record. The envelope only
standardizes how this credential is discovered and explained to the authority
runtime.

For generic resources such as `kdcube-services@1-0/public/mcp/named_services`,
the access/refresh grant record also carries server-side data that should not be
trusted from the client:

```text
selected MCP tools
selected grants
identity_scope
grantor authority facts
nested named-service namespace catalog
```

MCP connector metadata such as server icon, website URL, server instructions,
and `ToolAnnotations` is not encoded in the credential envelope. It is
advertised by the MCP server and can be cached by the external client.

### Bundle Custom Authority

A bundle can declare a custom authority provider in its entrypoint:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import authority_provider

class CustomAuthorityBundle:
    @authority_provider(
        authority_id="custom.identity",
        authenticator_id="custom.identity.oauth",
        credential_kinds=["authority_access"],
        audiences=["bundle:custom-app@1-0"],
        label="Custom Identity",
    )
    async def custom_identity_provider(self):
        return self.custom_authority_provider
```

On proc load, the manifest declaration is registered into Redis authority
discovery. Runtime verification still requires the declaring bundle to be
reachable in the process.
