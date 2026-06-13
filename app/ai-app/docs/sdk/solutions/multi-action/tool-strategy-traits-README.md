---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
title: "Tool Strategy Traits"
summary: "Tool metadata traits used by ReAct stream governance and multi-action compatibility checks."
tags: ["sdk", "tools", "traits", "react", "multi-action"]
keywords: ["tool_traits", "tool_trait", "strategy", "exploration", "exploitation", "neutral", "unknown"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
---
# Tool Strategy Traits

Tool traits are metadata attached to concrete model-callable tools. The first
runtime trait used by ReAct multi-action policy is `strategy`.

## Strategy Values

```
exploration   The tool obtains information: search, read, inspect, pull.
exploitation  The tool mutates state or produces durable output: write, patch,
              render, delete, upsert, host.
neutral       The tool records side information or runtime bookkeeping that
              does not change the answer premise.
unknown       Default when no trait is configured or discovered.
```

A tool may carry more than one strategy when that is true for the tool. For
example an exec tool can inspect data and also produce output, so it may be:

```yaml
tool_traits:
  execute_code_python:
    strategy: [exploration, exploitation]
```

## Where Traits Come From

Traits can come from tool code or consumer config.

```
Python function decorator
  @tool_trait(strategy=["exploration"])
        |
        v
ToolSubsystem introspection
        |
        v
catalog metadata

Consumer config
  surfaces.as_consumer.agents.<agent>.tools[].tool_traits
        |
        v
agent_tool_config_from_bundle_props
        |
        v
ToolSubsystem config override
```

Consumer config is the deployment policy. It can override decorator-provided
traits for the agent that consumes the tool.

## Compatibility Policy

The multi-action harness uses strategy traits to decide whether actions can
share one ReAct round. The round-level cap is two actions. The compatibility
matrix is ordered and general: candidate action `n` is checked against every
previously accepted action `0..n-1`. A candidate is accepted only if every
prior accepted action allows it by the matrix. With the current cap, action #3
and later are denied categorically before matrix checks and must be retried in
a later round.

```
            later candidate
accepted    exploration  exploitation  neutral  unknown
exploration ok           no            ok       no
exploitation no          ok            ok       no
neutral     ok           ok            ok       no
unknown     no           no            no       no
```

`unknown` means the runtime did not receive a precise strategy for that tool. It
runs alone. It cannot share a round with any other action because the harness
has no causality signal for it.

## Config Shape

`tool_traits` is keyed by the callable name from that connection. The runtime
qualifies it with the connection alias.

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - name: web
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
            alias: web_tools
            allowed: [web_search, web_fetch]
            tool_traits:
              web_search:
                strategy: [exploration]
              web_fetch:
                strategy: [exploration]

          - name: rendering
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.tools.rendering_tools
            alias: rendering_tools
            allowed: [write_pdf]
            tool_traits:
              write_pdf:
                strategy: [exploitation]

          - name: knowledge
            kind: mcp
            server_id: knowledge
            alias: knowledge
            allowed: ["*"]
            tool_traits:
              "*":
                strategy: [exploration]
```

For named-service tools, use the ReAct-facing tool callable names:

```yaml
- name: task_service
  kind: named_service
  alias: named_services
  namespaces:
    task:
      allowed:
        - provider.about
        - object.search
        - object.schema
        - object.upsert
  tool_traits:
    provider_about:
      strategy: [exploration]
    search_objects:
      strategy: [exploration]
    object_schema:
      strategy: [exploration]
    upsert_object:
      strategy: [exploitation]
```

## Catalog Rendering

The tool catalog renders traits as scope metadata:

```
🔧 [1] web_tools.web_search [async]

   Search the web.

   Scope:
       • strategy: exploration

   📥 Parameters:
       • query: str
```

Named-service tools also show applicable namespaces:

```
Scope:
    • namespaces applicable: task, memo
    • strategy: exploration
```

The model sees concrete tool ids and traits. Provider operation ids remain a
configuration/provider protocol detail.

## Runtime Use

During streaming, `RoundActionOverseer` resolves the tool id to traits and
applies compatibility policy before a gate can emit user-visible data.

After streaming, the full parsed multi-action bundle is validated again using
the same trait metadata. This completed parse is a safety backstop; it is not
the mechanism that opens user-visible stream gates.
