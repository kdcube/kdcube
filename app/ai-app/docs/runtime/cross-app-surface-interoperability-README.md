---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-app-surface-interoperability-README.md
title: "Cross-App Surface Interoperability"
summary: "Canonical implemented routing guide for app-to-app composition inside one KDCube, covering local API bridges, named services, Data Bus, jobs, conversation ingress, MCP, REST, widgets, identity, authority, and proxy reachability."
status: active
tags: ["runtime", "apps", "interoperability", "api", "mcp", "named-services", "data-bus", "jobs", "widgets", "identity"]
keywords:
  [
    "cross app interoperability",
    "call bundle operation",
    "local app call",
    "named service bridge",
    "data bus app to app",
    "background job target",
    "conversation ingress",
    "same cluster MCP",
    "private proxy",
    "cross app identity",
  ]
updated_at: 2026-09-03
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/integrate-cross-app-surface-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/runtimes-map-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-kdcube-app-surface-interoperability-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
---
# Cross-App Surface Interoperability

KDCube apps can compose through several platform surfaces. Each surface owns a
different execution and delivery contract, so the caller chooses the path from
the behavior it needs: synchronous request/response, a provider-neutral domain
contract, durable delivery, an agent turn, protocol-compatible MCP or REST, or
browser UI composition.

This document is the **implemented same-KDCube baseline**. It owns route
selection and the complete under-the-hood map for apps deployed under one
effective tenant/project runtime. The transport reference documents how each
endpoint is served; the runtime context and boundary references document the
fields and enforcement model carried across each hop.

Calls to an app owned by another KDCube cross a separate authority boundary.
The implemented building blocks, missing source-side authorization client, and
proposed end-to-end flow are owned by [Cross-KDCube App Surface
Interoperability](cross-kdcube-app-surface-interoperability-README.md).

## Terms And Scope

| Term | Meaning here |
| --- | --- |
| App | A KDCube app package, retained as `bundle` in existing code and identifiers. |
| Same KDCube | Apps deployed under the same effective tenant/project runtime, with access to the same app registry and deployment coordination services. |
| Another KDCube | A separate deployment with its own runtime, registry, and authority boundary. This document names the boundary only; the cross-KDCube design owns its mechanics. |
| Local bridge | A trusted processor-side call into another app without an HTTP round trip. |
| Proxy path | HTTP or MCP sent through the deployment's OpenResty entry service. The address may be private inside one deployment or public across deployments. |
| Surface | A declared app contract such as `@api`, `@mcp`, a named service, Data Bus handler, job handler, chat ingress, or widget. |

The core rule is:

```text
choose the semantic contract first;
choose the shortest supported transport for that contract second.
```

## Choose The Interoperability Path

| Caller need | Contract to use | Same KDCube path | Cross-KDCube equivalent |
| --- | --- | --- | --- |
| Call one bounded app operation and receive its result now | App `@api` | `call_bundle_operation(...)` or `call_bundle_operation_stream(...)` | Authenticated HTTP to the target app's `operations` or `public` route |
| Work with an owner-defined object/domain vocabulary while keeping the provider replaceable | Named service | `bundle_registry` or `bundle_operation`; isolated callers use the trusted Data Bus relay | Expose an explicit REST or MCP adapter accepted by the target KDCube |
| Deliver an app-owned command/event durably, with retry and partition ordering | Data Bus | `comm.data_bus.publish(...)` or `publish_and_wait(...)` to the target app | Publish through the target KDCube's authenticated Data Bus ingress when that integration is configured |
| Submit ready background work to another app's single job dispatcher | Background job stream | Enqueue a target `bundle_id`; proc invokes its async `@on_job` | Use a target HTTP/MCP command that creates the remote job |
| Start or continue an agent/chat turn | Conversation ingress | `ChatIngressSubmitter.submit(...)` to the target app and conversation lane | Call a target ingress adapter that authenticates and submits the event there |
| Preserve MCP discovery, tool-call, and client compatibility | MCP | MCP client over its configured transport; another hosted KDCube app uses streamable HTTP through the private proxy | MCP client over the target KDCube ingress |
| Preserve an external HTTP contract or traverse deployment boundaries | REST | Private proxy when HTTP semantics are required | Target KDCube ingress |
| Compose interfaces owned by several apps | Scene/widget composition | Scene mounts configured app widgets and brokers browser interactions | Scene mounts a configured remote surface whose target runtime accepts the browser identity |
| Notify connected browser surfaces about work already running | Communicator | SSE/Socket.IO relay to the current peer, session, or opted-in project listeners | Connect to the target runtime's authenticated client transport |

