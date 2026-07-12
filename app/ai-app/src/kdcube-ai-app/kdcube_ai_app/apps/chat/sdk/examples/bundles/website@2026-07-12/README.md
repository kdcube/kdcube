---
id: website@2026-07-12
title: "Website App"
summary: "Reference app-owned website shell for a KDCube deployment."
status: active
tags: ["app", "website", "main-view", "scene", "local-runtime"]
links:
  config: config/bundles.template.yaml
  interface: interface/README.md
  design: docs/README.md
  storage: docs/storage/README.md
---

# Website App

`website@2026-07-12` demonstrates a website owned and served by a KDCube app.
It builds a public `ui.main_view`, reads platform/auth metadata from
`/api/cp-frontend-config`, reads its composition from `public/site_config`, and
hosts the configured app scene.

Every enabled site has a unique public alias. The runtime can also select a root
site by request host and then by one explicit default. Platform and API paths
keep their normal ownership:

```text
/                         -> host match, then the default site
/sites/workspace          -> website@2026-07-12 public main view
/sites/workspace/<path>   -> the same site with SPA fallback
/platform/* -> platform frontend
/api/*      -> platform/application APIs
```

Enable it in the app entry in `bundles.yaml`:

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
          title: KDCube Workspace
          scene_application_id: workspace@2026-03-31-13-36
```

Many apps may enable a site. Aliases must be unique. At most one site may be
the default, and one host must not match multiple sites. Site routing stays in
`bundles.yaml`; it does not belong in `assembly.yaml` or the CLI.
