---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/README.md
title: "Integration Recipes"
summary: "Recipes for connecting external provider accounts to KDCube through Connection Hub delegated-to-KDCube connected accounts."
status: active
tags: ["recipes", "connections", "connection-hub", "integrations", "delegated-to-kdcube", "connected-accounts"]
updated_at: 2026-07-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/provider-error-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/resolve-connected-credential-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
---
# Integration Recipes

These recipes cover the **delegated to KDCube** direction: a signed-in KDCube
user connects an external provider account, and KDCube tools or named services
later use that provider credential on the user's behalf.

```text
external account credential -> Connection Hub -> KDCube tool/named service
```

**How that arrow works:**
[Resolve a Connected Credential in Tool Code](resolve-connected-credential-README.md)
explains the credential-resolution mechanism every recipe below shares - how a
tool obtains the user's provider credential at the trusted boundary, for one
call, with no token in your code or the model.

Every integration must also follow the
[Provider Error And Observability Contract](../../../sdk/integrations/provider-error-contract-README.md).
It defines how disabled APIs, insufficient scopes, provider denials, transport
failures, and ambiguous writes reach clients and server logs without exposing
credentials.

## Recipes

| Recipe | Use when |
| --- | --- |
| [Custom OAuth/OIDC Service Integration](custom-oauth-oidc-service-README.md) | A service such as S1 exposes OAuth/OIDC, and KDCube tools need that user's S1 token. |
| [Google Services (Gmail, Sheets, Docs)](google-service-README.md) | Users should connect their Google account, and KDCube tools/named services should act on Gmail (search, read, send, forward, attachments), Sheets, or Docs (typed productivity tools or the provider-neutral `sheets`/`docs` named-service namespaces) without receiving the Google token. |
| [Slack Integration](slack-README.md) | Users should connect Slack workspaces, and KDCube tools should search, list channels, read history, read/write files, or post messages. |
| [LinkedIn Integration](linkedin-README.md) | Users should connect their LinkedIn account, and KDCube tools/named services should publish posts, attach images, or comment as that member. LinkedIn exposes no content reads to standard OAuth apps. |
| [Mail Named Service Over MCP](mail-named-service-README.md) | Connected mail accounts should be exposed to external agents as a provider-neutral `mail` namespace. |
| [Telegram Integration](telegram-README.md) | Telegram users should connect a channel identity to a KDCube platform user. |

## Serving Integrations

Connections in the other direction — external serving capacity plugged into
the platform rather than a user account delegated to it:

| Recipe | Use when |
| --- | --- |
| [Ollama Integration (Locally Served Models)](olama-README.md) | A locally hosted model (Ollama) should serve as a selectable brain for platform agents — streaming, accounting, thinking handling, multimodal input, descriptor wiring. |
