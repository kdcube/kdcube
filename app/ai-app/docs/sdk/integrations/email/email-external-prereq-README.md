---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
title: "Email External Prerequisites"
summary: "External provider and deployment setup required before KDCube Email SDK integrations can work."
tags: ["sdk", "integrations", "email", "gmail", "icloud", "oauth", "prerequisites"]
keywords: ["email prerequisites", "gmail oauth setup", "gmail api", "icloud app password", "email deployment secrets"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
---

# Email External Prerequisites

This document lists work that must happen outside KDCube before a bundle can use
the Email SDK integration.

The Email SDK provides reusable account storage, Gmail/iCloud provider access,
OAuth callback handling, attachment materialization, Email MCP, and delivery
helpers. It cannot create external Google projects, enable Gmail API, configure
OAuth consent, or issue iCloud app-specific passwords.

## What Is External

External setup includes:

- Google Cloud project and Gmail API enablement.
- Google OAuth Web application client.
- Authorized redirect URI for the bundle OAuth callback.
- Deployment secrets for Google OAuth client secret and state signing.
- iCloud app-specific passwords created by each user when iCloud mail is used.
- Public HTTPS base URL when OAuth callbacks must reach a local or hosted
  runtime.

The bundle or platform still owns:

- route exposure for `email_oauth_callback` and other email account operations
- storage root and target user resolution
- user-facing Settings UI or Telegram Mini App actions
- user-scoped token storage through KDCube user secrets
- product policy for which account can be used by a task or agent

## Gmail / Google OAuth

Official references:

- Google OAuth for web server apps:
  <https://developers.google.com/identity/protocols/oauth2/web-server>
- Gmail API:
  <https://console.cloud.google.com/apis/library/gmail.googleapis.com>

Human/operator actions:

| Step | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | Google Cloud Console | Create or choose the Google Cloud project used for this deployment. | Project id or project number. |
| 2 | Google Cloud Console | Enable Gmail API in that project. | Gmail API enabled. |
| 3 | Google Cloud Console | Configure OAuth consent screen and test/published users as required. | Users can grant the configured scopes. |
| 4 | Google Cloud Console | Create a Web application OAuth client. | Client id and client secret. |
| 5 | Google Cloud Console | Add the bundle callback URL as an authorized redirect URI. | `https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/email_oauth_callback` |
| 6 | KDCube descriptors/config | Set non-secret email config. | Updated bundle config. |
| 7 | KDCube secrets provider | Set secret values. | Updated bundle secrets or secrets-provider entries. |

Common Gmail scopes:

```text
openid
email
profile
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
```

Use `gmail.readonly` for mailbox reading and attachment fetch. Use
`gmail.send` when the bundle sends reports or task outputs through Gmail.

## Callback URL

The OAuth callback must be reachable by Google's browser redirect. For a bundle
public operation alias named `email_oauth_callback`, the route shape is:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/email_oauth_callback
```

For local development behind a tunnel, keep the Google authorized redirect URI
and the bundle config aligned with the current tunnel host.

## Descriptor Values

Non-secret config typically lives in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      config:
        integrations:
          email:
            enabled: true
            google:
              client_id: "<GOOGLE_OAUTH_CLIENT_ID>"
              scopes:
                - "openid"
                - "email"
                - "profile"
                - "https://www.googleapis.com/auth/gmail.readonly"
                - "https://www.googleapis.com/auth/gmail.send"
            oauth:
              public_base_url: "https://<PUBLIC_HOST>"
              redirect_uri: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/email_oauth_callback"
```

Secrets live in `bundles.secrets.yaml` or the configured secrets provider:

```yaml
bundles:
  version: "1"
  items:
    - id: "<BUNDLE_ID>"
      secrets:
        integrations:
          email:
            google:
              client_secret: "<GOOGLE_OAUTH_CLIENT_SECRET>"
            oauth_state_secret: "<RANDOM_STATE_SIGNING_SECRET>"
```

Generate `oauth_state_secret` outside source control:

```bash
printf '%s\n' "$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
```

## User Gmail Connection

Deployment config only prepares the OAuth client. Each user still connects
their own Gmail account through a bundle Settings UI, Telegram Mini App, or
another route that calls the Email SDK account settings operations.

The OAuth token is stored as a user-scoped KDCube secret. Do not store user
refresh tokens in descriptor files.

## Gmail API Disabled Symptom

An OAuth token can have the correct Gmail scopes while Gmail calls still fail if
Gmail API is disabled in the Google Cloud project. If a tool or route reports a
`google_gmail_api_not_enabled` style error, enable Gmail API here:

```text
https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=<GOOGLE_PROJECT_ID_OR_NUMBER>
```

Wait one or two minutes after enabling the API, then retry.

## iCloud Mail

iCloud mail does not use Google OAuth. Each user must create an app-specific
password in their Apple account and enter it through the bundle Settings UI or
Telegram Mini App route that calls `connect_app_password(...)`.

External user actions:

| Step | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | Apple Account settings | Enable two-factor authentication if it is not already enabled. | Account can create app-specific passwords. |
| 2 | Apple Account settings | Create an app-specific password for the KDCube/bundle mail integration. | One app-specific password. |
| 3 | Bundle Settings UI | Enter iCloud email address and app-specific password. | User-scoped account record and secret. |

The SDK uses IMAP for reading and SMTP for sending. The app-specific password is
stored as a user-scoped secret, not in descriptor files.

## Local Development Notes

For local handoff between developers, each developer can use their own Google
Cloud project and OAuth client. They must update:

- Gmail API enablement in their project.
- OAuth consent/test users in their project.
- Web application OAuth client redirect URI.
- `integrations.email.google.client_id`.
- `integrations.email.google.client_secret`.
- `integrations.email.oauth.public_base_url`.
- `integrations.email.oauth.redirect_uri`.

Do not share Google client secrets, OAuth state secrets, refresh tokens, or
iCloud app-specific passwords in source control or chat history.
