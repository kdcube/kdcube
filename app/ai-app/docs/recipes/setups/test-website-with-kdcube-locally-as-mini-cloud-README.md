---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/setups/test-website-with-kdcube-locally-as-mini-cloud-README.md
title: "Test A Website That Uses KDCube Locally, Simulating The Cloud"
summary: "Step by step: run a website and a local KDCube runtime on two HTTPS hostnames under one parent domain, the same-site cross-origin shape a cloud deployment has, and prove sign-in, cookies, embedded widgets and every login mode there before touching an environment."
status: active
tags: ["recipes", "setups", "website", "local", "mini-cloud", "same-site", "cross-origin", "cognito", "login-lane", "scene"]
updated_at: 2026-09-11
keywords: ["mini cloud", "local emulator", "same-site cross-origin", "local.kdcube.tech", "runtime.local.kdcube.tech", "local CA", "hosts file", "OpenResty patch", "cors.allow_origins", "frame_embedding", "return_origins", "loginMode", "kdcube_profile"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/identity-provider-urls-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/browser-sign-in-situations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/identity-provider-urls-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/website-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - https://github.com/kdcube/website/blob/main/README.md
---

# Test A Website That Uses KDCube Locally, Simulating The Cloud

In the cloud the website and the platform live on different origins of one
site: `kdcube.tech` beside `demo.kdcube.tech`. A page on `localhost` does
not reproduce that: it is cross-site to any runtime, so cookies, embedded
widgets and sign-in behave differently from production, and a bug that
depends on the same-site rule never shows. This recipe gives you the cloud
shape on one machine: two HTTPS hostnames under one parent domain, the
website on one, a local runtime on the other. With it you can prove sign-in
in every login mode, the cookie behaviour, widget embedding and sign-out
before any environment switches.

```text
https://local.kdcube.tech            the website (a static checkout)
        |  page loads config, probes /profile, embeds widgets
        v
https://runtime.local.kdcube.tech    a local KDCube runtime behind its proxy
        |
        v
identity provider (Cognito): the app client of the pool the runtime selects
```

Any two hostnames under one parent domain work; the examples use these two.
The runtime directory below is `$RUNTIME`, normally
`~/.kdcube/kdcube-runtime/<tenant>__<project>`.

## Before you start

- A local KDCube runtime is running with its web proxy container (the
  Docker Compose name is `custom-ui-managed-infra-web-proxy-1`).
- The website checkout is served by any static server on a loopback port,
  for example:

  ```bash
  python3 -m http.server 48913 --bind 127.0.0.1 --directory <website checkout>
  ```

- Back up the two generated runtime files you will patch:
  `$RUNTIME/config/nginx_proxy.conf` and `$RUNTIME/config/assembly.yaml`.
  A `kdcube refresh` regenerates the proxy file; re-apply Step 3 after each.

## Step 1. A local certificate the browser trusts

Create a local CA and a leaf certificate for both hostnames; trust the CA,
not the leaf (browsers reject ad-hoc self-signed leaves even when "trusted").

```bash
CERTS="$RUNTIME/data/nginx/webroot/local-certs"; mkdir -p "$CERTS"
cat > "$CERTS/local.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:local.kdcube.tech,DNS:runtime.local.kdcube.tech
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout "$CERTS/local-ca.key" -out "$CERTS/local-ca.crt" \
  -subj '/CN=KDCube Local Test CA' \
  -addext basicConstraints=critical,CA:TRUE \
  -addext keyUsage=critical,keyCertSign,cRLSign -addext subjectKeyIdentifier=hash
openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERTS/local.key" -out "$CERTS/local.csr" -subj /CN=local.kdcube.tech \
  -addext subjectAltName=DNS:local.kdcube.tech,DNS:runtime.local.kdcube.tech
openssl x509 -req -in "$CERTS/local.csr" -CA "$CERTS/local-ca.crt" \
  -CAkey "$CERTS/local-ca.key" -CAcreateserial -out "$CERTS/local.crt" \
  -days 7 -sha256 -extfile "$CERTS/local.ext"
```

Trust the CA (macOS shown; on Chrome trust it in the System keychain and
restart the browser; on Linux use the distribution's CA store):

```bash
security add-trusted-cert -d -r trustRoot -p ssl \
  -k ~/Library/Keychains/login.keychain-db "$CERTS/local-ca.crt"
```

## Step 2. Local DNS

```bash
sudo sh -c "printf '\n127.0.0.1 local.kdcube.tech runtime.local.kdcube.tech\n' >> /etc/hosts"
```

Flush the resolver cache on macOS with `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.

## Step 3. The proxy: two HTTPS hosts

Patch `$RUNTIME/config/nginx_proxy.conf` (the proxy container mounts it as
its `nginx.conf`). The certificate directory is mounted at
`/var/www/letsencrypt/local-certs` inside the container.

1. **The runtime host.** In the main `server` block add HTTPS and the
   hostname:

   ```nginx
   listen 443 ssl;
   server_name _ runtime.local.kdcube.tech;
   ssl_certificate     /var/www/letsencrypt/local-certs/local.crt;
   ssl_certificate_key /var/www/letsencrypt/local-certs/local.key;
   ```

2. **CORS for the website origin**, in the same block. Hide the upstream's
   CORS pair first; a duplicated header pair makes the browser reject
   `/api/cp-frontend-config` even though `curl` shows 200:

   ```nginx
   proxy_hide_header Access-Control-Allow-Origin;
   proxy_hide_header Access-Control-Allow-Credentials;
   add_header Access-Control-Allow-Origin      $http_origin always;
   add_header Access-Control-Allow-Credentials "true" always;
   ```

3. **The website host**, a second `server` block near the end of `http`,
   serving the static checkout through HTTPS:

   ```nginx
   server {
       listen 443 ssl;
       server_name local.kdcube.tech;
       ssl_certificate     /var/www/letsencrypt/local-certs/local.crt;
       ssl_certificate_key /var/www/letsencrypt/local-certs/local.key;
       location / {
           proxy_pass http://host.docker.internal:48913;
           proxy_set_header Host              $host;
           proxy_set_header X-Real-IP         $remote_addr;
           proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
   }
   ```

Test and reload:

```bash
docker exec custom-ui-managed-infra-web-proxy-1 /usr/local/openresty/nginx/sbin/nginx -t
docker exec custom-ui-managed-infra-web-proxy-1 /usr/local/openresty/nginx/sbin/nginx -s reload
```

## Step 4. The runtime knows the website origin

In `$RUNTIME/config/assembly.yaml`, both origins must be allowed by the
platform processes, not only by the proxy: `chat-proc` serves the bundle
routes and `chat-ingress` validates the WebSocket `Origin` before accepting
an upgrade.

```yaml
cors:
  allow_origins:
    - https://local.kdcube.tech
    - https://runtime.local.kdcube.tech
proxy:
  frame_embedding:
    allowed_origins:
      - https://local.kdcube.tech
```

If the runtime hosts the sign-in (the `server_login` provider), the
website origin must also be a return origin on that provider in
`bundles.yaml`, so a sign-in started from the website comes back to it:

```yaml
issuer:
  return_origins:
    - https://local.kdcube.tech
```

Restart `chat-proc` and `chat-ingress` (`kdcube stop` and `kdcube start`).

## Step 5. The website profile

In the website's `kdcube.config.json`, add a profile that names the runtime
as identity origin and default runtime, and pick it on the page with
`?kdcube_profile=<name>`:

```json
"local-cross": {
  "authProvider": { "origin": "https://runtime.local.kdcube.tech" },
  "runtimes": {
    "default": { "origin": "https://runtime.local.kdcube.tech", "tenant": "<tenant>", "project": "<project>" }
  },
  "auth": {
    "loginMode": "auto",
    "cookies": { "secure": true, "sameSite": "Lax", "domain": "kdcube.tech" },
    "cognitoLogoutDomain": "https://<hosted ui domain>",
    "hostedLogout": true
  }
}
```

Two rules hide here:

- `loginMode` is the switch you will flip in Step 8: `auto` follows the
  runtime's lane, `platform` forces the platform-hosted sign-in, `own-oidc`
  forces the website's own OIDC client.
- The cookie `domain` matters only for `own-oidc`: the website writes the
  token cookies itself, and a host-only cookie on `local.kdcube.tech` would
  never reach the widget iframes on `runtime.local.kdcube.tech`. Under the
  platform-hosted sign-in the runtime sets its own host-only cookie and the
  same-site rule carries it to the website's requests and iframes; the
  website writes nothing.

## Step 6. The identity provider

On the app client of the pool the runtime selects, register the runtime
host's two session routes and the control plane's own pages, and the
website host's own client pages:

```text
callbacks
  https://runtime.local.kdcube.tech/api/platform/session/callback
  https://runtime.local.kdcube.tech/platform/callback
  https://local.kdcube.tech/callback.html
sign-outs
  https://runtime.local.kdcube.tech/api/platform/session/signed-out
  https://runtime.local.kdcube.tech/platform/chat
  https://local.kdcube.tech/
  https://local.kdcube.tech/logout-complete.html
```

Why these and not others: [Register KDCube On Your Identity Provider](../connections/platform-authority/identity-provider-urls-README.md).
The KDCube deployments' own record: [Identity Provider URLs For The KDCube Deployments](../../service/cicd/identity-provider-urls-README.md).

## Step 7. Open it, and check the plumbing first

```text
https://local.kdcube.tech/?kdcube_profile=local-cross
```

Before signing in, prove the transport from the shell (no `-k`: if these
need it, the CA is not trusted):

```bash
curl -I 'https://local.kdcube.tech/?kdcube_profile=local-cross'
curl -i -H 'Origin: https://local.kdcube.tech' https://runtime.local.kdcube.tech/api/cp-frontend-config
curl -k -m 3 -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Origin: https://runtime.local.kdcube.tech' \
  'https://runtime.local.kdcube.tech/socket.io/?EIO=4&transport=websocket'
```

Pass: the page is 200; the config answer carries exactly one
`Access-Control-Allow-Origin: https://local.kdcube.tech` and one
`Access-Control-Allow-Credentials: true`; the upgrade answers `101`.

## Step 8. Walk the login matrix

Switch the runtime's lane with the two lines in `assembly.yaml`
(`auth.type`, `auth.connection_hub.provider_id`) and a restart; switch the
website's mode with `loginMode` in the local config, no deploy. Then, for
each combination, sign in from the website, open `/platform/chat`, use a
widget, sign out. The table of what to expect and the pass criteria per
cell, including the deliberate failure of `platform` mode against a runtime
with server-side login off:
[the mini-cloud test matrix](../../service/cicd/identity-provider-urls-README.md#the-end-to-end-setup-and-testing-it-as-a-mini-cloud).

Two checks that catch the classic mistakes:

- In DevTools, the chat widget's conversation list request goes to
  `https://runtime.local.kdcube.tech/api/cb/conversations/...` and answers
  the expected `user_id`. A parent page that looks signed in while the
  iframe shows another user means the cookie never reached the iframe: on
  `own-oidc` the cookie domain is wrong, on the platform-hosted sign-in the
  two hosts are not under one parent domain.
- Under the platform-hosted sign-in the runtime host's `__Secure-LATC`
  cookie shows as HttpOnly in the cookie inspector and no `__Secure-LITC`
  exists; under `own-oidc` both exist and JavaScript can read them.

## Step 9. Undo

Restore the two backed-up runtime files and reload the proxy, remove the
hosts entries, remove the website profile if it was temporary, and delete
the trusted CA from the keychain. The identity-provider entries may stay;
they are exact hostnames that serve nothing else.
