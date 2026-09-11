---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/identity-provider-urls-README.md
title: "Identity Provider URLs For The KDCube Deployments"
summary: "The record of every callback and sign-out URL the KDCube demo and dev Cognito app clients carry, each explained; the end-to-end sign-in setup across a website origin and a platform origin; and the lane-by-mode test matrix that proves every combination locally as a mini cloud before any environment switches server-side login on or off."
status: active
tags: ["service", "cicd", "cognito", "identity-provider", "callback-urls", "login-lane", "website", "mini-cloud"]
keywords: ["Cognito app client", "allowed callback URLs", "allowed sign-out URLs", "server-side login", "login lane", "own OIDC client", "loginMode", "mini cloud", "two-origin emulator", "tunnel origin", "human approval callback"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/identity-provider-urls-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/browser-sign-in-situations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/server-side-login-and-platform-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - https://github.com/kdcube/website/blob/main/README.md
---

# Identity Provider URLs For The KDCube Deployments

An identity provider only sees the redirect targets of the client that talks
to it. The generic rule, three URL sets per origin, is in
[Browser Sign-In Situations](../auth/browser-sign-in-situations-README.md#what-the-identity-provider-must-know),
and the procedure in
[Register KDCube On Your Identity Provider](../../recipes/connections/platform-authority/identity-provider-urls-README.md).
This page is the record for the KDCube deployments themselves: what the two
Cognito app clients carry and why, the end-to-end setup those entries
serve, and how every combination is tested locally before an environment
switches. It doubles as a worked example for any deployment with a website
beside the platform.

| Set | Who talks to Cognito | Callback | Sign-out |
| --- | --- | --- | --- |
| S | the platform, server-side login on | `/api/platform/session/callback` | `/api/platform/session/signed-out` |
| C | the control plane web app's own client, server-side login off | `/platform/callback` | `/platform/chat` |
| W | the website's own client (`loginMode: own-oidc`) | `/callback.html` | `/` and `/logout-complete.html` |
| H | the management human-approval sign-in (secret export and similar) | `/api/integrations/management/v1/human-approval/oidc/callback` | none |

## The layouts these URLs serve

- **Cloud**: the demo platform behind `demo.kdcube.tech`, the staging
  platform behind `dev.kdcube.tech`, and the static website on
  `kdcube.tech`, `www.kdcube.tech` and the `pr<N>.kdcube.tech` previews,
  which take their identity from the demo platform (their profile in the
  website's `kdcube.config.json` names it as the identity origin).
- **Local, one origin**: the website and a local runtime behind one proxy
  and one tunnel origin ([ngrok setup](ngrok-README.md)), so the tunnel
  origin is a platform origin and a website origin at once.
- **Local, two origins, the mini cloud**: the website at
  `local.kdcube.tech` and the runtime at `runtime.local.kdcube.tech`, the
  same-site cross-origin shape the cloud has, served by a local proxy with a
  local certificate and two hosts-file entries. Setup:
  [Test A Website That Uses KDCube Locally, Simulating The Cloud](../../recipes/setups/test-website-with-kdcube-locally-as-mini-cloud-README.md).

`NGROK` below stands for a developer's tunnel origin, whatever hostname the
tunnel has at the time. Each developer adds their own tunnel origin's rows
to both clients, since a local runtime switches pools. Rows marked optional
serve a local layout only while it is in use.

## The switches, and why nothing here changes for them

- The platform sign-in selection per environment: `auth.type` locates the
  definition and `auth.connection_hub.provider_id` selects its registry entry
  in that environment's `assembly.yaml`, followed by a refresh. Every
  maintained descriptor carries the comment with both
  values ([assembly descriptor](../../configuration/assembly-descriptor-README.md),
  [descriptors overview](descriptors-README.md)).
- The website's login: `auth.loginMode` per profile in `kdcube.config.json`
  (the website's README, section "Sign-in mode"). The cloud profiles are
  pinned to `own-oidc` until their backend switches.

With S and C on every platform origin and W on every website origin, both
switches are descriptor or config edits only. What each switch does to the
pages alive at that moment:
[Switching server-side login on and off](../auth/server-side-login-and-platform-session-README.md#switching-server-side-login-on-and-off).

## The end-to-end setup, and testing it as a mini cloud

The cloud shape is a website on one origin and the platform on another
origin of the same site, with Cognito behind both. The two-origin local
layout reproduces it, which is why every combination below can be proven on
a laptop before any environment switches.

```text
 browser
   |
   |  page: https://local.kdcube.tech            the website, profile local-cross
   |        auth.loginMode: platform | own-oidc | auto
   |
   |  platform: https://runtime.local.kdcube.tech  a local KDCube runtime
   |        assembly.yaml  auth.type + auth.connection_hub.provider_id   (the sign-in selection)
   |        provider server_login: issuer.return_origins has the website origin
   |        cors.allow_origins + frame_embedding.allowed_origins have both origins
   |
   |  Cognito app client of the pool the runtime selects
   |        S  runtime.local.kdcube.tech/api/platform/session/callback, /signed-out
   |        C  runtime.local.kdcube.tech/platform/callback, /platform/chat
   |        W  local.kdcube.tech/callback.html, /, /logout-complete.html
   v
 the same three things, with the cloud hostnames, are the kdcube.tech setup
```

Bring the local layout up with the
[setup recipe](../../recipes/setups/test-website-with-kdcube-locally-as-mini-cloud-README.md),
then walk the matrix. The platform lane is switched in
the runtime's `assembly.yaml` followed by `kdcube refresh`;
the website mode is switched in the local `kdcube.config.json`, no deploy.

| Platform lane | Website `loginMode` | What you should see | Pass when |
| --- | --- | --- | --- |
| server-side login on | `platform` | Sign in on the website navigates the tab to the runtime's `/api/platform/session/login?next=https://local.kdcube.tech/...`, through Cognito, back to the website page. | `/profile` on the runtime shows the user from the website page and from `/platform/chat`; the browser holds one `__Secure-LATC` cookie for the runtime host marked HttpOnly; a widget iframe is signed in without anything passed to it; sign out passes through Cognito and lands on the website page. |
| server-side login on | `own-oidc` | The website runs its own client against the pool (`auth.oidc` in the profile, or the pool the runtime still advertises) and writes the token cookies. | `/profile` shows the user because the gateway accepts the tokens beside the session; the token cookies are readable by JavaScript, which is the point of this mode; sign out lands on `/`. |
| server-side login off | `own-oidc` | Today's cloud shape. | Same as the row above; `/api/platform/session/status` answers `configured: false`. |
| server-side login off | `platform` | The website refuses to start a login and logs that the platform advertises no `loginUrl`. | The console error appears and nothing else breaks. This is why the cloud profiles are pinned to `own-oidc` until their backend switches. |
| either | `auto` | Behaves as `own-oidc` while the runtime advertises an OIDC client, as `platform` once it does not. | Matches the corresponding row above after the lane switch, with no website change. |

In every row the control plane web app at `/platform/chat` follows the
lane on its own: its own client and set C while server-side login is off,
the server route and set S once it is on.

The one-origin variant walks the same matrix with `return_origins` playing
no part, since `next` is a same-origin path there; it is the shape a
colleague reproduces with their own tunnel rows on both clients.

## Demo app client (demo pool, behind `demo.kdcube.tech`)

Callback URLs:

| URL | Set | What it is |
| --- | --- | --- |
| `https://demo.kdcube.tech/api/platform/session/callback` | S | The platform's own OIDC callback when server-side login is on. Cognito returns the authorization code here; the platform exchanges it and sets the session cookie. |
| `https://demo.kdcube.tech/platform/callback` | C | The control plane web app's `redirect_uri` when server-side login is off (oidc-client-ts in the browser). |
| `https://demo.kdcube.tech/api/integrations/management/v1/human-approval/oidc/callback` | H | The management human-approval sign-in. The infrastructure code registers it. |
| `NGROK/api/platform/session/callback` | S | Same as the first row, for a local runtime whose main pool is demo. |
| `NGROK/platform/callback` | C | Same as the second row, for that local runtime. |
| `NGROK/callback.html` | W | The website's own client, when the website is served at the tunnel's root. |
| `NGROK/api/integrations/management/v1/human-approval/oidc/callback` | H | Local human-approval testing. Optional. |
| `https://kdcube.tech/callback.html` | W | The public website's own OIDC callback (its profile is pinned to `own-oidc`). |
| `https://www.kdcube.tech/callback.html` | W | Same, for the `www` host. |
| `https://pr<N>.kdcube.tech/callback.html` | W | Same, for a website preview deployment; current previews only. |
| `https://runtime.local.kdcube.tech/api/platform/session/callback` | S | The two-origin local layout's platform origin. Optional. |
| `https://runtime.local.kdcube.tech/platform/callback` | C | Same layout, control plane web app. Optional. |
| `https://local.kdcube.tech/callback.html` | W | The local website in the two-origin layout. Optional. |

Sign-out URLs:

| URL | Set | What it is |
| --- | --- | --- |
| `https://demo.kdcube.tech/api/platform/session/signed-out` | S | Cognito's post-logout target on server-side login. The route reads the return cookie and continues to the page the user left. |
| `https://demo.kdcube.tech/platform/chat` | C | The control plane web app's post-logout page when it runs its own client. |
| `NGROK/api/platform/session/signed-out` | S | Same as the first row, local runtime. |
| `NGROK/platform/chat` | C | Same as the second row, local runtime. |
| `NGROK/` | W | Where the website's redirect sign-out lands, when the website is served at the tunnel's root. |
| `NGROK/logout-complete.html` | W | The page the website's popup sign-in mode completes on, same condition. |
| `https://kdcube.tech/` | W | The public website's redirect sign-out landing. |
| `https://kdcube.tech/logout-complete.html` | W | The public website's popup completion page. |
| `https://www.kdcube.tech/` | W | Same, `www`. |
| `https://www.kdcube.tech/logout-complete.html` | W | Same, `www`. |
| `https://pr<N>.kdcube.tech/` | W | Same, previews. |
| `https://pr<N>.kdcube.tech/logout-complete.html` | W | Same, previews. |
| `https://runtime.local.kdcube.tech/api/platform/session/signed-out` | S | Two-origin layout. Optional. |
| `https://runtime.local.kdcube.tech/platform/chat` | C | Two-origin layout. Optional. |
| `https://local.kdcube.tech/` | W | Two-origin layout, local website. Optional. |
| `https://local.kdcube.tech/logout-complete.html` | W | Two-origin layout, local website. Optional. |

## Dev app client (staging pool, behind `dev.kdcube.tech`)

No public website origin appears here: `kdcube.tech`, `www` and the previews
take identity from the demo platform, and no host rule in the website's
configuration selects the `staging` profile.

Callback URLs:

| URL | Set | What it is |
| --- | --- | --- |
| `https://dev.kdcube.tech/api/platform/session/callback` | S | The platform's own OIDC callback on server-side login. |
| `https://dev.kdcube.tech/platform/callback` | C | The control plane web app's `redirect_uri` when server-side login is off. |
| `https://dev.kdcube.tech/api/integrations/management/v1/human-approval/oidc/callback` | H | Management human-approval sign-in. The infrastructure code registers it. |
| `NGROK/api/platform/session/callback` | S | Local runtime whose main pool is dev. |
| `NGROK/platform/callback` | C | Same. |
| `NGROK/callback.html` | W | The website served at the tunnel's root, same condition. |
| `NGROK/api/integrations/management/v1/human-approval/oidc/callback` | H | Local human-approval testing. Optional. |
| `https://runtime.local.kdcube.tech/api/platform/session/callback` | S | Two-origin layout. Optional. |
| `https://runtime.local.kdcube.tech/platform/callback` | C | Two-origin layout. Optional. |
| `https://local.kdcube.tech/callback.html` | W | Two-origin layout, local website. Optional. |

Sign-out URLs:

| URL | Set | What it is |
| --- | --- | --- |
| `https://dev.kdcube.tech/api/platform/session/signed-out` | S | Cognito's post-logout target on server-side login. |
| `https://dev.kdcube.tech/platform/chat` | C | The control plane web app's post-logout page. |
| `NGROK/api/platform/session/signed-out` | S | Local runtime. |
| `NGROK/platform/chat` | C | Local runtime. |
| `NGROK/` | W | Website redirect sign-out landing, website at the tunnel's root. |
| `NGROK/logout-complete.html` | W | Website popup completion page, same condition. |
| `https://runtime.local.kdcube.tech/api/platform/session/signed-out` | S | Two-origin layout. Optional. |
| `https://runtime.local.kdcube.tech/platform/chat` | C | Two-origin layout. Optional. |
| `https://local.kdcube.tech/` | W | Two-origin layout. Optional. |
| `https://local.kdcube.tech/logout-complete.html` | W | Two-origin layout. Optional. |

Everything else that accumulated on the clients over time (an earlier route
prefix, bare `/callback`, `/index.html`, `/platform/signedout`, dev-server
ports, retired tunnel hosts) is produced by no current client and can be
removed.
