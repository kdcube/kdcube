---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
title: "Serving Local KDCube With Ngrok"
summary: "Short operational recipe for exposing a local KDCube development stack through one ngrok HTTPS origin for Cognito, Telegram, and WebSocket/Data Bus testing."
tags: ["service", "cicd", "local", "ngrok", "cognito", "telegram"]
keywords: ["ngrok local kdcube", "kdcube web proxy", "caddy reverse proxy", "cognito callback ngrok", "telegram webhook ngrok", "socket.io websocket ngrok"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
---
# Serving Local KDCube With Ngrok

Use this when a local KDCube runtime must be reachable through a public HTTPS
origin, for example for Telegram webhooks, Telegram Mini Apps, or Cognito
callback testing.

There are three local shapes:

- **CLI-started runtime**: `kdcube start` runs Docker Compose and already puts
  the KDCube web proxy in front of frontend, ingress, and proc. This is the
  normal user path. Point ngrok directly at the web proxy port.
- **Website root through Caddy**: a local website is served at `/`, while
  KDCube runtime paths are routed to the CLI-started KDCube web proxy. This is
  the normal shape for testing website pages that embed KDCube app widgets or a
  scene. Point ngrok at Caddy, not directly at the KDCube web proxy.
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
ngrok http 5173 --url https://<stable-ngrok-domain>
```

For manual split services through Caddy:

```bash
ngrok http 18080 --url https://<stable-ngrok-domain>
```

For a website root served through Caddy, use the same ngrok target:

```bash
ngrok http 18080 --url https://<stable-ngrok-domain>
```

Do not use `--host-header=rewrite` for KDCube browser flows. The browser
configuration, static links, Socket.IO/Data Bus origin, and callback URLs must
see the public Host that the browser is using.

On a free ngrok account, use the automatically assigned Dev Domain. On a paid
account, create the desired ngrok-managed or custom domain in the ngrok
dashboard. In both cases, the important runtime rule is the same: pass the URL
explicitly with `--url`.

CLI-started runtime:

```text
https://<ngrok-domain>
  -> KDCube web proxy :<proxy-http-port>
      /api/integrations/*      -> proc
      /sse/*, /api/*           -> ingress
      /cb/socket.io/           -> ingress /socket.io/ websocket
      /*                       -> frontend
```

Manual split services:

```text
https://<ngrok-domain>
  -> Caddy :18080
      /api/integrations/* -> proc :8020
      /sse/*, /api/*      -> ingress :8010
      /cb/socket.io/*     -> ingress :8010 as /socket.io/*
      /*                  -> frontend :<frontend-port>
```

Website root through Caddy, backed by a CLI-started runtime:

```text
https://<ngrok-domain>
  -> Caddy :18080
      /*                       -> local website files/server
      /api/*, /sse/*           -> KDCube web proxy :<proxy-http-port>
      /api/integrations/*      -> KDCube web proxy :<proxy-http-port> -> proc
      /cb/socket.io/*          -> KDCube web proxy :<proxy-http-port> -> ingress websocket
      /platform/*              -> KDCube web proxy :<proxy-http-port> -> platform frontend
```

In this shape the KDCube web proxy does not serve the website root. It only
serves runtime/API/platform paths behind Caddy. If ngrok reports
`config.addr: "http://localhost:18080"`, the public website origin is entering
through Caddy first.

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

## Identify The Active Local Path

When it is unclear whether the public URL enters through Caddy or through the
KDCube web proxy, inspect the local ngrok agent:

```bash
curl http://127.0.0.1:4040/api/tunnels
```

Read `tunnels[].config.addr`:

```text
http://localhost:5173   -> ngrok enters the KDCube web proxy directly
http://localhost:18080  -> ngrok enters Caddy first
```

Then check the local ports:

```bash
curl -I http://127.0.0.1:5173/
curl -I http://127.0.0.1:18080/
```

Typical result:

```text
127.0.0.1:5173  Server: openresty  -> KDCube runtime web proxy
127.0.0.1:18080 Server: Caddy       -> website bridge / local site root
```

If a local cross-site scene emulator is running, it may expose a separate
static site server on another localhost port. That server is only the parent
site for that local cross-site test path; it is not the normal KDCube runtime
web proxy and it is not automatically the ngrok target.

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
ngrok http 5173 --url https://<stable-ngrok-domain>
```

If `kdcube start` printed another port, use that port instead:

```bash
ngrok http <proxy-http-port> --url https://<stable-ngrok-domain>
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
  handle /cb/socket.io* {
    uri strip_prefix /cb
    reverse_proxy 127.0.0.1:8010
  }
  reverse_proxy /api/* 127.0.0.1:8010
  reverse_proxy /profile* 127.0.0.1:8010
  reverse_proxy /admin/* 127.0.0.1:8010
  reverse_proxy /monitoring/* 127.0.0.1:8010

  reverse_proxy /* 127.0.0.1:<frontend-port>
}
```

Use the actual port where the frontend process listens.

> **Serving your own static site at the root instead?** To embed KDCube bundle
> widgets on your own page at the same origin (so the browser auth cookie stays
> same-site), you'd replace the catch-all `reverse_proxy /* <frontend-port>`
> with a `file_server` for your site and keep the platform-path proxies above.
> One gotcha: the frontend SPA references its hashed bundles at the ROOT
> (`/assets/*`, `/img/*`), which collide with your site's own `/assets`. Route
> those paths to serve the site file when it exists on disk and otherwise proxy
> to the frontend — Caddy's `file` matcher does this:
>
> ```caddyfile
> @kdcube path /api/* /sse/* /socket.io /socket.io/* /cb/socket.io /cb/socket.io/* /profile /profile/* /admin/* /monitoring/* /platform /platform/*
> handle @kdcube {
>   reverse_proxy 127.0.0.1:<proxy-http-port> {
>     flush_interval -1
>   }
> }
>
> @sharedAssets path /assets/* /img/*
> handle @sharedAssets {
>   @siteHasFile file
>   handle @siteHasFile { file_server }
>   handle { reverse_proxy 127.0.0.1:<frontend-port> }
> }
> ```
>
> Without it, `/platform/chat` loads its HTML but the JS/CSS 404 and the page
> renders blank. (The frontend's bundle names are hashed, so they never exist on
> your site — the `file` check routes them correctly.)

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
ngrok http 5173 --url https://<stable-ngrok-domain>
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

If the restart should also move the existing runtime to another platform ref,
add one selector: `--latest`, `--upstream`, or `--release <ref>`. Add `--build`
when images must be rebuilt.

7. Reload after bundle descriptor/config changes:

```bash
kdcube bundle reload <bundle_id> --tenant <t> --project <p>
```

This is the targeted bundle reload path: proc replays the bundle authority,
evicts that bundle from loader caches, invalidates static widget entrypoint
state, and broadcasts the changed bundle id to other workers. Details:

- [cli-README.md#bundle-reload-flow](cli-README.md#bundle-reload-flow)

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

10. Check the public Socket.IO/Data Bus websocket route when a bundle uses
    socket transport:

```bash
curl -i -m 5 --http1.1 \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Sec-WebSocket-Version: 13' \
  'https://<ngrok-domain>/cb/socket.io/?EIO=4&transport=websocket'
```

Expected: `HTTP/1.1 101 Switching Protocols`. A timeout after the 101 is fine
for this raw curl check because the websocket stays open.

11. Register Telegram webhook:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'

curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://<ngrok-domain>/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/public/telegram_webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

12. Open the app:

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
ngrok http 18080 --url https://<stable-ngrok-domain>
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

9. Check `/cb/socket.io` if any bundle uses Socket.IO/Data Bus. With the Caddy
   config above, this route is rewritten to ingress `/socket.io`.

```bash
curl -i -m 5 --http1.1 \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Sec-WebSocket-Version: 13' \
  'https://<ngrok-domain>/cb/socket.io/?EIO=4&transport=websocket'
```

Expected: `HTTP/1.1 101 Switching Protocols`.

10. Confirm the Cognito redirect URI generated by the browser.

Open browser network tools, preserve the log, then open:

```text
https://<ngrok-domain>/platform
```

Find the Cognito `authorize` request and check the `redirect_uri` query
parameter. It must exactly match one of the Cognito app client's callback URLs.

11. Register Telegram webhook:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'

curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://<ngrok-domain>/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/public/telegram_webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

12. Open the app:

```text
https://<ngrok-domain>/platform/chat
```

## Known Failure

`502 Bad Gateway` from ngrok usually means ngrok reached the local proxy target,
but that proxy could not reach the downstream service.

`404 Not Found` from `Server: Caddy` for `/cb/socket.io` means ngrok reached a
Caddy bridge that is missing the `/cb/socket.io` route. In the normal
CLI-started runtime, point ngrok directly at the KDCube web proxy port instead
of Caddy. If Caddy is intentionally in front, add `/cb/socket.io` and
`/cb/socket.io/*` to the KDCube proxy matcher, or rewrite `/cb/socket.io*` to
ingress `/socket.io*` as shown above.

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
