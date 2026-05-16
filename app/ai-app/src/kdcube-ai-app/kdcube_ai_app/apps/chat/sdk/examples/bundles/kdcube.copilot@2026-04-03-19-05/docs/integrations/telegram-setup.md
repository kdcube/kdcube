---
id: ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/docs/integrations/telegram-setup.md
title: "KDCube Copilot Telegram Setup"
summary: "Compact operator commands for configuring the KDCube Copilot Telegram bot webhook, Mini App menu button, bot commands, and pending user approval flow."
tags: ["bundle", "copilot", "telegram", "webhook", "mini-app", "botfather", "operator-setup"]
keywords: ["kdcube copilot telegram setup", "telegram webhook", "setWebhook", "secret_token", "getWebhookInfo", "setChatMenuButton", "setMyCommands", "copilot_webapp", "pending telegram user", "telegram admin"]
updated_at: 2026-05-16
see_also:
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/docs/README.md
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/doc-reader-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---

# KDCube Copilot Telegram Setup

The bundle exposes:

```text
POST /public/telegram_webhook
GET  /public/widgets/copilot_webapp
POST /operations/telegram_user_admin_*
```

Set these variables:

```bash
export TENANT="demo-tenant"
export PROJECT="demo-project"
export BUNDLE_ID="kdcube.copilot@2026-04-03-19-05"
export WIDGET_ALIAS="copilot_webapp"
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
App title: KDCube Copilot
Short description: KDCube documentation assistant
App URL: ${MINI_APP_URL}
```

For local testing, `PUBLIC_HOST` must be the currently running public HTTPS
ingress, for example the active ngrok URL. Do not keep a trailing slash:
`https://host//api/...` returns `{"detail":"Not Found"}` before KDCube reaches
the widget or webhook route. After every ngrok/redeploy host change, update both
the Telegram webhook and the Mini App/menu button URL.

Use the same `MINI_APP_URL` for the menu button:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Open KDCube\",\"web_app\":{\"url\":\"${MINI_APP_URL}\"}}}"
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
2. Or open the Mini App once from Telegram.
3. Open the Copilot widget in KDCube.
4. Go to Admin (requires KDCube admin role).
5. Refresh users.
6. Promote the pending anonymous Telegram user to registered or admin.
```

Either `/start` or the Mini App profile load records the Telegram user as
`anonymous`, so the admin has a pending row to approve.

Admin approval flow:

```text
1. Open the Copilot widget in KDCube.
2. Go to Admin (requires KDCube admin role).
3. Refresh users.
4. Promote the pending anonymous Telegram user to registered or admin.
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
copilot_webapp imports shared SDK UI:
- @kdcube/memory-widget    -> sdk://context/memory/ui/widget/memories
- @kdcube/telegram-widget  -> sdk://integrations/telegram/ui/widget.telegram
```

These sources must be present in `ui.web_app_widgets.copilot_webapp.shared_sources`
or in the bundle's configuration defaults. If the Mini App fails with
`Could not load /integrations/telegram/ui/widget.telegram/src/index.tsx`, the
Telegram shared widget source was not materialized into `_shared/telegram-widget`
before Vite built the widget.

If `/start` does not appear in Admin:

```text
1. Run getWebhookInfo.
2. Confirm result.url equals WEBHOOK_URL.
3. Confirm result.last_error_message is empty.
4. Confirm setWebhook used secret_token.
5. Confirm TELEGRAM_WEBHOOK_SECRET matches the bundle secret.
6. Confirm PUBLIC_HOST points to the running KDCube ingress.
7. POST a fake update to WEBHOOK_URL with X-Telegram-Bot-Api-Secret-Token;
   if it does not hit KDCube logs, the URL/proxy is wrong.
```

If the Mini App opens with `{"detail":"Not Found"}` and KDCube logs show
nothing, Telegram is opening a stale or wrong URL. Re-run `setChatMenuButton`
with the current `MINI_APP_URL`, and update the BotFather Mini App URL if the
app was opened from BotFather's Mini App surface rather than the menu button.
`getChatMenuButton` must show the same URL as `MINI_APP_URL`.

Quick local route check:

```bash
curl -i "${MINI_APP_URL}"
```
