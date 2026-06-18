# Local Same-Site Cross-Origin Scene Emulator

This procedure builds a local browser topology that behaves like a PR scene
deployment without publishing the website or widgets first.

For quick switching by an agent, use:

```text
agent-runbook-setup-and-rollback.md
```

The agent runbook should be used together with this procedure, not instead of
it. This file is the canonical explanation of why each runtime/proxy/browser
piece is needed.

The problem this solves:

- `http://localhost:<port>` as the scene host is cross-site relative to
  `https://dev.kdcube.tech` or an ngrok runtime. Browser cookies and iframe auth
  behave differently from PR deployments.
- PR deployments are cross-origin but same-site, for example
  `https://pr21.kdcube.tech` embedding widgets from `https://dev.kdcube.tech`.
- Scene drag/drop bugs can depend on that distinction.

The emulator uses two local HTTPS hostnames under the same parent domain:

```text
Parent scene page:   https://local.kdcube.tech
Local runtime host:  https://runtime.local.kdcube.tech
Website source:      /Users/elenaviter/src/kdcube/website
Runtime proxy:       local OpenResty container custom-ui-managed-infra-web-proxy-1
```

This gives a same-site but cross-origin browser setup:

```text
https://local.kdcube.tech
  embeds iframes from
https://runtime.local.kdcube.tech
```

Use `local-cross` to test local runtime/widget code. Use `staging` to compare
against deployed `dev.kdcube.tech` widgets.

Companion patch/profile examples live next to this procedure:

```text
examples/nginx_proxy.local-scene-emulator.patch
examples/assembly.local-scene-emulator.patch
examples/kdcube.config.local-cross.profile.json
examples/patched-files/before/
examples/patched-files/after/
agent-runbook-setup-and-rollback.md
```

## Preconditions

Local kdcube is running and has the OpenResty proxy container:

```bash
docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Ports}}'
```

Expected relevant container:

```text
custom-ui-managed-infra-web-proxy-1
```

The local static website server is running on port `48913`:

```bash
python3 -m http.server 48913 \
  --bind 127.0.0.1 \
  --directory /Users/elenaviter/src/kdcube/website
```

Check it:

```bash
curl -I http://127.0.0.1:48913/index.html
```

## 1. Back Up Runtime Files

These files are generated local runtime files. Keep backups before applying the
manual emulator overlay.

```bash
RUNTIME=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project

cp -a "$RUNTIME/config/nginx_proxy.conf" \
  "$RUNTIME/config/nginx_proxy.conf.before-local-scene-emulator"

cp -a "$RUNTIME/config/assembly.yaml" \
  "$RUNTIME/config/assembly.yaml.before-local-scene-emulator"
```

If the website config is changed, back it up too:

```bash
cp -a /Users/elenaviter/src/kdcube/website/kdcube.config.json \
  /Users/elenaviter/src/kdcube/website/kdcube.config.json.before-local-scene-emulator
```

## 2. Create A Temporary Local CA And Server Certificate

Use a local CA and a server leaf certificate. Trust the CA, not the served
leaf. Chrome/macOS can reject an ad-hoc self-signed leaf certificate even when
it looks trusted in Keychain; a local CA plus leaf is the normal browser shape.

Create the leaf extension file:

```bash
RUNTIME=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project

mkdir -p "$RUNTIME/data/nginx/webroot/local-certs"

cat > "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:local.kdcube.tech,DNS:runtime.local.kdcube.tech
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
```

Create the local CA:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.key" \
  -out "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.crt" \
  -subj '/CN=KDCube Local Scene Test CA' \
  -addext basicConstraints=critical,CA:TRUE \
  -addext keyUsage=critical,keyCertSign,cRLSign \
  -addext subjectKeyIdentifier=hash
```

Create and sign the server certificate:

```bash
openssl req -newkey rsa:2048 -nodes \
  -keyout "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.key" \
  -out "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.csr" \
  -subj /CN=local.kdcube.tech \
  -addext subjectAltName=DNS:local.kdcube.tech,DNS:runtime.local.kdcube.tech

