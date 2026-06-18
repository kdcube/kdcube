---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
title: "Bundle Transports"
summary: "Complete transport map for bundle capabilities: chat, Data Bus, background jobs, REST operations, widgets, static UI, communicator streams, public routes, and MCP endpoints."
tags: ["sdk", "bundle", "transport", "protocol", "mcp", "rest", "sse", "socketio", "widgets", "auth", "background-jobs", "data-bus"]
keywords: ["bundle transport map", "chat transport", "data bus transport", "background job transport", "on_job transport", "operations rest transport", "widget transport", "static ui transport", "communicator streaming", "public route transport", "mcp endpoint transport"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
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

There are two different auth ownership models:

- `@api(route="operations")`, widgets, and static UI are
  **KDCube-authenticated** surfaces
- Data Bus publish uses either the existing authenticated Socket.IO connection
  or HTTP `POST /sse/data_bus.publish` with an open SSE peer, then applies
  bundle/subject visibility checks
- `@api(route="public")` can be either:
  - **KDCube-authenticated** via built-in `public_auth`
  - **bundle-authenticated** via `public_auth="bundle"`
- `@mcp(...)` is a **bundle-authenticated** surface, if the bundle wants auth at all

So:

- proc owns route dispatch for all surfaces
- proc owns transport auth for `@api(route="operations")` and browser-facing
  integration routes
- proc owns built-in `public_auth="none"` / `public_auth={"mode":"header_secret", ...}`
  for `@api(route="public")`
- proc does **not** own transport auth for `@api(route="public",
  public_auth="bundle")`
- proc does **not** own transport auth for `@mcp(...)`
- the bundle method authenticates bundle-owned public APIs itself
- the bundle MCP app authenticates MCP requests itself

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
| bundle-authenticated MCP | `@mcp(route="operations")` | MCP over `streamable-http` | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}` | bundle MCP app | MCP client |
| public MCP | `@mcp(route="public")` | MCP over `streamable-http` | `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}` | nobody by default | MCP client |

Chat clients can also send external events to the currently active conversation
turn over the same chat transport. A `followup` or `steer` is not a separate
bundle transport: it is a normal `/sse/chat` or Socket.IO `chat_message` request
with continuation intent. Ingress stores it in the shared conversation external
event source. A live React owner consumes it on the current turn, or proc later
promotes the stored `task_payload` into one normal ready-queue turn. See
[Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md),
[Bundle Client Communication](bundle-client-communication-README.md) and
[Bundle Chat Stream Events](bundle-chat-stream-events-README.md).

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
    public_auth={
        "mode": "header_secret",
        "header": "X-Webhook-Secret",
        "secret_key": "incoming_webhook",
    },
)
async def incoming_webhook(self, **kwargs):
    ...
```

Open public route:

```python
@api(
    alias="public_ping",
    route="public",
    public_auth="none",
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
- proc enforces the declared `public_auth` unless the endpoint uses
  `public_auth="bundle"`

Current built-in public auth modes:

- `public_auth="none"`
- `public_auth={"mode":"header_secret", ...}`
- `public_auth="bundle"`

Behavior by mode:

- `public_auth="none"`
  - open public route
- `public_auth={"mode":"header_secret", ...}`
  - proc verifies the configured header secret before invoking the bundle
- `public_auth="bundle"`
  - proc forwards the request into the bundle method
  - if the bundle method accepts `request=`, proc passes the original FastAPI
    request object
  - the bundle reads headers/body and decides whether to accept the request
  - if the bundle rejects it, it should raise `HTTPException(...)`

Canonical bundle-authenticated public hook:

```python
from fastapi import HTTPException, Request

from kdcube_ai_app.apps.chat.sdk.config import get_secret

