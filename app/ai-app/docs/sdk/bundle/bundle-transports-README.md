---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
title: "Bundle Transports"
summary: "Complete transport map for bundle capabilities: chat, Data Bus, background jobs, REST operations, widgets, static UI, communicator streams, public routes, and MCP endpoints."
tags: ["sdk", "bundle", "transport", "protocol", "mcp", "rest", "sse", "socketio", "widgets", "auth", "background-jobs", "data-bus"]
keywords: ["bundle transport map", "chat transport", "data bus transport", "background job transport", "on_job transport", "operations rest transport", "widget transport", "static ui transport", "communicator streaming", "public route transport", "mcp endpoint transport"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-provider-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-consumer-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/dataflow/live-widget-updates-README.md
---
# Bundle Transports

This page explains the implemented transport surfaces for bundles.

It answers:

- how traffic reaches bundle code
- how bundle code sends data back out
- which decorator shape is relevant for each surface
- which layer authenticates the request
- what `operations` vs `public` means for REST and MCP

This page is transport-focused.

For the specific contract that takes authored `external_events[]` from a
browser, webhook, Telegram adapter, or backend submitter into a conversation
ReAct turn, and for reducing ReAct output back to a non-browser channel, use
[Bundle Conversation Events And React Output](bundle-conversation-events-and-react-output-README.md).

Named service providers define semantic provider/client contracts above these
transports. A named service provider can expose the same operation through
local, API, MCP, or Data Bus adapters while keeping one owner-side
implementation. Local/headless calls use the same provider contract without
round-tripping through ingress; API, MCP, and Data Bus adapters only hydrate
the caller context and protocol envelope. See
[Namespace Services: Providers](../namespace-services/providers-README.md).

For agent-level wiring of React tools/skills, bundle-served MCP, and Claude Code
MCP clients, read [Bundle Agent Integration](bundle-agent-integration-README.md).

It intentionally does **not** repeat the full field-by-field decorator reference.
Use [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
for the full decorator contract and manifest model.

## 1. Core Rule

Surface auth ownership is explicit:

- `@api(route="operations")`, widgets, and static UI are
  **KDCube-authenticated** surfaces
- Data Bus publish uses either the existing authenticated Socket.IO connection
  or HTTP `POST /sse/data_bus.publish` with an open SSE peer, then applies
  bundle/subject visibility checks
- `@api(route="public")` is public at the proc routing layer; any webhook,
  provider proof, or callback verification is handler/SDK-owned unless a
  descriptor `surfaces.as_provider.*.auth` boundary is configured
- `@mcp(...)` selects one of three policy owners through
  `surfaces.as_provider.mcp.<alias>.auth`: intentionally public, app-owned
  (`mode: bundle`), or platform-managed delegated credentials (`mode: managed`)

So:

- proc owns route dispatch for all surfaces
- proc owns transport auth for `@api(route="operations")` and browser-facing
  integration routes
- proc does **not** infer public-route authentication from decorator metadata
- the bundle method authenticates bundle-owned public APIs itself
- for `auth.mode: managed`, proc/Connection Hub authenticate and authorize the
  delegated MCP request before app code runs
- for `auth.mode: bundle`, the app MCP provider authenticates the request itself

## 2. Inbound Surface Matrix

| Surface | Decorator / entry | Transport | Routes | Who authenticates | Typical caller |
| --- | --- | --- | --- | --- | --- |
| chat turn | `run()` / `@on_reactive_event` | platform chat ingress + proc | chat endpoints such as `/sse/chat` or Socket.IO | KDCube | platform chat client |
| Data Bus message | `@data_bus_handler(...)` | Socket.IO `data_bus.publish`, HTTP `POST /sse/data_bus.publish`, or `comm.data_bus.publish(...)` + Redis Stream + proc worker | stream key `kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages` | KDCube connection auth + bundle/subject visibility, or current runtime actor for server-side publishers | widget, custom frontend, bundle tool, internal service |
| background job | `@on_job` | Redis Stream + proc | no HTTP route; processor operation `__kdcube_on_job__` | producer/platform context | `@cron`, widget/API run-now, internal service |
| authenticated bundle operation | `@api(route="operations")` | HTTP REST | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}` | KDCube | widget, custom frontend, internal platform UI |
| public bundle operation | `@api(route="public")` | HTTP REST | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}` | KDCube or bundle | webhook, external caller |
| widget fetch | `@ui_widget(...)` | HTTP GET | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}` or `/public/widgets/{alias}` | KDCube for `/widgets`; public/static session for `/public/widgets` | platform widget loader, Telegram Mini App, browser client |
| main bundle UI | `@ui_main` | static HTTP asset serving | `/api/integrations/static/{tenant}/{project}/{bundle_id}/...` | KDCube | platform UI / browser client |
| app MCP | `@mcp(route="operations")` | MCP over `streamable-http` | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}` | app for `mode: bundle`; proc/Connection Hub for `mode: managed`; nobody by default when no policy is declared | MCP client |
| public-route MCP | `@mcp(route="public")` | MCP over `streamable-http` | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}` | app for `mode: bundle`; proc/Connection Hub for `mode: managed`; nobody by default when no policy is declared | MCP client |

Chat clients can also send external events to the currently active conversation
turn over the same chat transport. A `followup` or `steer` is not a separate
bundle transport: it is a normal `/sse/chat` or Socket.IO `chat_message` request
with continuation intent. Ingress stores it in the shared conversation external
event source. A live React owner consumes it on the current turn, or proc later
promotes the stored `task_payload` into one normal ready-queue turn. See
[Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md),
[Client Transport Protocols](../../service/comm/client-transport-protocols-README.md) and
[Chat Stream Events](../solutions/chat/chat-stream-events-README.md).

Background jobs are intentionally not URL-addressable. A producer writes a
ready job to the Redis Stream with tenant/project/bundle/user routing metadata.
Proc claims it fairly, constructs a normal bundle runtime context, and calls the
bundle's async `@on_job` handler. Use [background-jobs-README.md](../../service/streams/background-jobs-README.md)
for the queue/envelope contract.

A bundle still has only one decorated `@on_job` method. If the entrypoint derives
from SDK mixins, call `await super().handle_job(**kwargs)` first and only handle
bundle-owned `work_kind` values when that returns `handled=false`.

Data Bus messages are also durable Redis Stream messages, but they are inbound
bundle-domain messages rather than background jobs. A browser client publishes a
package with `bundle_id` and `messages[]` through Socket.IO `data_bus.publish`
or HTTP `POST /sse/data_bus.publish`; server side bundle code can publish the
same message through
`comm.data_bus.publish(...)` / `publish_and_wait(...)`, including from trusted
isolated tools through `comm_ctx.data_bus_publish*` helpers. Both paths write to
the bundle's Data Bus stream, and processor-owned workers invoke the registered
`@data_bus_handler(...)` methods. This path is separate from `chat_message`,
conversation `external_events[]`, and transient communicator Pub/Sub. See
[Conversation Event Bus And Data Bus](../../service/comm/conversation-event-bus-and-data-bus-README.md)
and [Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md)
for the bus-level routing contract.

## 3. REST Operations

### 3.1 Authenticated operations

Minimal shape:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import api

@api(
    alias="preferences_exec_report",
    route="operations",
    user_types=("registered",),
)
async def preferences_exec_report(self, **kwargs):
    ...
```

