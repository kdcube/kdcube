---
id: ks:docs/sdk/namespace-services/clients-README.md
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
  - ks:docs/sdk/namespace-services/README.md
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/namespace-services/integration-README.md
---
# Namespace Services: Clients

A client is any runtime surface that consumes a namespace service: a ReAct
agent, Codex, Claude Code, an MCP client, a widget, a scene host, a bundle API,
a Data Bus handler, or a scheduled job.

When the client is an agent, it is still an agent. The config section is named
`clients` because agents are only one category of service consumer.

## Bundle Configuration

Client bundles configure namespace providers under one bundle prop root:

```yaml
named_services:
  namespaces:
    task:
      provider:
        bundle_id: task-tracker@1-0
        provider: task.issue
        operation: named_service
      clients:
        default_client:
          tools:
            operations:
              - provider.about
              - object.list
              - object.search
              - object.get
              - object.action
            actions:
              - preview
              - open
              - describe
```

`provider` is bundle-level. It tells this bundle how to reach the namespace
owner.

`clients` is tool-policy level. It controls which client ids receive
model-callable named-service tools. Canvas, chat, event, and block/render
resolvers only need the namespace provider config.

## Client Ids

Use the concrete runtime identity when you need a narrow policy:

```yaml
clients:
  solver.react.v2.decision.v2.strong:
    tools:
      operations: [object.search, object.get, object.action]
      actions: [preview, open]
```

Use `default_client` when every configured model/client surface in the bundle
may use the namespace service tools:

```yaml
clients:
  default_client:
    tools:
      operations: [provider.about, object.search, object.get]
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
`named_service` operation through the request-bound local operation bridge.