The communicator is a client-delivery path. Data Bus, the background job
stream, and the conversation event lane are durable work paths. Keeping these
roles distinct prevents a live browser connection from becoming the source of
truth for backend work.

## Same-KDCube Paths

### Complete Implemented Runtime Map

The implemented paths share one app registry and one effective tenant/project
scope, but they do not all use the same transport:

```text
                           ONE KDCUBE
                 one effective tenant/project scope

browser or external caller
  -> OpenResty / integrations ingress
  -> authenticate cookie, bearer, or app-owned proof
  -> build RequestContext for app A
  -> app A @api / @mcp / event adapter
                         |
                         +---------------------------------------------+
                         |                                             |
                         v                                             v
              SYNCHRONOUS LOCAL PATHS                         PROTOCOL PATHS
                         |                                             |
        +----------------+----------------+                    REST or MCP client
        |                                 |                    -> private OpenResty
        v                                 v                    -> authenticate again
call_bundle_operation              named-service discovery     -> build app B context
  -> resolve app B                   -> provider app B          -> app B @api / @mcp
  -> endpoint visibility             -> selected bridge         -> live target guards
  -> build app B context              -> AuthContext             -> response
  -> await app B @api                 -> provider checks
  -> response                         -> response

                         +---------------------------------------------+
                         |
                         v
                         DURABLE / ORDERED PATHS
                         |
        +----------------+----------------------+------------------+
        |                                       |                  |
        v                                       v                  v
Data Bus stream                         background-job stream   conversation lane
  -> target app partition                 -> target app id        -> target app + conv
  -> at-least-once handler                 -> @on_job              -> accepted event batch
  -> @data_bus_handler                     -> app-owned result     -> later agent turn

scene host
  -> mount app-owned widgets from configured runtimes
  -> each widget calls its owning authenticated backend surface
  -> communicator delivers live UI events; it is not durable work storage
```

Context is rebuilt at every target boundary:

```text
source app A context
  tenant + project + actor + user + session + roles + authority provenance
        |
        | platform-controlled local bridge or durable envelope
        v
target app B context
  same tenant/project and represented caller facts
  + target app identity
  + target endpoint/provider policy evaluated at B
```

The bridge carries identity and authority facts so the target can decide. It
does not make discovery, reachability, a resource id, or a copied context into
permission.

### 1. Direct Peer App Operations

Use the local operation bridge when trusted app code is already executing with
a bound request context and needs a bounded `@api` operation from another app:

```python
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    call_bundle_operation,
)

result = await call_bundle_operation(
    bundle_id="task-tracker@1-0",
    operation="issue_get",
    data={"issue_id": "BUG-123"},
)
```

The runtime path is:

```text
app A request/job runtime
  -> request-bound local operation caller
  -> resolve app B and its current descriptor-backed props
  -> resolve app B @api alias and route
  -> check app/operation enabled state and caller visibility
  -> build and bind app B request context
  -> await app B method directly
  -> return JSON result or stream
```

The bridge rebuilds the target context from the current tenant, project, actor,
user, session, routing, roles, permissions, and identity-authority projection.
The target gets its own app id and may make nested peer calls through newly
bound local callers. Other ambient subsystems, including accounting, retain
their own runtime context; they are not serialized into the target
`ExternalEventPayload` by this bridge.

`call_bundle_operation(...)` requires a local caller supplied by the runtime.
It is therefore a platform-hosted composition API, not a free-standing Python
import. A headless producer supplies explicit saved authority through the job
or service contract instead of assuming a browser session.

