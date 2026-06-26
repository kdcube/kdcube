---
id: docs/sdk/solutions/scene/scene-auth-README.md
title: "Scene Auth State Contract"
summary: "How a scene host announces authentication state to surfaces (kdcube-auth-changed) and how surfaces react to it instead of carrying or polling for auth."
status: design
tags: ["sdk", "solutions", "scene", "auth", "session", "components", "widgets", "event-bus"]
updated_at: 2026-06-24
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
    "auth broadcast"
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
