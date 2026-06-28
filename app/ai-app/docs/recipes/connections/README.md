---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/README.md
title: "Connection Recipes"
summary: "Short recipes for Connection Hub flows such as linking identities from external channels and using linked identities safely in app features."
status: active
tags: ["recipes", "connections", "connection-hub", "identity-linking", "external-channel"]
updated_at: 2026-06-28
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/link-from-external-channel-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
---
# Connection Recipes

These recipes are practical entry points for building with Connection Hub. They
are intentionally shorter and more task-oriented than the SDK architecture docs.

## Recipes

| Recipe | Use when |
| --- | --- |
| [Link From External Channel](link-from-external-channel-README.md) | A user starts inside Telegram, Slack, WhatsApp, a partner app, or another runtime that already carries provider auth material, and must link that identity to their KDCube platform user. |

## Canonical SDK Docs

For deeper design and implementation contracts, read:

- [Connection Hub Solution](../../sdk/solutions/connections/connection-hub-solution-README.md)
- [Channel-First Identity Linking](../../sdk/solutions/connections/link-flows/channel-first-identity-linking-README.md)
- [Widget Auth Context Transport](../../sdk/solutions/connections/widget-auth-context/widget-auth-context-README.md)
- [Request Authenticators](../../sdk/solutions/connections/request-authenticators/request-authenticators-README.md)
