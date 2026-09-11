---
id: repo:kdcube-ai-app/app/ai-app/ui/chat-web-app/README.md
title: "KDCube Web App"
summary: "Describes the browser shell, runtime configuration contract, app presentation, authentication, and local development workflow."
tags: ["frontend", "web-app", "control-plane", "authentication"]
keywords: ["KDCube web app", "cp-frontend-config", "platform session", "app presentation"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/server-side-login-and-platform-session-README.md
---
# KDCube Web App

This React application is KDCube's browser shell. It selects an app, presents
that app's main browser surface, hosts platform control panels, and connects the
SDK chat experience to the backend transports.

It is not the UI every KDCube app must use. Apps may provide their own main
view, widgets, Scene, website, APIs, or no UI at all.

## Runtime Contract

At startup the client requests runtime configuration from:

```text
CHAT_WEB_APP_CONFIG_ENDPOINT=/api/cp-frontend-config
```

If that endpoint is unavailable, it falls back to:

```text
CHAT_WEB_APP_CONFIG_FILE_PATH=<control-plane-mount>/config.json
```

Use the endpoint in descriptor-driven deployments. The static file is intended
for local development or static hosting without the control-plane endpoint.
The default fallback is computed at runtime from the control-plane mount, so a
single built artifact can use `/platform/config.json` or
`/control/ui/config.json` without rebuilding.

The endpoint supplies the effective tenant/project, app catalog, browser auth
configuration, cookie/header names, and platform routes. The browser should not
reconstruct those values from deployment internals.

The built artifact must keep frontend files under the control-plane mount:

```text
/platform/chat                  chat route
/platform/assets/index-....js   JavaScript
/platform/img/favicon.svg       image

/control/ui/chat                chat route with a multi-segment mount
/control/ui/assets/index-....js JavaScript
/control/ui/img/favicon.svg     image
```

Do not emit root frontend-file URLs such as `/assets/...`, `/img/...`, or
`/config.json`. Root clean paths may belong to application-hosted sites.

## App Presentation

For the selected app, the shell follows this order:

```text
app has a built main view
  -> display that main view

otherwise
  -> display the automatic app scene
     -> visible app widgets become summonable chips
     -> an explicitly declared default chat is the reserved `chat` widget
```

An app declares chat intent with:

```yaml
surfaces:
  as_provider:
    bundle:
      default_chat: true
```

Inheriting a reactive entrypoint is capability, not browser intent.

The quick-access rail is for platform control surfaces such as economics,
usage, apps, gateway, conversation/Redis/storage browsers, and the pinned
connections widget. Conversation-specific controls belong inside the chat
component.

## Authentication

The web app supports the backend-provided auth modes in the runtime config,
including Cognito, simple token, server-side platform session, and no-auth local
profiles.

`/profile` is the source of the current browser session. A visible email, local
OIDC state, or a client-readable cookie is not sufficient evidence that a
platform user is authenticated.

For server-side session auth, the config may provide a concrete
`loginUrl` or a Connection Hub authority/provider reference. The client can
resolve the provider's login entrypoint when no concrete URL is materialized.

## Development

Install dependencies and start Vite:

```bash
npm install
npm run dev
```

The development proxy defaults to:

```text
VITE_APP_API_BASE=http://localhost:8010/
VITE_APP_INTEGRATIONS_API_BASE=http://localhost:8020/
```

Optional settings:

```text
VITE_HTTPS=true
CHAT_WEB_APP_SHOW_DEBUG_CONTROLS=true
CHAT_WEB_APP_CHAT_API_BASE_PATH=/custom-prefix
```

Validate a change with:

```bash
npm run lint
npm run build
```

`npm run build_no_lint` runs the Vite build without the TypeScript build step;
use it only when that distinction is intentional.

## Read Next

- [Control Plane Web App](../../docs/arch/control-plane-web-app-README.md)
- [What You Can Do With KDCube](../../docs/what-you-can-do-with-kdcube-README.md)
- [Architecture Short](../../docs/arch/architecture-short.md)
- [Chat Widget Solution](../../docs/sdk/solutions/chat/chat-widget-solution-README.md)
- [Application-Hosted Sites](../../docs/sdk/solutions/sites/application-sites-README.md)
- [Platform Authority Setup](../../docs/recipes/connections/platform-authority/setup-platform-authority-README.md)
