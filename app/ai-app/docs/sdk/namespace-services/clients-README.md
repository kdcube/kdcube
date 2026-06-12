---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
title: "Namespace Services: Clients"
summary: "How bundles, agents, widgets, jobs, and external clients consume configured namespace service providers."
status: design
tags: ["sdk", "namespace-services", "clients", "tools", "resolvers", "bundles"]
updated_at: 2026-06-12
keywords:
  [
    "namespace service client",
    "named_services config",
    "client id",
    "agent client",
    "model-callable tools",
    "canvas resolver",
    "chat resolver",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Namespace Services: Clients

A client is any runtime surface that consumes a namespace service: a ReAct
agent, Codex, Claude Code, an MCP client, a widget, a scene host, a bundle API,
a Data Bus handler, or a scheduled job.

When the client is an agent, it is still a service consumer. Namespace-service
access is therefore configured under the consumer bundle's
`surfaces.as_consumer` surface, alongside the other tools and UI resolver
surfaces that the bundle consumes.

## Bundle Configuration

Client bundles configure namespace access under one bundle prop root:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - id: task_service
            kind: named_service
            alias: named_services
            namespaces:
              task:
                allowed:
                  - provider.about
                  - object.list
                  - object.search
                  - object.schema
                  - object.host_file
                  - object.upsert
                  - object.delete
        event_sources:
          - kind: named_service
            namespace: task
            enabled: true
            discovery:
              mode: service_discovery
            policies:
              block_production:
                mode: provider
                operation: block.produce
              pull:
                mode: provider
                operation: object.get
    ui:
      canvas:
        resolvers:
          - kind: named_service
            namespace: task
            enabled: true
            discovery:
              mode: service_discovery
            allowed: [object.resolve, object.action]
```

The namespace key declares that this bundle may consume refs in that namespace.
Provider location is normally resolved from Named Service Discovery. A provider
bundle registers its available providers into the tenant/project Redis table
when it is loaded.

The discovery scope itself is portable runtime context. The platform carries a
JSON-safe tenant/project discovery descriptor through `comm_ctx`, and the
target runtime reconstructs `RedisNamedServiceDiscovery` from runtime
configuration. Do not pass Redis clients through tool registries. See
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

An explicit `providers` list is optional and should be used only when a client
must pin concrete endpoints instead of using discovery. The list is plural
because one namespace may be served by multiple providers. For bundles in the
same KDCube runtime, the resolved or explicit transport should normally be
`bundle_registry`. That path calls the owner bundle's `named_services()`
registry object under the current request/session context. Use
`bundle_operation` when the owner should be reached through its
`@api(alias="named_service")` facade. Use `module` when the provider registry
is in an importable Python module in the same runtime.

Explicit providers are still provider endpoint overrides, not the normal
consumer surface. Keep them outside the agent-visible tool contract and use
them only when service discovery is not available for that deployment. The
normal same-runtime path is:

```text
Consumer.surfaces.as_consumer
  -> namespace task
  -> discovery.mode service_discovery
  -> Redis Named Service Discovery for tenant/project
  -> Provider registry entry
  -> provider operation
```

Inside an agent tool item, `namespaces.<namespace>.allowed` controls which
model-callable named-service tools are visible to that agent. If a UI resolver
surface is allowed to call `object.action`, the provider remains authoritative
for the concrete action name it accepts or rejects.

The namespace `pull` policy is separate from model-callable tools. A client may
allow `react.pull` to materialize `task:` refs through provider `object.get`
without exposing the generic `named_services.get_object` tool to the agent.

## Client Ids

Use the concrete runtime identity when you need a narrow policy:

```yaml
surfaces:
  as_consumer:
    agents:
      solver.react.v2.decision.v2.strong:
        tools:
          - id: task_service
            kind: named_service
            alias: named_services
            namespaces:
              task:
                allowed: [provider.about, object.search, object.schema, object.upsert]
```

Use `default_client` when every configured model/client surface in the bundle
may use the namespace service tools:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - id: task_service
            kind: named_service
            alias: named_services
            namespaces:
              task:
                allowed: [provider.about, object.search]
```

## Runtime Use

Model-callable tools are added by extending a bundle's tool specs:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    extend_tool_specs_for_named_services,
)

def tools_for_client(client_id: str, *, bundle_props=None):
    return extend_tool_specs_for_named_services(
        BASE_TOOLS_SPECS,
        bundle_props=bundle_props,
        client_id=client_id,
    )