The local bridge applies app and endpoint visibility. It is a trusted
same-KDCube path rather than an external OAuth/MCP exchange. The target app's
domain authorization, provider claims, and record-level checks remain the
target's responsibility and run normally inside its method.

Use `call_bundle_operation_stream(...)` for a target operation that returns
bytes or a streamed result. The same identity and target-context rules apply.

#### Peer Caller Identity And `peer_callers`

On every platform peer call the target can read which app called it:

```python
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import current_peer_caller_bundle_id

caller = current_peer_caller_bundle_id()  # "connection-hub@1-0", or "" for a direct request
```

The platform sets this for one invocation, around the target method, and
resets it afterwards. It comes from the calling app's own platform-built
context: the local bridge uses the source context's app id, and the REST and
MCP peer callers pass their own app id. It is never read from the payload,
headers or any request field. A direct HTTP or MCP request sees an empty
value. For A → B → C, C sees B: each call names its immediate caller,
including a call B makes from a task it spawned.

An operation that only some apps may call declares them:

```python
@api(alias="project_authorize_decide", peer_callers=("connection-hub@1-0",))
async def project_authorize_decide(self, **kwargs): ...
```

When `peer_callers` is set, any other caller, and every direct HTTP or MCP
request, is refused before the method runs: HTTP 403 with detail
`{"code": "bundle_operation_caller_not_allowed", "caller": "<caller app id>"}`
(the caller is `""` for a direct request), or
`BundleOperationCallerNotAllowed` on the local bridge. An operation that does
not declare `peer_callers` behaves as before. `peer_callers` is a decorator
declaration only; descriptor props cannot widen it.

Threat model: this stops external and direct-HTTP callers, spoofed payload or
header values, and accidental calls from other apps through platform paths. It
does not protect against malicious code running in the same process, which can
build its own local caller. Shared-secret checks between apps (for example an
HMAC) remain until the operator decides to retire them for a runtime.

### 2. Named Services

Named services are the semantic app-to-app contract. The consumer asks for a
namespace operation; discovery identifies the owner and the configured bridge:

```text
app A / agent / trusted tool
  -> consumer policy for namespace + allowed operations
  -> tenant/project Named Service Discovery
  -> provider record owned by app B
  -> configured bridge
       bundle_registry  -> load app B and call named_services() locally
       bundle_operation -> call app B @api(alias="named_service") locally
       module           -> call an importable same-runtime provider
  -> app B provider validates AuthContext and domain rules
```

The consumer descriptor is an authority ceiling for that consumer; discovery
is only a location directory. A provider id, namespace, or object ref does not
grant access by itself.

Generated code in isolated execution uses the trusted named-service relay:

```text
restricted executor
  -> authenticated supervisor tool call
  -> Data Bus request/reply relay
  -> provider app worker
  -> named-service provider under carried AuthContext
```

This lets the executor use a configured capability without receiving the
provider credential, Redis client, or another app's storage.

An app may expose the same domain vocabulary through REST, MCP, Data Bus, or UI
surfaces. Those adapters are explicit provider surfaces. KDCube does not infer
or mount a generic MCP endpoint merely because a named-service provider
exists. The currently implemented same-KDCube named-service bridges are
`bundle_registry`, `bundle_operation`, and `module`, plus the isolated-runtime
Data Bus relay.

### 3. Data Bus

Use Data Bus for durable app-owned messages that should run even when the
originating request, worker, or browser disappears:

```text
app A
  -> comm.data_bus.publish(bundle_id=app_B, subject=..., payload=...)
  -> target app-scoped Redis Stream
  -> proc claims/reclaims the message
  -> binds DataBusContext and actor/auth metadata
  -> app B @data_bus_handler
  -> durable result/state update
  -> optional live reply through communicator
```

`publish_and_wait(...)` waits for a correlated handler result; it does not
turn the stream into exactly-once RPC. Delivery is at least once. Mutating
messages need stable `message_id` and `idempotency_key` values, and the target
owns durable idempotency and concurrency checks.