Routes:

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

Auth model:

- proc resolves the request session through the normal platform auth stack
- bearer header is the preferred client contract
- browser/session fallbacks may also apply:
  - cookie fallback
  - query-param token injection where integrations middleware supports it

Proc enforces:

- authentication
- route matching
- `user_types`
- raw `roles`

Then proc calls the bundle method.

### 3.2 Public operations

Minimal shape:

```python
@api(
    alias="incoming_webhook",
    route="public",
)
async def incoming_webhook(self, request: Request, **kwargs):
    ...
```

Open public route:

```python
@api(
    alias="public_ping",
    route="public",
)
async def public_ping(self, **kwargs):
    ...
```

Routes:

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}
```

Auth model:

- proc does not require the normal authenticated user-flow here
- if the bundle method accepts `request=`, proc passes the original FastAPI
  request object
- the bundle reads headers/body and decides whether to accept the request
- if the bundle rejects it, it should raise `HTTPException(...)`
- descriptor `surfaces.as_provider.api.public.<alias>.<METHOD>.auth` can add a
  platform-managed authority/grant boundary

Canonical bundle-authenticated public hook:

```python
from fastapi import HTTPException, Request

from kdcube_ai_app.apps.chat.sdk.config import get_secret

@api(
    alias="telegram_webhook",
    route="public",
)
async def telegram_webhook(self, request: Request, **kwargs):
    header_name = self.bundle_prop(
        "telegram.webhook.auth.header_name",
        "X-Telegram-Bot-Api-Secret-Token",
    )
    expected_token = await get_secret("b:telegram.webhook.auth.shared_token")
    provided_token = request.headers.get(header_name)
    if not expected_token or provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"ok": True}
