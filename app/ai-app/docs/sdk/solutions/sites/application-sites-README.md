---
id: sdk/solutions/sites/application-sites
title: "Application-Hosted Sites"
summary: "How KDCube apps register directly addressable websites and participate in root host routing."
status: active
tags: ["sites", "website", "main-view", "routing", "bundles.yaml"]
---

# Application-Hosted Sites

An app can publish its normal `ui.main_view` as a website. Site registration is
app configuration in `bundles.yaml`; it is not an `assembly.yaml` setting and
is not interpreted by the CLI.

```yaml
- id: website@2026-07-12
  config:
    ui:
      main_view:
        site:
          enabled: true
          alias: workspace
          default: true
          hosts:
            - workspace.example.com
```

| Field | Contract |
| --- | --- |
| `enabled` | Registers the already-built public main view as a site. |
| `alias` | Required unique route key. `_root` is reserved. |
| `default` | Optional root fallback. At most one enabled site may be default. |
| `hosts` | Optional exact hosts or `*.example.com` patterns used before the default. |

```text
request /sites/{alias}/{path}
        |
        +--> OpenResty stable forward
        +--> proc loads active app registry and authoritative app props
        +--> alias selects app
        +--> standard app static lifecycle serves main view/assets

request /
        |
        +--> proc /api/integrations/site-root
        +--> host match
        +--> otherwise one default
        +--> otherwise configured platform chat route
```

OpenResty does not contain an app list. It only forwards `/` and `/sites/*` to
proc. This allows descriptor reloads to add, remove, or remap sites without
regenerating proxy configuration.

The site shell should read platform/auth browser configuration from
`/api/cp-frontend-config` and authenticated session truth from `/profile`.
Provider-specific login settings do not belong in site source.

The standard main-view static lifecycle supplies cache policy. Entry HTML and
root-level non-hashed files revalidate with `no-cache`; hashed files under
`assets/` are immutable for one year. A site does not use the public-content
publication registry merely to cache its shell.

The reference implementation is
`sdk/examples/bundles/website@2026-07-12`.
