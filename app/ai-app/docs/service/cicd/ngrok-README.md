---
id: ks:docs/service/cicd/ngrok-README.md
title: "Serving Local KDCube With Ngrok"
summary: "Short operational recipe for exposing a local KDCube development stack through one ngrok HTTPS origin for Cognito and Telegram testing."
tags: ["service", "cicd", "local", "ngrok", "cognito", "telegram"]
keywords: ["ngrok local kdcube", "caddy reverse proxy", "cognito callback ngrok", "telegram webhook ngrok"]
see_also:
  - ks:docs/service/cicd/cli-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
---
# Serving Local KDCube With Ngrok

Use this when a local KDCube runtime must be reachable through a public HTTPS
origin, for example for Telegram webhooks, Telegram Mini Apps, or Cognito
callback testing.

There are two local shapes:

- **CLI-started runtime**: `kdcube start` runs Docker Compose and already puts
  the KDCube web proxy in front of frontend, ingress, and proc. This is the
  normal user path.
- **Manual split services**: frontend, ingress, and proc are started by hand.
  In that case use a local Caddy proxy first, then point ngrok at Caddy.

Use one ngrok HTTPS URL for the whole local stack. Do not expose proc as a
separate public ngrok URL.

## Stable Ngrok Domain

Do not run ngrok without an explicit URL when Telegram, Cognito, or bundle
descriptors depend on the public origin. A plain command such as
`ngrok http 5173` can produce a different hostname, which then forces updates
to webhooks, callback URLs, CORS, and bundle config.

Use a Domain assigned to your ngrok account, then pass it every time:

```bash
ngrok http 5173 --url https://<stable-ngrok-domain> --host-header=rewrite
```

For manual split services through Caddy:

```bash
ngrok http 18080 --url https://<stable-ngrok-domain> --host-header=rewrite
```

On a free ngrok account, use the automatically assigned Dev Domain. On a paid
account, create the desired ngrok-managed or custom domain in the ngrok
dashboard. In both cases, the important runtime rule is the same: pass the URL
explicitly with `--url`.

CLI-started runtime:

```text
https://<ngrok-domain>
  -> KDCube web proxy :<proxy-http-port>
      /api/integrations/* -> proc
      /sse/*, /api/*      -> ingress
      /*                  -> frontend
```

Manual split services:

```text
https://<ngrok-domain>
  -> Caddy :18080
      /api/integrations/* -> proc :8020
      /sse/*, /api/*      -> ingress :8010
      /*                  -> frontend :<frontend-port>
```

## Descriptor Rule

KDCube runtime configuration is descriptor-driven. For this flow, edit the
active descriptor set used by the running local stack:

- `assembly.yaml` controls service ports, CORS, auth, and the frontend browser
  config served by ingress at `/api/cp-frontend-config`
- `bundles.yaml` controls bundle config, including public integration URLs
- `bundles.secrets.yaml` or the configured secrets provider controls secrets

When using the CLI, `kdcube init --tenant <t> --project <p> --descriptors-location ...`
stages the active descriptor set under:

```text
<runtime-workdir>/config/
```

That staged copy is the runtime authority for `kdcube start`. Do not configure
this flow by editing frontend-local config files or generated environment files.
The frontend must receive its browser config from ingress.

## What To Configure

### CLI-Started Runtime

This is the main path for local users.

Initialize and start the runtime:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --cors-origin https://<stable-ngrok-domain> \
  --set-secret services.openai.api_key "<openai-key>" \
  --set-secret services.anthropic.api_key "<anthropic-key>" \
  --set-secret services.git.http_token "<github-token>" \
  --set-secret git.http_token "<github-token>"

