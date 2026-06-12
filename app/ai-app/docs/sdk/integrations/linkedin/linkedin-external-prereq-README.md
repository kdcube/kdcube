---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-external-prereq-README.md
title: "LinkedIn External Prerequisites"
summary: "External provider and deployment setup required before KDCube LinkedIn SDK integrations can work."
tags: ["sdk", "integrations", "linkedin", "oauth", "prerequisites"]
keywords: ["linkedin prerequisites", "linkedin developer app", "linkedin oauth setup", "linkedin redirect uri", "linkedin deployment secrets"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/linkedin/linkedin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
---

# LinkedIn External Prerequisites

This document lists work that must happen outside KDCube before a bundle can use
the LinkedIn SDK integration.

The LinkedIn SDK provides reusable account storage, OAuth callback handling, and
UGC Posts API access. It cannot create LinkedIn Developer Apps, request API
product access, or register redirect URIs.

## What Is External

External setup includes:

- LinkedIn Developer App creation and configuration.
- OAuth 2.0 product enablement (`Sign In with LinkedIn using OpenID Connect`
  and `Share on LinkedIn`).
- Authorized redirect URI registration for the bundle OAuth callback.
- Deployment secrets for the LinkedIn OAuth client secret and state signing.
- Public HTTPS base URL when OAuth callbacks must reach a local or hosted
  runtime.

The bundle or platform still owns:

- route exposure for `linkedin_oauth_callback` and other account operations
- storage root and target user resolution
- user-facing Settings UI or Telegram Mini App actions
- user-scoped token storage through KDCube user secrets
- product policy for which account can publish

## LinkedIn Developer App Setup

Official reference: <https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow>

Human/operator actions:

| Step | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | LinkedIn Developer Portal | Create a new app at `developer.linkedin.com`. Associate it with a LinkedIn Company Page. | App client id and client secret. |
| 2 | App → Products tab | Request access to **Sign In with LinkedIn using OpenID Connect**. This provides `openid`, `profile`, `email` scopes. | Product approved (usually instant). |
| 3 | App → Products tab | Request access to **Share on LinkedIn**. This provides the `w_member_social` scope required for posting. | Product approved (may require review). |
| 4 | App → Auth tab | Add the bundle callback URL as an authorized redirect URI. | Redirect URI registered. |
| 5 | KDCube descriptors/config | Set non-secret LinkedIn config. | Updated bundle config. |
| 6 | KDCube secrets provider | Set secret values. | Updated bundle secrets or secrets-provider entries. |

## Callback URL

The OAuth callback must be reachable by LinkedIn's browser redirect. For a
bundle public operation alias named `linkedin_oauth_callback`, the route shape
is:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/linkedin_oauth_callback
```

The redirect URI registered in the LinkedIn Developer App must match this URL
exactly, including scheme, host, path, and absence of trailing slash. For local
development behind a tunnel, keep the LinkedIn authorized redirect URI and the
bundle config aligned with the current tunnel host.

## Descriptor Values

Non-secret config typically lives in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      config:
        integrations:
          linkedin:
            enabled: true
            client_id: "<LINKEDIN_CLIENT_ID>"
            # scopes is optional — omit it to use the default set:
            #   openid, profile, email, w_member_social
            # Only set it if you need additional scopes beyond the defaults.
            # The SDK always ensures the four default scopes are included.
            oauth:
              public_base_url: "https://<PUBLIC_HOST>"
              redirect_uri: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/linkedin_oauth_callback"
```

Secrets live in `bundles.secrets.yaml` or the configured secrets provider:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      secrets:
        integrations:
          linkedin:
            client_secret: "<LINKEDIN_CLIENT_SECRET>"
            oauth_state_secret: "<RANDOM_STATE_SIGNING_SECRET>"
```

Generate `oauth_state_secret` outside source control:

```bash
openssl rand -hex 32
```

Or via the KDCube CLI:

```bash
kdcube bundle <BUNDLE_ID> --set-secret integrations.linkedin.oauth_state_secret "$(openssl rand -hex 32)"
```

## User LinkedIn Connection

Deployment config only prepares the OAuth client. Each user still connects
their own LinkedIn account through a bundle Settings UI or another route that
calls the LinkedIn SDK account settings operations.

The OAuth access token is stored as a user-scoped KDCube secret. Do not store
user tokens in descriptor files.

## w_member_social Approval

The `Share on LinkedIn` product (which grants `w_member_social`) is sometimes
subject to LinkedIn review for newly created apps. If posting returns a 403
with `NOT_ENOUGH_PERMISSIONS`, verify that:

1. The `Share on LinkedIn` product is approved under the app's Products tab.
2. The connected user account re-authorized after the product was approved
   (token predates the scope grant).
3. The `w_member_social` scope appears in the stored token's `scope` field.
