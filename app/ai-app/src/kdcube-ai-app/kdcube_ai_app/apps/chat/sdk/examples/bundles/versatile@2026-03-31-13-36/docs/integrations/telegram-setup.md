---
id: ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/docs/integrations/telegram-setup.md
title: "Versatile Telegram Setup"
summary: "Compact operator commands for configuring the Versatile reference bundle Telegram bot webhook, Telegram Mini App menu button, bot commands, and pending user approval flow."
tags: ["bundle", "versatile", "telegram", "webhook", "mini-app", "botfather", "operator-setup"]
keywords: ["versatile telegram setup", "telegram webhook", "setWebhook", "secret_token", "getWebhookInfo", "setChatMenuButton", "setMyCommands", "telegram_miniapp", "pending telegram user", "telegram admin"]
updated_at: 2026-05-16
see_also:
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---

# Versatile Telegram Setup

The bundle exposes:

```text
POST /public/telegram_webhook
GET  /public/widgets/telegram_miniapp
POST /operations/telegram_user_admin_*
```

Set these variables:

```bash
export TENANT="demo-tenant"
export PROJECT="demo-project"
export BUNDLE_ID="versatile@2026-03-31-13-36"
export WIDGET_ALIAS="telegram_miniapp"
export PUBLIC_HOST="https://YOUR_PUBLIC_HTTPS_HOST" # no trailing slash
export PUBLIC_HOST="${PUBLIC_HOST%/}"

export TELEGRAM_BOT_TOKEN="..."       # from bundles.secrets.yaml / secrets provider
export TELEGRAM_WEBHOOK_SECRET="..."  # same value as integrations.telegram.webhook_secret

export WEBHOOK_URL="${PUBLIC_HOST}/api/integrations/bundles/${TENANT}/${PROJECT}/${BUNDLE_ID}/public/telegram_webhook"
export MINI_APP_URL="${PUBLIC_HOST}/api/integrations/bundles/${TENANT}/${PROJECT}/${BUNDLE_ID}/public/widgets/${WIDGET_ALIAS}"
```

Register the webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${WEBHOOK_URL}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

Check what Telegram currently uses:

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

If `result.url` is empty, `/start` will not reach KDCube.

Create the Mini App in `@BotFather` before registering the menu button:

```text
/newapp
select @<bot_username>
App title: Versatile
Short description: KDCube versatile reference assistant
App URL: ${MINI_APP_URL}
```

Use the same `MINI_APP_URL` for the menu button:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Open Versatile\",\"web_app\":{\"url\":\"${MINI_APP_URL}\"}}}"
```

Check what the blue chat button currently opens:

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getChatMenuButton"
```

Register bot commands:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{"commands":[{"command":"start","description":"Start the assistant"},{"command":"help","description":"Show help"},{"command":"settings","description":"Open settings"}]}'
```

Test:

```text
1. Send /start to the bot.
2. Open the Versatile widget in KDCube.
3. Go to Admin (requires KDCube admin role).
4. Refresh users.
5. Promote the pending anonymous Telegram user to registered or admin.
```

Visible Mini App surfaces:

```text
anonymous Telegram user  -> Pending approval banner
registered Telegram user -> User Memory + Chats
admin Telegram user      -> User Memory + Chats + Admin
KDCube admin widget user -> User Memory + Chats + Admin
```

Widget build note:

```text
telegram_miniapp imports shared SDK UI:
- @kdcube/memory-widget    -> sdk://context/memory/ui/widget/memories
- @kdcube/telegram-widget  -> sdk://integrations/telegram/ui/widget.telegram
```

These sources must be present in
`ui.widgets.telegram_miniapp.shared_sources` or in the bundle's
configuration defaults. If the Mini App fails with
`Could not load /integrations/telegram/ui/widget.telegram/src/index.tsx`, the
Telegram shared widget source was not materialized into `_shared/telegram-widget`
before Vite built the widget.

If `/start` does not appear in Admin:

```text
1. Run getWebhookInfo.
2. Confirm result.url equals WEBHOOK_URL.
3. Confirm setWebhook used secret_token.
4. Confirm TELEGRAM_WEBHOOK_SECRET matches the bundle secret.
5. Confirm PUBLIC_HOST points to the running KDCube ingress.
6. If the blue chat button opens `{"detail":"Not Found"}`, run
   getChatMenuButton and confirm its URL equals MINI_APP_URL.
```
