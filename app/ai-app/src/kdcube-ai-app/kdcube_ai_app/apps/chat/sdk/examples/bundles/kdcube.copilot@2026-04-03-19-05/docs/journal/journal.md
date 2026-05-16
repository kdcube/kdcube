---
title: KDCube Copilot Bundle Journal
kind: bundle-journal
bundle_id: kdcube.copilot@2026-04-03-19-05
updated_at: 2026-05-16
---

# Journal

## 2026-05-16

- Released `2026.5.16.407` together with the platform release line.
- Added the `copilot_webapp` widget as the shared Copilot WebApp surface for
  KDCube iframe usage and Telegram Mini App usage.
- Added durable user-memory management to the Copilot WebApp through the shared
  memory widget source.
- Added shared Telegram WebApp admin/channel UI components so approved Telegram
  users can manage conversations and admins can review/approve pending users.
- Registered Telegram WebApp routes for profile, conversations, memory, and
  admin operations.
- Fixed Telegram chat submission to use the mounted runtime bundle id from
  comm/request context, not the short module id.
- Kept economics hook invocation compatible with Copilot's knowledge-space
  pre-run reconciliation hook.