@api(
    alias="telegram_webhook",
    route="public",
    public_auth="bundle",
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
def tools_mcp(self, request=None, **kwargs):
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("my-bundle")

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

Authenticated/bundle-gated MCP:

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

The bundle authenticates MCP, not KDCube.

Current behavior:

- proc does not authenticate MCP requests before dispatch
- proc does not validate bearer tokens or ID tokens for MCP
- proc does not enforce `public_auth` on MCP
- proc forwards the original request headers and body into the MCP subapp

This is the key difference from `@api(...)`.

### 4.4 Header names and token verification

There is currently **no platform-defined per-bundle MCP auth header**.

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

The platform does not interpret those for MCP.

### 4.5 What `route="operations"` and `route="public"` mean for MCP

For MCP, the route value selects the URL family, not the auth verifier.

Use:

- `route="operations"`
  - when the bundle intends to authenticate or otherwise gate the caller itself
- `route="public"`
  - when the endpoint is intentionally public

If a bundle still wants auth on a `public/mcp` route, it must implement that
inside the bundle MCP app. Proc will not do it.

### 4.6 Canonical bundle-authenticated MCP pattern

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
        mcp:
          inbound:
            auth:
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
        mcp:
          inbound:
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
        "mcp.inbound.auth.header_name",
        "X-Partner-MCP-Token",
    )
    expected_token = await get_secret("b:mcp.inbound.auth.shared_token")
    provided_token = request.headers.get(header_name)

    if not expected_token:
        raise RuntimeError(
            "Bundle secret b:mcp.inbound.auth.shared_token is not configured."
        )
    if provided_token != expected_token:
        raise HTTPException(
            status_code=401,
            detail=f"Missing or invalid {header_name}",
        )

    app = FastMCP("partner-tools")

    @app.tool()
    async def ping() -> dict:
        return {"ok": True}

    return app
```

What this contract means:

- the client must send the header named by
  `self.bundle_prop("mcp.inbound.auth.header_name")`
- KDCube does not negotiate or verify that header for MCP
- the bundle can rotate the secret by updating
  `b:mcp.inbound.auth.shared_token`
- the bundle can change the client-facing header name by updating the prop

This pattern is intentionally bundle-owned:

- the prop path is a bundle convention, not a platform-reserved key
- the secret path is bundle-scoped because it is read as `b:...`
- the same approach works for other bundle-defined schemes such as:
  - bearer token verification
  - HMAC signatures
  - custom JWT validation

The only invariant is that the bundle, not proc, owns the MCP auth decision.

### 4.7 What `@mcp(...)` does not support

`@mcp(...)` does not support proc-side:

- `user_types`
- `roles`
- `public_auth`

Those concepts belong to proc-authenticated bundle HTTP operations, not to
bundle-authenticated MCP.

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
  such as Telegram `initData` verification or a bundle-issued federated token

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
[Bundle Client Communication](bundle-client-communication-README.md#non-chat-bundle-events-over-the-shared-stream).

For tenant/project-level widget refreshes, SSE clients opt in with
`project_events=true`, and bundle code emits `comm.project_event(...)`. Keep
those payloads compact and already safe for all viewers in the tenant/project.
The detailed recipe is in
[Bundle Client Communication](bundle-client-communication-README.md#tenantproject-sse-broadcast).

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
- programmatic MCP client with bundle-defined auth → `@mcp(route="operations")`
- public MCP caller → `@mcp(route="public")`
- iframe/widget UI surface → `@ui_widget(...)` / `@ui_main`
- bundle streaming live updates back to connected clients → communicator over SSE / Socket.IO

## 10. Reader Map

- full decorator contract and manifest fields:
  [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- widget/static/browser-facing integration:
  [bundle-interfaces-README.md](bundle-interfaces-README.md)
- browser/UI request contract:
  [bundle-client-communication-README.md](bundle-client-communication-README.md)
- streaming payload catalog:
  [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)
- durable Data Bus messages:
  [../../service/comm/data-bus-README.md](../../service/comm/data-bus-README.md)
- routing and partitioning:
  [../../service/comm/bus-routing-and-partitioning-README.md](../../service/comm/bus-routing-and-partitioning-README.md)
- runtime objects available to bundle code:
  [bundle-runtime-README.md](bundle-runtime-README.md)