kdcube start --tenant <t> --project <p>
```

`kdcube start` prints the local UI URL. Use the port from that URL as the ngrok
target.

The port comes from the staged runtime config:

- `ports.proxy_http` / `KDCUBE_PROXY_HTTP_PORT` when explicitly configured
- otherwise `ports.ui` / `KDCUBE_UI_PORT`

For the common local descriptor shape, `ports.ui: "5173"` means:

```text
http://localhost:5173/platform/chat
```

Start ngrok against that same port:

```bash
ngrok http 5173 --url https://<stable-ngrok-domain> --host-header=rewrite
```

If `kdcube start` printed another port, use that port instead:

```bash
ngrok http <proxy-http-port> --url https://<stable-ngrok-domain> --host-header=rewrite
```

You do not need Caddy for the CLI-started runtime. Docker Compose already starts
the KDCube web proxy, and that proxy already routes frontend, ingress, and proc.

### Caddy

Caddy is only needed for the manual split-services flow. It routes one local
port to separately started frontend, ingress, and proc processes.

Install once:

```bash
brew install caddy
```

Put this file at:

```text
~/.kdcube/ngrok/Caddyfile
```

```caddyfile
:18080 {
  reverse_proxy /api/integrations/* 127.0.0.1:8020
  reverse_proxy /admin/integrations/* 127.0.0.1:8020

  reverse_proxy /sse/* 127.0.0.1:8010 {
    flush_interval -1
  }
  reverse_proxy /socket.io* 127.0.0.1:8010
  reverse_proxy /api/* 127.0.0.1:8010
  reverse_proxy /profile* 127.0.0.1:8010
  reverse_proxy /admin/* 127.0.0.1:8010
  reverse_proxy /monitoring/* 127.0.0.1:8010

  reverse_proxy /* 127.0.0.1:<frontend-port>
}
```

Use the actual port where the frontend process listens.

### Frontend / Cognito

Frontend browser config is defined in the active `assembly.yaml` used by
ingress, under `frontend.config`.

Set:

```yaml
cors:
  allow_origins:
    - "https://<ngrok-domain>"

frontend:
  config:
    auth:
      authType: "cognito"
      idTokenHeaderName: "X-ID-Token"
      oidcConfig:
        authority: "https://cognito-idp.<region>.amazonaws.com/<user-pool-id>"
        client_id: "<app-client-id>"
    routesPrefix: "/platform"
```

In Cognito app client, add:

```text
Callback URL:
https://<ngrok-domain>/platform/callback

Sign-out URL:
https://<ngrok-domain>/platform/chat

Allowed web origin:
https://<ngrok-domain>
```

### Telegram

In the active bundle descriptor, set:

```yaml
bundles:
  version: "1"
  items:
    - id: "task-and-memo-app@1-0"
      config:
        integrations:
          telegram:
            enabled: true
            webhook_url: "https://<ngrok-domain>/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/public/telegram_webhook"
```

In the active bundle secrets descriptor or configured secrets provider, set:

```yaml
bundles:
  version: "1"
  items:
    - id: "task-and-memo-app@1-0"
      secrets:
        integrations:
          telegram:
            bot_token: "<TELEGRAM_BOT_TOKEN>"
            webhook_secret: "<TELEGRAM_WEBHOOK_SECRET>"
```

## Run Steps: CLI-Started Runtime

1. Initialize/start with the CLI:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --cors-origin https://<stable-ngrok-domain>

kdcube start --tenant <t> --project <p>
```

2. Copy the local UI/proxy port printed by `kdcube start`.

Example:

```text
Open the UI:
  http://localhost:5173/platform/chat
```

The ngrok target port is `5173`.

3. Start ngrok:

```bash
ngrok http 5173 --url https://<stable-ngrok-domain> --host-header=rewrite
```

4. Confirm the ngrok HTTPS URL is the stable domain you configured.

Example:

```text
https://<stable-ngrok-domain>
```

5. CORS is handled by `--cors-origin` during init. Configure bundle public URLs
   with the relevant `kdcube bundle ... --set-config/--set-secret ...`
   commands so Telegram webhooks and any OAuth redirect/public-base URLs use
   the ngrok origin.

6. Restart after `assembly.yaml` changes:

```bash
kdcube refresh --tenant <t> --project <p>
```

7. Reload after bundle descriptor/config changes:

```bash
kdcube reload <bundle_id> --tenant <t> --project <p>
```

8. Check local proxy first:

```bash
curl -i http://127.0.0.1:<proxy-http-port>/api/cp-frontend-config
```

For the common local shape:

```bash
curl -i http://127.0.0.1:5173/api/cp-frontend-config
```

If this fails with `502`, the Docker Compose stack is not healthy or the wrong
port was used.

9. Check ngrok:

```bash
curl -i https://<ngrok-domain>/api/cp-frontend-config
```

Expected: JSON with the configured auth block and `routesPrefix: "/platform"`.

10. Register Telegram webhook:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'

curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://<ngrok-domain>/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/public/telegram_webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

11. Open the app:

```text
https://<ngrok-domain>/platform/chat
```

## Run Steps: Manual Split Services

1. Start local services by hand:

```text
frontend :<frontend-port>
ingress  :8010
proc     :8020
```

2. Start Caddy:

```bash
caddy run --config ~/.kdcube/ngrok/Caddyfile
```

3. Start ngrok:

```bash
ngrok http 18080 --url https://<stable-ngrok-domain> --host-header=rewrite
```

4. Confirm the ngrok HTTPS URL is the stable domain you configured.

Example:

```text
https://<stable-ngrok-domain>
```

5. Restart ingress after `assembly.yaml` changes.

6. Restart or reload proc after `bundles.yaml` / `bundles.secrets.yaml` changes.

7. Check local proxy first:

```bash
curl -i http://127.0.0.1:18080/api/cp-frontend-config
```

If this fails with `502`, one of the local services is not running or Caddy is
pointing at the wrong port.

8. Check ngrok:

```bash
curl -i https://<ngrok-domain>/api/cp-frontend-config
```

Expected: JSON with `auth.authType: "cognito"` and `routesPrefix: "/platform"`.

9. Confirm the Cognito redirect URI generated by the browser.

Open browser network tools, preserve the log, then open:

```text
https://<ngrok-domain>/platform
```

Find the Cognito `authorize` request and check the `redirect_uri` query
parameter. It must exactly match one of the Cognito app client's callback URLs.

10. Register Telegram webhook:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'

curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://<ngrok-domain>/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/public/telegram_webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

11. Open the app:

```text
https://<ngrok-domain>/platform/chat
```

## Known Failure

`502 Bad Gateway` from ngrok usually means ngrok reached the local proxy target,
but that proxy could not reach the downstream service.

For the CLI-started runtime, first check the KDCube web proxy port:

```bash
curl -i http://127.0.0.1:<proxy-http-port>/api/cp-frontend-config
curl -i http://127.0.0.1:<proxy-http-port>/platform/chat
```

If those fail, check Docker Compose status and logs for the runtime workdir.

For manual split services, check:

```bash
curl -i http://127.0.0.1:8010/api/cp-frontend-config
curl -i http://127.0.0.1:8020/health
curl -i http://127.0.0.1:<frontend-port>/
```

The previous IPv6 failure looked like:

```text
dial tcp [::1]:8010: connect: connection refused
```

Use `127.0.0.1` in the Caddyfile, not `localhost`.

`redirect_mismatch` from Cognito means the `redirect_uri` in the browser's
Cognito `authorize` request is not listed exactly in the Cognito app client's
callback URLs. For the CLI-started runtime the expected values are:

```text
http://localhost:<proxy-http-port>/platform/callback
https://<ngrok-domain>/platform/callback
```

For manual split services, use the local URL that the browser actually opens
for that setup, for example `http://localhost:<frontend-port>/platform/callback`
when the frontend is opened directly.
