---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
title: "Named Service Tools"
summary: "How named-service namespace operations become model-callable tools, how per-agent namespace allow-lists scope those tools, and how ReAct sees the namespace scope in its catalog."
tags: ["sdk", "tools", "named-services", "namespaces", "react", "configuration"]
keywords: ["named_service", "named_services", "surfaces.as_consumer", "namespaces_applicable", "provider search scopes", "search_scopes_by_namespace", "named_service.search_results", "object.get", "object.host_file", "object.upsert", "react.pull"]
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
          - id: sensor_service
            kind: named_service
            alias: named_services
            namespaces:
              sensor:
                allowed:
                  - provider.about
                  - object.list
                  - object.search
                  - object.schema
                  - object.host_file
                  - object.upsert
                  - object.delete
              front-door:
                allowed:
                  - provider.about
                  - object.search
                  - object.get
                  - object.upsert
                tool_traits:
                  upsert_object:
                    strategy: [neutral]
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
```

`kind: named_service` does not name a provider bundle. Provider location comes
from service discovery or explicit provider config on the namespace. The
consumer config says which namespace operations this agent is allowed to call.
`tool_traits` is keyed by the concrete ReAct-facing named-service tool names,
not by provider operation ids. The strategy trait is used by ReAct multi-action
policy. Connection-level `tool_traits` define the default trait for the generic
tool. A namespace may also define `tool_traits` next to `allowed`; that
namespace-specific trait is used only when the model calls the tool with that
namespace in `params.namespace`.

## Catalog Shape

Bundle config uses provider operation ids such as `object.search` because that
is the named-service provider protocol. The runtime maps those configured
operations to concrete model-callable tools such as
`named_services.search_objects`.

The rendered ReAct catalog does not show provider operation ids. A tool is
visible only if at least one configured namespace allows the matching operation,
and the rendered ReAct catalog includes scope fields:

- `namespaces applicable`: namespaces where this agent may call that tool.
- `provider search scopes`: provider-declared scoped namespaces that may be
  passed to `named_services.search_objects(namespace=...)` for that base
  namespace.
- `strategy overrides by namespace`: namespace-specific effective traits for
  the same generic tool, when configured.

Example rendered catalog entry:

```text
🔧 [1] named_services.search_objects [async]

   Search objects from a configured named-service namespace with cursor
   pagination. Uses namespace-service search when available.

   Scope:
       • namespaces applicable: sensor, front-door
       • provider search scopes:
           sensor:
             - sensor:temperature — temperature readings (filters: room, thresholds; details: object_schema(namespace="sensor:temperature"))
             - sensor:humidity:aggr — humidity aggregates (filters: room, scoring; details: object_schema(namespace="sensor:humidity:aggr"))
       • strategy: exploration

   📥 Parameters:
       • namespace: typing.Annotated[str
         "Configured named-service namespace or provider-declared searchable scoped namespace."]
       • query: typing.Annotated[str
         "Search query. Namespace about/schema declares per-scope semantics and filters."]

   📞 Usage: named_services.search_objects(...)
