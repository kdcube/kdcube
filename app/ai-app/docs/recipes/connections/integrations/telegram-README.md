---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/telegram-README.md
title: "Telegram Integration"
summary: "Recipe for wiring a Telegram webhook and Mini App through Connection Hub so Telegram actors can connect to KDCube platform authority before using platform-backed features."
status: active
tags: ["recipes", "connections", "telegram", "connection-hub", "mini-app", "webhook"]
updated_at: 2026-06-30
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/link-from-external-channel-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
---
# Telegram Integration

Use this recipe when a bundle receives Telegram webhook updates and hosts a
Telegram Mini App such as KDCube Companion.

The clean model is:

```text
Telegram proves telegram:<id>.
KDCube platform sign-in proves platform:<id>.
Connection Hub stores the connection/delegation edge.
Telegram keeps actor identity telegram_<id>.
Platform roles/economics are projected only when the edge exists.
```

Do not treat a Telegram-local row as a platform role assignment. Telegram
metadata is useful for routing and conversation binding; Connection Hub owns
the authority edge.

## Webhook Flow

```text
Telegram Bot API
  POST /public/telegram_webhook?integration_id=telegram.kdcube_ref
    X-Telegram-Bot-Api-Secret-Token: <webhook secret>
        |
        v
bundle public API
  Telegram SDK validates webhook secret for integration_id
        |
        v
normalize Telegram update
  actor = telegram:<telegram_user_id>
        |
        v
Connection Hub edge resolver
        |
        +-- no edge
        |     reply: open KDCube Companion and use Connect
        |
        +-- edge exists
              create actor session user_id=telegram_<id>
              project platform authority for roles/economics
              run the bundle workflow
```

Unlinked Telegram actors are externally authenticated, but they are not
platform users. They should not receive platform free-user quota or platform
budget bypass.

## Mini App Link Flow

```text
Telegram opens KDCube Companion
  Telegram.WebApp.initData is available to the host
        |
        v
host embeds Connection Hub widget
  CONFIG_RESPONSE.authContext.headers:
    X-KDCube-Auth-Provider: telegram
    X-KDCube-Auth-Integration-ID: telegram.kdcube_ref
    X-Telegram-Init-Data: <Telegram.WebApp.initData>
        |
        v
Connection Hub widget creates a link challenge
        |
        v
browser opens KDCube claim page
  user signs in to KDCube if needed
  user confirms delegation/connection edge
        |
        v
Connection Hub stores the edge
        |
        v
Data Bus notifies the Mini App widget
  KDCube Companion unlocks Memories and Chats
```

The Mini App can show only Connect while the Telegram actor is unlinked. Once
linked, it can show platform-backed tabs such as Memories and Chats.

## Descriptor Shape

Use a stable integration id. The id is non-secret and may be carried in webhook
URLs and widget auth-context headers.

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      config:
        integrations:
          telegram.kdcube_ref:
            provider: telegram
            where: built-in
            enabled: true
            secret_refs:
              bot_token: integrations.telegram_kdcube_ref.definition.bot_token
              webhook_secret: integrations.telegram_kdcube_ref.definition.webhook_secret
            definition:
              bot_name: kdcube-ref
              bot_username: kdcube_doc_bot
              webhook:
                url: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook?integration_id=telegram.kdcube_ref"
                send_responses: true
                stream_activity: true
              mini_apps:
                companion:
                  widget_alias: telegram_miniapp
              web_app_auth_max_age_seconds: 86400
```

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      secrets:
        integrations:
          telegram_kdcube_ref:
            definition:
              bot_token: "<TELEGRAM_BOT_TOKEN>"
              webhook_secret: "<TELEGRAM_WEBHOOK_SECRET>"
```

## Bot API Setup

Register the webhook with the integration selector:

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${PUBLIC_HOST}/api/integrations/bundles/${TENANT}/${PROJECT}/${BUNDLE_ID}/public/telegram_webhook?integration_id=${TELEGRAM_INTEGRATION_ID}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

Register the programmable menu button:

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Open KDCube\",\"web_app\":{\"url\":\"${MINI_APP_URL}\"}}}"
```

The menu button is not the same as BotFather's Main Mini App URL. Configure
both to the canonical widget URL:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/widgets/telegram_miniapp
```

Recommended BotFather display name:

```text
KDCube Companion
```

## Testing

1. Open the Telegram bot and send `/start`.
2. If the Telegram actor is unlinked, confirm the bot replies with a connect
   prompt instead of running an economics-protected workflow.
3. Open KDCube Companion and use the Connect tab.
4. Complete KDCube sign-in and confirm the connection/delegation edge.
5. Return to Telegram. The Mini App should show Memories and Chats.
6. Send another message. Logs should show `user_id=telegram_<id>` and projected
   platform authority/economics subject when the edge exists.

Useful log markers:

```text
[telegram.connection] actor=telegram_<id> linked=<true|false>
[connection-hub.identity_family_resolve]
[run] init ... actor_user_id=telegram_<id> economics_user_id=<platform_user_id>
```

If Telegram opens an error that says the bundle does not define a widget, the
BotFather Main Mini App URL or a cached direct link still points to a removed
widget alias. Reconfigure BotFather to the canonical `telegram_miniapp` URL.
