---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/README.md
title: "Connection Recipes"
summary: "Short recipes for Connection Hub flows such as creating connection edges from external channels, hosting bundle-backed platform login, using connected identities safely in app features, and delegating KDCube services to external clients."
status: active
tags: ["recipes", "connections", "connection-hub", "connection-edges", "external-channel", "delegated-credentials"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/link-from-external-channel-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/host-platform-authority-in-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/use-connected-identities-in-product-feature-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
---
# Connection Recipes

These recipes are practical entry points for building with Connection Hub. They
are intentionally shorter and more task-oriented than the SDK architecture docs.

## Recipes

| Recipe | Use when |
| --- | --- |
| [Telegram Integration](integrations/telegram-README.md) | A bundle exposes a Telegram webhook and Mini App, and Telegram users should connect to KDCube through Connection Hub before using platform-backed features. |
| [Link From External Channel](link-from-external-channel-README.md) | A user starts inside Telegram, Slack, WhatsApp, a partner app, or another runtime that already carries provider auth material, and must create a connection edge to their KDCube platform user. |
| [Set Up A Platform Authority Provider](platform-authority/setup-platform-authority-README.md) | A deployment needs to choose and configure the platform authority method: Cognito/multi-Cognito, SimpleIDP, or bundle-hosted platform session. |
| [Host A Platform Authority Flow In A Bundle](platform-authority/host-platform-authority-in-bundle-README.md) | A deployment wants a bundle-owned login UI/flow, such as Google login without Cognito, while Connection Hub owns platform authority registration and policy. |
| [Use Connected Identities In A Product Feature](use-connected-identities-in-product-feature-README.md) | A product feature stores data by runtime actor, but should read one coherent set across the current user's connected identities. |
| [Protect Bundle MCP With Managed Credentials](protect-bundle-mcp-with-managed-credentials-README.md) | A bundle exposes MCP tools and wants Connection Hub to manage delegated external-client access with per-tool grants. |
| [Delegate A KDCube Service To An External Client](delegate-kdcube-service-to-external-client-README.md) | A user wants to connect Claude or another external client to a KDCube service with explicit consent and least-privilege tools. |

## Canonical SDK Docs

For deeper design and implementation contracts, read:

- [Connection Hub Solution](../../sdk/solutions/connections/connection-hub-solution-README.md)
- [Connection Edges](../../sdk/solutions/connections/connection-edges/connection-edges-README.md)
- [Channel-First Connection Edge Flow](../../sdk/solutions/connections/link-flows/channel-first-connection-edge-flow-README.md)
- [Identity Family Resolver](../../sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md)
- [OAuth Delegated Credential Protocol Adapter](../../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md)
- [Widget Auth Context Transport](../../sdk/solutions/connections/widget-auth-context/widget-auth-context-README.md)
- [Request Authenticators](../../sdk/solutions/connections/request-authenticators/request-authenticators-README.md)
