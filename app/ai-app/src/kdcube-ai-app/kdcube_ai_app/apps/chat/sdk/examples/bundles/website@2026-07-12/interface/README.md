---
id: website@2026-07-12/interface
title: "Website App Interface"
summary: "Public main view and site-configuration contract."
status: active
tags: ["interface", "website", "main-view", "public-api"]
---

# Website App Interface

## Public Main View

```text
GET /api/integrations/bundles/{tenant}/{project}/website@2026-07-12/public/static
```

The proc static route serves the app-built `ui.main_view` and injects its base
path. The site registry also exposes it at `/sites/{alias}`. The proxy forwards
`/` to root resolution without changing `/api/*` or `/platform/*`.

```text
GET /sites/{alias}
GET /sites/{alias}/{path}
GET /                         # host match, then default
```

## Site Configuration

```text
GET /api/integrations/bundles/{tenant}/{project}/website@2026-07-12/public/site_config
```

The operation returns non-secret composition only:

```json
{
  "application_id": "website@2026-07-12",
  "site_alias": "workspace",
  "title": "KDCube Workspace",
  "scene_application_id": "workspace@2026-03-31-13-36",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "platform_config_url": "/api/cp-frontend-config",
  "profile_url": "/profile"
}
```

Platform authority, provider, login URL, route prefix, cookie names, profile
URL, and logout URL come from `/api/cp-frontend-config`; the app does not own or
duplicate them.

## Browser Host Contract

The site:

1. fetches `public/site_config`;
2. fetches `/api/cp-frontend-config`;
3. fetches `/profile` with same-origin cookies;
4. mounts the configured scene app;
5. answers relayed `CONFIG_REQUEST` messages with `CONFIG_RESPONSE`;
6. announces session transitions with `kdcube-auth-changed`;
7. opens the backend-provided login URL, or the configured platform frontend
   when the active authority uses the platform browser flow.