```

Concrete example when a bundle configures task, memory, and canvas card search:

```text
🔧 [8] named_services.search_objects [async]

   Search objects from a configured named-service namespace with cursor
   pagination. Uses provider hybrid search when available.

   Scope:
       • namespaces applicable: task, mem, cnv
       • provider search scopes:
           task:
             - task — task issues (filters: state, tags, factor_weights, thresholds, scoring; details: object_schema(namespace="task"))
             - task:issue — task issues (filters: state, tags, factor_weights, thresholds, scoring; details: object_schema(namespace="task:issue"))
             - task:attachment — task attachments/files (filters: state, tags, mime, thresholds; details: object_schema(namespace="task:attachment"))
           mem:
             - mem — all memory objects (filters: origin, mode, labels, keywords, kind, status, visible_to_user, factor_weights, thresholds, scoring; details: object_schema(namespace="mem"))
             - mem:record — memory records (filters: origin, mode, labels, keywords, kind, status, visible_to_user, factor_weights, thresholds, scoring; details: object_schema(namespace="mem:record"))
           cnv:
             - cnv — canvas cards (filters: canvas_name, canvas_id, all_boards, kinds, namespaces, thresholds; details: object_schema(namespace="cnv"))
       • strategy: exploration

   📥 Parameters:
       • namespace: Annotated[str
         "Configured named-service namespace or provider-declared searchable
         scoped namespace, for example 'sensor:temperature' or
         'sensor:humidity:aggr'."]
       • query: Annotated[str
         "Search query. Namespace about/schema declares per-scope semantics
         and filters."]
       • limit: Annotated[int [default: 10]
         "Maximum objects to return. Keep this bounded."]
       • cursor: Annotated[str [optional]
         "Optional pagination cursor from a previous response."]
       • filters: Annotated[str [optional]
         "Optional JSON object with provider-specific filters."]

   📤 Returns:
       Annotated[Dict[str, Any], "Named service response envelope with
       matching items."]

   📞 Usage: named_services.search_objects(...)
```

The concise `filters: ...` list is only a hint. The authoritative contract for
one namespace/scope is returned by `named_services.object_schema`; read
`ret.extra.schema.search.filters` for filter fields and
`ret.extra.search_scopes` for all search scopes the provider exposes in that
schema response.

If `sensor` allows `object.schema` but `front-door` does not, then
`named_services.object_schema` is still visible, but its `namespaces
applicable` list contains only `sensor`.

## ReAct Instruction Guidance

The ReAct shared instructions should teach the model this ecosystem concept
once, instead of repeating it in every named-service tool description:

- `named_services.*` tools are generic clients for external namespace refs.
  The namespace service explains the ref grammar, object schema, permissions,
  and business meaning it can operate.
- Each tool record tells the model which `namespaces applicable` may be passed
  as the `namespace` argument.
- Provider operation ids are config/protocol details, not ReAct-facing tool
  data. To call the tool, use the normal tool parameters with a namespace from
  the tool's visible scope.
- For search, first use the provider search scopes already rendered under
  `named_services.search_objects` when present. If the rendered scope list or
  its semantics are not enough, call `provider.about` before the first
  search/list/mutation unless a fresh provider description is already visible in
  the current context.
- ReAct starts each turn with a sparse local workspace. It can directly use
  only current-turn `su:`, `so:`, `fi:`, and `ar:` refs that were produced in
  this turn or explicitly materialized in this turn.
- Existing namespace refs in events, pins, canvas drops, prior messages, or
  prior tool results are handles. Use `react.pull` to materialize such a handle
  into this turn's local `fi:` workspace before reading concrete content.
- In `search_objects`, the `namespace` argument is the search scope. A scoped
  namespace searches that provider-declared object space.
- Use `object_schema` when exact body fields, search filter contracts, or
  tool payload recipes are needed. Search filters live under
  `ret.extra.schema.search.filters`; providers may also include
  `ret.extra.search_scopes` so the agent can inspect all searchable scopes.
- If the agent passes the base namespace to `search_objects`, the namespace
  service's declared default search scope handles the call. If the service advertises
  narrower searchable scoped namespaces, the agent may pass one of those scoped
  names as `namespace`; the runtime authorizes the base namespace and preserves
  the scoped namespace for the namespace service.
- Use `object.search` or `object.list` to find objects when no exact ref is
  already present.
- `named_services.search_objects` returns the provider response to the model
  and also emits a generic `named_service.search_results` subsystem artifact.
  Capable chat/scene clients can render those result rows as clickable and
  draggable context objects; model instructions must not assume that such a UI
  exists.
- Use pull/read for existing refs already in the timeline when content is
  needed; use live `object.get` only when the configured tool surface exposes
  it and the model deliberately needs current namespace service state.
- Use `host_file` when the agent already has a ReAct/runtime file or artifact
  and needs the namespace service to create or register a file ref in that
  namespace.
  After hosting, use the namespace schema to cite that returned ref in an
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

Existing external refs are handles. ReAct cannot read their concrete content
from a fresh turn workspace until it materializes them. `react.pull` is the
materialization step and should be the normal path, instead of exposing
`object.get` to the model by default.

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        event_sources:
          - kind: named_service
            namespace: sensor
            enabled: true
            policies:
              pull:
                mode: provider
                operation: object.get
```

`react.pull(paths=["sensor:temperature:reading-123"])` uses the configured
namespace pull policy, calls the configured resolver operation, and stores the
selected MIME as a current-turn `fi:` artifact. The model can then use
`react.read` for bounded reads of the materialized artifact.

Expose `named_services.get_object` only when the agent must deliberately query
live namespace service state as a tool call. For event refs already present on
the timeline, prefer pull/read so the handle becomes a visible `fi:` artifact
with normal ReAct provenance.

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

## Hosting Files Into Namespace Refs

`named_services.host_file` is the reverse of pull materialization. Pull brings a
namespace ref into ReAct as an `fi:` artifact. Host-file sends an agent-owned
runtime file/ref to a namespace service so the service can create or register a
namespace ref.

Clean flow:

```text
ReAct owns file/ref
  ReAct.file_ref = fi:turn_1.files/report.md

        |
        v

named_services.host_file(
  namespace="sensor",
  object_ref="sensor:temperature:reading-123",
  file_ref=ReAct.file_ref,
  filename="report.md",
  mime="text/markdown"
)

        |
        v

Namespace service returns:
  ret.attrs.object_ref = sensor:temperature:file:report-123
  ret.object.identity.object_kind = sensor.file

        |
        v

named_services.upsert_object(
  namespace="sensor",
  object_ref="sensor:temperature:reading-123",
  object_json={
    "attachment_refs": [{
      "ref": "sensor:temperature:file:report-123",
      "filename": "report.md",
      "mime": "text/markdown"
    }]
  }
)
```

Hosting a file and citing that file on a domain object are separate operations.
The first creates or registers a file ref in the namespace and returns that
ref. The second mutates the namespace object according to that namespace
schema.

Canvas uses the same `upsert_object` tool when `cnv` is configured and allowed,
but the object kind is explicit:

```python
named_services.upsert_object(
    namespace="cnv",
    object_json={
        "object_kind": "canvas.card",
        "canvas_name": "main",
        "card": {
            "kind": "user.text",
            "title": "Release note",
            "mime": "text/markdown",
            "content": {"text": "Ship the release note text here."},
        },
    },
)
```

The canvas provider applies this as a `new_card` patch. If the card hosts inline
content, the returned card contains the concrete hosted ref under
`ret.object.card.logical_path`, for example
`cnv:canvas/users/<user>/canvases/<board>/objects/user-text/<card-id>/v000001.md`.
