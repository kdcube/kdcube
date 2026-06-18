# Agent Runbook: Local Cross-Site Scene Emulator

This is the short operational runbook for an agent that needs to switch the
local machine into, and back out of, the same-site cross-origin scene emulator.

Canonical procedure:

```text
local-emlator.md
```

Before changing local runtime files, read `local-emlator.md` at least through
the setup, validation, and undo sections. This runbook is the quick command
path; the canonical procedure explains the browser/site model, Cognito URLs,
certificate details, and failure interpretation.

Full-file examples:

```text
examples/patched-files/before/
examples/patched-files/after/
```

## Agent And User Boundary

An agent should execute every filesystem, Docker, validation, and descriptor
step it can complete directly. When a required step needs user-only authority
or a browser/console action, the agent must stop and give the user exact
commands or clicks, then report what remains blocked until the user completes
that step.

User-owned steps include:

- entering a sudo password for `/etc/hosts`, DNS cache, or System keychain
  changes;
- approving or changing Cognito callback/logout URLs in the AWS console or via
  authenticated AWS credentials that are not available to the agent;
- completing browser trust actions such as Chrome `chrome://restart`,
  `chrome://net-internals/#hsts`, or Keychain trust prompts;
- signing in through the browser after the local origin changes.

When such a step is needed, say exactly what to run or do. Example rollback
handoff:

```text
I restored descriptors and ran `kdcube refresh`. I could not remove the
temporary hosts entry because sudo needs your password. Please run:

sudo sed -i '' '/local\.kdcube\.tech/d;/runtime\.local\.kdcube\.tech/d' /etc/hosts
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

## Scope

The emulator makes the local website look like a PR-style scene:

```text
https://local.kdcube.tech
  embeds widgets from
https://runtime.local.kdcube.tech
```

The setup intentionally stays websocket-only for the Data Bus. A websocket
failure is a runtime/proxy/origin bug to fix, not a reason to fall back to
polling.

## Fast Setup

Set paths:

```bash
ROOT=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/deployment/cicd/kdcube/procedures/crossisite-scene
RUNTIME=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project
WEBSITE=/Users/elenaviter/src/kdcube/website
COMPOSE=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/repo/app/ai-app/deployment/docker/custom-ui-managed-infra/docker-compose.yaml
ENVFILE=/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/config/.env
```

Create backups if they do not already exist:

```bash
test -f "$RUNTIME/config/nginx_proxy.conf.before-local-scene-emulator" || \
  cp -a "$RUNTIME/config/nginx_proxy.conf" "$RUNTIME/config/nginx_proxy.conf.before-local-scene-emulator"

test -f "$RUNTIME/config/assembly.yaml.before-local-scene-emulator" || \
  cp -a "$RUNTIME/config/assembly.yaml" "$RUNTIME/config/assembly.yaml.before-local-scene-emulator"

test -f "$WEBSITE/kdcube.config.json.before-local-scene-emulator" || \
  cp -a "$WEBSITE/kdcube.config.json" "$WEBSITE/kdcube.config.json.before-local-scene-emulator"
```

Copy the known-good emulator files:

```bash
cp -a "$ROOT/examples/patched-files/after/nginx_proxy.conf" "$RUNTIME/config/nginx_proxy.conf"
cp -a "$ROOT/examples/patched-files/after/assembly.yaml" "$RUNTIME/config/assembly.yaml"
cp -a "$ROOT/examples/patched-files/after/kdcube.config.json" "$WEBSITE/kdcube.config.json"
```

Ensure local hostnames resolve:

```bash
sudo sed -i '' '/local\.kdcube\.tech/d;/runtime\.local\.kdcube\.tech/d' /etc/hosts
sudo sh -c "printf '\n127.0.0.1 local.kdcube.tech runtime.local.kdcube.tech\n' >> /etc/hosts"
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

If sudo prompts for a password that the agent cannot provide, hand this block
to the user and wait for confirmation before validating browser behavior.

Create/trust certificates if `local.kdcube.tech` is not already trusted. Use
the full certificate commands in `local-emlator.md`.

