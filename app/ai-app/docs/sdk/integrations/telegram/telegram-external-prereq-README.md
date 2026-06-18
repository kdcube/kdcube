---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
title: "Telegram External Prerequisites"
summary: "External Telegram Bot API, BotFather, webhook, public URL, and Mini App setup required before KDCube Telegram SDK integrations can work."
tags: ["sdk", "integrations", "telegram", "webhook", "mini-app", "prerequisites"]
keywords: ["telegram prerequisites", "telegram botfather", "telegram webhook", "telegram mini app", "telegram bot token"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
---

# Telegram External Prerequisites

This document lists work that must happen outside KDCube before a bundle can use
the Telegram SDK integration.

The Telegram SDK provides reusable Bot API helpers, webhook update
normalization, attachment hydration, progress streaming, Mini App auth,
user-registry storage, widget operations, and signed download helpers. It
cannot create the Telegram bot, choose a public HTTPS host, register a webhook
with Telegram, or configure BotFather menu buttons.

## What Is External

External setup includes:

- Telegram bot creation through `@BotFather`.
- Bot token storage in deployment secrets.
- Public HTTPS URL that Telegram can reach.
- Webhook secret generation outside source control.
- Telegram Bot API `setWebhook` call.
- Optional BotFather command list and Mini App / menu button configuration.
- Web client download expectations for Mini Apps.

The bundle or platform still owns:

- public bundle route for the webhook alias
- validation of `X-Telegram-Bot-Api-Secret-Token`
- Telegram user allow-list, roles, and KDCube user mapping
- conversation binding for Telegram-originated turns
- workflow submission through conversation `external_events[]`
- final response delivery through the bundle's Telegram queued-delivery wrapper

For the internal runtime data path after Telegram reaches the webhook, see
`telegram-webhook-submit-and-delivery-README.md`.

## Telegram Bot Setup

Official references:

- Bot creation and management:
  <https://core.telegram.org/bots/features#botfather>
- Webhook setup:
  <https://core.telegram.org/bots/api#setwebhook>
- Mini Apps / Web Apps:
  <https://core.telegram.org/bots/webapps>

Human/operator actions:

| Step | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | Telegram `@BotFather` | Create or choose a bot for the bundle. | Bot username and display name. |
| 2 | Telegram `@BotFather` | Get the bot token. | `TELEGRAM_BOT_TOKEN` for deployment secrets. |
| 3 | Deployment/runtime config | Decide the public HTTPS base URL Telegram can reach. | Public host and final webhook URL. |
| 4 | Operator workstation or secret workflow | Generate a random webhook secret token. | `TELEGRAM_WEBHOOK_SECRET`. |
| 5 | KDCube descriptors/config/secrets | Fill Telegram config and secrets, then reload/restart as required. | Updated bundle config and secrets. |
| 6 | Telegram Bot API | Register the webhook with Telegram after the public route exists. | Successful `setWebhook` result. |
| 7 | Telegram `@BotFather` | Configure bot commands and optional Mini App/menu button. | User-visible command list and web app launch point. |

## Webhook URL

For a bundle public operation alias named `telegram_webhook`, the route shape is:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook
```

Telegram requires a public HTTPS endpoint. For local development, expose the
runtime with a tunnel such as ngrok and update both KDCube config and Telegram
webhook registration whenever the host changes.

## Descriptor Values

Non-secret config typically lives in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      config:
        integrations:
          telegram:
            enabled: true
            webhook_url: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook"
            send_responses: true
            web_app_auth_max_age_seconds: 86400
```

Secrets live in `bundles.secrets.yaml` or the configured secrets provider:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      secrets:
        integrations:
          telegram:
            bot_token: "<TELEGRAM_BOT_TOKEN>"
            webhook_secret: "<TELEGRAM_WEBHOOK_SECRET>"
```

## Webhook Secret Token

Generate the webhook secret outside the bundle. Store it in the deployment
secrets provider, not in source control.

Allowed characters:

```text
A-Z a-z 0-9 _ -
```

Example generation:

```bash
printf '%s\n' "$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
```

Use the same value in two places:

- KDCube bundle secret:
  `secrets.integrations.telegram.webhook_secret`
- Telegram `setWebhook` request:
  `secret_token=<TELEGRAM_WEBHOOK_SECRET>`

Telegram sends the value back on each webhook request in:

```text
X-Telegram-Bot-Api-Secret-Token
```

The bundle webhook must reject requests when the header is missing or does not
match the configured secret.

## Webhook Registration

After the bundle exposes the webhook public route, register it through Telegram:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

When replacing a local tunnel or changing deployments, call `setWebhook` again
with the new URL.

## Commands And Mini App

Recommended initial commands:

```text
/start - start the assistant
/help - show available actions
/settings - open settings
```

If the bundle ships a Telegram Mini App, configure the bot menu button or app
entry in `@BotFather` with the public static widget URL:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/widgets/<WIDGET_ALIAS>/
```

Do not use the authenticated control-plane widget URL:

```text
/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/widgets/<WIDGET_ALIAS>/
```

The static widget URL only loads the Mini App shell and assets. The public API
calls it makes should validate Telegram Mini App `initData` through the SDK
`widget_auth` integration.

## User Approval

Telegram can reach the webhook before a Telegram user is approved. A typical
bundle should record pending users and require an operator/admin to assign a
role such as `registered` or `admin` before allowing turns or Mini App
operations.

The SDK provides `TelegramUserAdminStorage` and configurable user-admin/widget
helpers. The bundle supplies where that registry lives and what migration or
role policy applies.

## Mini App Download Requirements

Telegram web clients are stricter than normal browsers for downloads. When a
Telegram Mini App downloads a generated file from a backend URL, the HTTP
response should include:

```text
Content-Disposition: attachment; filename="<file_name>"
Access-Control-Allow-Origin: https://web.telegram.org
Access-Control-Expose-Headers: Content-Disposition
```

The SDK Telegram widget operations add the Telegram CORS headers around binary
artifact responses. The final binary response path must still provide an
attachment filename so KDCube can emit `Content-Disposition`.

If more Telegram origins are supported later, return the matched Telegram
origin and include `Vary: Origin`.
