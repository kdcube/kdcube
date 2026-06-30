---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/link-from-external-channel-README.md
title: "Link From External Channel"
summary: "Recipe for creating a connection edge when a user starts inside an external runtime such as Telegram, Slack, WhatsApp, or another authenticated app surface."
status: active
tags: ["recipes", "connections", "connection-hub", "connection-edges", "external-channel", "telegram", "data-bus", "widgets"]
updated_at: 2026-06-29
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/link-flows/channel-first-connection-edge-flow-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/widget-auth-context/widget-auth-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/request-authenticators/request-authenticators-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
---
# Link From External Channel

Use this recipe when the user starts in a place that already proves some
non-KDCube identity:

```text
Telegram Mini App
Slack modal
WhatsApp webview
customer portal iframe
bundle-owned public hook or widget
```

The goal is not to turn that channel session into the browser session. The goal
is to safely say:

```text
this Telegram/Slack/etc. identity
  belongs to
this KDCube platform user
```

After that link exists, KDCube can recognize the same person when they arrive
from either side.

## Plain Shape

```text
External channel
  "I can prove telegram:434804821"
        |
        v
Connection Hub
  verifies the proof and creates a temporary claim code
        |
        v
Browser claim page
  "Sign in to KDCube and approve this link"
        |
        v
Connection Hub
  writes:
    telegram:434804821 -> platform user 02e53484-...
        |
        v
External channel UI
  receives live update and shows "linked"
```

## Parties

```text
Provider runtime
  Telegram / Slack / WhatsApp / another external app
  owns the raw provider auth material.

Host app
  The KDCube app loaded inside that provider runtime.
  Example: KDCube Companion Telegram Mini App.
  It knows which configured integration it is using.

Connection Hub widget or host UI
  The user-facing linking UI.
  It does not know provider secrets and does not validate provider proof itself.

Connection Hub backend
  Verifies provider proof through configured request authenticators.
  Stores connection-edge challenges and connection edges.

KDCube platform browser session
  Proves the platform user through normal KDCube login.

Data Bus
  Sends the live "linked/unlinked" result back to the original external UI.
```

## End-To-End Diagram

```text
1. User opens external runtime
   Telegram / Slack / WhatsApp / customer app
        |
        | provider auth material
        |   Telegram initData
        |   Slack signed request
        |   webhook HMAC headers
        |   provider ID token
        v

2. Host app receives runtime config
        |
        | from bundle/app config:
        |   authority_id = telegram.kdcube_ref
        |   authenticator_id = telegram.kdcube_ref
        |   provider = telegram
        |   Connection Hub widget URL
        v

3. Host app embeds Connection Hub UI
        |
        | CONFIG_REQUEST
        v
   Connection Hub iframe
        |
        | CONFIG_RESPONSE.authContext.headers
        |   X-KDCube-Auth-Provider: telegram
        |   X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
        |   X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref
        |   X-Telegram-Init-Data: <Telegram.WebApp.initData>
        v

4. Connection Hub UI asks for live channel
        |
        | POST public/federated_data_bus_claim
        | carries the same authContext headers
        v
   Connection Hub backend
        |
        | verifies provider proof
        | creates/reuses actor session:
        |   actor_user_id = telegram_434804821
        |   no projected platform authority before link
        | returns:
        |   federated_token
        |   session_id
        v
   Data Bus socket connects to session_id

5. User presses "Link this account"
        |
        | POST public/telegram_connection_edge_start
        | or equivalent provider start operation
        | includes:
        |   live_event_session_id = <Data Bus session_id>
        v
   Connection Hub backend
        |
        | verifies provider proof again
        | stores pending challenge:
        |   challenge_id
        |   provider = telegram
        |   provider_subject = 434804821
        |   integration_id = telegram.kdcube_ref
        |   live_event_session_id
        |   expires_at
        | returns:
        |   platform_claim_url
        v

6. Browser opens platform_claim_url
        |
        | /api/integrations/bundles/<tenant>/<project>/
        |   connection-hub@1-0/public/widgets/connections_settings
        |   ?claim_challenge=<challenge_id>
        v
   Claim page
        |
        | if not signed in:
        |   use /api/cp-frontend-config
        |   start normal KDCube OIDC/Cognito login
        |
        | after sign-in:
        |   show explicit "Link this external account to this KDCube account"
        v

7. User confirms the claim
        |
        | POST operations/connection_edge_challenge_claim
        | authenticated by KDCube browser session
        v
   Connection Hub backend
        |
        | reads platform user from browser session
        | reads provider subject from challenge
        | writes connection edge:
        |   telegram:434804821 -> 02e53484-...
        | emits:
        |   connection_hub.edge.changed
        | to live_event_session_id
        v

8. Original external UI receives Data Bus event
        |
        | refreshes link status
        | reconnects Data Bus so the actor session carries projected authority
        v
   UI shows:
        linked Telegram user
        linked KDCube user
        Unlink button
```

## What Each Side Must Provide

### External Runtime

It must provide proof material for the provider identity.

Examples:

```text
Telegram Mini App
  Telegram.WebApp.initData

Slack
  signed request headers/body

Webhook
  HMAC signature headers/body

OIDC app
  ID token or authorization result
```

### Host App

The host app must know which configured authenticator it is using. Use stable
authority and authenticator ids from server-side configuration.

```yaml
integrations:
  telegram.kdcube_ref:
    provider: telegram
    where: connection-hub
    enabled: true
    definition:
      bot_name: kdcube-ref
      bot_username: kdcube_doc_bot
      mini_apps:
        versatile:
          widget_alias: telegram_miniapp
```

