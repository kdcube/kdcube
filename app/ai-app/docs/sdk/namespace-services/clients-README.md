---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
title: "Namespace Services: Clients"
summary: "How bundles, agents, widgets, jobs, and external clients consume configured namespace service providers."
status: design
tags: ["sdk", "namespace-services", "clients", "tools", "resolvers", "bundles"]
updated_at: 2026-06-11
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

When the client is an agent, it is still an agent. The config section is named
`clients` because agents are only one category of service consumer.

## Bundle Configuration

Client bundles configure namespace access under one bundle prop root:

```yaml
named_services:
  namespaces:
    task:
      clients:
        default_client:
          tools:
            allowed_operations:
              - provider.about
              - object.list
              - object.search
              - object.get
              - object.schema
              - object.upsert
              - object.delete
        canvas:
          resolver:
            enabled: true
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

```yaml
named_services:
  namespaces:
    task:
      providers:
        - bundle_id: task-tracker@1-0
          provider: task.issue
          transport: bundle_registry
          refs: [task:issues/*]
          object_kinds: [task.issue]
          operations: [provider.about, object.list, object.search, object.get, object.schema, object.upsert, object.delete, block.produce, block.render]
        - bundle_id: task-files@1-0
          provider: task.attachment
          transport: bundle_registry
          refs: [task:issues/*/attachments/*]
          object_kinds: [task.attachment]
          operations: [object.action]
```

Inside `providers`, `operations` means advertised provider capabilities. Inside
`clients.<client_id>.tools`, `allowed_operations` controls which model-callable
tools are visible to that client. If a client is allowed to call
`object.action`, the provider remains authoritative for the concrete action
name it accepts or rejects.

`clients` is per consumer surface. `clients.<client_id>.tools` controls
model-callable named-service tools. `clients.canvas.resolver.enabled` enables
canvas/chat object resolution for refs in the namespace; the resolver calls the
provider and the provider decides concrete object actions.

## Client Ids

Use the concrete runtime identity when you need a narrow policy:

```yaml
clients:
  solver.react.v2.decision.v2.strong:
    tools:
      allowed_operations: [provider.about, object.search, object.get, object.schema, object.upsert]
```

Use `default_client` when every configured model/client surface in the bundle
may use the namespace service tools:

```yaml
clients:
  default_client:
    tools:
      allowed_operations: [provider.about, object.search, object.get]
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
`object.schema` to learn the shape of concrete objects before mutation. The
generic `object.upsert` and `object.delete` tools intentionally do not encode
domain-specific fields; the provider owns those schemas.

## Resolver Use

Canvas and chat object actions use a configured resolver registry:

```python
register_configured_named_service_canvas_resolvers(
    registry,
    namespaces=self.bundle_prop("named_services.namespaces", {}) or {},
    tenant=tenant,
    project=project,
    logger=_log,
)
```

This lets a scene or chat widget open `task:issues/issue_123` without knowing
task-tracker API aliases. The resolver calls the owning bundle's
named-service endpoint through the configured transport. Same-KDCube
integrations normally use `bundle_registry`; large object bytes are streamed
only by explicit pull/rehost operations, not during normal render.
The client bundle does not configure provider-specific resolver actions here:
`clients.canvas.resolver.enabled` only opts the namespace into resolution.
The owning provider decides whether generic requests such as `open`, `preview`,
`describe`, or `capabilities` are accepted for the concrete object ref.

ReAct uses the same namespace config for backend artifact rehosting:

```python
register_configured_named_service_artifact_rehosters(
    event_sources,
    namespaces=self.bundle_prop("named_services.namespaces", {}) or {},
    tenant=tenant,
    project=project,
)
```

This lets `react.pull` materialize refs such as
`task:issues/issue_123/attachments/ta_1/v000001/evidence.md` by streaming bytes
from the owning provider. Access checks happen in the provider under the
current auth context.

Configured namespaces can also publish ReAct block-production policies:

```python
register_configured_named_service_event_sources(
    event_sources,
    namespaces=self.bundle_prop("named_services.namespaces", {}) or {},
)
```

The helper registers event sources such as `named_services.task`. When a lane
event uses that event source and carries a `task:` ref, the policy calls the
provider's `block.produce` operation and appends the returned blocks.
