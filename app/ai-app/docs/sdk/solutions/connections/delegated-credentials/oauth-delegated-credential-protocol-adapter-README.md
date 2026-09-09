---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
title: "OAuth Delegated Credential Protocol Adapter"
summary: "Points from KDCube's OAuth host routes to Connection Hub's canonical delegated-credential protocol contract."
status: active
tags: ["sdk", "connections", "connection-hub", "oauth", "delegated-credentials"]
keywords: ["OAuth2 authorization server", "PKCE", "CIMD", "dynamic client registration", "client metadata", "Connection Hub"]
updated_at: 2026-09-09
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

The KDCube DCR host accepts the bounded public registration metadata defined by
that canonical contract, persists it with the client snapshot, and passes it to
the resulting Card. The fields are owner-facing identification only; KDCube
does not project them into caller identity or policy.