The native publisher and stream belong to one tenant/project deployment. A
caller in another KDCube enters through the target KDCube's authenticated Data
Bus HTTP/Socket.IO ingress when that cross-deployment integration is desired;
the two deployments do not share a Redis Stream.

### 4. Background Jobs

Use the background job stream when work is already ready and the target app's
single `@on_job` dispatcher owns its execution:

```text
app A / scheduler / admin operation
  -> RedisBackgroundJobStream.enqueue(target bundle_id, work_kind, payload)
  -> proc fair scheduler and consumer-group recovery
  -> target app @on_job
  -> target-owned result/status/artifacts
```

Jobs are headless by default. A job acting for a user persists and rebinds
explicit identity and authority metadata. A queue label selects scheduling
and fairness; it is not user authority. The target handler is async and owns
idempotency because pending work can be reclaimed after worker loss.

Use Data Bus for durable domain messages addressed by subject and partition.
Use background jobs for ready work addressed to the target app's job
dispatcher.

### 5. Conversation And Agent Turns

Use conversation ingress when the target app's agent should receive an event
as part of its ordered conversational work:

```text
app A API/webhook/service adapter
  -> build target app/conversation identity in RequestContext + IngressConfig
  -> ChatIngressSubmitter.submit(... external_events[] ...)
  -> atomic conversation-lane admission
  -> reserve one serialized target turn
  -> target adapter maps its live or pending-lane input
  -> target app @on_reactive_event / run()
  -> timeline + communicator output
```

This path returns an admission result, not the completed agent answer. It
provides ordered, at-least-once conversational delivery and allows a later
worker to execute the turn. A direct peer operation remains the synchronous
choice when the caller needs one immediate app result.

## MCP And REST Paths

### MCP Inside One KDCube

An app's `@mcp(...)` surface is served by the integrations router as MCP over
streamable HTTP. KDCube's MCP consumer resolves a server map and then uses a
real MCP client transport:

```text
consumer app or hosted agent
  -> configured MCP connection {url, transport, resource, scopes}
  -> optional per-user delegated bearer from Connection Hub
  -> MCP client: streamable HTTP | SSE | stdio
  -> target @mcp surface
  -> target endpoint policy + current grant checks
```

For an HTTP MCP endpoint in the same deployment, the URL may use the
deployment-provided private OpenResty address. In the ECS reference topology,
Cloud Map exposes the proxy as `web-proxy.kdcube.local`; local profiles use
their own service address. Apps should consume the configured endpoint rather
than hardcode one deployment's DNS name.

Managed authorization still compares the request URL observed by the target
with the card's configured protected-resource pattern. A private endpoint is
therefore configured together with a resource pattern that matches the
canonical path under private routing, commonly a host-independent
`*/api/integrations/...*` pattern. If a deployment uses an exact public URL as
the resource id, its private caller must preserve that public host and scheme
through trusted proxy headers. Changing only `url` while retaining a
non-matching exact `resource` produces a resource-mismatch denial.

The current SDK has no app-facing `call_bundle_mcp(...)` local shortcut
parallel to `call_bundle_operation(...)`. The proc router can dispatch an
inbound MCP request to an app in-process, but an MCP consumer still speaks an
MCP transport. Use a direct app operation or named service when protocol
compatibility is not part of the contract.

For managed MCP, `url` is the transport destination and `resource` is the
protected-resource identity used by the grant. They may be the same value, but
their jobs are distinct. The target validates a bearer accepted by that
KDCube; the model never supplies the caller identity as a tool argument.

### REST Inside One KDCube

Use the private proxy when the caller intentionally needs the target's real
HTTP contract: HTTP status and headers, an app-owned authentication scheme, a
webhook-compatible route, or transport parity with an external client.

```text
app A trusted HTTP client
  -> private OpenResty address
  -> integrations route
  -> authenticate request
  -> bind target app context
  -> app B @api
```

Server-side callers use an explicit credential accepted by the target route.
They do not replay a user's browser cookie as portable authority. When only a
bounded same-KDCube operation is needed, the local operation bridge avoids
this network and authentication round trip.

