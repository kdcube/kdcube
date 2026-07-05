---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/connection-hub@1-0/docs/journal/journal.md
title: "Connection Hub Build Journal"
summary: "Compatibility index for the connection-hub@1-0 dated build journal."
status: active
tags: ["connection-hub", "journal", "connections", "identity"]
---

# Connection Hub Build Journal

This file is kept as the stable journal link from older docs. New entries live
as dated files in this directory.

## Entries

- [2026-07-05 - Managed REST guard](2026-07-05-managed-rest-guard.md)
- [2026-07-05 - Delegated access for automation](2026-07-05-delegated-access-for-automation.md)
- [2026-07-01 - Authority registry descriptor](2026-07-01-authority-registry-descriptor.md)
- [2026-06-29 - Delegated credential surface split](2026-06-29-delegated-credential-surface-split.md)
- [2026-06-28 - Identity family resolver](2026-06-28-identity-family-resolver.md)
- [2026-06-28 - Explicit Telegram claim confirmation](2026-06-28-explicit-telegram-claim-confirmation.md)
- [2026-06-27 - Telegram claim platform auth](2026-06-27-telegram-claim-platform-auth.md)
- [2026-06-26 - Request authenticators](2026-06-26-request-authenticators.md)
- [2026-06-25 - Identity links and delegated connections](2026-06-25-identity-links-and-delegated-connections.md)

## Baseline carried forward

Connection Hub started as the user-scoped third-party account connection hub:

- provider -> client app -> user account;
- one shared `connection_oauth_callback` for OAuth providers;
- user-scoped tokens in `ConnectionStore`;
- Gmail and Slack through the `connections` framework;
- iCloud through app-password email settings;
- named-service discovery registration on app load.

The current dated journal entry adds the missing identity layer and makes clear
that role resolution belongs to a platform principal/role resolver, not to the
Connection Hub app itself.
