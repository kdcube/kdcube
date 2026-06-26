---
id: docs/sdk/solutions/scene/scene-auth-README.md
title: "Scene Auth State Contract"
summary: "How a scene host announces authentication state to surfaces (kdcube-auth-changed) and hands them runtime config (CONFIG_REQUEST/CONFIG_RESPONSE), including opaque authContext header promotion for hosted widgets."
status: design
tags: ["sdk", "solutions", "scene", "auth", "session", "components", "widgets", "event-bus"]
updated_at: 2026-06-26
keywords:
  [
    "kdcube-auth-changed",
    "scene auth",
    "auth state",
    "authenticated",
    "session",
    "profile endpoint",
    "surface auth",
    "login event",
    "auth broadcast",
    "CONFIG_REQUEST",
    "CONFIG_RESPONSE",
    "CONN_RESPONSE",
    "config handshake",
    "authContext",
    "host auth context",
    "auth header promotion"
  ]
see_also:
  - docs/sdk/solutions/scene/generic-scene-contract-README.md
  - docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - docs/sdk/solutions/ecosystem-component/event-subscription-and-transport-README.md
  - src/kdcube-ai-app/npm/packages/components-core/src/scene
---
# Scene Auth State Contract

Surfaces do not carry, store, or manage authentication. Auth is **inferred from
the browser session** (cookies) and owned by the scene host (the page that runs
login/logout). A surface must never embed credentials, read tokens, or decide on
its own whether a visitor is signed in.

The problem this contract solves: a scene mounts its surfaces while the visitor
is still anonymous, and the visitor signs in *afterward*. A surface that reads
auth only once at mount captures "anonymous" forever and silently hides the
features the now-signed-in user is entitled to (for example an admin section).

The central rule:

```text
The host announces auth-state transitions.
Surfaces react to the announcement and re-derive their auth-dependent state.
Nobody polls an identity endpoint just to learn "am I signed in?".
```

## The `kdcube-auth-changed` announcement

Whenever the session transitions, the host broadcasts `kdcube-auth-changed` on
two transports so both iframe surfaces and in-page surfaces receive it:

- **iframe surfaces** — `postMessage` to every `iframe[data-kdcube]` /
  `iframe[data-widget]`:
  ```js
  frame.contentWindow.postMessage({ type: 'kdcube-auth-changed', auth: detail }, '*');
  ```
- **in-page surfaces** — a window `CustomEvent('kdcube-auth-changed', { detail })`.

The host also dispatches a window `kdcube-scene-reset` event on the same
transitions so the scene can drop per-session state.

The `auth` detail is a **coarse** snapshot — deliberately *not* a privilege
record:

```json
{
  "ready": true,
  "authenticated": true,
  "user": { "sub": "…", "email": "…", "name": "…" },
  "reason": "login"
}
```

`reason` is one of: `login`, `logout-start`, `user-loaded`,
`user-loaded-error`, `user-unloaded`, `user-signed-out`, `token-expired`. The
host fires `user-loaded` during normal page load once the session resolves, so a
surface that mounts before the session is ready still gets a transition to react
to.

## Coarse signal vs. fine-grained privileges

The announcement answers exactly one question: **is there a session, yes or
no** (`authenticated`). It intentionally omits roles and permissions.