```

The current ReAct integration passes the ReAct agent id as the namespace
service client id. Other runtimes can pass their own client id when their tool
adapters are wired.

Agents should use `provider.about` to learn what a namespace service is for and
`object.schema` to learn the shape of concrete objects before mutation. For
ReAct specifically, fully reading any object from an external namespace means
`react.pull(<external_ref>)` first, then `react.read(<materialized fi:...>)`.
This applies even when the external object is JSON or markdown, not only when
it is a binary file. The provider decides the materialized representation and
MIME. `named_services.get_object` is the provider operation behind configured
pull, not the default ReAct-facing way to read external object content. The
generic `object.upsert` and `object.delete` tools intentionally do not encode
domain-specific fields; the provider owns those schemas.

When an agent has a file in its own runtime and needs that file represented in
the provider's namespace, it should call `named_services.host_file` if the tool
catalog lists the target namespace as applicable. The provider returns its own
file object/ref. If the target object schema supports attachments or file
links, the agent then cites that returned provider ref in a separate
`named_services.upsert_object` call. Hosting the file and mutating the domain
object are different operations.

## Resolver Use

Canvas and chat object actions use a configured resolver registry:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_canvas_resolver_namespaces,
)

register_configured_named_service_canvas_resolvers(
    registry,
    namespaces=named_service_canvas_resolver_namespaces(self.bundle_props),
    tenant=tenant,
    project=project,
    logger=_log,
)
```

This lets a scene or chat widget open `task:issue:issue_123` without knowing
task-tracker API aliases. The resolver calls the owning bundle's
named-service endpoint through the configured transport. Same-KDCube
integrations normally use `bundle_registry`; large object bytes are streamed
only by explicit pull materialization, not during normal render. Here "normal
render" means canvas/chat/timeline preview, open, and block projection. Those
paths may resolve metadata or model-visible blocks, but they do not copy
provider-owned bytes into ReAct's `fi:` workspace.
The client bundle does not configure provider-specific resolver semantics here:
`surfaces.as_consumer.ui.canvas.resolvers` only opts the namespace and canvas
operation families into use. `object.resolve` resolves a concrete ref into
metadata, parent refs, capabilities, and cheap display information.
`object.action` runs explicit UI actions such as `open`, `preview`, or
`download`. The owning provider decides which actions are accepted for the
concrete object ref.

### Consumer-Owned Versus Provider-Owned Values

The client bundle owns connection policy and runtime context, not namespace
semantics.

```text
Consumer-owned:
  Consumer.config.surfaces.as_consumer.agents.<agent>.tools
  Consumer.config.surfaces.as_consumer.agents.<agent>.event_sources
  Consumer.config.surfaces.as_consumer.ui.canvas.resolvers
  Consumer.request.auth_context
  Consumer.event.logical_path or Consumer.card.object_ref

Provider-owned:
  Provider.object_kind for a URI
  Provider.event_source_id for provider rendering
  Provider.block markdown/shape
  Provider.object schema and mutation payloads
  Provider.permission decisions
```

When ReAct receives a foreign ref on a lane event, the consumer event-source
subsystem first tries the event's authored source. If that source does not
produce blocks, it calls the namespace resolver function for the ref namespace.
For configured named-service namespaces, that resolver function calls provider
`event.resolve` with `request.object_ref = Consumer.event.logical_path`. The
provider returns `ret.extra.event_source_id` and any other resolution metadata.
The consumer then applies the resolved event source and delegates block
production to provider `block.produce`.

ReAct uses the same namespace config for backend artifact materialization:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_agent_pull_namespaces,
)

register_configured_named_service_artifact_rehosters(
    event_sources,
    namespaces=named_service_agent_pull_namespaces(self.bundle_props, client_id=agent_id),
    tenant=tenant,
    project=project,
)
```

This lets `react.pull` materialize refs such as `task:issue:issue_123` or
`task:issue:attachment:issue_123/attachments/ta_1/v000001/evidence.md`. The rehoster
calls the owning provider's configured pull operation, normally `object.get`,
with `response_mode: stream`. The provider returns structured named-service
metadata plus async byte chunks; the runtime writes the chunks into the ReAct
`fi:` workspace. Access checks happen in the provider under the current auth
context, and provider errors are returned in the `react.pull` tool result under
`errors`.

Configured namespaces can also publish ReAct block-production policies:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_agent_event_source_namespaces,
)

register_configured_named_service_event_sources(
    event_sources,
    namespaces=named_service_agent_event_source_namespaces(self.bundle_props, client_id=agent_id),
)
```

The helper registers event sources such as `named_services.task`. When a lane
event already uses that event source and carries a `task:` ref, the policy
calls the provider's `block.produce` operation and appends the returned blocks.
When a lane event uses another authored source but carries a configured
foreign ref, the resolver bridge calls provider `event.resolve` first and uses
the provider-returned event source id before block production.