If certificate trust requires Keychain UI, System keychain sudo, Chrome
restart, or HSTS cleanup, hand the exact steps from `local-emlator.md` to the
user. The agent should then re-run the `curl`/browser validation after the user
confirms completion.

Start the website source server if needed:

```bash
python3 -m http.server 48913 --bind 127.0.0.1 --directory "$WEBSITE"
```

Reload/restart runtime pieces:

```bash
docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -t

docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -s reload

docker compose -f "$COMPOSE" --env-file "$ENVFILE" restart chat-proc chat-ingress
```

Open:

```text
https://local.kdcube.tech/?kdcube_profile=local-cross
```

## Required External Auth Setup

Cognito must allow the parent scene origin:

```text
callback: https://local.kdcube.tech/callback.html
logout:   https://local.kdcube.tech/
```

The runtime host is not a Cognito redirect target.

If the agent cannot update Cognito itself, it must tell the user to add those
two URLs before login validation. The agent should not treat a login failure as
a scene/runtime bug until the Cognito redirect/logout URLs are confirmed.

## Validation

REST CORS through proc:

```bash
curl -k -i \
  -H 'Origin: https://runtime.local.kdcube.tech' \
  -H 'Access-Control-Request-Method: POST' \
  -X OPTIONS \
  'https://runtime.local.kdcube.tech/api/integrations/bundles/demo-tenant/demo-project/versatile%402026-03-31-13-36/operations/namespace_presentation_config'
```

Expected:

```text
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://runtime.local.kdcube.tech
Access-Control-Allow-Credentials: true
```

Data Bus websocket through ingress:

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

Scene drag logs should show:

```text
[kdc-scene] context drag start ... crossOrigin: true
[kdc-scene] armed context targets ...
[kdc-scene] context drag end point ... calibrated: true
[kdc-scene] drop context ...
```

## Fast Rollback

Prefer the local backups made at setup time for the staged descriptors and
website profile:

```bash
cp -a "$RUNTIME/config/assembly.yaml.before-local-scene-emulator" "$RUNTIME/config/assembly.yaml"
cp -a "$WEBSITE/kdcube.config.json.before-local-scene-emulator" "$WEBSITE/kdcube.config.json"
```

If backups are missing, use the example rollback files:

```bash
cp -a "$ROOT/examples/patched-files/before/assembly.yaml" "$RUNTIME/config/assembly.yaml"
cp -a "$ROOT/examples/patched-files/before/kdcube.config.json" "$WEBSITE/kdcube.config.json"
```

Then let the KDCube CLI regenerate runtime-managed files and restart the stack.
`refresh` does not touch staged descriptors; it regenerates the platform/runtime
files around the restored descriptors.

```bash
KDCUBE=${KDCUBE:-kdcube}
TENANT=${TENANT:-demo-tenant}
PROJECT=${PROJECT:-demo-project}
REPO=${REPO:-/Users/elenaviter/src/kdcube/kdcube-ai-app}

"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --path "$REPO" --build
```

Remove the local DNS override if the emulator is not needed:

```bash
sudo sed -i '' '/local\.kdcube\.tech/d;/runtime\.local\.kdcube\.tech/d' /etc/hosts
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

If sudo prompts for a password that the agent cannot provide, report rollback
as complete except for this host/DNS cleanup and give the user the command
block above.

Remove the trusted local CA only when no other active emulator depends on it:

```bash
security delete-certificate \
  -c "KDCube Local Scene Test CA" \
  ~/Library/Keychains/login.keychain-db
```

If it was trusted in the System keychain, remove it there as well.

## Notes For Agents

- Keep `chat-proc` and `chat-ingress` in sync with `assembly.yaml`.
- Keep the Data Bus websocket transport as websocket-only.
- Refresh `examples/patched-files/after/` after a known-good emulator run if
  the local generated descriptors or proxy file shape changes.
- Use backups first for rollback because local runtime descriptors may include
  local machine-specific values.
- Directly copying `nginx_proxy.conf` and reloading OpenResty is an emergency
  recovery path only. Normal rollback restores descriptors/profile and runs
  `kdcube refresh`.
- Never hide a user-owned step. If sudo, Cognito, Keychain, Chrome, or browser
  login work remains, say so explicitly and provide the exact action for the
  user.
