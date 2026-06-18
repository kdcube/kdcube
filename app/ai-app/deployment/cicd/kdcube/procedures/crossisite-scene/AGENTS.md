# Cross-Site Scene Emulator Agent Onboarding

This folder documents and carries examples for the local same-site
cross-origin scene emulator.

## What This Is

The emulator makes a local browser session behave like a PR-style KDCube scene:

```text
https://local.kdcube.tech
  embeds widgets from
https://runtime.local.kdcube.tech
```

This is intentionally same-site and cross-origin. It is used to reproduce
iframe auth, cookie, Data Bus websocket, and scene drag/drop behavior before a
website/runtime deployment.

## First Files To Read

Read these before editing local runtime files:

```text
local-emlator.md
agent-runbook-setup-and-rollback.md
examples/patched-files/README.md
```

`local-emlator.md` is the canonical explanation. It describes the browser
model, certificate setup, Cognito callback requirements, proc/ingress route
split, websocket validation, and troubleshooting signals.

`agent-runbook-setup-and-rollback.md` is the quick operational path for setup
and rollback. It must be used together with the canonical procedure.

`examples/patched-files/` contains full before/after snapshots for cases where
patches no longer apply cleanly.

## Non-Negotiable Constraints

- Keep the Data Bus websocket-only. Do not change widgets to polling fallback
  to hide websocket failures.
- Execute all agent-accessible setup/rollback steps directly. For user-owned
  steps such as sudo password entry, Cognito console changes, Keychain trust,
  Chrome restart/HSTS cleanup, or browser login, stop and give the user exact
  commands or UI actions. Do not leave those steps implicit.
- Treat `chat-proc` and `chat-ingress` as separate runtime processes:
  - `chat-proc` serves `/api/integrations/*` bundle routes.
  - `chat-ingress` serves `/socket.io` and ingress/chat routes.
- If `assembly.yaml` CORS origins change, restart both `chat-proc` and
  `chat-ingress`.
- Use the full OpenResty binary path for validation/reload:

```bash
docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -t

docker exec custom-ui-managed-infra-web-proxy-1 \
  /usr/local/openresty/nginx/sbin/nginx -s reload
```

## Origins That Must Be Understood

```text
https://local.kdcube.tech
```

Parent scene origin. This is the Cognito redirect/logout origin.

```text
https://runtime.local.kdcube.tech
```

Runtime/widget iframe origin. This origin must be allowed by runtime CORS
because widget frames open proc REST calls and Socket.IO/Data Bus websocket
connections from this origin.

Do not add query-string URLs as Cognito callback URLs. The profile query string
is a website config selector, not a redirect URI.

## Expected Validation Signals

Proc CORS:

```text
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://runtime.local.kdcube.tech
Access-Control-Allow-Credentials: true
```

Ingress websocket:

```text
HTTP/1.1 101 Switching Protocols
Access-Control-Allow-Origin: https://runtime.local.kdcube.tech
```

Scene drag/drop:

```text
[kdc-scene] context drag start ... crossOrigin: true
[kdc-scene] armed context targets ...
[kdc-scene] context drag end point ... calibrated: true
[kdc-scene] drop context ...
```

`calibrated: false` means the child widget is probably serving an old drag
protocol build that did not send coordinates on drag start.

## Rollback Discipline

Prefer real local backups:

```text
*.before-local-scene-emulator
```

Use `examples/patched-files/before/` only when backups are missing.

After restoring `assembly.yaml` and the website profile, run:

```bash
KDCUBE=${KDCUBE:-kdcube}
TENANT=${TENANT:-demo-tenant}
PROJECT=${PROJECT:-demo-project}
REPO=${REPO:-/Users/elenaviter/src/kdcube/kdcube-ai-app}

"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --path "$REPO" --build
```

`kdcube refresh` preserves staged descriptors and regenerates runtime-owned
files, including the generated proxy config. Directly copying
`nginx_proxy.conf` and reloading OpenResty is an emergency recovery path, not
the normal rollback path.