```

Server-side contract:

```yaml
# bundles.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      config:
        telegram:
          webhook:
            auth:
              header_name: "X-Telegram-Bot-Api-Secret-Token"
```

```yaml
# bundles.secrets.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      secrets:
        telegram:
          webhook:
            auth:
              shared_token: "replace-in-real-deployment"
```

Client call shape:

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/telegram_webhook" \
  -H "X-Telegram-Bot-Api-Secret-Token: <shared-token>" \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1}'
```

What the bundle shares with the client:

- the public operations route
- the header name from bundle props
- the token provisioned in bundle secrets

## 4. MCP Endpoints

### 4.1 Minimal shape

```python
from kdcube_ai_app.infra.plugin.bundle_loader import mcp

@mcp(
    alias="tools",
    route="operations",
    transport="streamable-http",
)
async def tools_mcp(self, request=None, **kwargs):
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("my-app", stateless_http=True)

    @app.tool()
    async def ping() -> dict:
        return {"ok": True}

    return app
```

Important:

- the decorated method is an MCP app provider/factory
- it is **not** the raw HTTP handler
- proc calls the provider method, obtains the MCP app, and dispatches the HTTP request into that subapp
- if the provider accepts `request=`, proc passes the original FastAPI request

### 4.2 Route families

Operations-family MCP:

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path}
```

Public MCP:

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path}
```

Current supported inbound transport:

- `streamable-http`

### 4.3 Who authenticates MCP

The effective provider descriptor selects the owner:

```text
no auth mode
  no MCP credential guard; use only for intentionally public work

auth.mode: bundle
  app code authenticates and authorizes the request

auth.mode: managed
  proc/Connection Hub validates the delegated bearer, concrete resource,
  required grants, and selected tool before invoking the app MCP provider
```

For managed requests, proc also projects the delegate actor, approving grantor,
platform/economics subject, grants, selected tools, and identity scope into the
normal request context. Product code still owns record-level authorization.

For non-managed requests, proc forwards the original request headers/body into
the MCP subapp and does not invent an app credential policy.

### 4.4 Header names and token verification

For `auth.mode: bundle`, there is no platform-defined per-app MCP auth header.

That means:

- `AUTH.ID_TOKEN_HEADER_NAME` is not the MCP auth contract
- the bundle decides which headers or cookies it wants to read
- the bundle decides how to verify them

Examples of bundle-defined MCP auth schemes:

- `Authorization: Bearer <token>`
- `X-Api-Key: <key>`
- signed webhook headers
- custom JWT headers
- cookie-based auth if the bundle wants to allow it

The platform does not interpret those app-owned schemes. This is distinct from
`auth.mode: managed`, whose delegated bearer is interpreted by the Connection
Hub guard.

### 4.5 What `route="operations"` and `route="public"` mean for MCP

For MCP, the route value selects the URL family, not the auth verifier.

The route is independent from `auth.mode`. In particular, a
`route="public"` MCP endpoint can use `auth.mode: managed`; the public URL is
reachable for MCP discovery and OAuth challenge, while the managed guard still
rejects unauthorized calls before app code runs.

### 4.6 Canonical app-owned MCP pattern

Use one explicit contract:

- bundle props define the non-secret client contract
- bundle secrets define the verification material
- the bundle MCP provider reads the incoming headers and decides whether to
  return the MCP app

Example bundle config in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      config:
        surfaces:
          as_provider:
            mcp:
              partner_tools:
                auth:
                  mode: "bundle"
                  header_name: "X-Partner-MCP-Token"
                  scheme: "shared-header-secret"
                  contract_version: "2026-04"
```

Matching bundle secret in `bundles.secrets.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      secrets:
        surfaces:
          as_provider:
            mcp:
              partner_tools:
                auth:
                  shared_token: "replace-in-real-deployment"
```

Bundle code:

```python
from fastapi import HTTPException, Request
from mcp.server.fastmcp import FastMCP

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.infra.plugin.bundle_loader import mcp

