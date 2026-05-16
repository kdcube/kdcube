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
export PUBLIC_HOST="https://YOUR_PUBLIC_HTTPS_HOST"

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

Register the Mini App/menu button:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Open KDCube\",\"web_app\":{\"url\":\"${MINI_APP_URL}\"}}}"
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
2. Open the Copilot widget in KDCube.
3. Go to Admin.
4. Refresh users.
5. Promote the pending anonymous Telegram user to registered or admin.
```

If `/start` does not appear in Admin:

```text
1. Run getWebhookInfo.
2. Confirm result.url equals WEBHOOK_URL.
3. Confirm setWebhook used secret_token.
4. Confirm TELEGRAM_WEBHOOK_SECRET matches the bundle secret.
5. Confirm PUBLIC_HOST points to the running KDCube ingress.
```