When a surface needs fine-grained privileges (for example "is this user an admin
of my namespace?"), it derives them from the authoritative session endpoint —
`GET /profile` — which is cookie-based, never returns `403`, and returns
`{ user_type, roles, permissions, … }`. The rule:

- Probe `/profile` **only when `authenticated` is true**, and **only** to read
  privileges you actually need (roles).
- Never probe `/profile` to discover whether an anonymous visitor is "allowed"
  — an anonymous visitor is simply not privileged, and a privileged operation
  route already enforces that server-side.

`/profile` is cookie-based: a surface fetches it with `credentials: 'include'`
and sends no auth headers of its own.

## Surface responsibilities

1. Treat auth at mount as provisional, not final.
2. Subscribe to `kdcube-auth-changed` (iframe `message` and/or window event).
3. On each announcement:
   - `authenticated === false` → disable/hide authenticated-only features;
   - `authenticated === true` → re-derive privileges (probe `/profile` for the
     roles you need) and reload the data those privileges unlock.
4. Make the derivation idempotent — the host may announce the same state more
   than once (for example `user-loaded` then `login`).

## Host → iframe config handshake (carrying auth context)

The announcement above tells a surface *that* auth changed. A separate, equally
standard channel hands a surface the runtime config it needs to talk to the
backend — including whatever auth proof the host holds. This is the existing
config handshake; there is **no `kdcube.auth.*` message family**.

An iframe surface requests its config from the host on mount:

```js
window.parent.postMessage({
  type: 'CONFIG_REQUEST',
  data: { identity: 'MY_WIDGET', requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject', 'defaultAppBundleId', 'authContext'] },
}, '*');
```

The host answers with `CONFIG_RESPONSE` (a relaying host may forward the
upstream `CONN_RESPONSE` instead — surfaces accept either) whose `config` the
surface applies, filtered by `identity`:

```js
{
  type: 'CONFIG_RESPONSE',
  identity: 'MY_WIDGET',
  config: {
    baseUrl,
    accessToken,
    idToken,
    idTokenHeader,
    defaultTenant,
    defaultProject,
    defaultAppBundleId,
    authContext: { headers: { /* server-authored request headers template */ } }
  }
}
```

The host gets runtime config from its backend. Browser hosts may receive
`accessToken` / `idToken`; the surface attaches those as `Authorization` / its
ID-token header. Hosts with channel-specific auth material receive a
server-authored `authContext.headers` template and promote that template to
hosted surfaces.

The surface does not interpret `authContext.headers`. It only promotes them on
its KDCube API calls:

```ts
const headers = new Headers(baseHeaders);
Object.entries(config.authContext?.headers || {}).forEach(([name, value]) => {
  if (value) headers.set(name, String(value));
});
fetch(url, { credentials: 'include', headers });
```

`credentials: 'include'` is not a credential value. It is the browser fetch
policy that sends the normal KDCube cookie/session path when cookies are
available. Auth material that is not cookie-based travels in
`authContext.headers`.

### Telegram proof and connection id as authContext headers

A Telegram Mini App backend returns the selector headers in
`authContext.headers`. The host promotes that template and adds the
browser-owned Telegram `initData` proof only when the server template says the
provider is Telegram:

```js
{
  type: 'CONFIG_RESPONSE',
  identity: 'MY_WIDGET',
  config: {
    /* …baseUrl/tenant/project… */
    authContext: {
      headers: {
        'X-Telegram-Init-Data': '<Telegram.WebApp.initData>',
        'X-KDCube-Auth-Provider': 'telegram',
        'X-KDCube-Auth-Connection-ID': 'telegram.default'
      }
    }
  }
}
```

The `X-KDCube-Auth-Connection-ID` value is a non-secret selector handle, not a
bot id, and it comes from server config. The host only reads
`window.Telegram.WebApp.initData` and inserts that browser-owned proof into the
server-authored header map. It never sends a bot token or any server secret. The
iframe only transports the header map; the gateway and Connection Hub
authenticate centrally (see
[ecosystem-component](../ecosystem-component/ecosystem-component-README.md) for
the backend split).

Telegram auth does not require the surface to switch to a
`/public/telegram_*` API. A surface that is meant to use normal platform/gateway
auth keeps calling its standard `/operations/{alias}` routes and sends the
host-provided `authContext.headers`.

For KDCube-controlled surfaces, a connection id header should be present
whenever an external proof is present. Uncontrolled inbound hooks may lack this
hint and can fall back to provider-specific request-shape matching inside
Connection Hub.

### `kdcube-auth-changed` refreshes the handshake

The handshake is event-driven, not a one-time mount snapshot. On
`kdcube-auth-changed`, a surface **re-requests its config** (re-sends
`CONFIG_REQUEST`) and re-applies the answer; the host **re-answers** with the
current proof. This covers a proof that arrives slightly after the surface
mounts (for example the Telegram client populating `initData` late) and a
visitor who authenticates after the surface loaded — the surface reloads the
data the now-available proof unlocks.

### Worked example: memories widget in the Telegram Mini App

The memories widget is the standard surface and is used unchanged across hosts.
In a browser scene/website it receives `accessToken` / `idToken` (or relies on
the session cookie) through `CONFIG_RESPONSE`. Hosted as an iframe in the
Telegram Mini App, it receives `authContext.headers` through the **same**
`CONFIG_RESPONSE` and promotes those headers on its API calls. The Mini App host
answers the widget's `CONFIG_REQUEST` (matched on its `MEMORIES_WIDGET`
identity) and nudges it with `kdcube-auth-changed` once `initData` is available
— one handshake, both hosts, no widget-specific Telegram protocol.

## Reference implementations

- **chat** routes `kdcube-auth-changed` to `engine.refreshAuth()` in its host
  message handler.
- **news / journal** surfaces re-probe `/profile` for the admin role on the
  announcement and (re)load their admin data, so the admin console appears the
  moment the operator signs in — without it, the admin section never shows for an
  operator who authenticates after the scene mounted.

The website scene is the current reference host for the announcement (its auth
module owns login and emits the broadcast); the same contract is the target for
`@kdcube/components-core/scene`. What a component must implement to consume it is
covered in
[ecosystem-component](../ecosystem-component/ecosystem-component-README.md).
