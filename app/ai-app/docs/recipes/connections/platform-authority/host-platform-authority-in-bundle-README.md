---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/host-platform-authority-in-bundle-README.md
title: "Host A Platform Authority Flow In A Bundle"
summary: "Recipe for deployments that do not want Cognito as the only platform login path: Connection Hub owns authority registration and policy, while a bundle hosts the login UI/operation and issues standard KDCube bundle-session credentials."
status: draft
tags: ["recipes", "connections", "connection-hub", "authority-registry", "bundle-session", "platform-auth", "custom-authority"]
updated_at: 2026-07-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-providers/authority-provider-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/docs/integrations/platform-session-issuer.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---
# Host A Platform Authority Flow In A Bundle

Use this recipe when a deployment wants a bundle to host the user-facing login
flow while KDCube still treats the result as a normal platform session.

Examples:

```text
A product app wants its own login page but standard KDCube sessions.
Versatile demonstrates Google login and Telegram-to-session flows.
```

The important rule:

```text
Connection Hub owns authority registration and policy.
The bundle owns only the hosted login UI/operation.
The platform verifies the issued session through standard bundle-session auth.
```

Do not put platform role policy, issuer TTL, cookie policy, or platform-ness in
bundle-local config. Register it in Connection Hub.

## Mental Model

```text
Browser opens KDCube
  no platform session
      |
      v
frontend config says auth.loginUrl = bundle hosted login page
      |
      v
Bundle login page
  Google button / custom login / Telegram session flow
      |
      v
Bundle public operation calls Connection Hub SDK runtime
      |
      v
Connection Hub registry resolves:
  platform authority
  provider instance
  upstream authenticator
  issuer settings
  grants/defaults/bootstrap rules
      |
      v
SDK runtime verifies upstream proof
      |
      v
SDK runtime issues standard kst1 bundle-session token
      |
      v
Browser receives platform auth/session cookie
      |
      v
Future ingress/proc requests use normal platform auth verifier
```

This is different from a channel link.

```text
Telegram initData verified by itself
  -> external actor
  -> no platform role

Bundle-hosted platform login succeeds
  -> platform subject
  -> kdcube:role:registered if no stronger role exists
```

## Platform Baseline

Any platform authority that authenticates a user and returns no roles is
normalized centrally to:

```text
kdcube:role:registered
```

This applies to Cognito, SimpleIDP, bundle-session platform providers, and
future platform authorities.

It does not apply to raw external proofs such as Telegram initData unless that
proof is consumed by a configured platform login provider.

## Assembly Descriptor

Configure the platform to use bundle-session auth. The canonical login endpoint
is declared on the authority provider as `entrypoints.login`. `assembly.yaml`
only selects the Connection Hub provider that owns that entrypoint.

```yaml
auth:
  type: bundle
  idp: session
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: versatile_google_session
    entrypoint: login
  authenticators:
    platform:
      id: kdcube.bundle-session
      authority_id: kdcube.platform
      provider: session
    connection_hub:
      enabled: true
      app_id: connection-hub@1-0
      operation: request_authenticate
```

The browser app asks the Connection Hub SDK client to resolve
`entrypoints.login` into the tenant/project-specific bundle URL when it needs a
platform session. Descriptors should not materialize `auth.login_url`.

The browser app should not know whether this provider is bundle-session,
Cognito, or another platform authority. It should use the auth URLs returned by
`/api/cp-frontend-config`:

```json
{
  "auth": {
    "loginUrl": "/api/integrations/bundles/.../public/platform_login",
    "profileUrl": "/profile",
    "logoutUrl": "/api/platform/logout"
  }
}
```

`profileUrl` is the canonical server-side "am I logged in?" check. `logoutUrl`
is the canonical browser logout endpoint. For bundle-session providers,
`/api/platform/logout` invalidates the active bundle-session record and clears
the platform auth/session cookie. The hosting bundle does not need a logout
operation for the normal browser shell, but the deployment proxy must route the
configured `logoutUrl` to ingress if the shell exposes it.

## Platform Secret

Every ingress/proc worker must share the same bundle-session verifier secret.

```yaml
services:
  session_token:
    secret: "<deployment secret>"
```

## Connection Hub Authority Registry

