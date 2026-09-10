---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/browser-sign-in-situations-README.md
title: "Browser Sign-In Situations"
summary: "Every way a browser page reaches KDCube signed in: who owns the login (KDCube or the host site), where the page lives (same origin, same site, cross-site), what carries the credential (the HttpOnly session cookie, token cookies the host writes, bearer and ID-token headers), how the gateway tells them apart, and what each situation needs configured. With before and after diagrams for the website and the control plane web app."
status: active
tags: ["service", "auth", "browser", "session", "cognito", "topology", "website", "control-plane", "diagrams"]
updated_at: 2026-09-10
keywords: ["browser sign-in", "session lane", "Cognito lane", "token-bearing host", "same origin", "same site", "cross-site", "X-ID-Token", "return_origins", "accept_upstream_tokens", "kst1"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/app-hosted-platform-login-and-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-selector-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/session-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/browser-session.md
---

# Browser Sign-In Situations

A page in a browser needs to reach KDCube as a signed-in user. Two questions
decide how:

1. **Who owns the login?** KDCube (the platform hosts the sign-in and holds
   the session), or the host site (it had a login before KDCube and keeps
   it).
2. **Where does the page live?** On the platform origin, on another origin
   of the same site (`www.example.com` beside `app.example.com`), or on a
   different site altogether.

Every combination is a supported situation. This page names them, shows
what carries the credential in each, and what a deployment configures. The
mechanics of the session lane itself are in
[Application-Hosted Platform Login And Session](app-hosted-platform-login-and-session-README.md).

## The situations

| # | Login owned by | Page lives | What carries the credential to KDCube | Configure |
| --- | --- | --- | --- | --- |
| 1 | KDCube (session lane) | platform origin | the HttpOnly session cookie KDCube set | provider `browser_session`, the two `/api/platform/session/` URLs on the identity provider |
| 2 | KDCube (session lane) | same site, other origin | the same cookie: same-site requests and iframes carry it | as 1, plus `issuer.return_origins` with the page's origin, and that origin in CORS |
| 3 | KDCube (session lane) | cross-site | nothing crosses: the cookie is not sent cross-site and no token exists in the browser | not a browser situation; the host must own the login (4 or 5) or embed the platform's pages top-level |
| 4 | host site (its own IdP, one KDCube trusts) | same origin | token cookies the host writes (`__Secure-LATC` = access token, `__Secure-LITC` = ID token) | the pool among the platform's trusted Cognito providers; the host's callback and sign-out pages on its app client |
| 5 | host site (its own IdP, one KDCube trusts) | other origin, same site or cross-site | headers on every call: `Authorization: Bearer <access>` and `X-ID-Token: <id>`, passed to embedded widgets by `CONFIG_RESPONSE` | as 4, plus the page's origin in CORS and frame embedding |
| 6 | KDCube (Cognito lane, the pre-session default) | platform origin | token cookies the platform's own frontends write after their browser OIDC | provider `cognito`, the frontends' callback and sign-out pages on the app client |

Situations 4 and 5 are the same host-owned login; only the transport
differs with the origin. Situation 6 is what every deployment ran before
the session lane and what the platform's frontends still run when the
`cognito` provider is selected.

## How the gateway tells them apart

The platform authenticator is one object per deployment, chosen by the
provider `auth.connection_hub` names. On the session lane it is a
dispatcher over two managers, and the credential's shape chooses:

```text
credential (header Authorization, or the auth cookie)
   |
   +-- starts with "kst1."  -> session authority: Redis record, user, slide
   |
   +-- anything else        -> Cognito token manager built from the session
                                provider's upstream pool and its trusted
                                providers: issuer + client id must match,
                                the ID token (header or cookie) adds claims
```

So a deployment on the session lane accepts both the cookie it set itself
and the tokens a host brought from the same pools, on the same routes,
with nothing to switch per request. The token manager exists only when the
session provider's upstream is Cognito; `input.accept_upstream_tokens:
false` on the provider turns it off for a deployment that wants the session
cookie to be the only browser credential.

Machine routes (MCP and other header-only paths) never read cookies; they
take headers only, as before.

## Diagrams

### The website: before and after

The website page lives at `/` of the platform origin in the local setup
(Caddy routes `/api/*` and `/profile` to the ingress and everything else to
the platform's nginx, which serves the hosted site at `/` and the control
plane at `/platform/`). In the cloud the page lives on `kdcube.tech` and the
platform on `demo.kdcube.tech`: the same walk, with `next` allowed by
`return_origins` and the cookie carried same-site.

```text
BEFORE: situation 6, the website's own OIDC in the page
==========================================================================
browser page https://<origin>/          (auth.js + oidc-client-ts)
  |
  |-- authority, client id from /api/cp-frontend-config
  |-- redirect to Cognito hosted UI, redirect_uri /callback.html
  |<- code; callback.html exchanges it in the browser
  |-- tokens in browser storage
  |-- auth.js writes cookies (Domain=kdcube.tech when the host allows):
  |     __Secure-LATC = access token, __Secure-LITC = ID token
  |-- fetch /profile: the platform validates the cookies with Cognito
  |-- widgets (iframes from the platform) read the same cookies
  |-- renewal: signinSilent in the page, only while the tab is open
  |
logout: userManager.signoutRedirect -> Cognito -> / or /logout-complete.html

app client needs, per website origin: /callback.html, /, /logout-complete.html


AFTER: situation 1 (or 2 in the cloud), KDCube hosts the sign-in
==========================================================================
browser page https://<origin>/          (auth.js, server-session branch)
  |
  |-- /api/cp-frontend-config: authType bundle, loginUrl, no oidcConfig
  |-- click Sign in: whole tab -> /api/platform/session/login?next=<page>
  |     ingress: one-time attempt, attempt cookie, 302 -> Cognito hosted UI
  |     redirect_uri = <platform origin>/api/platform/session/callback
  |<- Cognito 302 with code -> callback
  |     ingress: code exchange, ID token verified, user record, session,
  |     __Secure-LATC = kst1 session token (HttpOnly), 302 -> next
  |-- auth.js: fetch /profile -> the user; no token in JavaScript
  |-- widgets: iframes carry the cookie (same origin or same site)
  |-- renewal: the server slides the session on activity
  |
logout: POST /api/platform/logout?next=<page> -> session ended, return cookie,
  upstreamLogoutUrl -> Cognito sign-out -> /api/platform/session/signed-out
  -> reads the return cookie -> the page

app client needs, per platform origin only:
  /api/platform/session/callback, /api/platform/session/signed-out
```

### The control plane web app: before and after

```text
BEFORE: situation 6, oidc-client-ts in the app
==========================================================================
browser https://<platform origin>/platform/chat
  |-- cognitoAuth.ts: redirect to Cognito, redirect_uri /platform/callback
  |<- code; the AuthCallback route exchanges it
  |-- authMiddleware writes __Secure-LATC and __Secure-LITC
  |-- API calls carry the cookies; the platform validates with Cognito
  |-- onAccessTokenExpiring: signinSilent
logout: signoutRedirect, logout_uri /platform/chat

app client needs: /platform/callback, /platform/chat


AFTER: situation 1, authType bundle
==========================================================================
browser https://<platform origin>/platform/chat
  |-- verifyBundleSession: GET /profile
  |-- anonymous -> /api/platform/session/login?next=/platform/chat
  |     ... the server walk above ...
  |<- 302 /platform/chat with the HttpOnly session cookie
  |-- API calls carry the cookie; the platform validates the session
logout: POST /api/platform/logout?next=/platform/chat -> upstream sign-out
  -> signed-out -> /platform/chat

app client needs: the same two /api/platform/session/ entries
```

### A host that keeps its own login

The host site existed before KDCube and authenticates its users itself,
against a Cognito pool KDCube trusts. KDCube never sees a login; it sees
the tokens the host already has.

```text
SITUATION 4: same origin, token cookies
==========================================================================
host page https://<origin>/            (the host's own login, any client)
  |-- the host obtains access + ID tokens from its pool
  |-- the host writes __Secure-LATC = access, __Secure-LITC = ID
  |-- every request to KDCube carries the cookies
  |     gateway: not "kst1." -> Cognito token manager -> issuer + client
  |     must be a trusted provider -> user with the ID token's claims
  |-- widgets (iframes, same origin) carry the same cookies

SITUATION 5: another origin, headers
==========================================================================
host page https://host.example/        (the host's own login)
  |-- the host holds access + ID tokens
  |-- calls to https://app.example/... carry
  |     Authorization: Bearer <access>   X-ID-Token: <id>
  |-- embedded widgets ask CONFIG_REQUEST; the host answers CONFIG_RESPONSE
  |     with the tokens; the widget sends the same headers
  |     gateway: same Cognito token manager, same trust rule
  |-- no cookie is involved; cross-site is fine
  |-- renewal and logout are the host's

platform needs: the host's pool among the trusted providers (the session
provider's upstream pool and its trusted_providers rows), the host origin in
CORS (5) and frame embedding (5). The identity provider needs the host's own
pages, not KDCube's.
```

## What stays, and why

The header lane (`Authorization` and `X-ID-Token`) and the Cognito token
manager stay for situations 4 and 5, and for machine clients. The session
lane replaces the platform's own frontends running an identity client in
the browser; it does not replace a host's right to bring tokens. The
consumers of the ID token on the server therefore remain, reading identity
claims from the token when a request carries one, and from the platform
user record when it carries a session.

## Choosing

- You maintain the site and it has no login of its own: situation 1 or 2.
  Nothing in the page handles tokens; use
  [`@kdcube/components-core/session`](../../sdk/npm/components-core/session-README.md).
- The site had a login before KDCube: situation 4 on the platform origin,
  situation 5 anywhere else. Trust its pool, keep its pages on its app
  client, and pass tokens to widgets through `CONFIG_RESPONSE`.
- The site is cross-site and has no login: it cannot use KDCube's cookie.
  Give it a login (5), or link to the platform's pages top-level (1).

The KDCube website reproduces any of these on demand: its profile's
`auth.loginMode` (`auto`, `platform`, `own-oidc`, with `auth.oidc` naming the
site's own identity provider) picks the login the page runs, independently
of what the platform advertises. That is the switch for emulating a site
that had a login before KDCube, and for local tests of situations 4 and 5.
