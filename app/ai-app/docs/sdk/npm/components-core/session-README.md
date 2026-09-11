---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/session-README.md
title: "@kdcube/components-core/session"
summary: "The browser side of one server-held platform session: probe /profile, redirect to the sign-in with a same-origin next, post the sign-out, hear kdcube-auth-changed. No token in JavaScript."
status: implementation
tags: ["sdk", "npm", "components-core", "session", "auth", "browser"]
updated_at: 2026-09-11
keywords: ["@kdcube/components-core/session", "usePlatformSession", "SessionGate", "kdcube-auth-changed", "profile probe", "sign-in redirect", "server-held session"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/server-side-login-and-platform-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
---

# @kdcube/components-core/session

A KDCube surface (a site shell, a widget, an application page) is signed in
when the server says so. The session is one HttpOnly cookie the server owns
and slides on activity; JavaScript never holds a token. What a surface needs
is small, and this module is exactly that:

| Move | Call | What happens |
|---|---|---|
| Am I signed in? | `probeSession(config)` | `GET profileUrl` with credentials. A non-ok answer or a network failure is an anonymous session, never an exception. |
| Sign in | `signIn(config, next?)` | Navigates to `loginUrl?next=<same-origin path>`. `next` defaults to the current location; a scheme, a host, `//` or a backslash never survive `safeNextPath`. Returns `false` when the deployment offers no sign-in. |
| Sign out | `signOut(config, { next?, followUpstream? })` | `POST logoutUrl?next=`. Answers `upstreamLogoutUrl` when the platform hosts the sign-in; with `followUpstream` the browser continues there so the identity provider forgets it too. |
| Hear changes | `onSessionChanged(handler)` | The in-page `kdcube-auth-changed` event and the same message relayed by a parent frame on this origin. Returns the unsubscribe. |
| Tell others | `emitSessionChanged(detail)` | Dispatches the event and relays it into `iframe[data-kdcube], iframe[data-widget]`. Emit after a probe, never from token state. |

`loadSessionConfig()` reads `/api/cp-frontend-config` and normalizes
`auth.authType`, `auth.sessionLane`, `auth.loginUrl`, `auth.profileUrl`
(default `/profile`) and `auth.logoutUrl` (default `/api/platform/logout`)
into absolute URLs. `createSessionClient(config)` binds the moves to one
config; `connectSession()` does both. `refresh(reason)` probes and announces.

Every function takes an optional environment (`fetch`, `location`,
`window`, `childFrames`), so the module runs and tests under Node without a
DOM.

## React

`@kdcube/components-react/session`:

- `usePlatformSession()` loads the config, probes once, re-probes on every
  `kdcube-auth-changed`, and exposes `session`, `authenticated`, `signIn`,
  `signOut`, `refresh`. `unavailable` is true when no frontend config could
  be read.
- `<SessionGate fallback={...} autoSignIn>` renders its children for a
  signed-in browser; otherwise the fallback, or one automatic redirect to the
  sign-in.

## In an application bundle

Apps consume the module through `shared_sources`, the way they consume the
scene runtime:

```yaml
ui:
  widgets:
    my_widget:
      src_folder: ui/widgets/my_widget
      shared_sources:
        components_core_session:
          src_folder: npm://components-core/src/session
          target: _shared/components-core/session
```

with a `tsconfig` path and a Vite alias mapping
`@kdcube/components-core/session` to `_shared/components-core/session/index.ts`.
The Connection Hub widget's `platformAuth.ts` is the reference consumer.

## Why

Three surfaces used to run their own identity client and write bearer tokens
into cookies any same-origin script could read, renewing only while one tab
stayed open. With the server-held session there is nothing to renew and
nothing to write: a surface asks the server, sends the browser to the
sign-in, and listens. The platform side is described in
[Platform-Hosted Server-Side Login](../../../service/auth/server-side-login-and-platform-session-README.md#platform-hosted-server-side-login).
