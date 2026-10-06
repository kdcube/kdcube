---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
title: "OAuth Delegated Credential Protocol Adapter"
summary: "Points from KDCube's OAuth host routes to Connection Hub's canonical delegated-credential protocol contract."
status: active
tags: ["sdk", "connections", "connection-hub", "oauth", "delegated-credentials"]
keywords: ["OAuth2 authorization server", "PKCE", "CIMD", "dynamic client registration", "client metadata", "Connection Hub"]
updated_at: 2026-10-06
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/oauth-delegated-credential-protocol.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---

# OAuth Delegated Credential Protocol Adapter

Connection Hub owns the canonical client resolution, PKCE, authorization, consent,
credential issuance, card lookup, and revocation contract. Read
[OAuth Delegated Credential Protocol](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/oauth-delegated-credential-protocol.md).

KDCube remains the first protocol host. Its authentication, descriptor, and MCP
recipes describe how the Connection Hub state machine is mounted on KDCube public
operations and guarded application surfaces.

Portable callers import consent and protocol policy from
`connection_hub.delegated_credentials.oauth`. The KDCube module
`kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.consent`
is a compatibility alias for split-era consumers; both paths resolve to the same
canonical module object.

The KDCube DCR host accepts the bounded public registration metadata defined by
that canonical contract, persists it with the client snapshot, and passes it to
the resulting Card. The fields are owner-facing identification only; KDCube
does not project them into caller identity or policy.

## Human consent admission

The KDCube host admits verified human platform sessions to authorization,
device verification, consent draft, consent decision, and consent submission.
An authenticated external-client principal whose verified identity starts with
`integration:` receives HTTP 403 with `oauth_human_consent_required` at their
shared admission boundary, before any consent draft is read or consumed, device
request approved, authorization code created, or credential minted. Both
Card-bound and unbound integration credentials receive that refusal.

This check uses the authenticator's subject fields (`sub`, `user_id`, `id`).
Display names and caller-supplied request data do not classify a principal.
Ordinary human login and delegated service authentication retain their existing
contracts; a client asks its human operator to complete authorization in their
own session. The host's broader platform-login policy remains a separate
boundary.