## Browser And Widget Composition

A scene can mount widgets from several apps, in-page components, and configured
external panels. A panel served by another KDCube remains a remote browser
surface whose owning runtime authenticates its requests. The composition path
is:

```text
scene host
  -> resolve configured {bundle_id, widget_alias or route}
  -> mount iframe/in-page surface
  -> runtime-config and auth handshake
  -> local postMessage/callback commands for UI coordination
  -> target API/Data Bus/SSE transport for backend work
```

The scene host owns surface registration, routing, readiness, and event relay.
The target widget owns its UI and calls its owning runtime through an
authenticated transport. Typed object refs and scene commands identify the
object and intended UI effect; the target backend resolves and authorizes the
object under current identity.

Cross-surface drag/drop, direct surface commands, and live event subscriptions
are browser contracts. They can trigger API, Data Bus, named-service, or
conversation work, but they do not replace those backend contracts.

## Boundary To Another KDCube

Separate KDCube deployments do not share a local app registry, Data Bus
stream, job stream, Python `ContextVar`, or Connection Hub authority store. A
call leaves the mechanisms documented above and enters the target KDCube
through an authenticated network surface:

```text
app A in KDCube A
  -> target protocol request + target-accepted credential
  -> KDCube B ingress
  -> KDCube B authenticates the proof
  -> KDCube B constructs a new local request context
  -> app B enforces B-owned grants and domain rules
```

Raw `REQUEST_CONTEXT`, `BUNDLE_CALL_CONTEXT`, and `comm_ctx` values remain
context records, not remote credentials. A shared identity provider can let
both runtimes recognize the same human in a browser; it does not authorize a
server-side app call or copy a grant between Connection Hubs.

See [Cross-KDCube App Surface
Interoperability](cross-kdcube-app-surface-interoperability-README.md) for the
current browser and target-server capabilities, the missing remote delegated
authorization client, and the proposed OAuth/PKCE, storage, event, retry,
refresh, and revocation flow.

## Identity And Authority By Path

| Path | How target context is established | Where authority is enforced |
| --- | --- | --- |
| Local app operation | Runtime derives target context from the bound caller and target app id; the caller app id is bound for `current_peer_caller_bundle_id()` | Target endpoint visibility, optional `peer_callers`, plus target domain/guard checks |
| Named service | `AuthContext` comes from current request, Data Bus context, job context, or explicit service context | Consumer policy ceiling, provider schema/operation checks, Connection Hub claims where declared |
| Data Bus | Ingress/publisher writes actor and auth metadata; worker binds `DataBusContext` | Target handler and guarded services |
| Background job | Producer stores headless or explicit on-behalf-of metadata; proc rebuilds request context | Target job/domain code and guarded services |
| Conversation ingress | Authenticated submitter builds `ExternalEventPayload`; lane preserves it with the accepted event batch | Target turn, tools, and guarded services |
| Same-KDCube MCP/REST | Target ingress authenticates the HTTP request | Endpoint policy, live delegated grant, provider account, and domain checks as configured |
| Cross-KDCube MCP/REST | Target authenticates a target-accepted credential and creates a new local context | Target KDCube authority and target app rules |
| Widget/scene | Browser session or scoped/federated token is presented to the widget's owning runtime | Target transport and backend surface |

Identity continuity supplies facts. A protected surface still makes the
authorization decision. Reachability, discovery, and an object/resource id do
not grant authority.

## Implemented Boundary And Absent Shortcuts

