---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/identity-provider-urls-README.md
title: "Register KDCube On Your Identity Provider"
summary: "Step by step: which callback and sign-out URLs an identity provider such as Cognito needs for every login configuration KDCube supports, per origin and per app client, so that server-side login can be switched on or off and a site can run its own OIDC client without touching the identity provider again."
status: active
tags: ["recipes", "connections", "platform-authority", "cognito", "oidc", "callback-urls", "session-lane", "website"]
updated_at: 2026-09-10
keywords: ["Cognito callback URLs", "allowed sign-out URLs", "app client", "server-side login", "session lane", "own OIDC client", "loginMode", "return_origins", "signed-out route"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/browser-sign-in-situations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/app-hosted-platform-login-and-session-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
---

# Register KDCube On Your Identity Provider

An identity provider only ever sees the redirect targets of the client that
talks to it. So the URLs to register on an app client follow from one
question per origin: which clients may run an OIDC flow there? This recipe
walks that question for every configuration KDCube supports, so that you
register once and then switch server-side login on or off, or let a site run
its own login, without returning to the identity provider.

The situations behind the configurations, with diagrams, are in
[Browser Sign-In Situations](../../../service/auth/browser-sign-in-situations-README.md).

## Step 1. List your origins

Two kinds:

- **Platform origins**: where KDCube answers. The cloud hostname of each
  environment, and any local public origin (a tunnel, a local hostname)
  through which you reach a local runtime.
- **Site origins**: where a page that uses KDCube lives without being served
  by KDCube: your website, its preview hosts, a local copy.

An origin that serves both (a site at the platform's root through a local
proxy) is on both lists.

## Step 2. Know the three URL sets

| Set | Who talks to the identity provider | Callback URL | Sign-out URLs |
| --- | --- | --- | --- |
| S | the platform, server-side login on | `<platform origin>/api/platform/session/callback` | `<platform origin>/api/platform/session/signed-out` |
| C | the control plane web app's own client, server-side login off | `<platform origin>/platform/callback` | `<platform origin>/platform/chat` |
| W | a site's own OIDC client (the KDCube website in `own-oidc`) | `<site origin>/callback.html` | `<site origin>/` and `<site origin>/logout-complete.html` |

Set S is two fixed URLs because the platform stores the destination of a
sign-in or sign-out itself and returns to one route; no page URL of any
frontend is registered. Set W is what the website's `auth.js` produces when
it runs oidc-client-ts; a different site with its own client has its own
pages, register those instead.

## Step 3. Decide what may run where

- A platform origin gets **S** if it may run server-side login, **C** if it
  may run the Cognito lane, both if you want to switch.
- A site origin gets **W** only if the site may run its own client. On
  server-side login a site needs nothing on the identity provider: the
  platform's `issuer.return_origins` brings the browser back to it.
- An origin on both lists gets S, C and W.

## Step 4. Assign origins to app clients

Each app client belongs to one user pool. An origin goes on the client of
the pool it authenticates against:

- a cloud platform origin: the client of its environment's pool;
- a site origin: the client of the pool its platform origin uses (the
  website's profile names that platform origin as its identity origin);
- a local runtime that switches pools: both clients.

## Step 5. Register

On each app client, add the callback URLs of every set the origin carries to
"Allowed callback URLs" and the sign-out URLs to "Allowed sign-out URLs".
Exact strings, `https` everywhere except `http://localhost`, no fragments.
For Cognito the management human-approval sign-in adds one more callback on
a platform origin, `/api/integrations/management/v1/human-approval/oidc/callback`;
cloud environments register it through their infrastructure code, a local
runtime only when human approval is tested there.

Remove what no client produces any more: earlier route prefixes, bare
`/callback`, `/index.html`, `/platform/signedout`, dev-server ports, retired
tunnel hosts.

## Step 6. Switch the platform lane

In the environment's `assembly.yaml`, two lines together:

```yaml
auth:
  type: bundle                  # server-side login on   (cognito: off)
  connection_hub:
    provider_id: browser_session  # server-side login on   (cognito: off)
```

`auth.connection_hub.provider_id` chooses the authenticator; `auth.type` is
what the browser is told. Restart the runtime. With S and C both registered,
this switch needs no identity-provider change, and no client is rebuilt or
redeployed: the pages read the auth contract at load. Browser sessions alive
at the switch sign in once more at most; a website pinned to
`loginMode: platform` must be moved to `auto` or `own-oidc` before switching
off. The full effect table:
[Switching server-side login on and off](../../../service/auth/app-hosted-platform-login-and-session-README.md#switching-server-side-login-on-and-off).

## Step 7. Switch the site's login

In the website's `kdcube.config.json`, per profile:
`auth.loginMode: auto | platform | own-oidc`. `auto` follows the platform's
lane, `platform` always uses server-side login, `own-oidc` always runs the
site's own client with the provider named in `auth.oidc`. With W registered
for the site's origins, this switch needs no identity-provider change either.
It is an edit to a static file, so it takes a website deploy; a platform lane
switch under `auto` takes none.

## Step 8. Verify

1. `GET <platform origin>/api/platform/session/status` answers
   `configured: true` when server-side login is on.
2. Sign in from the site and from `/platform/chat`; `GET /profile` shows the
   user on both.
3. Sign out; the browser passes through the identity provider's sign-out
   and lands where it started.
4. A refused redirect (`redirect_mismatch` or the provider's equivalent)
   means an origin is missing a set on that client: compare the URL in the
   error with Step 3.

## Worked example

Two pools, one app client each: `demo` behind `https://app.example.com`,
`dev` behind `https://dev.example.com`. A public website at
`https://www.example.com` takes identity from the demo platform. A local
runtime behind a tunnel origin `https://<tunnel>` switches between pools and
sometimes serves the website at its root.

```text
demo client
  platform origins (S + C): https://app.example.com, https://<tunnel>
  site origins (W):         https://www.example.com, https://<tunnel>
dev client
  platform origins (S + C): https://dev.example.com, https://<tunnel>
  site origins (W):         https://<tunnel>
```

For the tunnel origin that is seven URLs on each client: three callbacks
(`/api/platform/session/callback`, `/platform/callback`, `/callback.html`)
and four sign-outs (`/api/platform/session/signed-out`, `/platform/chat`,
`/`, `/logout-complete.html`).

The real record for the KDCube deployments, every URL explained, with the
end-to-end setup and the test matrix run as a mini cloud:
[Identity Provider URLs For The KDCube Deployments](../../../service/cicd/identity-provider-urls-README.md).
