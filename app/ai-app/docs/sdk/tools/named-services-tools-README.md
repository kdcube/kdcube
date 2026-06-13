---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
title: "Named Service Tools"
summary: "How named-service namespace operations become model-callable tools, how per-agent namespace allow-lists scope those tools, and how ReAct sees the namespace scope in its catalog."
tags: ["sdk", "tools", "named-services", "namespaces", "react", "configuration"]
keywords: ["named_service", "named_services", "surfaces.as_consumer", "namespaces_applicable", "object.get", "object.host_file", "object.upsert", "react.pull"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/sdk-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
---
# Named Service Tools

Named-service tools are a generic model-callable client surface over configured
namespace providers. They let one agent use common operations such as search,
schema, create/update, or delete without linking to provider-specific bundle
code.

They are configured per consuming agent under `surfaces.as_consumer`.

```yaml
surfaces:
  as_consumer:
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
            tool_traits:
              provider_about:
                strategy: [exploration]
              list_objects:
                strategy: [exploration]
              search_objects:
                strategy: [exploration]
              object_schema:
                strategy: [exploration]
              host_file:
                strategy: [exploitation]
              upsert_object:
                strategy: [exploitation]
              delete_object:
                strategy: [exploitation]
              memo:
                allowed:
                  - provider.about
                  - object.search
                  - object.get
```

`kind: named_service` does not name a provider bundle. Provider location comes
from service discovery or explicit provider config on the namespace. The
consumer config says which namespace operations this agent is allowed to call.
`tool_traits` is keyed by the concrete ReAct-facing named-service tool names,
not by provider operation ids. The strategy trait is used by ReAct multi-action
policy.

## Catalog Shape

Bundle config uses provider operation ids such as `object.search` because that
is the named-service provider protocol. The runtime maps those configured
operations to concrete model-callable tools such as
`named_services.search_objects`.

The rendered ReAct catalog does not show provider operation ids. A tool is
visible only if at least one configured namespace allows the matching operation,
and the rendered ReAct catalog includes one scope field:

- `namespaces applicable`: namespaces where this agent may call that tool.

Example rendered catalog entry:

```text
🔧 [1] named_services.search_objects [async]

   Search objects from a configured named-service namespace with cursor
   pagination. Uses provider hybrid search when available.

   Scope:
       • namespaces applicable: task, memo
       • strategy: exploration

   📥 Parameters:
       • namespace: typing.Annotated[str
         "Configured named-service namespace, for example 'task'."]
       • query: typing.Annotated[str
         "Search query. Providers should use hybrid search when available."]

   📞 Usage: named_services.search_objects(...)
```

If `task` allows `object.schema` but `memo` does not, then
`named_services.object_schema` is still visible, but its `namespaces
applicable` list contains only `task`.

## ReAct Instruction Guidance

The ReAct shared instructions should teach the model this ecosystem concept
once, instead of repeating it in every named-service tool description:

- `named_services.*` tools are generic clients for external namespace
  providers. The provider owns the object schema, permissions, and business
  meaning.
- Each tool record tells the model which `namespaces applicable` may be passed
  as the `namespace` argument. Do not call the tool for a namespace that is not
  listed on that tool.
- Provider operation ids are config/protocol details, not ReAct-facing tool
  data. To call the tool, use the normal tool parameters, for example
  `named_services.search_objects(namespace="task", query="...")`.
- Use `provider.about` to learn what a namespace represents and what base object
  kinds it exposes.
- Use `object.schema` when the model needs the body shape for a specific object
  kind before creating or updating objects.
- Use `object.search` or `object.list` to find objects when no exact ref is
  already present.
- Use pull/read for existing refs already in the timeline when that ref can be
  materialized; use live `object.get` only when the configured tool surface
  exposes it and the model deliberately needs current provider state.
- Use `host_file` when the agent already has a ReAct/runtime file or artifact
  and needs the provider to create a provider-owned file ref in that namespace.
  After hosting, use the provider schema to cite that returned ref in an
  object update when the domain object supports attachments or file links.
- Never infer that all namespaces support all operations. The visible generic
  tool is callable only for the namespaces listed in that tool's scope.

## Config Mapping

This mapping is used by runtime configuration. It is not rendered into the
ReAct tool catalog.

| Config operation | Concrete tool |
| --- | --- |
| `provider.about` | `named_services.provider_about` |
| `object.list` | `named_services.list_objects` |
| `object.search` | `named_services.search_objects` |
| `object.get` | `named_services.get_object` |
| `object.schema` | `named_services.object_schema` |
| `object.host_file` | `named_services.host_file` |
| `object.upsert` | `named_services.upsert_object` |
| `object.delete` | `named_services.delete_object` |

UI resolver surfaces such as canvas configure their own resolver operations
outside the model-callable tool catalog. Those resolver policies are not shown
as ReAct tools.

## Pull And Existing External Refs

Existing external refs should normally be materialized with `react.pull`, not
by exposing `object.get` to the model by default.

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        event_sources:
          - kind: named_service
            namespace: task
            enabled: true
            policies:
              pull:
                mode: provider
                operation: object.get
```

`react.pull(paths=["task:issue:<id>"])` uses the configured namespace pull
policy, calls provider `object.get`, and stores the provider-selected MIME in a
conversation `fi:` artifact. The model can then use `react.read` for bounded
reads of the materialized artifact.

Expose `named_services.get_object` only when the agent must deliberately query
live provider state as a tool call. For event refs already present on the
timeline, prefer pull/read so the result becomes a visible artifact with normal
ReAct provenance.

## Provider About And Schema

`provider.about` is for a concise description of the namespace and base object
kinds. `object.schema` is for the exact object body fields and tool payload
guidance. Keep those responses bounded and operational:

- tell the agent what object kinds exist;
- list canonical ref patterns;
- describe fields that go inside `object_json`;
- state whether attachments or refs are supplied through `object.upsert`;
- avoid duplicating full provider capabilities in every object result.

The object body returned by provider operations should match the schema
advertised for that object kind.

## Hosting Provider-Owned Files

`named_services.host_file` is the reverse of pull materialization. Pull brings a
provider-owned object into ReAct as an `fi:` artifact. Host-file sends an
agent-owned runtime file/ref to a provider so the provider can create its own
namespace ref.

Clean flow:

```text
ReAct owns file/ref
  ReAct.file_ref = fi:turn_1.files/report.md

        |
        v

named_services.host_file(
  namespace="task",
  object_ref="task:issue:issue_123",
  file_ref=ReAct.file_ref,
  filename="report.md",
  mime="text/markdown"
)

        |
        v

Provider returns:
  ret.attrs.object_ref = task:issue:attachment:issue_123/attachments/ta_1/v000001/report.md
  ret.object.identity.object_kind = task.attachment

        |
        v

named_services.upsert_object(
  namespace="task",
  object_ref="task:issue:issue_123",
  object_json={
    "attachment_refs": [{
      "ref": "task:issue:attachment:issue_123/attachments/ta_1/v000001/report.md",
      "filename": "report.md",
      "mime": "text/markdown"
    }]
  }
)
```

Hosting a file and citing that file on a domain object are separate operations.
The first creates provider-owned file storage and returns a provider URI. The
second mutates the provider object according to that provider's schema.