@mcp(
    alias="partner_tools",
    route="operations",
    transport="streamable-http",
)
async def partner_tools_mcp(self, request: Request, **kwargs):
    header_name = self.bundle_prop(
        "surfaces.as_provider.mcp.partner_tools.auth.header_name",
        "X-Partner-MCP-Token",
    )
    expected_token = await get_secret("b:surfaces.as_provider.mcp.partner_tools.auth.shared_token")
    provided_token = request.headers.get(header_name)

    if not expected_token:
        raise RuntimeError(
            "Bundle secret b:surfaces.as_provider.mcp.partner_tools.auth.shared_token is not configured."
        )
    if provided_token != expected_token:
        raise HTTPException(
            status_code=401,
            detail=f"Missing or invalid {header_name}",
        )

    app = FastMCP("partner-tools", stateless_http=True)

    @app.tool()
    async def ping() -> dict:
        return {"ok": True}

    return app
```

What this contract means:

- the client must send the header named by
  `self.bundle_prop("surfaces.as_provider.mcp.partner_tools.auth.header_name")`
- KDCube does not negotiate or verify that header for MCP
- the bundle can rotate the secret by updating
  `b:surfaces.as_provider.mcp.partner_tools.auth.shared_token`
- the bundle can change the client-facing header name by updating the prop

This pattern is intentionally bundle-owned:

- `mode: bundle` means the path is provider-surface metadata owned by the
  bundle, not a platform-managed delegated grant policy
- the secret path is bundle-scoped because it is read as `b:...`
- the same approach works for other bundle-defined schemes such as:
  - bearer token verification
  - HMAC signatures
  - custom JWT validation

The invariant for `mode: bundle` is that the app, not proc, owns this particular
auth decision. It does not apply to `mode: managed`.

For managed delegated-client access, point the decorator at the descriptor:

```python
@mcp(
    alias="partner_tools",
    route="public",
    transport="streamable-http",
    auth_config="surfaces.as_provider.mcp.partner_tools.auth",
)
async def partner_tools_mcp(self, request: Request, **kwargs):
    return FastMCP("partner-tools", stateless_http=True)
```

```yaml
surfaces:
  as_provider:
    mcp:
      partner_tools:
        auth:
          mode: managed
          authority_id: delegated_client
          selected_tool_grants: true
```

Connection Hub then owns the matching resource/tool/grant catalog. See
[Expose An MCP Service From A KDCube App](../../recipes/kdcube_for_agents/expose-mcp-service-README.md)
and [Protect Bundle MCP With Managed Credentials](../../recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md).

### 4.7 What `@mcp(...)` does not support

`@mcp(...)` does not support proc-side:

- `user_types`
- `roles`

MCP endpoint auth policy is declared through MCP `auth` / descriptor
`surfaces.as_provider.mcp.<alias>.auth`. Use `mode: managed` for the
platform-managed authority/grant boundary; use `mode: bundle` for an app-owned
scheme. Product code may apply additional record-level checks after either
boundary succeeds.

## 5. Widgets and Static UI

### 5.1 `@ui_widget(...)`

Minimal shape:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import ui_widget

@ui_widget(
    alias="preferences",
    icon={"tailwind": "heroicons-outline:adjustments-horizontal"},
    user_types=("registered",),
)
def preferences_widget(self, **kwargs):
    return ["<html>...</html>"]
```

