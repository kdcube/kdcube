---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/control-plane-web-app-README.md
title: "Control Plane Web App"
summary: "Browser architecture of the KDCube control-plane web app: runtime configuration, provider-neutral authentication, app selection, main-view and automatic-scene presentation, quick-access controls, chat transport, and site boundaries."
status: current
tags: ["arch", "control-plane", "web-app", "browser", "apps", "chat", "auth"]
updated_at: 2026-09-11
keywords: ["KDCube web app", "control plane web app", "cp frontend config", "automatic app scene", "default chat", "quick access rail"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-we-built-README.md
  - repo:kdcube/app/ai-app/docs/arch/application-hosted-websites-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/sites/application-sites-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/platform-authority/setup-platform-authority-README.md
---
# Control Plane Web App

The KDCube control-plane web app is the browser shell for selecting and using
apps in one tenant/project deployment. It presents each app's browser surfaces,
hosts platform control panels, and connects chat UI to backend transports.

It is not a mandatory frontend framework for every app. An app may provide its
own main view, widgets, Scene, complete website, or no browser surface.

## Browser Journey

```text
load browser shell
      |
      v
GET /api/cp-frontend-config
      |
      +-- tenant/project and app catalog
      +-- provider-neutral auth contract
      +-- cookie/header and platform-route configuration
      `-- browser feature/configuration values
      |
      v
GET /profile
      |
      +-- authenticated platform user -> continue
      `-- anonymous -> follow configured login route
      |
      v
select app
      |
      +-- app main view exists -> show it
      `-- otherwise -> show automatic app scene
```

`/api/cp-frontend-config` is the effective browser configuration contract.
The client must not reconstruct provider, app, tenant, project, or cookie
configuration from internal URLs or deployment assumptions.

This endpoint is public deployment metadata. Ingress builds it from the mounted,
mtime-cached `assembly.yaml` and `bundles.yaml` descriptors. It does not create a
user session, call a Connection Hub operation, or wait on Redis or Postgres.

`/profile` is the source of logged-in state. A visible email, local OIDC cache,
or client-readable cookie is not sufficient evidence of an authenticated
platform user.

## Provider-Neutral Authentication

The backend can select Cognito, multi-Cognito, SimpleIDP, or server-side login
defined by an app. The browser consumes the resolved auth
contract rather than branching on provider internals.

For server-side login defined by an app, ingress reads the selected entrypoint from
the mounted authority registry, constructs its public app-operation URL, and
returns that concrete URL in frontend config. The browser does not call
Connection Hub to resolve it.

Connection Hub remains the authority/provider registry and policy owner even
when another app hosts login, session-issue, or consent presentation.

## App Presentation

The selected app controls what it provides. The control-plane web app follows
this presentation order:

```text
app provides built ui.main_view
  -> iframe the app's main view
  -> retain visible widget chips/panels as companion surfaces

app has no main view
  -> build the automatic app scene from visible widgets
  -> first widget opens on arrival
  -> reserved chat widget opens first when declared

app has no browser surfaces
  -> show an honest background-app empty state
```

An app declares the ready chat surface with:

```yaml
config:
  surfaces:
    as_provider:
      bundle:
        default_chat: true
```

This serves the SDK chat widget under the reserved alias `chat`. It is an
explicit intent declaration. An inherited `@on_reactive_event` handler only
proves runtime capability; it does not imply that the app wants chat UI.

## Provider And Consumer Roles

The web app presents provider surfaces exposed by selected apps. App
dependencies remain a separate consumer concern:

```text
surfaces.as_provider
  what the control plane may discover and present/call for this app

surfaces.as_consumer
  what the app, its agents, and its composed Scene may call or mount
```

For example, a Scene component entry is consumer wiring because the Scene app
mounts another app's widget. The mounted widget's own serving route remains its
provider app identity; the host does not rewrite that identity to itself.

## Quick-Access Rail

The side rail is the home for platform control surfaces. Depending on what the
deployment serves and the current user's visibility, it can open:

- economics and usage;
- app controls;
- gateway state;
- conversation browser;
- Redis browser;
- the services app's storage browser;
- the pinned connections widget;
- selected app widget panels while an app main view is active.

Conversation UX and conversation controls belong inside the chat widget rather
than the platform rail.

## Chat And Surface Communication

The chat component owns conversation streaming, attachments, files, tool
progress, followups, steer, model/capability settings, and connection actions.
The shell supplies the selected app/tenant/project context and maintains the
backend connection.

Cross-surface actions use declared surface commands. A command such as opening
connections follows an acknowledgement contract; the sender can use a direct
served-widget fallback when no host acknowledges it. A row that cannot perform
either route should not render as a silent no-op.

Context drag/drop preserves object kind and ref. In particular,
`conv:fi:...` is a conversation-owned file/context object, not a conversation
id. Only a strict `conv:<conversation_id>` ref enters conversation loading.

## Application Websites Are Separate

The control-plane app surface and an application-hosted website use the same app
UI build/storage lifecycle but different browser routes:

```text
control-plane app view
  selected inside the KDCube shell

application-hosted site
  complete built main-view tree served by alias or host
  resolved through ApplicationSiteCatalog
```

One app has at most one main view and therefore at most one registered
application site. Many apps can register sites in the same deployment.

A CDN may preserve the viewer hostname and rewrite a clean public path to the
reserved `site-root` origin route. The CDN does not own the site catalog.

`@public_content` is another separate mechanism for indexed public records,
catalogs, structured metadata, and sitemaps.

## Control-Plane Mount And Assets

The control-plane web app is served under the configured `proxy.route_prefix`.
The route prefix is the browser mount for the built frontend files and is
separate from the chat route inside the app. For example:

```text
/platform/chat                  chat route
/platform/assets/index-....js   frontend JavaScript
/platform/img/favicon.svg       frontend image
/platform/config.json           optional static config fallback

/control/ui/chat                chat route with a multi-segment mount
/control/ui/assets/index-....js frontend JavaScript
/control/ui/img/favicon.svg     frontend image
/control/ui/config.json         optional static config fallback
```

The same built artifact must work under both mounts. It therefore must not emit
root frontend-file URLs such as `/assets/...`, `/img/...`, or `/config.json`.
At runtime the HTML shell computes the mount from the current browser path and
loads its JavaScript, CSS, image, and optional static configuration files under
that mount.

Root API routes remain root API routes. In particular,
`/api/cp-frontend-config` stays at root because it is a backend configuration
endpoint, not a static frontend file.

When application-hosted sites are enabled, `proxy.route_prefix: /` is invalid.
Root clean paths are reserved for host/default-selected sites, while the
control-plane frontend must live under a non-root mount.

## Source And Development

Source package:

```text
app/ai-app/ui/chat-web-app
```

Key implementation areas:

```text
src/BuildConfig.ts                     runtime endpoint and local fallback
src/features/auth/                     browser auth behavior
src/features/bundles/AppScene.tsx      automatic app scene
src/components/chat/Chat.tsx           main-view versus scene selection
src/features/chatSidePanel/            quick-access rail and panels
src/features/chatController/           SSE/Socket.IO connection behavior
```

Local commands:

```bash
npm install
npm run dev
npm run lint
npm run build
```

The Vite development server proxies platform API, integration, profile,
monitoring, Socket.IO, and SSE routes to configured local backends.

## Do Not Infer

- The control-plane web app is not required for a standalone app website.
- Selecting an app does not make all of its surfaces visible; provider policy
  and current authority still apply.
- Consumer tool/MCP/named-service configuration is not a browser publication
  mechanism.
- A main view does not remove the app's widget chips and side panels.
- A session id alone is not the server-side relay routing key; relay state is
  tenant/project/session scoped.
- Browser auth state is not authority for backend operations; every request is
  authenticated and authorized again by the serving runtime.

## Read Next

- [Architecture Of What We Built](architecture-of-what-we-built-README.md)
- [Architecture Of What You Build](architecture-of-what-you-build-README.md)
- [Chat Widget Solution](../sdk/solutions/chat/chat-widget-solution-README.md)
- [Application-Hosted Sites](../sdk/solutions/sites/application-sites-README.md)
- [Application-Hosted Website Architecture](application-hosted-websites-README.md)
- [Platform Authority Setup](../recipes/connections/platform-authority/setup-platform-authority-README.md)
