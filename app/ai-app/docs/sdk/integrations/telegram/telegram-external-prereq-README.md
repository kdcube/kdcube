---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
title: "Telegram External Prerequisites"
summary: "External Telegram Bot API, BotFather, webhook, private-chat topic, public URL, and Mini App setup required before KDCube Telegram SDK integrations can work."
tags: ["sdk", "integrations", "telegram", "topics", "webhook", "mini-app", "prerequisites"]
keywords: ["telegram prerequisites", "telegram botfather", "telegram webhook", "topics in private chats", "has_topics_enabled", "allows_users_to_create_topics", "telegram mini app", "telegram bot token"]
updated_at: 2026-09-14
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
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
- Optional BotFather **Topics in Private Chats** setting.
- Web client download expectations for Mini Apps.

The bundle or platform still owns:

- public bundle route for the webhook alias
- validation of `X-Telegram-Bot-Api-Secret-Token`
- Telegram user metadata and conversation binding
- Connection Hub connection-edge lookup for KDCube platform authority
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
- Topics in private chats:
  <https://core.telegram.org/bots/features#topics-in-private-chats>
- Telegram Bot API 9.4 topic controls:
  <https://core.telegram.org/bots/api-changelog#february-9-2026>

Human/operator actions:

| Step | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | Telegram `@BotFather` | Create or choose a bot for the bundle. | Bot username and display name. |
| 2 | Telegram `@BotFather` | Get the bot token. | `TELEGRAM_BOT_TOKEN` for deployment secrets. |
| 3 | Deployment/runtime config | Decide the public HTTPS base URL Telegram can reach. | Public host and final webhook URL. |
| 4 | Operator workstation or secret workflow | Generate a random webhook secret token. | `TELEGRAM_WEBHOOK_SECRET`. |
| 5 | KDCube descriptors/config/secrets | Fill Telegram config and secrets, then reload/restart as required. | Updated bundle config and secrets. |
| 6 | Telegram Bot API | Register the webhook with Telegram after the public route exists. | Successful `setWebhook` result. |
| 7 | Telegram `@BotFather` | Configure bot commands, optional private-chat topics, and optional Mini App/menu button. | User-visible commands and chat surfaces. |

## Private Chat Topics

Private-chat topics are enabled per bot in the **BotFather Mini App**. The
classic `/mybots` chat keyboard does not contain the thread controls:

```text
1. Open the @BotFather chat and then open the @BotFather profile.
2. Choose Open App to enter the BotFather Mini App.
3. Select the bot, then open Bot Settings > Thread Settings.
4. Turn on Threaded Mode.
5. Leave "Disallow users to create new threads" off when users should create
   and delete their own workspaces in the chat.
```

Topic-aware routing activates directly when Telegram includes
`message_thread_id` in an update; the existing KDCube descriptor remains
unchanged. The SDK uses the routing contract documented in
[Native Private-Chat Topics](telegram-README.md#native-private-chat-topics).

Verify the BotFather setting through `getMe` without printing the token:

```bash
read -r -s -p "Telegram bot token: " TELEGRAM_BOT_TOKEN; echo
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" \
  | jq '{ok, username: .result.username, has_topics_enabled: .result.has_topics_enabled, allows_users_to_create_topics: .result.allows_users_to_create_topics}'
unset TELEGRAM_BOT_TOKEN
```

Expected after enablement:

```json
{
  "ok": true,
  "username": "<BOT_USERNAME>",
  "has_topics_enabled": true,
  "allows_users_to_create_topics": true
}
```

`has_topics_enabled` proves that the private bot chat is topic-aware.
`allows_users_to_create_topics` proves that users may create and delete those
topics themselves. KDCube routing works for topics created by either the bot or
the user; the second flag is required for the user-created-topic workflow.

Create two topics in the private bot chat and send a message in each. Each
reply should stay in its source topic. This verifies Telegram configuration and
the deployed KDCube topic-aware delivery path together.

## Webhook URL

For a bundle public operation alias named `telegram_webhook`, the route shape is:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook
```

Telegram requires a public HTTPS endpoint. For local development, expose the
runtime with a tunnel such as ngrok and update both KDCube config and Telegram
webhook registration whenever the host changes.

New webhook registrations should include the non-secret integration selector:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook?integration_id=<TELEGRAM_INTEGRATION_ID>
```

The selector lets the SDK validate the callback against the intended Telegram
integration row instead of trying every configured Telegram bot.

## Descriptor Values

Non-secret config typically lives in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      config:
        integrations:
          telegram.default:
            provider: telegram
            enabled: true
            definition:
              webhook:
                url: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook?integration_id=telegram.default"
                send_responses: true
                stream_activity: true
                stream_activity_display: true
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
          telegram.default:
            definition:
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
  `secrets.integrations.<integration_id>.definition.webhook_secret`
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
  -d "url=https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook?integration_id=<TELEGRAM_INTEGRATION_ID>" \
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

## Connection Hub Link

Telegram can reach the webhook before the Telegram actor is connected to a
KDCube platform user. In that state, the actor is authenticated by Telegram but
does not have platform authority or platform economics budget.

The recommended behavior is:

```text
unlinked telegram:<id>
  -> record Telegram metadata and conversation binding
  -> reply with a Connection Hub connect prompt
  -> do not run platform/economics-protected work

linked telegram:<id>
  -> keep actor user_id=telegram_<id>
  -> project platform authority through the Connection Hub edge
  -> run authorized workflows with platform economics attribution
```

The SDK provides Telegram metadata storage and widget operations. Connection
Hub owns the connection edge and authority projection.

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