Register the platform authority and the provider instance hosted by the bundle.

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            label: KDCube platform authority
            platform: true
            grants:
              subjects:
                google:<verified_google_sub>:
                  label: bootstrap_admin
                  roles:
                    - kdcube:role:super-admin
                  permissions:
                    - kdcube:*:*:*
              bootstrap_rules:
                - id: bootstrap_admin_by_google_email
                  when:
                    provider: google
                    claims:
                      email: owner@example.com
                      email_verified: true
                  roles:
                    - kdcube:role:super-admin
                  permissions:
                    - kdcube:*:*:*
            providers:
              versatile_google_session:
                type: bundle_session_login
                enabled: true
                label: Google sign-in for KDCube
                login_label: Sign in to KDCube
                login_description: Use your Google account to continue.
                entrypoints:
                  login:
                    bundle_id: versatile@2026-03-31-13-36
                    route: public
                    operation: platform_login
                  session_issue:
                    bundle_id: versatile@2026-03-31-13-36
                    route: public
                    operation: auth_google_session
                  consent:
                    bundle_id: versatile@2026-03-31-13-36
                    route: public
                    operation: delegated_consent
                input:
                  authenticator_ref:
                    authority_id: google.accounts
                    provider_id: google_oidc
                issuer:
                  type: kdcube_session_token
                  ttl_seconds: 43200
                  cookie:
                    secure: true
                    same_site: lax
                grants:
                  default:
                    roles:
                      - kdcube:role:registered
                    permissions: []
                  assignable:
                    roles:
                      - kdcube:role:registered
                      - kdcube:role:super-admin
                    permissions:
                      - kdcube:*:chat:*;read;write
                      - kdcube:*:*:*

          google.accounts:
            label: Google Accounts
            platform: false
            providers:
              google_oidc:
                type: google_id_token
                enabled: true
                authenticator:
                  client_id: "<google OAuth client id>"
```

### What These Fields Mean

`kdcube.platform.platform: true`
: This authority may produce the platform subject used by platform surfaces,
  ownership projection, and economics.

`providers.versatile_google_session.type: bundle_session_login`
: This provider instance is implemented by the Connection Hub SDK
  bundle-session login runtime.

`entrypoints.login`
: The bundle-hosted browser page for this provider's sign-in UX.

`entrypoints.session_issue`
: The bundle action/callback that verifies the upstream proof and asks the
  Connection Hub SDK to issue the platform session.

`entrypoints.consent`
: Optional bundle-hosted delegated credential consent renderer. Use this when
  the product wants its own complete consent layout. The approve/deny POST
  still targets Connection Hub so CSRF, grant narrowing, authorization-code
  creation, and token issuance remain central.

The consent renderer is a page renderer, not a policy engine. Connection Hub
passes it a payload with:

- `form_action`
- `csrf_token`
- `request.client_id`
- `request.redirect_uri`
- `request.response_type`
- `request.scope` / `request.scopes`
- `request.resource`
- `request.state`
- `request.code_challenge`
- `request.code_challenge_method`
- `platform_grants`
- `tools`

The page should submit approve/deny to `form_action`, include the request fields
as hidden inputs, and send selected `platform_grants` and `tools`. Connection
Hub re-validates all of that before issuing the authorization code. It also has
a defensive fallback that can recover missing non-secret request fields from the
same-origin `/oauth/authorize?...` referrer, but correct renderers should not
depend on that fallback.

`input.authenticator_ref`
: The upstream proof verifier. In the example, Google ID token verification
  lives under `google.accounts.providers.google_oidc`.

`issuer`
: The KDCube credential issued after upstream proof verification succeeds. This
  is a standard bundle-session token, not a bundle-local private token format.

`grants.default`
: Access every successful session from this provider receives.

`grants.assignable`
: Maximum roles/permissions this provider may assign through authority-level
  subject grants and bootstrap rules.

`grants.subjects`
: Stable per-subject role assignment. Use authority subjects such as
  `google:<sub>`, not email.

`grants.bootstrap_rules`
: Bootstrap mechanism for the first login when the stable subject is not known
  yet. Email can be used only as a verified claim matcher, for example Google
  `email + email_verified`.

## Bundle Descriptor

The bundle descriptor should not repeat platform-session policy. It only needs
what the hosted operation needs to run, plus the Connection Hub pointer.

```yaml
items:
  - id: versatile@2026-03-31-13-36
    config:
      connections:
        connection_hub:
          bundle_id: connection-hub@1-0
```

## Frontend Auth Descriptor

The platform frontend should not hardcode the bundle login URL. It should name
the Connection Hub authority provider. The control-plane frontend-config
endpoint resolves the provider's `login` entrypoint through the Connection Hub
SDK client and returns the concrete `auth.loginUrl` to the browser.

```yaml
auth:
  type: bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: versatile_google_session
    entrypoint: login
```

For clients that cannot use the in-process SDK client, Connection Hub also
exposes a thin public resolver facade:

```text
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/authority_provider_entrypoint_resolve
```

The SDK resolver/facade returns the concrete URL for the configured provider
entrypoint, for example:

```text
/api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/platform_login
```

This keeps route construction under the Connection Hub SDK boundary and keeps
descriptors focused on the authority/provider contract.

For Telegram-hosted login flows, the bundle also needs the Telegram integration
secret refs. Those secrets verify the upstream Telegram proof; they do not
define platform grants.

## Bundle Code

The bundle operation should be thin. It hosts UI and delegates runtime logic to
the SDK.

```python
@api("platform_login", route="public")
async def platform_login(self, request: Request, **kwargs):
    return await platform_session_issuer.google_login_page(
        self,
        request=request,
        bundle_id=self.bundle_id,
    )

