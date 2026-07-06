---
id: connection-hub@1-0/integrations/slack
title: "Connection Hub — Slack setup"
summary: "Operator step-by-step to create the Slack OAuth connector app the connection-hub uses for search, post, channel listing/history, files, and assistant search; includes the delegated to KDCube redirect URI, app approval, and where the client_id/secret go."
status: "active"
tags: ["integration", "connections", "oauth", "slack", "operator-setup", "prerequisites"]
keywords: ["slack oauth app", "slack app approval", "search:read", "chat:write", "delegated_to_kdcube_oauth_callback", "slack client_secret", "workspace admin"]
see_also:
  - ./README.md
---

# Connection Hub — Slack setup

Slack rides the **delegated to KDCube framework** (OAuth, **user-token** flow).
See [the overview](./README.md) for the delegated to KDCube callback URL and
state secret.

> **The goal: any user connects *their own* Slack.** You create **one** app (the
> OAuth client). With **Public Distribution** turned on, the app is **not** bound
> to your workspace — a user from **any** workspace can run the OAuth flow and
> consent to install it into **their** workspace, and we store **their** user
> token. You only install it into your own workspace for testing.

> **User token, not bot token.** Reading/posting/searching as the connected
> Slack user needs a **user** token. The connector requests scopes under
> `user_scope` and stores `authed_user.access_token` — so the requested scopes
> below are **User Token Scopes**.

Official refs: Slack apps <https://api.slack.com/apps> · distribution
<https://api.slack.com/start/distributing> · scopes <https://api.slack.com/scopes>

| # | Where | Action | Output |
| --- | --- | --- | --- |
| 1 | <https://api.slack.com/apps> → **Create New App** → *From scratch* | Name it, pick a (your) workspace to develop in. | App created |
| 2 | **OAuth & Permissions** → Scopes → **User Token Scopes** | Add `search:read`, `chat:write`, `channels:read`, `groups:read`, `im:read`, `mpim:read`, `channels:history`, `groups:history`, `im:history`, `mpim:history`, `files:read`, `files:write`, and the split `search:read.*` assistant-search scopes. These act as the user. | Scopes set |
| 3 | **OAuth & Permissions** → Redirect URLs | Add the delegated to KDCube callback: `…/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback` | Redirect registered |
| 4 | **Manage Distribution** → **Activate Public Distribution** | Complete the checklist (HTTPS redirect, no hardcoded info) and turn it ON. **This is what lets random users in other workspaces connect.** | App is publicly installable |
| 5 | **Basic Information** → App Credentials | Copy **Client ID** and **Client Secret**. | Credentials |
| 6 | `bundles.yaml` | Client ID → the Slack connector app (below). | config updated |
| 7 | `bundles.secrets.yaml` | Client Secret → the matching secret (below). | secret updated |

> **A connecting user's *own* workspace admin** may still need to approve the app
> if that workspace restricts installs (their *Settings → Manage apps → App
> approval*) — that's the user's admin, not yours.

**Who to ask:** standard workspace → a **Workspace Admin**; Enterprise Grid → an
**Org Owner/Admin**. App approval lives under *Settings → Manage apps → App
approval*. (A ready-to-send request message is in the bundle README.)

`bundles.yaml`:

```yaml
config:
  connections:
    delegated_to_kdcube:
      enabled: true
      providers:
        slack:
          label: Slack
          adapter: slack.oauth_user_token
          enabled: true
          claims:
            slack:search:
              label: Search Slack
              provider_scopes: [search:read]
            slack:post:
              label: Post to Slack
              provider_scopes: [chat:write]
            slack:channels:
              label: List Slack channels
              provider_scopes: [channels:read, groups:read, im:read, mpim:read]
            slack:history:
              label: Read Slack history
              provider_scopes: [channels:history, groups:history, im:history, mpim:history]
            slack:files:read:
              label: Read Slack files
              provider_scopes: [files:read]
            slack:files:write:
              label: Upload Slack files
              provider_scopes: [files:write]
            slack:assistant:search:
              label: Use Slack assistant search
              provider_scopes:
                - search:read.public
                - search:read.private
                - search:read.im
                - search:read.mpim
                - search:read.files
                - search:read.users
          connector_apps:
            demo:
              label: Demo Slack
              client_id: <SLACK_CLIENT_ID>
              client_secret_ref: connections.delegated_to_kdcube.providers.slack.connector_apps.demo.client_secret
              allowed_claims:
                - slack:search
                - slack:post
                - slack:channels
                - slack:history
                - slack:files:read
                - slack:files:write
                - slack:assistant:search
              enabled: true
```

`bundles.secrets.yaml`:

```yaml
secrets:
  connections:
    delegated_to_kdcube:
      providers:
        slack:
          connector_apps:
            demo:
              client_secret: <SLACK_CLIENT_SECRET>
```

## Why scopes appear in two places

Scopes are declared **on the Slack app** (step 2) **and** in our config — these are
two different things in two systems, and they must be aligned:

- **Slack app → User Token Scopes** — Slack's **registration**: the universe of
  scopes Slack will *permit* this app to request. Slack rejects an OAuth request for
  any scope not registered here. Set once, on Slack's side.
- **`connections.delegated_to_kdcube.providers.slack.connector_apps.<connector_app_id>.allowed_claims`**
  — what our connector app is allowed to request when a user connects, and the
  **admin ceiling** for the per-connect claim checkboxes in the widget. The
  provider maps selected claims to Slack OAuth scopes.

Keep them aligned: our requested scopes must be a **subset** of the app's registered
scopes (simplest — make the two lists identical). This is standard OAuth (the
provider registers the app's scope universe; the client requests a subset per
authorization), the same as Google's consent-screen scopes vs. the authorize URL.

The **`client_secret`** (in `bundles.secrets.yaml`) is unrelated to scopes — it is
the app's credential proving the code→token exchange comes from *our* app.

## Notes

- **Multiple connector apps per provider** are supported — add more entries under
  `connector_apps:` (each with its own connector app id, `client_id`, and
  secret). The widget lets the user pick which connector app to connect through.
- **Per-connect claims:** the widget shows connector app claims as
  checkboxes; a user can untick to request a smaller consent. Selected
  claims are clamped to the connector app's configured allowed-claims ceiling.