openssl x509 -req \
  -in "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.csr" \
  -CA "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.crt" \
  -CAkey "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.key" \
  -CAcreateserial \
  -out "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.crt" \
  -days 7 \
  -sha256 \
  -extfile "$RUNTIME/data/nginx/webroot/local-certs/local.kdcube.tech.ext"
```

Trust the CA locally if the browser blocks the page:

```bash
security add-trusted-cert -d -r trustRoot \
  -p ssl \
  -k ~/Library/Keychains/login.keychain-db \
  "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.crt"
```

Safari normally follows the login keychain immediately. Chrome may keep showing
`NET::ERR_CERT_AUTHORITY_INVALID` even after Safari accepts the same URL. In
that case, trust the CA in the System keychain too:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -p ssl \
  -k /Library/Keychains/System.keychain \
  "$RUNTIME/data/nginx/webroot/local-certs/kdcube-local-scene-ca.crt"
```

Then restart Chrome completely:

```text
chrome://restart
```

If Chrome has cached a bad HSTS/certificate state, open
`chrome://net-internals/#hsts`, use "Delete domain security policies", and
delete both:

```text
local.kdcube.tech
runtime.local.kdcube.tech
```

For a one-off emergency test, Chrome's warning page also accepts the hidden
keyboard bypass `thisisunsafe`. Apply it to both `local.kdcube.tech` and
`runtime.local.kdcube.tech` if the runtime iframe is also blocked.

## 3. Add Local DNS

Add both local hostnames to `/etc/hosts`.

Use a replace-style command rather than `grep ... || append`: it is common to
already have `local.kdcube.tech` from an earlier test while
`runtime.local.kdcube.tech` is still missing.

```bash
sudo sed -i '' '/local\.kdcube\.tech/d;/runtime\.local\.kdcube\.tech/d' /etc/hosts
sudo sh -c "printf '\n127.0.0.1 local.kdcube.tech runtime.local.kdcube.tech\n' >> /etc/hosts"
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

Check resolution:

```bash
python3 - <<'PY'
import socket
for host in ["local.kdcube.tech", "runtime.local.kdcube.tech"]:
    print(host, socket.gethostbyname(host))