@api("auth_google_session", route="public")
async def auth_google_session(self, request: Request, **kwargs):
    payload = await request.json()
    return await platform_session_issuer.issue_google_session(
        self,
        request=request,
        credential=payload.get("credential", ""),
        bundle_id=self.bundle_id,
    )
```

The reusable implementation lives in:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

The Versatile example wrapper lives in:

```text
kdcube_ai_app.apps.chat.sdk.examples.bundles.versatile@2026-03-31-13-36.services.platform_session_issuer
```

The bundle must not hardcode:

- platform roles;
- admin subjects;
- issuer TTL;
- cookie names;
- Google client id;
- Telegram bot identity.

Those belong to descriptors and Connection Hub registry.

## Google OAuth Client Setup

For a Google-backed provider, the descriptor `google.accounts.providers.google_oidc.authenticator.client_id`
must point to a Google OAuth client that allows the runtime origin where the
login page is opened.

In Google Cloud Console:

1. Open **APIs & Services** -> **Credentials**.
2. Open the OAuth 2.0 client used by `google_oidc.authenticator.client_id`.
3. Make sure the client type is **Web application**.
4. Add the exact browser origin under **Authorized JavaScript origins**.

Examples:

```text
https://demo.kdcube.tech
https://kdcube.tech
https://broodier-maxie-uninferrably.ngrok-free.dev
http://localhost:5173
```

Use only the origin: scheme, host, and optional port. Do not include a path.

Correct:

```text
https://broodier-maxie-uninferrably.ngrok-free.dev
```

Wrong:

```text
https://broodier-maxie-uninferrably.ngrok-free.dev/platform/chat
https://broodier-maxie-uninferrably.ngrok-free.dev/api/integrations/bundles/...
```

If Google shows:

```text
Error 400: origin_mismatch
You can't sign in to this app because it doesn't comply with Google's OAuth 2.0 policy.
```

then the page origin is not registered on that Google OAuth client. Add the
origin shown in the browser address bar, wait for Google configuration to
propagate, and retry. Temporary tunnel URLs such as ngrok domains must be added
every time the public hostname changes, unless the deployment uses a stable
reserved domain.

## Delegated Consent Troubleshooting

If an external connector reaches the consent page but approve returns:

```json
{"error":"invalid_client","error_description":"unknown client_id"}
```

then inspect the proc logs for:

```text
[connection-hub.oauth] authorize_consent rejected ...
[connection-hub.oauth] dynamic_client_missing ...
[connection-hub.oauth] consent params recovered from authorize referrer ...
```

`dynamic_client_missing` means the submitted `client_id` was not found in the
tenant/project OAuth store. `authorize_consent rejected ... form_keys=...` shows
which form fields reached Connection Hub. If the custom consent page dropped
hidden OAuth fields, Connection Hub may recover them from the same-origin
authorize referrer, but the renderer should still preserve the payload request
fields.

## Login Page

The login page is bundle-owned UI and is registered at
`authority_registry.authorities.<platform-authority>.providers.<provider>.entrypoints.login`.
In the Versatile example, it renders Google Identity Services, posts the
credential to `auth_google_session`, and relies on the SDK runtime to set the
configured KDCube platform auth/session cookie.

The page may be replaced by a product-specific login UI as long as the public
operation calls the same SDK runtime and the provider is registered in
Connection Hub.

## How To Test

1. Start an environment with `auth.type: bundle` / `auth.idp: session`.

2. Open the normal platform frontend route.

3. If there is no valid platform session, the frontend should ask Connection
   Hub for the selected provider `entrypoints.login` URL and redirect there.

4. Complete the hosted login flow.

5. Confirm the response sets the configured platform auth/session cookie
   (`AUTH_TOKEN_COOKIE_NAME`, commonly `__Secure-LATC`). A Cognito-style ID
   token cookie is not required for bundle-session auth.

6. Reload the platform frontend.

7. Confirm a normal platform user can open registered-user surfaces.

8. Call the generated `auth.logoutUrl`, or press the shell logout button if it
   uses that URL. Confirm `/profile` returns anonymous and registered-user
   surfaces are hidden. If a local proxy does not route logout yet, clear site
   data for the test origin before switching to another platform provider.

9. Confirm an admin bootstrap rule grants admin only when the verified upstream
   claim matches.

10. Confirm raw Telegram Mini App auth without a platform login/link remains
   external and does not receive `kdcube:role:registered`.

## Failure Rules

The hosted operation must fail closed if:

- Connection Hub provider registration is missing;
- provider registration is disabled;
- registered authority is not `platform: true`;
- hosted operation does not match the current bundle/operation;
- upstream authenticator is missing or rejects the proof;
- subject/bootstrap grants ask for roles or permissions outside
  `grants.assignable`;
- platform session token secret is missing or inconsistent across workers.

## Related Recipes

- [Set Up A Platform Authority Provider](setup-platform-authority-README.md)
- [Link From External Channel](../link-from-external-channel-README.md)
- [Telegram Integration](../integrations/telegram-README.md)
- [Delegate A KDCube Service To An External Client](../delegate-kdcube-service-to-external-client-README.md)
