---
id: sdk/solutions/sites/application-sites
title: "Application-Hosted Sites"
summary: "How KDCube apps register directly addressable websites and participate in root host routing."
status: active
tags: ["sites", "website", "main-view", "routing", "bundles.yaml"]
updated_at: 2026-09-14
keywords: ["application site", "site catalog", "host routing", "route prefix", "clean paths"]
see_also:
  - repo:kdcube/app/ai-app/docs/arch/application-hosted-websites-README.md
  - repo:kdcube/app/ai-app/docs/sdk/bundle/bundle-website-integration-README.md
  - repo:kdcube/app/ai-app/docs/recipes/components/website-README.md
  - repo:kdcube/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube/app/ai-app/docs/service/cicd/ngrok-README.md
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
          auth:
            mode: platform_session
          default: true
          hosts:
            - workspace.example.com
```

| Field | Contract |
| --- | --- |
| `enabled` | Registers the already-built main view as a site. |
| `alias` | Required unique route key. `_root` is reserved. |
| `auth.mode` | Browser authentication owner: `public` (default) or `platform_session`. A signed-out document request for a `platform_session` site enters the configured sign-in lane before the shell loads. |
| `default` | Optional root fallback. At most one enabled site may be default. |
| `hosts` | Optional exact hosts or `*.example.com` patterns used before the default. |

## App And Deployment Cardinality

The registered-site field is singular because the app browser root is
singular:

```text
one app -> zero or one effective ui.main_view -> zero or one site
deployment -> many apps -> many registered sites
```

The catalog reads the one effective `ui.main_view.site` mapping from each app.
The main view may be configuration-backed without `@ui_main`. If the app also
declares that optional code surface, the loader rejects multiple `@ui_main`
methods on one entrypoint. Multiple `hosts` values route several names to the
same app, alias, and built main-view tree; they do not create several sites.
Use separate apps for independently built or independently configured
websites.

```text
request /sites/{alias}/{path}
        |
        +--> OpenResty stable forward
        +--> proc reads its immutable in-memory SiteCatalog
        +--> alias selects app without Redis or descriptor reads
        +--> platform_session site requires a verified browser session
        +--> standard app static lifecycle serves main view/assets

request /
        |
        +--> proc /api/integrations/site-root
        +--> host match
        +--> otherwise one default
        +--> otherwise configured platform chat route

request /{clean-path} without a resolved site
        |
        +--> proc /api/integrations/site-root/{clean-path}
        +--> host match
        +--> otherwise one default
        +--> otherwise controlled 404