| Contract | Implemented now | Deliberately absent or not yet implemented |
| --- | --- | --- |
| Same-KDCube app `@api` | Local request-bound operation and stream bridges. | The bridge does not cross deployments. |
| Same-KDCube named service | Local registry/operation/module bridges and trusted isolated-runtime relay. | A named-service declaration does not automatically create REST or MCP. |
| Same-KDCube MCP | Real MCP client transport through the configured endpoint, including a private proxy URL. | There is no app-facing `call_bundle_mcp(...)` Python shortcut. |
| Same-KDCube durable work | Data Bus, background jobs, and conversation lanes owned by this tenant/project runtime. | Their Redis streams and local context are not shared with another KDCube. |
| Cross-KDCube HTTP/MCP | A caller can use a configured endpoint and a credential already accepted by the target. | Automatic remote discovery, browser consent, PKCE exchange, per-agent credential storage, and retry are design work; see the cross-KDCube document. |
| Browser composition | A scene can mount configured remote widgets when their owning runtime accepts the browser identity. | Browser identity acceptance does not create server-to-server authority. |

## Proxy Reachability Inside A Deployment

Same-KDCube apps may deliberately call HTTP or MCP through OpenResty. A cloud
network profile therefore needs two explicit sources for the proxy listener:

```text
public ingress path
  deployment load balancer security identity
      -> web-proxy security identity : TCP 80

trusted same-KDCube protocol path
  application-task security identity
      -> web-proxy security identity : TCP 80
```

The proxy task keeps only its dedicated proxy security identity. Attaching the
general application-task identity to the proxy would also give it every
database, cache, and inter-service permission granted to application tasks.
The explicit application-to-proxy ingress rule supplies the intended route
without merging those identities.

Network admission only makes the proxy reachable. API/MCP authentication and
app authorization still run at the target surface. Cross-KDCube calls use the
target's external or explicitly peered ingress instead of this local security
group path. Application code is trusted in KDCube's current trust model. When
a managed grant uses an exact public resource id, the private connection must
be configured so the target observes that canonical host and scheme; a
host-independent resource pattern avoids that coupling.

## Builder Decision Flow

```text
Does the target agent need this as an ordered conversation event?
  yes -> conversation ingress
  no
   |
Does the target need durable app-owned handling after the caller exits?
  yes -> Data Bus, or background job stream for ready job work
  no
   |
Is this an owner-defined domain/object contract used by several consumers?
  yes -> named service; choose its configured local bridge
  no
   |
Does the consumer require MCP or HTTP protocol compatibility?
  yes -> private proxy in one KDCube; target ingress across KDCubes
  no
   |
Use the request-bound local @api operation bridge.
```

For a browser experience, compose the UI through a scene/widget surface and
then apply the same decision flow to the backend action it starts.

For implementation steps and copy-ready SDK examples, follow
[Integrate One KDCube App With Another](../recipes/apps/integrate-cross-app-surface-README.md).

## Implementation Anchors

| Concern | Primary implementation or depth owner |
| --- | --- |
| Local app operation and stream bridges | `kdcube_ai_app/apps/chat/sdk/infra/bundle_operations.py` |
| Named-service provider/client bridges | [Named Service Providers](../sdk/namespace-services/providers-README.md) |
| Data Bus producer and handler paths | [Data Bus](../service/comm/data-bus-README.md) |
| Background job stream | [Background Job Streams](../service/streams/background-jobs-README.md) |
| Conversation submitter | [Servicing Interfaces](../service/servicing-interfaces-README.md) |
| App REST/MCP routes and auth ownership | [Bundle Transports](../sdk/bundle/bundle-transports-README.md) |
| MCP client transports | [MCP Integration](../sdk/tools/mcp-README.md): `app-foundation[mcp]` owns client construction; `kdcube_ai_app/apps/chat/sdk/runtime/mcp/client.py` is the compatibility facade |
| Per-user MCP connection resolution | Connection Hub policy plus `kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/delegated_mcp.py` |
| Cross-KDCube implemented baseline and planned delegated client | [Cross-KDCube App Surface Interoperability](cross-kdcube-app-surface-interoperability-README.md) |
| Scene and widget composition | [Scene Composition](../sdk/solutions/scene/scene-composition-README.md) |
| Portable fields carried on platform-controlled hops | [Cross-Runtime Context](cross-runtime-context-README.md) |
| Tenant, user, authority, and isolation boundaries | [Tenant, User, Authority, And Execution Boundaries](tenant-project-user-and-execution-boundaries-README.md) |
