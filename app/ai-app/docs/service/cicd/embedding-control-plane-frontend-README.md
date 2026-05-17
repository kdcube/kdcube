---
id: ks:docs/service/cicd/embedding-control-plane-frontend-README.md
title: "Embedding The Control Plane Frontend"
summary: "Deployment pattern for embedding the KDCube control-plane/chat frontend inside another web application while preserving nested bundle widgets and secure frame policies."
tags: ["service", "cicd", "frontend", "embedding", "iframe", "proxy", "security"]
keywords: ["control plane iframe embedding", "frame ancestors", "x-frame-options", "bundle widget iframe", "nested iframe deployment", "embedded chatbot frontend"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/service/cicd/ngrok-README.md
---
# Embedding The Control Plane Frontend

This page describes how to deploy a KDCube control-plane frontend so it can be
embedded inside another web application as an iframe.

The rule is deployment-level, not frontend-code-only. The browser decides
whether a page may be framed from response headers emitted by the web/proxy
layer. The same KDCube frontend may work as a top-level page and fail inside an
iframe if these headers are not configured for embedding.

## Problem Shape

The default security posture is to prevent the KDCube web app from being
framed:

```text
X-Frame-Options: DENY
```

That is correct for a standalone control plane, but it blocks this deployment
shape:

```text
+--------------------------------------------------------------+
| Host application                                             |
|                                                              |
|  <iframe src="https://kdcube.example.com/platform/chat">     |
|  +--------------------------------------------------------+  |
|  | KDCube control-plane / chatbot frontend                |  |
|  |                                                        |  |
|  |  bundle widget iframe / canvas iframe / artifacts      |  |
|  |  +--------------------------------------------------+  |  |
|  |  | Bundle UI or generated document                  |  |  |
|  |  +--------------------------------------------------+  |  |
|  +--------------------------------------------------------+  |
+--------------------------------------------------------------+
```

There are two different frame decisions:

- Can the host application frame the KDCube frontend?
- Can the embedded KDCube frontend frame its own bundle widgets, static
  integration UI, canvas documents, or generated artifacts?

Both must be allowed.

## Header Model

Use `Content-Security-Policy: frame-ancestors ...` for embeddable deployments.
Do not use `X-Frame-Options` for cross-origin allowlists.

`X-Frame-Options` only supports coarse policies:

| Header | Effect | Suitable for |
|---|---|---|
| `DENY` | Cannot be framed anywhere | standalone control plane |
| `SAMEORIGIN` | Can be framed only by the same origin | same-origin shell embeds bundle widgets |

For an embedding host application, use CSP:

```nginx
more_clear_headers "X-Frame-Options";
more_set_headers "Content-Security-Policy: frame-ancestors 'self' https://host-app.example.com";
```

`frame-ancestors` is checked for every ancestor in the iframe chain. In a nested
KDCube page, the top host app and the intermediate KDCube page must both match
the policy for each framed document.

## Descriptor-Driven Deployment Contract

All embedding levers must come from `assembly.yaml`. The same descriptor values
must drive both supported deployment families:

- CLI / single-node deployment: `kdcube init` stages descriptors and renders the
  OpenResty/nginx config and frontend runtime config used by `kdcube start`.
- ECS / Terraform deployment: descriptor input is used to prepare the nginx
  files, task/runtime config, frontend runtime config, and any Terraform-facing
  values needed by the deployment.

Do not solve embedding by manually editing a generated nginx file or by
hardcoding a host application URL in a proxy template. Generated files can
differ between local CLI, EC2 single-node, and ECS deployment; `assembly.yaml`
is the shared source of truth.

The deployment renderer must consume the frame embedding section and prepare:

- OpenResty/nginx headers for the control-plane frontend document routes
- OpenResty/nginx headers for every KDCube document that can be displayed
  inside that frontend, including bundle widgets, static integration UI, canvas
  HTML, and generated HTML previews
- frontend runtime config, when the frontend needs to know it is embedded
- auth/cookie settings, when iframe auth requires a different cookie mode
- CORS origins, only for browser fetches that cross origin

```text
assembly.yaml
  proxy.frame_embedding
  auth / cookies / cors
        |
        +--> CLI init / single-node renderer
        |      -> <workdir>/config/nginx_proxy*.conf
        |      -> <workdir>/config/frontend*.json
        |
        +--> ECS / Terraform renderer
               -> ecs/nginx/nginx_proxy*.conf
               -> task/runtime env and frontend config
```

The OpenResty/nginx headers are the enforcement point. Frontend runtime config
is only advisory for UI behavior, for example hiding standalone navigation or
choosing a compact embedded layout.

Do not rely on frontend config for security. A browser only enforces the
response headers emitted by the proxy.

## Deployment Modes

### 1. Standalone Control Plane

The KDCube frontend is opened directly by users.

```text
Browser
  -> https://kdcube.example.com/platform/chat
```

Recommended frame policy:

```text
X-Frame-Options: DENY
```

Bundle widget routes may still use `SAMEORIGIN` if the standalone KDCube
frontend embeds bundle widgets from the same origin.

### 2. Embedded On The Same Origin

The host application and the full KDCube app are served under the same browser
origin, for example through one reverse proxy.

```text
https://app.example.com/
  /app/*       -> host application
  /platform/*  -> KDCube frontend
  /api/*       -> KDCube ingress/proc
```

Recommended frame policy:

```text
X-Frame-Options: SAMEORIGIN
```

This is the simplest deployment when the host application can route KDCube
under its own origin. Cookies and auth redirects are also easier because the
browser treats the application as same-site.

### 3. Embedded From Another Origin

The host application and the full KDCube app are served from different browser
origins. This includes both cases:

- same parent domain, different subdomain
- completely different domain

```text
https://app.example.com/
  iframe -> https://ai.example.com/platform/chat
              iframe -> https://ai.example.com/api/integrations/...
```

```text
https://host-app.example.net/
  iframe -> https://kdcube.example.com/platform/chat
              iframe -> https://kdcube.example.com/api/integrations/...
```

Recommended frame policy:

```text
Content-Security-Policy: frame-ancestors 'self' https://host-app.example.net
```

Do not keep `X-Frame-Options: DENY` on frameable routes. It can still block the
page even when CSP is present.

Different subdomains under the same parent domain can be same-site for some
cookie decisions, but they are still cross-origin for frame policy.
`X-Frame-Options: SAMEORIGIN` is not enough unless the iframe parent and KDCube
are exactly the same origin.

Completely different domains need the strictest auth testing. Browser
third-party cookie policy, identity-provider frame restrictions, and
popup/callback behavior can all affect the embedded app.

## API Origin In Embedded Frontends

Opening the KDCube frontend inside another web page does not make relative API
URLs resolve against the outer page. The browser resolves relative URLs against
the document that runs the JavaScript.

Cross-origin iframe deployment:

```text
https://host-app.example.net
  iframe src="https://kdcube.example.com/platform/chat"
```

Inside the KDCube frontend iframe:

```text
window.location.origin == "https://kdcube.example.com"
fetch("/api/...")     -> "https://kdcube.example.com/api/..."
```

Nested bundle widgets follow the same rule. KDCube widgets should:

- first fetch `GET /api/cp-frontend-config` from their own KDCube frame origin;
  if the endpoint returns usable config, the widget should not wait for parent
  messaging; or
- fall back to the KDCube frontend runtime config handshake, where the parent
  answers `CONFIG_REQUEST` with `baseUrl` and auth metadata based on the KDCube
  frontend frame origin; or
- fall back to their own `window.location.origin`, which is also the KDCube
  origin when loaded from KDCube widget routes.

Therefore widgets should not call `https://host-app.example.net/api/...` in a
normal cross-origin iframe deployment.

They can call the host app domain only if the deployment intentionally uses a
same-origin reverse proxy:

```text
https://app.example.com/app/*       -> host application
https://app.example.com/platform/*  -> KDCube frontend
https://app.example.com/api/*       -> KDCube API
https://app.example.com/api/integrations/* -> bundle widgets/static integration UI
```

In that topology, `/api/...` resolves to `https://app.example.com/api/...` by
design, and the proxy must route those paths to KDCube. Proxying only
`/platform` is not enough.

## Frame Policy Scope

When embedded mode is enabled, the whole KDCube control-plane experience must
work. Operators should not need to know which internal frame contains a bundle
widget, static integration UI, generated HTML preview, or canvas document.

The renderer must apply the selected frame policy to every KDCube response that
can produce a browser document inside the control-plane experience.

| Route surface | Why it matters |
|---|---|
| frontend shell, such as `/platform/*` | the host application frames this page |
| frontend static `index.html` fallback | SPA routes usually return the same document |
| `/api/integrations/bundles/.../widgets/...` | bundle widgets are iframed by the KDCube frontend |
| `/api/integrations/static/...` | static integration UI can be iframed by the KDCube frontend |
| generated HTML/canvas document routes | generated previews may be rendered in an iframe |

Do not apply the relaxed frame policy blindly to unrelated APIs. JSON APIs,
SSE, upload endpoints, and internal service endpoints do not need to be
frameable.

Frame policy is separate from iframe sizing. When KDCube is framed from another
origin, the embedding page cannot read the KDCube iframe DOM to call
`scrollHeight` or `scrollWidth`. KDCube frameable HTML entrypoints post a
cooperative resize event instead:

```js
window.parent.postMessage({
  type: 'kdcube-resize',
  height: document.documentElement.scrollHeight,
  width: document.documentElement.scrollWidth,
}, '*');
```

The host application should listen for `kdcube-resize` and update the iframe
box. Nested KDCube frames use the same message shape so resize information can
be forwarded through each iframe layer.

When `bundles_preload_on_start` is enabled, bundle UI builds should happen
during processor startup. Runtime iframe requests should normally find the
fresh current build already present. If an entrypoint request still triggers
`npm install`, treat that as a preload/build readiness problem, not as a reason
to serve stale UI.

In multi-proc deployments, every proc should still run local bundle preload.
Shared storage build signatures and locks are responsible for making the UI
build itself run once. A cluster-level preload lock must not allow non-leader
procs to skip preload and then accept iframe traffic cold.

## Proposed Assembly Descriptor Surface

Embedding must be descriptor-driven. The proxy template should not contain a
hardcoded external application URL.

Recommended `assembly.yaml` shape to add:

```yaml
proxy:
  frame_embedding:
    # standalone | same_origin | allowlist
    mode: standalone

    # Used only when mode: allowlist.
    # These are public browser origins, not URL paths.
    allowed_origins: []
```

Defaults:

- `mode: standalone`
- `allowed_origins: []`

For same-origin embedding:

```yaml
proxy:
  frame_embedding:
    mode: same_origin
    allowed_origins: []
```

For embedding from another origin, whether it is another subdomain or another
domain entirely:

```yaml
proxy:
  frame_embedding:
    mode: allowlist
    allowed_origins:
      - https://host-app.example.net
```

For KDCube-owned website embedding where the parent may be the apex domain or a
subdomain, configure both the apex and the wildcard subdomain source:

```yaml
proxy:
  frame_embedding:
    mode: allowlist
    allowed_origins:
      - https://kdcube.tech
      - https://*.kdcube.tech
```

The wildcard subdomain source does not replace the apex entry. Keep both when
`https://kdcube.tech` and `https://www.kdcube.tech` or another subdomain should
be allowed.

The proxy renderer should translate the descriptor into headers.

For `standalone`:

```nginx
more_set_headers "X-Frame-Options: DENY";
```

For `same_origin`:

```nginx
more_clear_headers "X-Frame-Options";
more_set_headers "X-Frame-Options: SAMEORIGIN";
```

For `allowlist`:

```nginx
more_clear_headers "X-Frame-Options";
more_set_headers "Content-Security-Policy: frame-ancestors 'self' https://host-app.example.com";
```

The current proxy templates do not emit another CSP header on these routes. If
another CSP is added later, `frame-ancestors` must be merged into one CSP header
instead of emitting conflicting CSP headers.

Renderer requirements:

- CLI init must render the selected policy into the staged OpenResty config
  used by `kdcube start`.
- ECS/Terraform deployment must render the same selected policy into the nginx
  config shipped with the ECS service.
- The public proxy must clear inherited `X-Frame-Options` before applying the
  selected policy, because upstream UI/static containers may keep conservative
  standalone defaults.
- Descriptor examples and release procedures must not require manual nginx
  edits after generation.
- The renderer must apply one frame policy consistently to every frameable
  KDCube document route that participates in the control-plane experience.

The renderer must treat `allowed_origins` as origins, not URL paths. These
are valid:

```yaml
allowed_origins:
  - https://app.example.com
  - https://host-app.example.net
  - https://*.kdcube.tech
```

These are not valid frame ancestors in KDCube descriptors:

```yaml
allowed_origins:
  - https://app.example.com/some/path
  - "*.example.com"
```

Wildcard subdomains must include a scheme and should be scoped to owned domains
only. Do not use a wildcard as a substitute for a deliberate embedding
allowlist.

## Auth And Cookies

Embedding the frontend changes the browser security context.

Same-origin embedding is preferred when possible:

```text
https://host.example.com/app
https://host.example.com/platform/chat
```

Embedding from another origin may require all of the following:

- auth mode that does not force an identity-provider login page inside the iframe
- cookies set with `SameSite=None; Secure` when they must be sent in a
  third-party iframe context
- explicit CORS origins for API calls made by the embedded frontend
- logout and token-refresh behavior tested inside the iframe

Same-site subdomain embedding can often use the normal secure cookie posture,
but it still must be tested with the deployed auth provider. Cross-site
embedding should assume third-party-cookie restrictions unless the auth flow is
designed to avoid them.

CORS does not permit iframe embedding. CORS controls fetch/XHR. Frame embedding
is controlled by `X-Frame-Options` and CSP `frame-ancestors`.

## Nested Iframe Checklist

Use this checklist before enabling an embedded frontend deployment:

- the host page can load the KDCube frontend iframe
- the KDCube frontend can open its own bundle widget iframe
- the KDCube frontend can open generated HTML/canvas artifacts if those are
  part of the user workflow
- the browser console has no `X-Frame-Options` or `frame-ancestors` violations
- auth works after browser reload inside the iframe
- logout works and does not leave the iframe in a half-authenticated state
- file downloads generated by the embedded app are allowed by iframe sandbox
  settings, if the host application uses a sandboxed iframe

## Host Iframe Requirements

If the host application uses an iframe `sandbox` attribute, it must include the
capabilities KDCube needs. A restrictive sandbox can break the app even when
headers are correct.

Typical starting point:

```html
<iframe
  src="https://kdcube.example.com/platform/chat"
  sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups"
></iframe>
```

The exact sandbox policy is owned by the embedding application, but it must be
tested with:

- chat streaming
- uploads
- downloads
- OAuth or delegated auth callback flow, if used
- bundle widgets
- generated HTML/canvas previews

## Operational Test

For a deployed environment, test headers with:

```bash
curl -I https://kdcube.example.com/platform/chat
curl -I https://kdcube.example.com/api/integrations/bundles/<tenant>/<project>/<bundle-id>/widgets/<widget>/index.html
```

Expected for standalone:

```text
X-Frame-Options: DENY
```

Expected for same-origin embedding:

```text
X-Frame-Options: SAMEORIGIN
```

Expected for embedding from another origin:

```text
Content-Security-Policy: frame-ancestors 'self' https://host-app.example.com
```

and no `X-Frame-Options: DENY` on frameable document routes.