The host passes provider proof plus the authority/authenticator hints to child
widgets:

```text
X-KDCube-Auth-Provider: telegram
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref
X-Telegram-Init-Data: <Telegram.WebApp.initData>
```

These IDs are non-secret selector hints. They do not prove identity; Connection
Hub still validates the Telegram proof against the configured authenticator
secret.

But do not make child widgets invent these ids. They come from host/server
configuration.

### Connection Hub

Connection Hub must have a matching request authenticator row:

```text
authenticator_id: telegram.kdcube_ref
authority_id: telegram.kdcube_ref
provider: telegram
secret_ref: identity.authenticators.telegram_kdcube_ref.bot_token
enabled: true
```

Secret values stay in bundle secrets or the configured secrets service. The
authenticator row stores only metadata and `secret_ref`.

## User Journey Text

For a regular user, the external UI should say:

```text
1. Confirm this Telegram account
   Press the button from inside Telegram.

2. Sign in to KDCube
   The browser opens KDCube. Sign in and approve the link.

3. Return here
   This panel updates when the browser finishes linking.
```

After the link exists, show concrete linked data:

```text
Telegram user id: 434804821
Telegram nickname: elena_viter
KDCube user id: 02e53484-...
```

Provide an `Unlink` action in the external UI.

## Live Update Rule

Do not poll the claim page from the external UI. Use Data Bus.

```text
external UI
  -> federated_data_bus_claim
  -> gets session_id
  -> opens Socket.IO Data Bus with federated_token
  -> sends live_event_session_id when starting link

browser claim page
  -> completes link
  -> Connection Hub emits connection_hub.edge.changed
     to live_event_session_id

external UI
  -> receives event
  -> refreshes link status
  -> reconnects Data Bus, now with projected authority if linked
```

Before link:

```text
actor_user_id = telegram_434804821
projected_platform_user = null
projected_grants = []
```

After link:

```text
actor_user_id = telegram_434804821
projected_platform_user = 02e53484-...
projected_grants = selected grants from the connection edge
```

The actor remains Telegram. It does not silently become the browser platform
session.

## Backend Logs That Prove It Works

During a successful unlinked-to-linked run, expect this sequence.

```text
1. request_authenticate accepted
   provider=telegram
   integration_id=telegram.kdcube_ref
   actor_user_id=telegram_434804821
   platform_user_present=False
   linked=False
   projected_grants=[]

2. data_bus claim issued
   actor_user_id=telegram_434804821
   session_id=<live-session>
   linked=False

3. Socket.IO federated connect verified
   session_id=<live-session>
   actor_user_id=telegram_434804821

4. link_start created provider claim
   challenge_id=<challenge>
   telegram_user_id=434804821
   live_event_session=<live-session>

5. challenge_claim linked
   challenge_id=<challenge>
   provider=telegram
   provider_subject=434804821
   platform_user_id=02e...
   live_event_session=<live-session>

6. socketio relay broadcasts
   type=connection_hub.edge.changed
   session_id=<live-session>

7. new data_bus claim issued
   actor_user_id=telegram_434804821
   linked=True
   projected_platform_user=02e...
   projected_grants=[identity:family, economics:platform-user, ...]

8. Socket.IO federated connect verified
   session_id=<live-session>
   actor_user_id=telegram_434804821
   projected_platform_user=02e...
```

If steps 1-6 happen and the UI does not update, the bug is in the widget
subscription or refresh handling, not in the backend link flow.

## Recipe For Another Channel

To add another channel, keep the same shape and swap only the provider proof and
authenticator.

```text
1. Define an integration id
   slack.support_bot
   whatsapp.sales
   oidc.partner_portal

2. Configure a Connection Hub request authenticator
   provider = slack / whatsapp / oidc / webhook / api-key
   authority_id = <authority id>
   authenticator_id = <verifier id>
   secret_ref = <secret path>

3. Make the host app pass auth context headers
   X-KDCube-Auth-Provider
   X-KDCube-Auth-Authority-ID
   X-KDCube-Auth-Authenticator-ID
   provider-specific proof headers/body

4. Expose or reuse a link-start operation
   verify provider proof
   create provider-proof challenge
   store live_event_session_id
   return platform_claim_url

5. Use the same claim page
   platform user signs in
   user explicitly confirms link
   Connection Hub writes provider:<subject> -> platform_user_id

6. Emit the same event
   connection_hub.edge.changed
   to the live_event_session_id

7. Refresh the external UI
   show provider subject, provider label, KDCube user id, unlink
```

## What Not To Do

- Do not let the external channel provide `platform_user_id`.
- Do not link automatically just because the browser has cookies; show an
  explicit confirmation.
- Do not store provider secrets in Postgres rows or widget config.
- Do not make iframes read provider globals such as `window.parent.Telegram`.
- Do not invent a second iframe auth protocol. Use the standard
  `CONFIG_REQUEST` / `CONFIG_RESPONSE.authContext.headers` path.
- Do not poll for link completion when Data Bus is available.
- Do not derive platform roles from provider-local roles.

## Minimal Test

```text
1. Unlink the external account.
2. Close the external runtime UI.
3. Reopen it so it starts unlinked.
4. Press "Link this account".
5. Browser opens the claim page.
6. Sign in to KDCube if needed.
7. Confirm the link.
8. External UI updates without manual refresh.
9. Logs show Data Bus event and reconnect with linked/projected authority.
```

For Telegram, the expected final state in the Mini App Connect tab is:

```text
Telegram is linked to your KDCube account.
Telegram user id: <id>
Telegram nickname: <username>
KDCube user id: <platform user id>
Unlink
```