```

OpenResty does not contain an app list. Its generated route matrix reserves
`proxy.route_prefix` for the control plane, preserves explicit platform
services, and forwards `/`, clean paths, and `/sites/*` to proc. This allows
descriptor reloads to add, remove, or remap sites without regenerating proxy
configuration.

## Catalog Projection And Hot Routing

`bundles.yaml` remains the only authority. Proc projects the routing and
browser-authentication fields into a versioned catalog:

```text
bundles.yaml application config
        |
        | startup / application update / properties update
        v
validated ApplicationSiteCatalog
        |
        +--> Redis catalog snapshot + monotonic generation
        +--> Redis update channel
                     |
                     v
             each proc worker
             immutable in-memory catalog
                     |
                     v
             request-time host/alias lookup
```

The projection is derived and rebuildable. Redis distributes generations; it
is not the descriptor authority. A proc subscribes before loading the current
snapshot, then rejects delayed generations. Request handlers never read Redis,
parse YAML, or scan application properties.

Invalid aliases, duplicate aliases, duplicate host declarations, and multiple
defaults are rejected while compiling the catalog. The previous valid hot
catalog remains active until a valid replacement is published.

If any application-hosted site is enabled, `proxy.route_prefix` must be a
non-root control-plane mount. `proxy.route_prefix: /` plus an enabled site is
invalid because root paths cannot simultaneously belong to the platform
frontend and to a host/default-selected website. `proxy.route_prefix: /` is
still valid when no application-hosted sites are enabled.

Neutral public fixture mounts used by tests and documentation:

```text
/platform/chat                  control-plane chat route
/platform/assets/index-....js   control-plane frontend JavaScript
/platform/img/favicon.svg       control-plane frontend image
/platform/config.json           optional static frontend fallback

/control/ui/chat                multi-segment control-plane chat route
/control/ui/assets/index-....js control-plane frontend JavaScript
/control/ui/img/favicon.svg     control-plane frontend image
/control/ui/config.json         optional static frontend fallback
```

`/api/cp-frontend-config` remains a root API route reserved for runtime
configuration. It is not a static frontend file and does not move under the
control-plane mount.

## Proc Result Matrix

| Request | Catalog outcome | Result |
| --- | --- | --- |
| `/` | host match | Serve the matched site's `index.html` with base `/`. |
| `/` | no host match, one default | Serve the default site's `index.html` with base `/`. |
| `/` | no resolved site | Redirect to `<proxy.route_prefix>/chat`. |
| `/{clean-path}` | host match or default | Serve file/directory index/SPA fallback for the selected site. |
| `/{clean-path}` | no resolved site | Return controlled `404`, not a platform fallback. |
| `/sites/{alias}` | known alias | Serve that site's `index.html` with base `/sites/{alias}/`. |
| `/sites/{alias}/{path}` | known alias | Serve file/directory index/SPA fallback for that site. |
| any `platform_session` site document | signed-out browser | Redirect to the configured sign-in lane with the complete path and query in `next`. |
| any `platform_session` site non-document request | signed-out caller | Return `401`; site bytes are not served. |
| any `platform_session` site request | verified platform user | Continue normal site serving; app operations still enforce their own authorization. |
| `/sites/{alias}` | unknown alias | Return controlled `404`. |
| any site route | invalid or unavailable catalog | Return `503` while the last valid hot catalog remains active. |

## Multipage And Edge Routing

Direct alias routes support files, directory indexes, and SPA fallback:

```text
/sites/docs/guide/index.html -> UI file guide/index.html
/sites/docs/guide/           -> UI directory guide/index.html
/sites/docs/client-route     -> index.html when no file exists
```

An edge that owns `docs.example.com` forwards clean public paths to OpenResty
while preserving the viewer host. The generated OpenResty route matrix owns
the internal rewrite to the reserved host-selected proc surface:

```text
viewer GET https://docs.example.com/guide/
  -> edge forwards /guide/
     Host: docs.example.com
  -> OpenResty rewrites internally
     /api/integrations/site-root/guide/
  -> proc hot catalog selects the application
  -> standard application UI storage serves the file
  -> edge may cache the response
```

The edge contains no site registry or duplicate path map. Entry HTML and
root-level non-hashed files revalidate; hashed files below `assets/` are
immutable for one year. The app declares the site, platform storage serves it,
OpenResty owns the stable route contract, and the edge only forwards and caches
responses.

The same ownership applies to local tunnels and Caddy. An outer proxy can
forward the complete origin while preserving `Host`, in which case KDCube owns
root selection. If a separate website already owns `/`, the adapter must route
`/sites` and `/sites/*` to KDCube; application sites are then available by
alias, while the separate website remains the root owner.

`site.auth.mode: platform_session` lets the shared platform route initiate
login before serving the shell. The redirect preserves the requested path and
query, and the login lane returns the browser there after authentication. This
is a browser-entry contract, not an authorization grant: every protected
widget, API, and operation continues to enforce its own user and authority
requirements. `site.auth.mode: public` serves the shell to anonymous visitors;
such a shell may read authenticated session truth from `/profile` when it
offers different anonymous and signed-in experiences. Provider-specific login
settings do not belong in site source.

The standard main-view static lifecycle supplies cache policy. Entry HTML and
root-level non-hashed files revalidate with `no-cache`; hashed files under
`assets/` are immutable for one year. A site does not use the public-content
publication registry merely to cache its shell.

The reference implementation is
`sdk/examples/bundles/website@2026-07-12`.

See [Application-Hosted Website Architecture](../../../arch/application-hosted-websites-README.md)
for the ownership/topology map and [Bundle Website Integration](../../bundle/bundle-website-integration-README.md)
for the app package contract.