Routes:

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{alias}
```

Auth:

- `/widgets/...` uses normal integrations/browser auth
- `/public/widgets/...` serves the built static shell with a public session
- data/action APIs called by a public widget use their own public API auth,
  such as Telegram `initData` verification or a federated Data Bus session
  token issued after provider proof is verified

### 5.2 `@ui_main`

`@ui_main` declares the bundle main UI static entry.

Route family:

```text
/api/integrations/static/{tenant}/{project}/{bundle_id}/...
```

Important rule:

- widget UI and main UI code should call back into the real integrations routes
- do not invent a separate widget-private transport

## 6. Chat Turn Path

This is the normal assistant workflow path.

Properties:

- the bundle does not declare a raw route for chat turns
- ingress and proc own the transport
- bundle code receives runtime context and can emit communicator events

This path is separate from `@api(...)` and `@mcp(...)`.

## 7. Outbound Surfaces

| Surface | Owned by | Transport | Target |
| --- | --- | --- | --- |
| communicator output | bundle runtime | SSE / Socket.IO through proc | active browser peer, session listeners, or opt-in tenant/project SSE listeners |
| widget/browser callback | widget or hosted UI code | HTTP REST to `/api/integrations/*` | proc bundle operations |
| static asset delivery | platform static handler | HTTP | platform UI / browser client |

### 7.1 Communicator output

The main bundle-to-client outbound path is the communicator.

It delivers:

- deltas
- steps
- typed events
- followups
- citations

Transports to the client:

- SSE
- Socket.IO

This path can be used by non-chat bundle operations too. A browser can open the
normal `/sse/stream` or Socket.IO connection, call a bundle REST operation, and
pass the connected peer id through the configured stream-id header so
`comm.service_event(...)` can reply to that exact peer or broadcast to the
current session. See the concrete recipe in
[Client Transport Protocols](../../service/comm/client-transport-protocols-README.md#non-chat-app-events-over-the-shared-stream).

For tenant/project-level widget refreshes, SSE clients opt in with
`project_events=true`, and bundle code emits `comm.project_event(...)`. Keep
those payloads compact and already safe for all viewers in the tenant/project.
Transport rule: tenant/project broadcasts are delivered over **SSE only** —
the Socket.IO gateway joins per-session relay channels and never carries
them. A scene host that relays events to embedded widgets therefore keeps an
SSE leg with `project_events=true` beside its authenticated socket. The
protocol details are in
[Client Transport Protocols](../../service/comm/client-transport-protocols-README.md#tenantproject-sse-broadcast).

A bundle can also push an out-of-band change to the open widgets of **one
specific user**: register the widget's authenticated session against the
subject it displays (a Redis live-session registry), then emit a
session-routed relay envelope per registered session. Connection Hub's
delegated-access delivery is the shipped implementation.

End-to-end walkthrough of both push shapes — emit, fleet-safe debounce/dedup
state, standalone and scene-hosted receive, and the trace path when an update
goes missing: [Live Widget Updates](../../recipes/dataflow/live-widget-updates-README.md).

### 7.2 Who owns outbound auth

For communicator delivery:

- proc/comm infrastructure owns routing to the already-authenticated session or peer

For widget/browser callback calls:

- the frontend sends the auth material required by the chosen inbound route
- proc authenticates that call again

## 8. What Is Not Available Through Proc

These are not current bundle-facing proc transports:

- bundle-owned raw WebSocket endpoints
- bundle-owned raw SSE endpoints
- MCP `stdio` serving through proc
- MCP SSE serving through proc
- arbitrary config-only MCP proxy endpoints exposed as first-class bundle routes

If a bundle needs external integrations, use normal bundle code, tools, or MCP
clients inside the bundle/tool runtime. That is separate from the proc-served
bundle interface surface described here.

## 9. Practical Choice Rule

Choose by caller and auth ownership:

- platform chat client talking to the assistant → chat turn path
- widget or service sending a durable bundle-owned domain mutation → Socket.IO
  `data_bus.publish` plus `@data_bus_handler(...)`
- widget/browser/frontend calling bundle logic through KDCube auth → `@api(route="operations")`
- webhook or public HTTP caller using KDCube public-auth contract → `@api(route="public")`
- programmatic MCP client with app-defined auth → `@mcp(...)` + `auth.mode: bundle`
- external MCP client with user consent and managed grants → `@mcp(route="public")` + `auth.mode: managed`
- intentionally public MCP caller → `@mcp(route="public")` with no managed/app-owned auth mode
- iframe/widget UI surface → `@ui_widget(...)` / `@ui_main`
- bundle streaming live updates back to connected clients → communicator over SSE / Socket.IO

## 10. Reader Map

- full decorator contract and manifest fields:
  [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- widget/static/browser-facing integration:
  [bundle-interfaces-README.md](bundle-interfaces-README.md)
- browser/UI request contract:
  [client-transport-protocols-README.md](../../service/comm/client-transport-protocols-README.md)
- streaming payload catalog:
  [chat-stream-events-README.md](../solutions/chat/chat-stream-events-README.md)
- durable Data Bus messages:
  [../../service/comm/data-bus-README.md](../../service/comm/data-bus-README.md)
- pushing live updates to open widgets:
  [../../recipes/dataflow/live-widget-updates-README.md](../../recipes/dataflow/live-widget-updates-README.md)
- routing and partitioning:
  [../../service/comm/bus-routing-and-partitioning-README.md](../../service/comm/bus-routing-and-partitioning-README.md)
- runtime objects available to bundle code:
  [bundle-runtime-README.md](bundle-runtime-README.md)