PY
```

Expected:

```text
local.kdcube.tech 127.0.0.1
runtime.local.kdcube.tech 127.0.0.1
```

## 4. Patch OpenResty

The active OpenResty config is mounted from:

```text
/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/config/nginx_proxy.conf
```

The proxy container reads it as:

```text
/usr/local/openresty/nginx/conf/nginx.conf
```

### 4.1 Runtime Host TLS

In the main `server` block, add HTTPS and make it answer
`runtime.local.kdcube.tech`.

Patch example:

```text
examples/nginx_proxy.local-scene-emulator.patch
```

Patch shape:

```diff
 server {
     listen 80;
+    listen 443 ssl;
     # Local dev: accept any host header
-    server_name _;
+    server_name _ runtime.local.kdcube.tech;
+    ssl_certificate     /var/www/letsencrypt/local-certs/local.kdcube.tech.crt;
+    ssl_certificate_key /var/www/letsencrypt/local-certs/local.kdcube.tech.key;
```

### 4.2 Runtime Host CORS

Add CORS handling in the same main `server` block. This makes runtime
responses usable from the parent scene host.

Hide upstream CORS first. The runtime backend may already emit
`Access-Control-Allow-*`; if OpenResty adds another pair without hiding the
upstream pair, Chrome rejects requests such as `/api/cp-frontend-config` and
the page logs `[kdauth] config not loaded`.

Patch shape:

```diff
     more_set_headers "X-XSS-Protection: 1; mode=block";
     more_set_headers "Referrer-Policy: strict-origin-when-cross-origin";
+    proxy_hide_header Access-Control-Allow-Origin;
+    proxy_hide_header Access-Control-Allow-Credentials;
+    add_header Access-Control-Allow-Origin      $http_origin always;
+    add_header Access-Control-Allow-Credentials "true" always;
```

The existing location-level `OPTIONS` handlers remain in place for operation
preflights.

### 4.3 Parent Website Host

Add a second `server` block near the end of the `http { ... }` block. This
serves the local website through OpenResty HTTPS while leaving the website files
served by the simple Python server on `127.0.0.1:48913`.

```nginx
    # Local same-site website emulator for PR/production scene tests.
    #
    # Parent page:
    #   https://local.kdcube.tech/?kdcube_profile=local-cross
    #
    # Runtime/widgets:
    #   https://runtime.local.kdcube.tech
    #
    # This keeps the browser origin under kdcube.tech, so auth cookies and
    # frame-embedding behavior match PR deployments more closely than a
    # localhost parent page.
    server {
        listen 443 ssl;
        server_name local.kdcube.tech;

        ssl_certificate     /var/www/letsencrypt/local-certs/local.kdcube.tech.crt;
        ssl_certificate_key /var/www/letsencrypt/local-certs/local.kdcube.tech.key;

        more_set_headers "X-Content-Type-Options: nosniff";
        more_set_headers "X-XSS-Protection: 1; mode=block";
        more_set_headers "Referrer-Policy: strict-origin-when-cross-origin";

        location / {
            proxy_pass http://host.docker.internal:48913;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $remote_addr;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
        }
    }
```

### 4.4 Test And Reload OpenResty

```bash
docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -t

docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -s reload
```

Host-side validation:

```bash
curl -I \
  'https://local.kdcube.tech/?kdcube_profile=local-cross'

curl -i \
  -H 'Origin: https://local.kdcube.tech' \
  https://runtime.local.kdcube.tech/api/cp-frontend-config

curl -i \
  -H 'Origin: https://local.kdcube.tech' \
  -H 'Access-Control-Request-Method: POST' \
  -X OPTIONS \
  'https://runtime.local.kdcube.tech/api/integrations/bundles/demo-tenant/demo-project/versatile%402026-03-31-13-36/operations/namespace_presentation_config'

curl -i \
  -H 'Origin: https://runtime.local.kdcube.tech' \
  -H 'Access-Control-Request-Method: POST' \
  -X OPTIONS \
  'https://runtime.local.kdcube.tech/api/integrations/bundles/demo-tenant/demo-project/versatile%402026-03-31-13-36/operations/namespace_presentation_config'
```

Expected:

- parent URL returns `HTTP/1.1 200 OK`
- runtime frontend config returns `HTTP/1.1 200 OK`
- these checks work without `-k`; otherwise the local CA is not trusted
- runtime frontend config includes:

```text
Access-Control-Allow-Origin: https://local.kdcube.tech
Access-Control-Allow-Credentials: true
```

There must be one `Access-Control-Allow-Origin` value and one
`Access-Control-Allow-Credentials` value. Duplicate header names here mean the
browser can still reject the request even when `curl` shows `HTTP/1.1 200 OK`.

- operation preflights return `HTTP/1.1 204 No Content`
- the proc-owned bundle operation preflight with
  `Origin: https://runtime.local.kdcube.tech` returns:

```text
Access-Control-Allow-Origin: https://runtime.local.kdcube.tech
Access-Control-Allow-Credentials: true
```

Validate the Data Bus WebSocket upgrade from the runtime iframe origin:

```bash
curl -k -m 3 -i -N \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Origin: https://runtime.local.kdcube.tech' \
  'https://runtime.local.kdcube.tech/socket.io/?EIO=4&transport=websocket'
```

Expected:

```text
HTTP/1.1 101 Switching Protocols
Access-Control-Allow-Origin: https://runtime.local.kdcube.tech
```

## 5. Patch Local Runtime Assembly

This keeps local generated proxy/frame policy and app CORS aligned with the
emulator host.

Patch example:

```text
examples/assembly.local-scene-emulator.patch
```

File:

```text
/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/config/assembly.yaml
```

Patch shape:

```diff
 proxy:
   frame_embedding:
     allowed_origins:
     - https://kdcube.tech
     - https://*.kdcube.tech
+    - https://local.kdcube.tech
     - http://localhost:48913
     - https://broodier-maxie-uninferrably.ngrok-free.dev
```

```diff
 cors:
   allow_origins:
   - http://localhost:8050
   - http://localhost:48913
+  - https://local.kdcube.tech
+  - https://runtime.local.kdcube.tech
   - https://broodier-maxie-uninferrably.ngrok-free.dev
```

Both local HTTPS origins are required:

- `https://local.kdcube.tech` is the parent scene page origin.
- `https://runtime.local.kdcube.tech` is the iframe/widget origin. Widgets that
  open Socket.IO/Data Bus connections send this value as the WebSocket `Origin`;
  the ingress rejects the upgrade with 403 if it is not in `cors.allow_origins`.

The OpenResty CORS overlay in step 4 fixes duplicate REST CORS headers at the
proxy boundary. The `assembly.yaml` `cors.allow_origins` edit is required by
both runtime processes:

- `chat-proc` serves bundle routes such as `/api/integrations/*`.
- `chat-ingress` serves `/socket.io` and validates the WebSocket `Origin`
  before accepting the upgrade.

Restart both `chat-proc` and `chat-ingress` after this edit.

## 6. Add Website Profile

File:

```text
/Users/elenaviter/src/kdcube/website/kdcube.config.json
```

Add the `local-cross` profile:

Profile example:

```text
examples/kdcube.config.local-cross.profile.json
```

```json
{
  "local-cross": {
    "authProvider": {
      "origin": "https://runtime.local.kdcube.tech"
    },
    "runtime": {
      "origin": "https://runtime.local.kdcube.tech",
      "tenant": "demo-tenant",
      "project": "demo-project",
      "apps": {
        "versatile": "versatile@2026-03-31-13-36",
        "taskTracker": "task-tracker@1-0",
        "stats": "kdcube.stats@2026-05-20-12-05",
        "news": "news@2026-05-20-12-05"
      }
    },
    "auth": {
      "cookies": {
        "secure": true,
        "sameSite": "Lax",
        "domain": "kdcube.tech"
      },
      "cognitoLogoutDomain": "https://auth.demo.kdcube.tech",
      "hostedLogout": false
    }
  }
}
```

Validate JSON:

```bash
python3 -m json.tool /Users/elenaviter/src/kdcube/website/kdcube.config.json >/dev/null
```

## 7. Cognito URLs

For login through the local parent host, the Cognito app client must allow the
parent scene URLs below.

```text
User pool: eu-west-1_JrKKhQUNp
Client ID: 6lgsqqbpatprt44a4i20hveu6u
Region: eu-west-1
```

Why only `local.kdcube.tech`:

- `auth.js` owns login in the parent page.
- It computes `redirect_uri` as `window.location.origin + "/callback.html"`.
- On this emulator, `window.location.origin` is `https://local.kdcube.tech`.
- `runtime.local.kdcube.tech` serves widgets/runtime APIs; it is not the
  Cognito redirect target.

Allowed callback URL:

```text
https://local.kdcube.tech/callback.html
```

Allowed sign-out URL:

```text
https://local.kdcube.tech/
```

The profile selection query string is not a Cognito callback URL. It is only
used by the website config loader.

Do not add these as Cognito callback URLs:

```text
https://local.kdcube.tech/?kdcube_profile=local-cross
https://runtime.local.kdcube.tech/callback.html
```

## 8. Open The Emulator

Open:

```text
https://local.kdcube.tech/?kdcube_profile=local-cross
```

For the deployed-runtime comparison path, open:

```text
https://local.kdcube.tech/?kdcube_profile=staging
```

Meaning:

| Profile | Parent | Runtime/widgets | Use |
| --- | --- | --- | --- |
| `local-cross` | local website | local runtime under `runtime.local.kdcube.tech` | Pre-release validation of local widget/runtime code |
| `staging` | local website | `https://dev.kdcube.tech` | Compare deployed widget behavior without publishing the website |

## 9. Drag/Drop Validation

Open DevTools on the parent page. A successful cross-origin context drag should
produce:

```text
[kdc-scene] context drag start ... crossOrigin: true
[kdc-scene] armed context targets ...
[kdc-scene] context drag end point ...
[kdc-scene] drop context ...
```

Interpretation:

| Log | Meaning |
| --- | --- |
| `context drag start` | Child widget announced a canonical context drag to the scene parent. |
| `armed context targets` | Scene found compatible target overlays by namespace/root target. |
| `context drag end point` | Child widget emitted drag-end coordinates and parent mapped them into parent viewport coordinates. |
| `drop context` | Parent synthesized the cross-origin drop and dispatched the target action. |

If `context drag start` appears but `context drag end point` does not appear,
the loaded child widget build does not contain the coordinate-bearing drag-end
change.

Check local widget assets:

```bash
python3 - <<'PY'
import re, subprocess, urllib.parse

checks = {
    "pinboard": "https://runtime.local.kdcube.tech/api/integrations/bundles/demo-tenant/demo-project/versatile%402026-03-31-13-36/public/widgets/pinboard/",
    "task_list": "https://runtime.local.kdcube.tech/api/integrations/bundles/demo-tenant/demo-project/task-tracker%401-0/public/widgets/task_tracker_tasks/?view=compact&host_controls=1&create=1&editor=1",
}

for name, url in checks.items():
    html = subprocess.check_output([
        "curl", "-k", "-sS",
        "--resolve", "runtime.local.kdcube.tech:443:127.0.0.1",
        url,
    ], text=True)
    print(name, "html bytes", len(html))
    for m in re.finditer(r'<script[^>]+src="([^"]+)"', html):
        asset = urllib.parse.urljoin(url, m.group(1))
        js = subprocess.check_output([
            "curl", "-k", "-sS",
            "--resolve", "runtime.local.kdcube.tech:443:127.0.0.1",
            asset,
        ], text=True)
        print(
            " ", m.group(1),
            "client_x=", "client_x" in js,
            "drag_end=", "kdcube-context-drag-end" in js,
            "context_mime=", "application/vnd.kdcube.context+json" in js,
        )
PY
```

Expected:

```text
client_x= True drag_end= True context_mime= True
```

## 10. Undo

### Restore Staged Descriptor And Website Profile

The normal rollback path is descriptor-first:

- restore `assembly.yaml`;
- restore `kdcube.config.json`;
- run `kdcube refresh`.

`kdcube refresh` preserves staged descriptors and regenerates the runtime-owned
files, including the generated proxy config, from those descriptors and the
current platform source.

```bash
RUNTIME=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project
WEBSITE=/Users/elenaviter/src/kdcube/website

cp -a "$RUNTIME/config/assembly.yaml.before-local-scene-emulator" "$RUNTIME/config/assembly.yaml"
cp -a "$WEBSITE/kdcube.config.json.before-local-scene-emulator" "$WEBSITE/kdcube.config.json"
```

If backups are missing, use the full-file `before/` snapshots:

```bash
ROOT=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/deployment/cicd/kdcube/procedures/crossisite-scene

cp -a "$ROOT/examples/patched-files/before/assembly.yaml" "$RUNTIME/config/assembly.yaml"
cp -a "$ROOT/examples/patched-files/before/kdcube.config.json" "$WEBSITE/kdcube.config.json"
```

Then run the CLI refresh:

```bash
KDCUBE=${KDCUBE:-kdcube}
TENANT=${TENANT:-demo-tenant}
PROJECT=${PROJECT:-demo-project}
REPO=${REPO:-/Users/elenaviter/src/kdcube/kdcube-ai-app}

"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --path "$REPO" --build
```

Directly restoring `nginx_proxy.conf`, validating OpenResty, and reloading the
proxy is an emergency recovery path only. The regular path is to restore the
descriptor and let `kdcube refresh` regenerate runtime-owned files.

### Remove Hosts Entry

Edit `/etc/hosts` and remove the line:

```text
127.0.0.1 local.kdcube.tech runtime.local.kdcube.tech
```

Then flush DNS:

```bash
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

### Remove Trusted Certificate

Open Keychain Access and remove the trusted `KDCube Local Scene Test CA`
certificate from the login keychain, or run:

```bash
security delete-certificate \
  -c "KDCube Local Scene Test CA" \
  ~/Library/Keychains/login.keychain-db
```

The certificate/key files under
`$RUNTIME/data/nginx/webroot/local-certs/` are local test artifacts and can be
deleted after the proxy config no longer references them.
