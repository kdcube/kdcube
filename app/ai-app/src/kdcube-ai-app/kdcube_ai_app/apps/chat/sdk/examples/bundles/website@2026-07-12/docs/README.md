---
id: website@2026-07-12/docs
title: "Website App Design"
summary: "Ownership and runtime flow for an app-hosted local KDCube website."
status: active
tags: ["design", "website", "scene", "auth"]
---

# Website App Design

## Boundary

The website app owns:

- the static website shell;
- which scene app is mounted;
- website title and presentation;
- browser handling of the standard scene config/auth messages.

The platform owns:

- active authority/provider configuration;
- resolved login and logout endpoints;
- `/profile` session truth;
- `/api/*` and `/platform/*` routes;
- building and serving app main views.

The platform runtime owns the site registry. OpenResty forwards stable root and
alias routes to proc; proc resolves the active app descriptors at request time.
No proxy regeneration is needed when site declarations change.

```text
bundles.yaml
  website.ui.main_view.site
          |
          +--> proc site registry: alias + hosts + default
          |
          +--> website public/site_config
                         |
browser ----------------+--> /api/cp-frontend-config
                         +--> /profile
                         +--> configured scene public/static

OpenResty / ---------------------> proc /api/integrations/site-root
OpenResty /sites/{alias}/{path} -> proc /api/integrations/sites/{alias}/{path}
```

No site selection or scene composition belongs in `assembly.yaml`.

Root resolution is deterministic:

1. select the single site whose `hosts` entry matches the request host;
2. otherwise select the single site with `default: true`;
3. otherwise redirect to the configured platform chat route.

Duplicate aliases, ambiguous host matches, and multiple defaults are invalid
registry states and return `503` rather than selecting an arbitrary site.

The website uses the normal main-view cache policy: entry HTML and root-level
non-hashed shell files revalidate, while content-hashed `assets/` files are
immutable. The public-content publication subsystem is not involved.
