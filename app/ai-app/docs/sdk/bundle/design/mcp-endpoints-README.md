# MCP Endpoints for Bundles

## Goal

Allow a bundle to expose an inbound MCP surface through proc routing, the same
way bundles already expose:

- `@api(...)`
- `@ui_widget(...)`
- `@ui_main`
- `@on_message`

The bundle-owned MCP surface must participate in:

- bundle interface manifest discovery
- bundle-level visibility metadata
- proc auth and public-auth enforcement
- explicit tenant/project/bundle routing

## Scope of v1

V1 adds **bundle-served MCP endpoints**.

It does **not** add:

- generic config-only proxying to arbitrary external MCP servers
- stdio serving through proc
- SSE serving through proc
- bundle-local MCP manifest introspection in the admin UI beyond endpoint
  metadata

V1 supports one inbound MCP transport:

- `streamable-http`

That is the cleanest first cut because it fits proc's HTTP routing model and
does not require long-lived stdio processes or a separate SSE session layer.

## Developer Contract

Bundles get a new decorator:

```python
from kdcube_ai_app.infra.plugin.agentic_loader import mcp

@mcp(
    alias="tools",
    route="operations",
    user_types=("registered",),
)
def tools_mcp(self):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("my-bundle")

    @mcp.tool()
    async def ping() -> dict:
        return {"ok": True}

    return mcp
```

The decorated method must return one of:

- a `FastMCP` application object exposing `streamable_http_app()`
- an ASGI app already prepared for MCP HTTP handling

The bundle method itself is **not** the MCP request handler. The method is an
app factory/provider. Proc resolves the bundle, calls the method, obtains the
MCP app, and dispatches the incoming HTTP request into that app.

## Visibility and Access Model

`@mcp(...)` uses the same access model as `@api(...)`:

- `route="operations"`
  - authenticated route
  - enforced by proc auth
- `route="public"`
  - public route
  - must declare `public_auth`

The decorator supports:

- `user_types=(...)`
  - inferred internal user types such as `anonymous`, `registered`, `paid`,
    `privileged`
- `roles=(...)`
  - raw external auth roles such as `kdcube:role:super-admin`

If both are present, both checks must pass.

MCP auth path is intentionally stricter than the normal bundle REST surface:

- MCP routes use header-only JWT auth
- they do not fall back to browser cookies
- they do not accept query-param token injection

This is deliberate because MCP clients are expected to present explicit bearer
credentials rather than reuse the browser/session-oriented auth path used by
the rest of the chat stack.

## Route Shape

Proc serves bundle MCP endpoints under explicit bundle routing:

- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path:path}`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path:path}`

Public MCP routes mirror the public API surface:

- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}`
- `POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path:path}`
- `GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path:path}`

Internally, proc rewrites the routed request path onto the MCP subapp's native
path shape.

For `streamable-http`, the current FastMCP app expects the MCP route at:

- `/mcp`

So a bundle request to:

- `/api/integrations/bundles/acme/demo/my.bundle/mcp/tools`

is dispatched into the subapp as:

- `/mcp`

and:

- `/api/integrations/bundles/acme/demo/my.bundle/mcp/tools/foo`

is dispatched as:

- `/mcp/foo`

## Manifest Model

Bundle interface manifest gets a new spec:

```python
MCPEndpointSpec(
    method_name="tools_mcp",
    alias="tools",
    route="operations",
    transport="streamable-http",
    user_types=("registered",),
    roles=(),
    public_auth=None,
)
```

`BundleInterfaceManifest` now includes:

- `mcp_endpoints`

This makes MCP part of the declarative bundle surface, not an implicit private
convention.

## Proc Dispatch Model

Proc-side flow:

1. Resolve bundle as usual.
2. Resolve `MCPEndpointSpec` by alias and route.
3. Enforce:
   - route auth
   - `public_auth` for public endpoints
   - `user_types`
   - raw `roles`
4. Call the decorated bundle method inside the normal bound request context.
5. Convert the returned value into an ASGI app:
   - prefer `streamable_http_app()` when the object exposes it
   - otherwise accept an ASGI app directly
6. Dispatch the current HTTP request into that ASGI app.

## Why This Is Better Than Generic MCP Proxying

Generic proxying from KDCube to arbitrary external MCP servers is a different
feature.

Problems with starting there:

- it is not bundle-owned behavior
- it is configuration-only, not declarative bundle interface
- it collapses auth, tenancy, and bundle lifecycle concerns into a transport
  tunnel

The first-class bundle endpoint model is cleaner:

- bundle owns the exposed MCP surface
- proc still enforces platform auth/visibility
- bundles may still internally proxy or wrap upstream MCP servers if they want

## Follow-up Work

Possible later extensions:

- SSE transport
- config-driven MCP upstream proxy endpoints
- admin discovery of MCP tool/resource metadata
- endpoint-level caching for MCP app providers
- default-bundle MCP shortcut routes
