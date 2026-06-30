---
id: ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/docs/integrations/telegram-setup.md
title: "KDCube Companion Telegram Setup"
summary: "Compact operator commands for configuring the KDCube Companion Telegram bot webhook, Telegram Mini App menu button, bot commands, and Connection Hub link flow."
tags: ["bundle", "versatile", "telegram", "webhook", "mini-app", "botfather", "operator-setup"]
keywords: ["kdcube companion telegram setup", "telegram webhook", "setWebhook", "secret_token", "getWebhookInfo", "setChatMenuButton", "setMyCommands", "telegram_miniapp", "connection hub", "telegram link"]
updated_at: 2026-05-16
see_also:
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---

# KDCube Companion Telegram Setup

The bundle exposes:

```text
POST /public/telegram_webhook?integration_id=<telegram-integration-id>
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

export TELEGRAM_BOT_TOKEN="..."       # same value as integrations.telegram_kdcube_ref.definition.bot_token
export TELEGRAM_WEBHOOK_SECRET="..."  # same value as integrations.telegram_kdcube_ref.definition.webhook_secret

export TELEGRAM_INTEGRATION_ID="telegram.kdcube_ref"
export WEBHOOK_URL="${PUBLIC_HOST}/api/integrations/bundles/${TENANT}/${PROJECT}/${BUNDLE_ID}/public/telegram_webhook?integration_id=${TELEGRAM_INTEGRATION_ID}"
export MINI_APP_URL="${PUBLIC_HOST}/api/integrations/bundles/${TENANT}/${PROJECT}/${BUNDLE_ID}/public/widgets/${WIDGET_ALIAS}"
```

Register the webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${WEBHOOK_URL}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

The Telegram webhook route is platform-public because Telegram is not a
browser/platform session. The Telegram handler still enforces
`X-Telegram-Bot-Api-Secret-Token`. New webhook registrations should always put
the non-secret `integration_id` selector in the webhook URL, as shown above.
With that selector present, the handler validates against that integration's
configured `webhook_secret` only. Runtime fallback can still validate by
checking enabled Telegram integration secrets when Telegram reaches a webhook
without the query selector, but new setups should always include it.

Check what Telegram currently uses:

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

If `result.url` is empty, `/start` will not reach KDCube.

Create the Mini App in `@BotFather` before registering the menu button:

```text
/newapp
select @<bot_username>
App title: KDCube Companion
Short description: Memories, chats, and KDCube account connection in Telegram
App URL: ${MINI_APP_URL}
```

Use the same `MINI_APP_URL` for the programmable chat menu button:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Open KDCube\",\"web_app\":{\"url\":\"${MINI_APP_URL}\"}}}"
```

Check what the programmable chat menu button currently opens:

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
2. Open the Telegram Mini App.
3. Use the Connect tab to link this Telegram account to the signed-in KDCube account.
4. Return to Telegram and send another message.
5. Confirm the message runs as telegram_<id> with projected KDCube authority.
```

Visible Mini App surfaces:

```text
unlinked Telegram user -> Connect tab only
linked Telegram user   -> Memory + Chats + Connect
KDCube admin user      -> platform-side bundle/admin operations where configured
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
6. If any Telegram launch button opens `{"detail":"Bundle does not define widget <old-alias>"}`,
   that launch surface still points to a removed widget alias.
   Run getChatMenuButton for the programmable menu button and confirm its URL
   equals MINI_APP_URL. If getChatMenuButton is correct, edit the BotFather
   Main Mini App/direct app URL manually to MINI_APP_URL.
```
