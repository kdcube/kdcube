# Platform Session Issuer Demo

Versatile can demonstrate a bundle-owned login authority without making that
behavior a built-in default.

The bundle hosts the user-facing operation/UI for an upstream proof, currently a
Google ID token, then calls the Connection Hub SDK bundle-session provider
runtime. The SDK runtime resolves the registered provider, verifies or delegates
verification of the upstream proof, resolves roles/provisioning policy, and
calls the platform bundle-session authority. This is a deliberate platform login
provider flow.

Plain Telegram channel identity remains an external actor until it is explicitly
linked to an existing platform identity through a Connection Hub connection edge.
For the Google-backed platform authority demo, Telegram is a channel
authenticator only; it is not configured as a platform-session provider.

The platform issues and later verifies the `kst1` session token. Versatile is
the issuer surface; ingress/proc remain the verifier.

The authority policy is not owned by Versatile. Roles, permissions, subject
grants, TTL, authority id, and the `platform` flag are registered in
Connection Hub under `authority_registry.authorities`. Versatile is discovered
by its registered host operation. Runtime mechanics live in the Connection Hub
SDK module:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

## Surfaces

```text
GET  /api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/platform_login
POST /api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/auth_google_session
POST /api/integrations/bundles/{tenant}/{project}/versatile@2026-03-31-13-36/public/delegated_consent
```

`platform_login` is a bundle-owned UI. It renders Google Identity Services and
posts the returned credential to `auth_google_session`.

`delegated_consent` is a bundle-owned delegated-credential consent renderer. It
returns HTML for presentation only. The approve/deny form posts back to
Connection Hub, which still validates CSRF, narrows grants/tools, creates the
authorization code, and issues delegated credentials.

On success, the response sets the descriptor-configured platform auth cookies.

## Platform Descriptor

`assembly.yaml` must select bundle-session auth:

```yaml
auth:
  type: bundle
  idp: session
  auth_token_cookie_name: "__Secure-LATC"
  id_token_cookie_name: "__Secure-LITC"
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

`secrets.yaml` must include the shared verifier secret:

```yaml
services:
  session_token:
    secret: "<deployment secret>"
```

Every ingress/proc worker must read the same `services.session_token.secret`.

## Connection Hub Registry

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
                      email: lena@example.com
                      email_verified: true
                  roles:
                    - kdcube:role:super-admin
                  permissions:
                    - kdcube:*:*:*
            providers:
              versatile_google_session:
                type: bundle_session_login
                enabled: true
                label: Versatile Google platform session
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
                  client_id: 960111679915-825b0cenujpavcmognp450l7ius4suje.apps.googleusercontent.com
```

This registry is the single source of provider policy. It can contain multiple
platform-capable authorities and multiple provider instances.

The provider `grants` block is provider policy:

- `grants.default` is what every successful session from this provider
  receives automatically. Keep it empty unless every successful login should
  really have those roles/permissions.
- `grants.assignable` is the maximum access this provider may assign
  through authority-level grants. It is the safety boundary for bootstrap rules and
  explicit subject assignments.

It is not a blanket rule that every Telegram or Google user owns platform
roles.

Per-user role assignment belongs to the platform authority. The canonical
assignment key is the authority subject, for example
`grants.subjects.google:<verified_google_sub>`.

Admins do not usually know Google `sub` before the first login. For that
bootstrap case, Connection Hub can define `grants.bootstrap_rules`. A
bootstrap rule may match verified login claims such as Google
`email + email_verified`, then assign roles to the resolved platform subject
`google:<sub>` during login. Email is not the identity key; it is only a trusted
claim matcher used to discover the subject.

In Cognito, equivalent role data comes from Cognito groups/claims. In a
production bundle-session authority this should become a Connection Hub
UI-managed authority user/role store. The roles are still roles of the platform
subject, not roles of the raw Telegram channel identity or an email attribute.

If authority-level grants ask for a role/permission outside
provider `grants.assignable`, the hosted operation fails closed.

## Bundle Descriptor

`bundles.yaml` does not define platform-session policy for Versatile. It only
needs the Connection Hub pointer and the Telegram integration config used by
the hosted operation:

```yaml
items:
  - id: versatile@2026-03-31-13-36
    config:
      connections:
        connection_hub:
          bundle_id: connection-hub@1-0
      integrations:
        telegram.kdcube_ref:
          provider: telegram
          where: built-in
          enabled: true
          secret_refs:
            bot_token: integrations.telegram_kdcube_ref.definition.bot_token
            webhook_secret: integrations.telegram_kdcube_ref.definition.webhook_secret
```

`bundles.secrets.yaml` must provide the referenced Telegram secrets. These
secrets verify the upstream Telegram proof. They do not define platform grants:

```yaml
items:
  - id: versatile@2026-03-31-13-36
    secrets:
      integrations:
        telegram_kdcube_ref:
          definition:
            bot_token: "<telegram bot token>"
            webhook_secret: "<telegram webhook secret>"
```

## Responsibility Split

| Responsibility | Owner |
|---|---|
| Verify Telegram `initData` | SDK `bundle_session_login` runtime via Versatile Telegram integration |
| Verify Google ID token | SDK `bundle_session_login` runtime via SDK Google OIDC verifier |
| Register platform authority and provider instance | Connection Hub `authority_registry` |
| Default/assignable grants this provider may issue | Connection Hub provider instance |
| Per-subject grants | Platform authority grants / future user-role store |
| Issue `kst1` platform session token | SDK `bundle_session_login` runtime via platform bundle-session authority |
| Verify future requests | ingress/proc bundle-session auth manager |
| Store active sessions and users | platform Redis session registry |

The bundle code must not hardcode issuer roles, permissions, bot identity, or
deployment cookie names. Versatile's file
`services/platform_session_issuer.py` is intentionally a thin UI/operation
wrapper. It delegates registry lookup, proof handling, grant resolution, and
session issuing to the Connection Hub SDK runtime, which resolves the provider
instance by hosted operation and fails closed if the provider is missing,
disabled, not platform-capable, or not hosted by this bundle.

## Google Login Flow

```text
Browser opens the normal platform UI route
  -> frontend fetches /api/cp-frontend-config
  -> auth.connection_hub points to kdcube.platform.providers.versatile_google_session entrypoint=login
  -> frontend asks Connection Hub to resolve the login entrypoint URL
  -> frontend redirects to Versatile platform_login when no valid platform session exists
  -> Versatile hosts the page and calls SDK bundle_session_login runtime
  -> SDK runtime resolves kdcube.platform.providers.versatile_google_session
  -> SDK runtime resolves google.accounts.providers.google_oidc
  -> page renders Google Identity Services with descriptor client_id
  -> browser posts Google credential to auth_google_session
  -> SDK runtime verifies the Google ID token
  -> SDK runtime computes platform subject google:<sub>
  -> SDK runtime resolves grants.subjects or grants.bootstrap_rules
     from Connection Hub authority registry
  -> bundle-session authority writes/updates that platform subject
  -> bundle-session authority issues kst1
  -> response sets platform auth cookies
```

This is the clean replacement for app-local `AUTH_PROVIDER=session`
monkeypatching: the platform verifier is standard bundle-session auth, while
the bundle only hosts a registered provider flow.
