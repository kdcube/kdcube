---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/delegated-authority-and-admission-README.md
title: "Delegated Authority And Admission"
summary: "Points from KDCube's host architecture to Connection Hub's delegated authority, transport-neutral operation policy, invocation policy, external MCP proxy, and direct-admission contract."
status: current
tags: ["arch", "security", "admission", "connection-hub", "delegated-access"]
keywords: ["delegated authority", "managed surface guard", "delegated access card", "invocation policy", "external MCP proxy", "Connection Hub"]
updated_at: 2026-09-12
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-authority-and-admission.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/connection-hub-architecture.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-external-service-with-connection-hub-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md
---

# Delegated Authority And Admission

Connection Hub owns the canonical delegated-card authority and per-call admission
contract. Read
[Delegated Authority And Admission](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-authority-and-admission.md)
for card/catalog resolution, once-or-always invocation policy, user-owned
external MCP proxying, managed-surface guards, connected-account claims,
direct protected-service admission, and direct or relayed named-service
invocation.

KDCube remains the host runtime. Its authenticated MCP, Data Bus, named-service,
request-session, and application-surface documents describe how KDCube supplies
the transport and runtime adapters around that authority.

A delegated Card bearer may be presented directly at Data Bus admission. The
Card remains the delegation edge; KDCube does not mint an intermediate token.
The caller selects one resource covered by the Card, and the app resolves each
service-owned canonical operation against that resource's current Card state
before doing domain work. MCP tool names and Data Bus operation fields may
project the same canonical IDs, but neither transport owns those IDs. The
[Data Bus](../service/comm/data-bus-README.md#delegated-card-clients) page owns
the transport and live-revocation details.

The [Delegated KDCube Management Service](../service/cicd/delegated-management-service-README.md)
is the platform reference for a state-changing protected service. It combines
live Connection Hub admission, an exact browser-approved request permit, and a
separate effect ledger before reloading one declared application.
