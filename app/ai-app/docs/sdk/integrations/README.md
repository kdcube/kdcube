---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/README.md
title: "SDK Integrations"
summary: "Index of reusable KDCube SDK integration packages that bundles can import instead of reimplementing provider or transport mechanics."
tags: ["sdk", "integrations", "email", "telegram", "bundles"]
keywords: ["sdk integrations", "email integration", "telegram integration", "bundle building blocks"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/README.md
---
# SDK Integrations

SDK integrations are reusable provider and transport packages for bundles.

The bundle supplies product policy, route aliases, user-scope resolution, and
UI composition. The integration package supplies the mechanics.

| Integration | Use it for |
| --- | --- |
| [Email](email/README.md) | Gmail OAuth/API, iCloud IMAP/SMTP, account settings, attachment materialization, Email MCP, Claude Code email processing, and email delivery helpers. |
| [LinkedIn](linkedin/README.md) | LinkedIn OAuth, UGC Posts API for text and image posts, content formatting helpers (`format_post_text`), image upload via Assets API. |
| [Telegram](telegram/README.md) | Webhooks, Bot API rendering, progress streaming, Mini App auth, user registry storage, widget operations, and signed downloads. |

For the broader bundle-builder selection map, start with
[How To Assemble A Bundle With SDK Building Blocks](../bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md).
