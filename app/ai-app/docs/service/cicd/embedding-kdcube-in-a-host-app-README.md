---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-kdcube-in-a-host-app-README.md
title: "Embedding KDCube In A Host App (Topologies + Auth)"
summary: "The big picture for putting a KDCube surface (bundle widget, bundle main view, or control-plane frontend) inside your own web page via iframe: the three independent browser checks (frame policy, cookies, CORS), the three deployment topologies (same-origin, same-site subdomain, cross-site) with diagrams, a decision matrix, the auth flow per topology, and the runtime config/token handshake."
tags: ["service", "cicd", "frontend", "embedding", "iframe", "auth", "cookies", "security"]
keywords: ["embed kdcube iframe", "host app integration", "same-site subdomain cookie", "cross-site iframe auth", "frame-ancestors", "samesite none cookie", "config_response token handoff", "bundle widget embedding topology", "shared parent domain cookie", "iframe auth decision matrix"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-control-plane-frontend-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---
# Embedding KDCube In A Host App (Topologies + Auth)

Use this when you put a KDCube surface inside **your own** web page as an iframe:

- a bundle **widget** (`/api/integrations/bundles/{t}/{p}/{bundle}/public/widgets/{alias}`)
- a bundle **main view** (`/api/integrations/static/...` authed, or `/public/static` anonymous)
- the **control-plane frontend** (`/platform/*`)

It is the integrator-facing picture. For the deployment/renderer contract that
emits the headers, see
[Embedding The Control Plane Frontend](embedding-control-plane-frontend-README.md).
For the bundle UI client transport, see
[Bundle Client UI](../../sdk/bundle/bundle-client-ui-README.md).

## The mental model: three independent browser checks

Embedding "working" is really **three separate browser decisions**. They are
independent — solving one does not solve the others, and people usually get
stuck because they conflate them.

```text
  HOST PAGE  (your app)                    KDCube runtime
  ┌───────────────────────┐
  │  <iframe src=KDCube>   │ ── 1. may the host FRAME KDCube? ─────────────┐
  │  ┌─────────────────┐   │      browser checks the iframe document's     │
  │  │ KDCube surface  │   │      CSP `frame-ancestors` / `X-Frame-Options`│
  │  │ (widget / UI)   │   │ <──────────────────────────────────────────── ┘
  │  │                 │   │
  │  │  fetch(KDCube)  │ ── 2. will the AUTH COOKIE be sent? ──────────────►
  │  │                 │      browser checks cookie Domain + SameSite vs
  │  │                 │      the iframe's request context (1st/3rd party)
  │  └─────────────────┘   │
  │  fetch(KDCube) ────────┼─ 3. may the HOST PAGE itself call KDCube? ────►
  └───────────────────────┘      browser checks CORS allow_origins
```

| # | Decision | Controlled by | Where |
|---|---|---|---|
| 1 | Can the host page frame the KDCube document? | `proxy.frame_embedding` → CSP `frame-ancestors` | `assembly.yaml` (proxy/nginx headers) |
| 2 | Will the auth cookie reach the iframe's requests? | cookie `Domain` + `SameSite` (where the cookie is **issued**) | login page / auth layer, not a single descriptor toggle |
| 3 | Can the **host page** (not the iframe) fetch KDCube? | `cors.allow_origins` | `assembly.yaml` |

Key facts that trip people up:

- **CORS does not control framing.** Framing is `frame-ancestors`; CORS is for
  `fetch`/XHR. Allowing an origin in CORS does not let it iframe you, and vice
  versa.
- **The iframe's own `fetch` is same-origin to the iframe**, not to the host
  page. A widget loaded from `kdcube.example.com` fetching `/api/...` hits
  `kdcube.example.com` — so it needs **no CORS** and resolves relative URLs to
  the KDCube origin, never the host origin.
- **"Cross-origin" (decision 1) and "cross-site" (decision 2) are different.**
  Two subdomains of one registrable domain are *cross-origin* (so they still
  need `frame-ancestors`) but *same-site* (so cookies behave gently).

## Topologies

### A. Same origin — one proxy serves both

```text
https://app.example.com/
  /app/*        -> your host application
  /platform/*   -> KDCube frontend
  /api/*        -> KDCube ingress/proc
  /api/integrations/* -> bundle widgets / static UI
```

Everything is one origin. Frame = same-origin, cookies = first-party, no CORS.
This is the **simplest** integration when you can route KDCube under your own
origin through one reverse proxy (e.g. local dev behind one Caddy/ngrok origin).

### B. Same-site subdomain — shared parent domain

```text
host:   https://app.example.com         (or https://example.com)
iframe: https://ai.example.com/platform/chat
                 (a different subdomain of the SAME registrable domain example.com)
```

Cross-**origin** (needs `frame-ancestors`) but same-**site** (`example.com`).
The top-level page logs in and sets the auth cookies on the **shared parent
domain**; the iframe reuses them with no in-iframe login and no `SameSite=None`.

### C. Cross-site — different registrable domains

```text
host:   https://host-app.example.net
iframe: https://kdcube.example.com/platform/chat
                 (a DIFFERENT registrable domain)
```

The iframe is a true third party. A shared cookie is impossible; the auth cookie
would have to be `SameSite=None; Secure` (a third-party cookie, increasingly
blocked), or auth is carried by a **token handoff** instead of cookies.

## Decision matrix

| | A. Same origin | B. Same-site subdomain | C. Cross-site |
|---|---|---|---|
| **Frame policy** (`proxy.frame_embedding`) | `same_origin` | `allowlist` (host origin) | `allowlist` (host origin) |
| Emits | `X-Frame-Options: SAMEORIGIN` | CSP `frame-ancestors` | CSP `frame-ancestors` |
| **Auth transport** | first-party cookie | **shared parent-domain cookie** | `SameSite=None` cookie **or** token handoff |
| Cookie `Domain` | host-only | parent domain (`example.com`) | `SameSite=None; Secure` (host-only) |
| In-iframe login needed? | no | no (reuse top-level login) | no, if the host hands tokens to the iframe |
| **CORS** (`cors.allow_origins`) | not needed | only for direct host→KDCube fetches | only for direct host→KDCube fetches |
| **API base** in the iframe | relative ⇒ the one origin | relative ⇒ KDCube subdomain | relative ⇒ KDCube domain |
| Difficulty | lowest | low | highest (browser third-party-cookie policy) |

## Auth flow per topology

**A / B — cookie-based (preferred when same-site).** A top-level login page
runs the OIDC flow and sets the two non-masquerade cookies:

| Descriptor field | Default name | Meaning |
|---|---|---|
| `auth.auth_token_cookie_name` | `__Secure-LATC` | access/auth token |
| `auth.id_token_cookie_name` | `__Secure-LITC` | identity token |

- **Same origin:** cookies are first-party; the iframe sends them automatically.
- **Same-site subdomain:** set the cookies with `Domain=<parent>` (e.g.
  `example.com`), `Secure`, `SameSite=Lax`. Because the iframe is *same-site*,
  Lax cookies are sent on its subresource requests. The iframe authenticates by
  reusing the login — **no flow runs inside the iframe**.

  ```text
  top-level login: https://app.example.com   sets  Domain=example.com; Secure
  iframe reuses:   https://ai.example.com/platform/chat   (same-site -> cookie sent)
  ```

  Make the cookie `Domain` a per-environment setting: the shared parent domain
  for the subdomain deploy, **host-only (no `Domain`)** for a single origin / a
  random tunnel host (a parent-domain cookie is rejected when the page host is
  not under that domain).

**C — cross-site, token handoff (cookie-free).** Browsers block third-party
cookies, so do not rely on the cookie reaching the iframe. Instead the host
hands auth **tokens** to the iframe over `postMessage`, and the iframe sends them
as headers. A KDCube token is **pool-scoped, not origin-scoped** — a token
minted by logging into the same identity-provider pool from any origin validates
at the KDCube backend.

```text
1. host page logs the user in (its own OIDC)  -> access_token, id_token
2. host -> iframe:  postMessage CONFIG_RESPONSE { identity, config: {
                      accessToken, idToken, idTokenHeader } }
3. iframe sends every request with
     Authorization: Bearer <accessToken>   and   <idTokenHeader>: <idToken>
4. SSE carries the same tokens as query params
```

The bundle UI already supports this: it keeps a listener for `CONFIG_RESPONSE`
and attaches `Authorization` + the id-token header to its KDCube calls. See the
config handshake below. (`SameSite=None; Secure` cookies are the alternative,
but plan around third-party-cookie blocking.)

## The runtime config / token handshake

A KDCube surface needs its runtime scope (base URL, tenant, project, and — for
cross-site — auth tokens). Resolution order:

1. `GET /api/cp-frontend-config` from the surface's **own** (KDCube) origin —
   public config (auth mode, oidc, cookie names). If it returns usable config,
   the surface does not wait for the parent.
2. Parent `CONFIG_REQUEST` → `CONFIG_RESPONSE` handshake — the host answers with
   `baseUrl`, tenant/project, and **optionally auth token metadata**. This is the
   token-handoff path for cross-site embeds.

```text
iframe -> host : { type: 'CONFIG_REQUEST', data: { requestedFields:[...], identity } }
host  -> iframe: { type: 'CONFIG_RESPONSE', identity, config: { baseUrl, defaultTenant,
                   defaultProject, accessToken?, idToken?, idTokenHeader? } }
```

The host should validate `event.origin` against the exact KDCube origin it
framed, and post with that origin as the target.

## API base inside the iframe

The browser resolves relative URLs against the document that runs the JS — the
**iframe's** origin, not the host page. So a KDCube surface fetching `/api/...`
always hits the KDCube origin. Only in topology **A** (one proxy) does a relative
`/api/...` resolve to the shared origin by design. Surfaces should not call the
host-app domain for KDCube APIs in B or C.

## Iframe sizing

The host cannot read a cross-origin iframe's DOM, so KDCube surfaces post a
cooperative resize message; the host listens and sets the height (let CSS own
the width):

```js
window.addEventListener('message', (event) => {
  if (event.origin !== KDCUBE_ORIGIN) return;
  if (event.data?.type !== 'kdcube-resize') return;
  const h = Number(event.data.height);
  if (Number.isFinite(h) && h > 0) iframe.style.height = `${Math.ceil(h)}px`;
});
```

## Checklist before going live

- Pick the topology (A/B/C) and set `proxy.frame_embedding` accordingly.
- Confirm `frame-ancestors` (or `SAMEORIGIN`) on the framed routes — including
  `/public/widgets/*`, `/public/static`, `/api/integrations/static/*`, and the
  generated-document routes — not just `/platform/*`.
- Choose the auth transport: shared parent-domain cookie (A/B) or token handoff
  (C); make the cookie `Domain` per-environment.
- Add the host origin to `cors.allow_origins` **only** if the host page makes
  direct credentialed calls to KDCube (the iframe's own calls don't need it).
- Test login, reload-inside-iframe, logout, chat streaming, uploads, downloads,
  and bundle-widget iframes from inside the embed.

## Descriptor pointers

- Frame policy + renderer contract:
  [Embedding The Control Plane Frontend](embedding-control-plane-frontend-README.md)
- Cookie names / auth section:
  [assembly descriptor](../../configuration/assembly-descriptor-README.md)
- Local dev (single origin via tunnel):
  [Serving Local KDCube With Ngrok](ngrok-README.md)
