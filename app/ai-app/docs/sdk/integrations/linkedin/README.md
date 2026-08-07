---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/README.md
title: "LinkedIn Integration Docs"
summary: "Index for the KDCube LinkedIn SDK integration docs."
tags: ["sdk", "integrations", "linkedin"]
keywords: ["linkedin integration", "linkedin oauth", "linkedin posts", "ugc posts"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/README.md
---

# LinkedIn Integration Docs

The SDK carries **two independent LinkedIn layers**. Pick before reading:

| Layer | Modules | OAuth owner | Docs |
| --- | --- | --- | --- |
| Connection Hub connected accounts | `rest_api.py`, `tools.py`, `named_service.py` | Connection Hub connector app | [LinkedIn Integration recipe](../../../recipes/connections/integrations/linkedin-README.md) |
| Bundle-owned OAuth (legacy) | `accounts.py`, `settings.py`, `delivery.py` | The bundle itself | this folder |

New work belongs on the Connection Hub layer: it gives agent tools, the
productivity MCP door, the `linkedin` named-service namespace, and one consent
model shared with Slack and Google. The legacy layer stays in place for
`task-and-memo-app@1-0` and targets LinkedIn's deprecated `/v2` API.

Use these docs in this order:

- [LinkedIn SDK Integration](linkedin-README.md) — reusable KDCube SDK modules,
  bundle wiring, account store, OAuth flow, UGC Posts API for text and image
  posts, content formatting utilities (`format_post_text`, `strip_markdown`),
  and PDF limitation notes.
- [LinkedIn External Prerequisites](linkedin-external-prereq-README.md) — work
  that must happen outside KDCube before the integration can function, including
  LinkedIn Developer App setup, OAuth redirect URI registration, and deployment
  secrets.
