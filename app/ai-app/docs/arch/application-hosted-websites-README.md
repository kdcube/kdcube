---
id: repo:kdcube/app/ai-app/docs/arch/application-hosted-websites-README.md
title: "Application-Hosted Website Architecture"
summary: "Architecture and ownership map for serving app-owned complete websites through one KDCube installation by stable alias, request host, or one default root."
status: current
tags: ["arch", "website", "application", "routing", "sites", "browser"]
updated_at: 2026-09-14
keywords: ["KDCube website architecture", "application hosted website", "multiple websites", "virtual host routing", "site alias", "site catalog"]
see_also:
  - repo:kdcube/app/ai-app/docs/sdk/solutions/sites/application-sites-README.md
  - repo:kdcube/app/ai-app/docs/sdk/bundle/bundle-website-integration-README.md
  - repo:kdcube/app/ai-app/docs/recipes/components/website-README.md
  - repo:kdcube/app/ai-app/docs/arch/control-plane-web-app-README.md
  - repo:kdcube/app/ai-app/docs/service/cicd/ngrok-README.md
---

# Application-Hosted Website Architecture

KDCube can serve a complete website from an app's built main view. The same app
may also provide agents, APIs, MCP, named services, jobs, widgets, and event
handlers; the website is its browser-facing composition rather than a separate
deployment unit.

## Cardinality

The current platform-managed contract is:

```text
one app
  +-- zero or one effective ui.main_view configuration
  +-- optional zero or one @ui_main surface declaration
  `-- zero or one ui.main_view.site registration

one KDCube deployment
  +-- app A -> zero or one site
  +-- app B -> zero or one site
  `-- app N -> zero or one site
```

The site catalog reads the singular effective `ui.main_view.site` declaration.
An app may define that main view entirely through configuration, as the
reference website app does. When it additionally declares a main-UI code
surface with `@ui_main`, the loader permits at most one such method. Therefore
one app can register at most one KDCube application site today.

`site.hosts` may contain several hostnames, but they all select that same site,
same built file tree, and same app identity. Use separate apps when the
websites need independent aliases, file trees, configuration, lifecycle, or
ownership. One site's own client router may still serve many pages and product
areas inside that website.

## Ownership

| Layer | Owns |
| --- | --- |
| App | HTML, CSS, JavaScript, assets, client routing, composition, app APIs, site alias/host/default declaration. |
| KDCube runtime | Main-view build and storage, catalog validation/distribution, alias and host selection, static serving, cache headers, browser/auth metadata. |
| Outer ingress | DNS, public hostname, TLS, tunnel or load-balancer endpoint, forwarding the request to KDCube while preserving `Host`. |
| Browser | Sends the selected URL and `Host`, loads the site, then uses `/api/cp-frontend-config` and `/profile` for platform and session state. |

The `hosts` declaration is a virtual-host selector after a request reaches
KDCube. It does not create DNS, reserve an ngrok domain, issue a certificate,
or configure a cloud edge.

## Addressing

Every enabled site has an explicit alias route:

```text
/sites/{alias}/
/sites/{alias}/{path}
```

Root and clean paths use host/default selection:

```text
request Host
    |
    v
exact or wildcard site.hosts match?
    | yes -> selected site owns / and clean paths
    | no
    v
one site has default: true?
    | yes -> default site owns / and clean paths
    | no  -> / redirects to the control plane; clean paths return 404
```

Alias selection is explicit and does not depend on `Host`. Host selection lets
several public or local hostnames point at one KDCube origin while each receives
its own clean root.

## One Origin, Several Possible Topologies

### Direct KDCube origin

```text
browser or tunnel
  -> KDCube web proxy
      /platform/*       -> control plane
      /api/*            -> platform and app APIs
      /sites/*          -> explicit application-site aliases
      / and clean paths -> host-selected or default application site
```

This is the normal CLI-started runtime. A tunnel can point directly at the web
proxy port.

### Complete-origin Caddy adapter

```text
several local names or local TLS
  -> Caddy preserves Host and scheme
  -> complete KDCube origin
  -> KDCube selects the site
```

Caddy transports the complete origin. It does not reproduce the KDCube route
matrix and does not select an app.

### Separately hosted root website plus KDCube

```text
one public hostname
  -> Caddy
      /                         -> separate website files
      /platform/*              -> KDCube control plane
      /api/* and service paths -> KDCube runtime
      /sites/*                 -> KDCube application-site aliases
```

In this composed-origin topology, the separate website owns `/`. A KDCube
host-selected or default site cannot also own that root on the same hostname;
KDCube sites remain available through `/sites/{alias}/`. Another hostname can
be routed wholly to KDCube when a clean-root KDCube site is also required.
Caddy selects the upstream only; KDCube resolves platform sessions and each
site's descriptor-owned authentication policy. The signed-out result matrix is
owned by [Serving Local KDCube With Ngrok](../service/cicd/ngrok-README.md#browser-entry-and-login-ownership).

### Several clean public roots

```text
site-a.example.com --+
site-b.example.com ---+--> one edge/tunnel --> KDCube web proxy
site-c.example.com --+          preserve Host

KDCube catalog
  app A hosts: [site-a.example.com]
  app B hosts: [site-b.example.com]
  app C hosts: [site-c.example.com]
```

The domain or tunnel provider must make each hostname reach the installation.
KDCube then maps the preserved host to the app site.

## Catalog And Request Path

```text
effective bundles.yaml app entries
  -> validate one optional site per app
  -> reject duplicate aliases, overlapping hosts, and multiple defaults
  -> publish catalog generation through Redis
  -> each proc keeps an immutable in-process snapshot
  -> request-time alias/host lookup reads that snapshot
  -> normal main-view static serving returns the file or SPA fallback
```

The descriptor is authority. Redis distributes the derived catalog; website
requests do not read Redis or parse descriptors. OpenResty owns only stable
route families and does not contain an application list.

## Browser And Trust Boundary

The website shell may be public while its data and operations remain protected.
It reads provider-neutral browser configuration from
`/api/cp-frontend-config` and treats `/profile` as authenticated-session truth.
Every API, MCP, event, file, and widget request still crosses its own runtime
authentication and authorization policy.

Application sites share the app's normal trusted backend and UI lifecycle.
They do not change the generated-code isolation boundary and do not isolate
operator-approved app backends from one another.

## Distinct Browser Capabilities

```text
widget
  embeddable focused surface; many may belong to one app

main view
  one primary built browser tree for an app

application-hosted site
  that main view registered as a standalone site

control plane
  platform shell that selects and presents apps

@public_content
  indexed records/pages with catalog and sitemap lifecycle
```

For the exact catalog implementation read
[Application-Hosted Sites](../sdk/solutions/sites/application-sites-README.md).
For the app package contract read
[Bundle Website Integration](../sdk/bundle/bundle-website-integration-README.md).
For configuration, commands, Caddy, ngrok, and acceptance tests follow the
[Application-Hosted Website recipe](../recipes/components/website-README.md).
