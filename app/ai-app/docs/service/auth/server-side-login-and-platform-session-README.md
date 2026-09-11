---
id: repo:kdcube-ai-app/app/ai-app/docs/service/auth/server-side-login-and-platform-session-README.md
title: "Server-Side Login And The Platform Session"
summary: "How an app-defined login turns an authenticator proof into one KDCube-owned, Redis-backed platform session, including the platform-hosted OIDC lane and its sliding lifetime."
tags: ["service", "auth", "application", "bundle", "session", "sso"]
keywords: ["server-side login", "app-defined authenticator", "platform session", "bundle", "kst1", "login lane", "login", "logout", "register", "invalidate", "sliding session", "OIDC", "Cognito hosted UI"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/app-simple-idp-bridge-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-firewall-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/platform-settings-live-update-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/server-side-login.md
---
# Server-Side Login And The Platform Session

Server-side login turns an authenticator proof into one KDCube platform
session. An app can host the user-facing login operation, or KDCube can host
the OIDC redirect and callback through a definition supplied by that app.

In both forms, KDCube owns the session token, Redis registry, revocation, role
lookup, and request authentication. The browser receives one HttpOnly cookie
and checks `/profile`; it does not manage the identity provider's tokens.

Use this when an app needs to accept identities from Telegram, Google,
another OAuth/OIDC provider, a front shell, or an embedded app and then make the
browser authenticated for normal platform routes such as `/profile`,
`/api/integrations/*`, `/sse`, and `/socket.io`.

The [platform-hosted lane](#platform-hosted-server-side-login) below uses the
host-neutral `connection_hub.server_side_login` package for Cognito or any
OIDC issuer.

## Sign-In Model

The configuration has two levels. `auth.type` locates the definition. The
resolved registry entry says what the authenticator is and where login runs.

| Name | Meaning |
|---|---|
| Authenticator | What proves the user's identity: Cognito, an OIDC issuer, Google, Telegram, the development authenticator, or an app's own login. |
| Authority | The realm that groups authenticators. `authority_id` names it. |
| Login lane | Where login runs. A Cognito definition can run in the browser; a registry entry of type `bundle` runs login on the server. |
| Platform session | The deployment-wide authenticated session KDCube issues and accepts across ingress, proc, APIs, SSE, Socket.IO, and applications. |
| `auth.type: bundle` | Resolve the selected authenticator through the app named by `auth.connection_hub.bundle_id`. |
| Registry `type: bundle` | The app provides the server-side login operation and issues a platform session. |
| `BundleSessionAuthority` / `BundleSessionAuthManager` | SDK/runtime implementation names. |
| `kst1` | KDCube's signed, Redis-backed platform-session token format. |
| `kdcube:auth:bundle-session:*` | Stable Redis storage-key family. |

The same word, `bundle`, means the app at both levels. At assembly level it is
the hop to an app-defined entry. At registry level it is the app's own login
kind. The platform session is shared by the deployment, not scoped to the app.

## Runtime Shape

```
Browser / front shell
  |
  | POST application public login endpoint
  v
Application sign-in handler
  |
  | validate external identity
  | call platform session authority
  v
Redis-backed platform session registry
  |
  | returns kst1 signed session token
  v
App response sets auth cookie
  |
  | browser sends cookie to platform routes
  v
Ingress/proc gateway
  |
  | effective auth provider: bundle
  | validate kst1 token + Redis session + current user record
  v
Platform UserSession
```

## Implementation Surface

| Surface | Owner | Purpose |
|---|---|---|
| Application public endpoint | Application | Hosts login UI or operations and validates the authenticator proof. Normal browser logout uses the platform logout endpoint. |
| `kdcube_ai_app.auth.bundle` | Platform | Async API used by the application to register/login/logout/delete/invalidate sessions. The package name is the technical alias. |
| Browser cookies | Connection Hub provider config | Carry the `kst1.*` auth token under configured cookie names. |
| Gateway auth manager | Platform | Validates token, Redis session, user record, and roles on each request. |
| Redis | Platform | Stores active session records, user records, token versions, and session indexes. |
| `secrets.yaml` / secret provider | Deployment | Stores `platform.services.session_token.secret` shared by all validating services. |

## Descriptor Contract

Select an app-defined authenticator in `assembly.yaml`. `auth.type: bundle`
means its definition lives in the referenced app:

```yaml
auth:
  type: "bundle"
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: workspace_google_session
    entrypoint: login
```

The resolver follows `auth.connection_hub` once and reads the selected entry's
type and public facts. It does not follow a `bundle` entry's own
`authenticator_ref` when deciding what the browser should do. A selected
Cognito entry reaches the frontend as `authType: "cognito"` with its pool
facts. A selected `bundle` entry reaches it as `authType: "bundle"` with a
server login URL. Inline deployments use `auth.type: cognito`, `oidc`, or
`simple` directly and do not declare `auth.connection_hub`.

The browser-facing auth contract is still provider-neutral. A host or scene
should use the URLs returned by `/api/cp-frontend-config`:

| Field | Meaning |
|---|---|
| `auth.loginUrl` | Optional browser login entrypoint resolved from Connection Hub provider metadata. |
| `auth.profileUrl` | Server auth-state endpoint. Default: `/profile`. This is the source of truth for "is the browser logged in?". |
| `auth.logoutUrl` | Server logout endpoint. Default: `/api/platform/logout`. |

The client should not inspect platform-session cookies directly. Those cookies
are HTTP-only in normal deployments. The client asks `profileUrl`; the platform
gateway resolves the configured authority and returns the current session.

The signing secret is stored in `secrets.yaml`:

```yaml
services:
  session_token:
    secret: "<generated shared signing secret>"
```

The canonical secret lookup key is `platform.services.session_token.secret`. CLI-managed
local runtimes generate this value when absent during init/refresh. Managed
deployments must materialize the same key through their configured secrets
provider. Every ingress/proc worker must read the same value. Secret rotation
is an operational restart boundary: rotate the secret, invalidate active platform
sessions if needed, and restart workers so all processes verify with one value.

Cookie names come from the selected Connection Hub sign-in entry, normally
under `provider.issuer.cookie` for
`bundle` providers:

| Descriptor field | Browser credential |
|---|---|
| `issuer.cookie.auth_token_cookie_name` | Auth/access cookie consumed by the gateway. |
| `issuer.cookie.id_token_cookie_name` | Optional compatibility field for clients that display the configured cookie names. Server-side platform sessions do not require this cookie. |

In app code, read these names from settings:

```python
from kdcube_ai_app.apps.chat.sdk.config import get_settings


auth_cookie = get_settings().AUTH.AUTH_TOKEN_COOKIE_NAME
```

## Platform-Hosted Server-Side Login

The lane above hosts the login in an application (the Google reference). The
platform can also host it: the browser is sent to the identity provider by the
platform's own route, comes back to the platform's own callback, and leaves
with the session cookie. No JavaScript identity client, no token in a cookie a
script can read, no per-tab renewal. The session slides on activity: a request
extends it by the idle limit, never past the maximum since sign-in.

The session itself is the host-neutral `connection_hub.server_side_login`
(one-time browser-bound login attempt with PKCE and nonce, the same-origin
`next` guard, sliding renewal, and an OIDC code-flow authenticator with a Cognito
preset). The platform supplies the backend over `BundleSessionAuthority`, the
attempt store in the same Redis namespace, and the routes
(`kdcube_ai_app/auth/bundle/login_lane.py`,
`kdcube_ai_app/apps/chat/ingress/platform_session.py`).

For Cognito and OIDC, the verified upstream `sub` is the platform user ID,
matching the browser-side authenticators. Moving the same authenticator to the
server-side lane therefore keeps the user's connected accounts, conversations,
memory, budgets, and other user-scoped records under the same principal. The
session record also retains `provider` and `provider_subject` as evidence of
how that principal was authenticated.

### Descriptor

Select a `bundle` login entry whose `input.authenticator_ref` names a Cognito
or OIDC authenticator. Its issuer, client id and hosted UI come from that
referenced entry; secret-bearing details do not reach browser configuration.

```yaml
auth:
  type: "bundle"                 # the selected definition lives in an app
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: server_login    # registry type bundle: server-side login
```

```yaml
authority_registry:
  authorities:
    kdcube.platform:
      providers:
        cognito_demo:                  # an existing Cognito authenticator
          type: multi_cognito
          authenticator:
            type: cognito_id_token
            region: <region>
            user_pool_id: <pool>
            app_client_id: <client id>
            hosted_ui_domain: https://auth.example.com
        server_login:
          type: bundle
          enabled: true
          label: Platform server-side login
          input:
            authenticator_ref:
              authority_id: kdcube.platform
              provider_id: cognito_demo
            scopes: [openid, email, profile]
            groups_claim: cognito:groups      # optional: authenticator groups become roles
            # accept_authenticator_tokens: false   # default true: the host may also accept tokens from these pools
            # client_secret_ref: ...           # only for a confidential app client
            # redirect_uri: https://...        # only when it differs from <origin>/api/platform/session/callback
          issuer:
            type: kdcube_session_token
            return_origins:                    # website origins a sign-in or sign-out may return to
            - https://www.example.com          # exact origin, or https://*.example.com for subdomains
            ttl_seconds: 43200                 # idle limit (12h)
            max_ttl_seconds: 604800            # maximum since sign-in (7d)
            touch_interval_seconds: 60         # a slide is written at most this often
            attempt_ttl_seconds: 600           # a login must complete within this
            cookie:
              secure: true
              same_site: lax
              auth_token_cookie_name: __Secure-LATC
          grants:
            default:
              roles: [kdcube:role:registered]
              permissions: []
            assignable:
              roles: [kdcube:role:registered, kdcube:role:super-admin]
              permissions: ["kdcube:*:*:*"]
```

Register exactly two URLs per public origin on the app client:

```text
callback:  https://<public origin>/api/platform/session/callback
sign-out:  https://<public origin>/api/platform/session/signed-out
```

The sign-out URL is fixed because the destination does not travel through
the identity provider: the logout stores it in a short-lived return cookie
and the signed-out route continues there. No page URL of any frontend ever
needs registering with the identity provider. The app client may stay public: the exchange
uses PKCE. What a deployment that can switch between this lane and the
Cognito lane, or hosts a site with its own OIDC client, registers per origin:
[What the identity provider must know](browser-sign-in-situations-README.md#what-the-identity-provider-must-know),
and as a procedure, [Register KDCube On Your Identity Provider](../../recipes/connections/platform-authority/identity-provider-urls-README.md).

### Routes

| Route | What it does |
|---|---|
| `GET /api/platform/session/login?next=<same-origin path>` | Starts a one-time attempt, sets the attempt cookie (`__Host-kdcube-login`), redirects to the identity provider. `next` must be a same-origin absolute path, else `/`. |
| `GET /api/platform/session/callback?code=&state=` | Takes the attempt (once), requires the attempt cookie of the browser that started it, exchanges the code, verifies the ID token (signature, issuer, audience, nonce), writes the platform user record and the session, sets the session cookie, redirects to `next`. A refused sign-in is a small page with a reason code and a retry link. |
| `GET /api/platform/session/signed-out` | The identity provider's post-logout target, one fixed URL per origin. Reads and clears the return cookie set by the logout and redirects to that same-origin path, else `/`. |
| `GET /api/platform/session/status` | Whether the lane is configured, its routes and lifetimes. |
| `POST /api/platform/logout?next=` | Ends the session and clears the cookies as before, stores the validated `next` in the return cookie, and answers `upstreamLogoutUrl`: the stable response field carrying the identity provider's sign-out URL. A client navigates there to end that provider session too. |

The proxy route matrix carries `/api/platform/` to the chat ingress
(`deployment/nginx/generate_application_site_routes.py`); without it the
callback would land on the application-site fallback as a 200 HTML page.

### Testing the lane

`kdcube_ai_app/apps/chat/ingress/tests/test_platform_session_end_to_end.py`
walks the whole sign-in against a mock OIDC issuer it serves itself: the
router, discovery, PKCE, the confidential client's secret, ID-token
verification through the issuer's JWKS, the session record, the gateway
validation, replay and foreign-browser refusals, logout. No Cognito, no
Redis, no proxy. Against a real deployment: select the provider, register
the callback URL on the app client, open `/api/platform/session/status`,
then sign in and ask `/profile`.

### What the browser sees

`/api/cp-frontend-config` on this lane answers `auth.authType: "bundle"`,
`auth.loginUrl: "<origin>/api/platform/session/login"`,
`auth.logoutUrl: "/api/platform/logout"`, `auth.profileUrl: "/profile"` and
`auth.sessionLane: "platform"`, and carries no `oidcConfig`. A surface signs
in by navigating to `loginUrl` with its own `next`, and asks `profileUrl`
whether it is signed in. Sign-out in one tab signs out all: there is one
cookie.

Every situation a page can be in (KDCube-owned or host-owned login; same
origin, same site, cross-site; cookie or headers), with before and after
diagrams for the website and the control plane web app:
[Browser Sign-In Situations](browser-sign-in-situations-README.md). On this
lane the gateway dispatches by credential shape: a `kst1.` token goes to the
session authority, any other token to the Cognito manager built from the
referenced authenticator's pools, so a host that keeps its own login keeps working
(`input.accept_authenticator_tokens: false` turns that off).

`@kdcube/components-core/session` is that contract as code for site shells,
widgets and application pages, with React bindings in
`@kdcube/components-react/session`:
[Session](../../sdk/npm/components-core/session-README.md). Signed-out browser
navigations to protected widget and management routes bounce to
`/api/platform/session/login` on this lane, and to the application site's
`/signin/` page otherwise (`sign_in_bounce_path`).

### A website on another origin of the same site

A website beside the platform (`www.example.com` beside `app.example.com`)
signs in through the platform's routes and comes back to its own pages:
`next` may be an absolute URL whose origin is listed in
`issuer.return_origins`, and the logout's `next` likewise. Anything else
collapses to `/` on the platform origin. The session cookie stays host-only
on the platform origin: the website's own requests to the platform, and the
widgets it embeds from the platform, carry it because the two origins are
the same site (SameSite=Lax permits same-site requests). Nothing about the
website's origin needs registering on the identity provider; only the
platform origin's callback and signed-out URLs do. The website's
server-session mode in `auth.js` sends its full URL as `next` for this
reason.

### Switching the login lane

For an app-defined authenticator, the switch changes
`auth.connection_hub.provider_id` and keeps `auth.type: bundle`. A runtime
refresh applies the new lane. No browser client is rebuilt: the control-plane
web app, widgets, and website read `/api/cp-frontend-config` at page load and
follow the resolved authenticator definition. Existing browser credentials
behave as follows:

Set `auth.proxy_login.enabled: false` for this server-side lane. Proxylogin belongs
to the legacy `auth.type: delegated` lane; the platform session endpoints run
in KDCube itself. The process-selection contract is documented in
[Assembly Descriptor](../../configuration/assembly-descriptor-README.md#platform-auth-selection).

| Switch | Control plane web app, widgets, website on `loginMode: auto` | Website on `own-oidc` | Website on `platform` |
| --- | --- | --- | --- |
| browser-side to server-side (`cognito_demo` to `server_login`) | The next page load probes `/profile` with the old Cognito token cookies, which the gateway still accepts, so nobody is signed out. When the access token expires, the user signs in once through the platform route. | unaffected, its tokens stay accepted | unaffected, it already used the server route |
| server-side to browser-side (`server_login` to `cognito_demo`) | The next page load runs the browser OIDC client; the `kst1` session cookie is not a Cognito token, so the user signs in once, often silently while the hosted UI session lives. | unaffected | requires `auto` or `own-oidc`, because a browser-side Cognito definition has no platform `loginUrl` |

Never affected: external MCP clients (Claude Desktop, Claude Code and the
others authenticate to Connection Hub's OAuth server and their cards, not
to the browser login), automation tokens, service-to-service calls, and
the identity provider once both URL sets are registered per origin
(section "What the identity provider must know" of
[Browser Sign-In Situations](browser-sign-in-situations-README.md)).

**From Connection Hub.** A platform administrator can make the same
switch, and edit the sign-in providers, on the Authenticators tab of
Connection Hub. The tab reads the platform's selection and every provider
of the authority registry (`authorities_describe`), and its editor writes
the staged descriptor files through `kdcube_ai_app.infra.descriptors.edit`:
the selected `provider_id` for the switch, restoring `auth.type: bundle` if a
descriptor drifted (`platform_sign_in_set`),
one provider block at a time for the providers (`authority_provider_set`,
after `authority_provider_validate` checked the buffer). Comments and every
other key are kept, the previous file stays beside the new one as
`.bak-<stamp>`, secret-bearing keys are merged from the file where the
buffer says `<unchanged>`. Editing is allowed only when
`management.platform_settings.editing.enabled` and its `auth` section are
true in `assembly.yaml`. Provider edits notify ingress immediately and are
reported as live only when a listener received the event. The lane switch
applies on the next refresh, as above. The generic mechanism and deployed
service ownership are defined in
[Live Platform Settings Updates](../../configuration/platform-settings-live-update-README.md).

### Sliding renewal

The token's `exp` is the hard bound (the maximum since sign-in). The Redis
session record carries the idle bound (`exp`), the hard bound (`max_exp`) and
`last_seen`. The gateway's `BundleSessionAuthManager` slides a validated
session whose last extension is older than the touch interval to the idle
limit from now, capped at the hard bound (`BundleSessionAuthority.touch`).
A deployment without this lane keeps fixed-expiry sessions: the manager
receives no policy and never touches.

## Google Login: Setup And Trust Boundaries

The default installation uses Google Identity Services as the authenticator
for server-side login hosted by the Workspace app. It uses a public Google Web
client id and a Google-signed ID token. It does not exchange an authorization
code and therefore does not use a Google OAuth client secret.

This is separate from connected-account OAuth for Gmail, Sheets, Docs, or
Drive. Connected-account OAuth does exchange authorization codes and keeps its
Google client secret server-side under Connection Hub app secrets.

### Operator Setup

```text
Google Cloud Console                         kdcube init
+----------------------------+               +----------------------------+
| OAuth Web client           |               | Ask for:                   |
|                            |               | - Google client_id         |
| Public client_id ----------+-------------->| - bootstrap admin email    |
| Authorized JS origins      |               |                            |
| Google private signing key |               | Generate locally:          |
| stays at Google            |               | - KDCube session secret    |
+----------------------------+               +-------------+--------------+
                                                            |
                                                            v
                                              Runtime descriptors + secrets
                                              +----------------------------+
                                              | bundles.yaml               |
                                              |   google_oidc.client_id    |
                                              |   bootstrap admin rule     |
                                              |                            |
                                              | secrets.yaml              |
                                              |   session_token.secret     |
                                              +----------------------------+
```

### Login Flow

```text
 User browser               KDCube Workspace app          Google Identity
      |                              |                         Services
      |  1. Open KDCube             |                            |
      |---------------------------->|                            |
      |                              |                            |
      |  2. Redirect to             |                            |
      |     platform_login          |                            |
      |<----------------------------|                            |
      |                              |                            |
      |  Login page contains:       |                            |
      |  - public client_id         |                            |
      |  - Google GIS script        |                            |
      |                              |                            |
      |  3. Choose Google account   |                            |
      |---------------------------------------------------------->|
      |                              |                            |
      |                     Google authenticates user             |
      |                              |                            |
      |  4. Signed Google ID token  |                            |
      |<----------------------------------------------------------|
      |                              |                            |
      |  ID token contains:         |                            |
      |  - iss: Google              |                            |
      |  - aud: KDCube client_id    |                            |
      |  - sub: stable Google user  |                            |
      |  - email / email_verified   |                            |
      |  - iat / exp                |                            |
      |                              |                            |
      |  5. POST credential         |                            |
      |     to auth_google_session  |                            |
      |---------------------------->|                            |
      |                              |                            |
      |                              |  6. Fetch Google public    |
      |                              |     signing keys (JWKS)     |
      |                              |--------------------------->|
      |                              |<---------------------------|
      |                              |                            |
      |                              |  7. Verify:                |
      |                              |  - RS256 signature         |
      |                              |  - issuer is Google        |
      |                              |  - audience == client_id   |
      |                              |  - token not expired       |
      |                              |  - stable subject exists   |
      |                              |                            |
      |                              |  8. Resolve platform grant |
      |                              |                            |
      |                              |  Exact verified admin email|
      |                              |      -> super-admin         |
      |                              |  Other admitted Google user|
      |                              |      -> registered user     |
      |                              |                            |
      |                              |  9. Issue KDCube session   |
      |                              |     signed with the private |
      |                              |     session_token.secret    |
      |                              |                            |
      | 10. Set-Cookie:             |                            |
      |     Secure                  |                            |
      |     HttpOnly                |                            |
      |     SameSite=Lax            |                            |
      |<----------------------------|                            |
      |                              |                            |
      | 11. Continue to KDCube UI   |                            |
      |---------------------------->|                            |
```

### Secret Boundaries

```text
Browser
  public Google client_id
  short-lived Google ID token during login
  KDCube session only as an HttpOnly cookie

KDCube trusted runtime
  platform.services.session_token.secret
  user and grant records
  no Google platform-login client secret

Google
  private key that signs Google ID tokens
```

The Google client id identifies the application. Google's signature proves the
ID token. KDCube's private session secret protects the resulting platform
session. The server uses the Google token only to establish the session; Google
is not contacted for every later KDCube request.

## Security Properties And Production Hardening

The current flow already enforces these boundaries:

| Boundary | Current behavior |
|---|---|
| Google identity proof | Server verifies the Google RS256 signature through Google's JWKS, exact configured audience, Google issuer, stable subject, and token expiry. |
| Bootstrap administrator | An email-based bootstrap rule matches only a Google-verified email. The canonical KDCube subject remains `google:<sub>`, not the email address. |
| KDCube session integrity | `kst1` is HMAC-SHA256 signed with the server-side `platform.services.session_token.secret`. |
| KDCube session liveness | Every request must match an active Redis session, current user version, and enabled user record. |
| Browser cookie | The default issuer sets `Secure`, `HttpOnly`, `SameSite=Lax`, and `Path=/`. |
| Secret placement | The Google Web client id is public configuration. The KDCube session secret remains in runtime secrets and never enters browser configuration. |

The reference Google login currently has three explicit production-hardening
items. They are not properties already supplied by a Google client secret:

1. **Bind the browser login attempt.** The current public
   `auth_google_session` operation accepts the Google credential body but does
   not validate a server-created, one-time login attempt, CSRF token, or Google
   nonce. CORS and JSON preflight handling reduce cross-origin request shapes,
   but they are not a one-time proof that the browser completing the callback
   initiated this login. A hardened flow should create a short-lived login
   attempt, bind it to the browser, send a nonce to Google, and consume the
   attempt once when issuing the KDCube session. See Google's
   [server-side ID-token guidance](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).
2. **Constrain the post-login destination.** The current Workspace login page
   reads `next` from the query string and passes it to
   `window.location.assign(...)`. Validate it as a same-origin relative path,
   or against an explicit deployment allowlist, before rendering it into the
   page. Reject absolute, protocol-relative, and malformed destinations.
3. **Declare account-admission policy.** The reference default grants the
   baseline registered-user role to any valid Google account and grants the
   configured, verified bootstrap email its administrative role. A deployment
   restricted to one Google Workspace organization should configure an
   admission rule and verify Google's `hd` claim. An email suffix alone is not
   proof of organization membership.

Before an internet-exposed production deployment, close the login-attempt and
post-login-destination gaps. Apply organization admission when the deployment
is intended for an organization rather than all Google accounts. Independently,
serve login only over HTTPS, generate a high-entropy KDCube session secret,
store it through the descriptor secret provider, and rotate it as a coordinated
session-invalidating operation across all replicas.

## Storage Surfaces

Server-side platform sessions use Redis as mutable runtime storage. Key
names are tenant/project namespaced.

| Storage | Shape | Lifetime | Used by |
|---|---|---|---|
| User record | `{tenant}:{project}:kdcube:auth:bundle-session:user:{sub}` | Until delete | Gateway validation and role freshness. |
| Session record | `{tenant}:{project}:kdcube:auth:bundle-session:session:{sid}` | Session TTL | Token activation, logout, and token hash match. |
| User sessions set | `{tenant}:{project}:kdcube:auth:bundle-session:user-sessions:{sub}` | Session TTL window | Invalidate/delete all sessions for a subject. |
| User version | `{tenant}:{project}:kdcube:auth:bundle-session:user-version:{sub}` | Until delete | Role/session revocation boundary. |
| Signing secret | `platform.services.session_token.secret` | Deployment secret lifecycle | HMAC signature verification. |
| Browser auth cookie | descriptor-configured name | Cookie lifecycle | Transport from browser to gateway. |

Validation reads the current user record. A role update made through
`register_user(...)` is reflected on the next request without waiting for the
browser cookie to expire.

## Application SDK API

Import the authority from the technically named `kdcube_ai_app.auth.bundle`
package:

```python
from kdcube_ai_app.auth.bundle import get_bundle_session_authority


authority = get_bundle_session_authority()
```

The API is fully async.

## Public Application Endpoint Pattern

Expose the login endpoint on a public application route. In configuration and
SDK decorators, this remains an app operation exposed through the technical
bundle route. The endpoint is public
because the user is not authenticated by the platform before the external
identity has been validated.

```python
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.bundle import get_bundle_session_authority
from kdcube_ai_app.infra.plugin.bundle_loader import api


@api(method="POST", alias="auth_external", route="public")
async def auth_external(self, request=None, **payload):
    external_user = await validate_external_identity(payload)

    authority = get_bundle_session_authority()
    grant = await authority.login_or_register(
        sub=f"{external_user.provider}:{external_user.subject}",
        username=external_user.username,
        email=external_user.email,
        name=external_user.name,
        roles=["kdcube:role:registered"],
        permissions=["kdcube:*:chat:*;read;write"],
        provider=external_user.provider,
        provider_subject=external_user.subject,
    )

    auth_cfg = get_settings().AUTH
    response = JSONResponse(
        {
            "ok": True,
            "session_id": grant.session_id,
            "expires_at": grant.expires_at,
        }
    )
    response.set_cookie(
        auth_cfg.AUTH_TOKEN_COOKIE_NAME,
        grant.token,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response
```

### Register Or Update User

```python
user = await authority.register_user(
    sub="google:123",
    username="Alice",
    email="alice@example.test",
    roles=["kdcube:role:registered"],
    permissions=["kdcube:*:chat:*;read;write"],
    provider="google",
    provider_subject="123",
)
```

`sub` is the canonical platform subject. Keep it stable. Accounting,
conversation ownership, and rate-limit identity are derived from this value.

For providers with stable external subjects, use a deterministic subject shape:

| Provider | Example `sub` |
|---|---|
| Telegram | `telegram:123456789` |
| Google | `google:10987654321` |
| OIDC provider | `oidc:<issuer-host>:<subject>` |
| Front shell local account | `front-shell:<account-id>` |

### Login

```python
grant = await authority.login(
    sub="google:123",
    provider="google",
    provider_subject="123",
    ttl_seconds=12 * 3600,
)
```

`grant.token` is the browser auth token. Set it in the descriptor-configured
auth cookie:

```python
response.set_cookie(
    "__Secure-LATC",
    grant.token,
    path="/",
    secure=True,
    httponly=True,
    samesite="lax",
)
```

Use the descriptor-configured cookie name in production code instead of a hard
coded value. Do not mirror the `kst1` platform-session token into the Cognito
ID-token cookie unless a deployment explicitly adds a compatibility bridge.

### Login Or Register

```python
grant = await authority.login_or_register(
    sub="telegram:42",
    username="Alice",
    roles=["kdcube:role:registered"],
    provider="telegram",
    provider_subject="42",
)
```

Use this when the external provider already proved the identity and the
application wants a single call for first login and subsequent login.

## Handshakes

### First Login

```
1. Browser submits external credential
     POST application public auth endpoint

2. Application validates the credential
     Telegram initData / OAuth code / provider JWT / front-shell session

3. Application calls:
     await authority.login_or_register(...)

4. Platform writes:
     user record
     user version
     session record
     user sessions set

5. Application response sets:
     auth auth-token cookie = kst1.*

6. Browser calls:
     GET /profile
     GET /api/integrations/bundles

7. Gateway resolves:
     effective platform authenticator bundle
     token -> Redis session -> current user record -> UserSession
```

### Subsequent Request

```
Browser request with auth cookie
  |
  v
token extractor
  |
  v
BundleSessionAuthManager
  |
  +-- verify signature
  +-- read active Redis session
  +-- read current user
  +-- derive roles/user type
  v
route handler / app API / SSE / Socket.IO
```

### Logout

```
Browser calls auth.logoutUrl from frontend config
  |
  v
POST /api/platform/logout
  |
  v
Platform reads configured auth cookie
  |
  +-- if provider type is bundle:
  |     await logout_bundle_session(token=token)
  |
  +-- delete Redis session record
  |
  v
Response clears platform cookies
```

The standard browser logout endpoint is:

```text
POST /api/platform/logout
```

It clears `AUTH_TOKEN_COOKIE_NAME`, `ID_TOKEN_COOKIE_NAME`, and
`MASQUERADED_TOKEN_COOKIE_NAME`. For the resolved `bundle` login kind it also invalidates the
active platform-session record in Redis. This endpoint is intentionally
platform generic: the browser uses the same route for a Cognito authenticator,
a `bundle` login lane, or another sign-in choice.

The normal browser shell uses this generic logout endpoint. An application may
also expose a branded "signed out" page or an identity-provider sign-out flow
for UI and identity-provider cleanup. The KDCube platform session ends through
the generic platform logout endpoint or the same SDK authority logout primitive.

If an application needs a custom logout operation for a non-browser surface,
use the same underlying authority primitive:

```python
@api(method="POST", alias="auth_logout", route="public")
async def auth_logout(self, request=None, **payload):
    auth_cfg = get_settings().AUTH
    token = request.cookies.get(auth_cfg.AUTH_TOKEN_COOKIE_NAME) if request else None
    await get_bundle_session_authority().logout(token=token)

    response = JSONResponse({"ok": True})
    response.delete_cookie(auth_cfg.AUTH_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(auth_cfg.ID_TOKEN_COOKIE_NAME, path="/")
    return response
```

### Role Change Or Admin Promotion

```
Admin action / app account operation
  |
  v
await authority.register_user(
    sub=existing_sub,
    roles=[...new roles...],
    permissions=[...new permissions...],
)
  |
  v
Next request reads current user record and uses new roles
```

Use `await authority.invalidate_user(sub)` when existing browser sessions should
be forced to log in again after the role change.

### Delete User

```
Account deletion
  |
  v
await authority.delete_user(sub)
  |
  +-- invalidate active sessions
  +-- remove user record
  |
  v
Existing browser cookies no longer authenticate
```

## Lifecycle API Summary

### Logout API

```python
await authority.logout(token=token_from_cookie)
```

Logout deletes the backing Redis session. The signed cookie is no longer enough
to authenticate.

### Invalidate User API

```python
await authority.invalidate_user("google:123")
```

This increments the user's token version and removes active sessions. Existing
cookies stop working.

### Delete User API

```python
await authority.delete_user("google:123")
```

Delete invalidates sessions and removes the platform user record.

## Request-Time Session Validation

When the selected authenticator resolves to `bundle`, the gateway uses
the technically named `BundleSessionAuthManager`.

```text
Browser                                              KDCube ingress / proc
   |                                                          |
   | HTTPS request                                            |
   | Cookie: __Secure-LATC=kst1.<body>.<signature>            |
   |--------------------------------------------------------->|
   |                                                          |
   |                              1. Extract configured cookie|
   |                                 and normalize token      |
   |                                                          |
   |                              2. Select descriptor-backed |
   |                                 BundleSessionAuthManager |
   |                                                          |
   |                              3. Parse kst1 claims:        |
   |                                 schema, sid, sub,         |
   |                                 provider, ver, iat, exp   |
   |                                                          |
   |                              4. Resolve private          |
   |                                 session_token.secret     |
   |                                                          |
   |                              5. Recompute HMAC-SHA256    |
   |                                 over encoded token body  |
   |                                                          |
   |                              6. Constant-time signature  |
   |                                 comparison               |
   |                                 mismatch -> 401          |
   |                                                          |
   |                              7. Validate schema, expiry, |
   |                                 and required sid/sub     |
   |                                                          |
   |                              8. Read tenant/project      |
   |                                 Redis state:             |
   |                                 - session by sid         |
   |                                 - user by sub            |
   |                                 - user token version     |
   |                                                          |
   |                              9. Require:                 |
   |                                 - active session         |
   |                                 - matching subject       |
   |                                 - exact token SHA-256    |
   |                                 - unexpired record       |
   |                                 - current user version   |
   |                                 - existing enabled user  |
   |                                                          |
   |                             10. Load current roles and   |
   |                                 permissions from Redis   |
   |                                                          |
   |                             11. Apply the requested      |
   |                                 surface guard            |
   |                                                          |
   |<---------------------------------------------------------|
   | Authorized response, or 401/403                          |
```

The two checks prove different things:

```text
HMAC signature
  proves that the token body was issued by a runtime holding the shared
  KDCube session secret and that the body was not modified

Redis session + user records
  prove that this exact session is still active now and supply the current
  server-side roles, permissions, and user status
```

All ingress and proc replicas that accept the session must resolve the same
`platform.services.session_token.secret` and the same tenant/project Redis namespace.
The token is signed, but Redis is the mutable source of truth. This gives these
properties:

| Operation | Behavior |
|---|---|
| Concurrent login | Safe. Each login writes a separate session key. |
| Concurrent registration | Safe. User upsert is per canonical subject. |
| Logout | Deletes one active session. |
| Invalidate | Revokes all known sessions for the subject and bumps version. |
| Delete | Revokes sessions and removes the user record. |
| Role change | Validation reads the current user record, so roles are not only taken from the cookie. |

## Concurrency Model

All public operations are async and use Redis as the shared coordination
surface.

| Operation | Concurrency behavior |
|---|---|
| `login(...)` | Creates a new independent session id and session record. Parallel logins for the same `sub` are allowed. |
| `register_user(...)` | Upserts the current user record for one canonical `sub`. Parallel updates converge on the last written profile for that subject. |
| `logout(...)` | Deletes one session record by token/session id. Repeated logout calls are safe. |
| `invalidate_user(...)` | Bumps the user version and removes known active session records for the subject. |
| `delete_user(...)` | Runs invalidation and then removes the user record. |

Validation checks the token signature, active session record, token hash,
current user version, and current user record. A stale cookie cannot authenticate
after logout, invalidate, delete, or signing-secret rotation.

## Data Bus Relationship

Server-side login creates browser/platform authentication. It
makes the browser a known platform user for platform routes.

Data Bus federated tokens are short-lived transport capability tokens for
Socket.IO Data Bus publishing. A public mini app can use both flows:

```
Public mini app
  |
  | login/claim endpoint validates external identity
  v
Application
  |
  +-- issue KDCube platform-session cookie for platform routes
  |
  +-- issue federated Data Bus token for Socket.IO publish
```

Use [Bundle Federated Auth](../../sdk/bundle/auth-bundle-federated-README.md)
for the Data Bus token claim flow.

## Token Shape

Server-side platform sessions use the stable `kst1` token format implemented
by `BundleSessionAuthority`:

```
kst1.<b64url-json-claims>.<b64url-hmac-sha256>
```

Claims include:

| Claim | Purpose |
|---|---|
| `schema` | `kdcube.session_token.v1` |
| `sid` | Redis session id |
| `sub` | Canonical platform subject |
| `provider` / `provider_subject` | External identity source that produced the session |
| `ver` | User token version for revocation |
| `iat` / `exp` | Issue and expiry time |

Applications should call the platform API instead of minting this token
themselves.

## Relationship To Other Auth Flows

| Flow | Purpose |
|---|---|
| Cognito | Platform owns login, registration, MFA, and JWT validation. |
| SimpleIDP bridge | App registers an opaque token in `idp_users.json`; useful for local/embedded simple auth. |
| Server-side login | An app-defined login operation validates an authenticator proof; KDCube owns session tokens and Redis-backed revocation. Registry type: `bundle`. |
| Federated Data Bus token | Short-lived capability token for Socket.IO Data Bus after an identity is already accepted. |

## Verification

After login, these checks should succeed from the browser or from a container on
the same network:

```bash
curl -i \
  -b '__Secure-LATC=<kst1-token>; __Secure-LITC=<kst1-token>' \
  http://chat-ingress:8010/profile
```

Expected profile shape:

```json
{
  "user_type": "REGISTERED",
  "username": "Alice",
  "email": "alice@example.test"
}
```

Admin users should resolve as `PRIVILEGED`.

If `/profile` is anonymous, check these items in order:

| Check | Expected |
|---|---|
| Descriptor | `auth.type: bundle` plus `auth.connection_hub` naming a registry entry of type `bundle`. |
| Secret | `platform.services.session_token.secret` exists and is identical for ingress/proc. |
| Cookie name | Browser sends the selected sign-in entry's auth cookie to the platform origin. |
| Token prefix | Cookie value starts with `kst1.`. |
| Redis session | The backing session key exists until logout/expiry. |
| User record | The user record exists and is not disabled. |
