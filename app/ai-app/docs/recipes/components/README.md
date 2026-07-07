---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/components/README.md
title: "Component Recipes"
summary: "Short recipes for composing KDCube app components, with links to the canonical architecture and provider contracts."
status: current
tags: ["recipes", "components", "scene", "chat", "pinboard", "named-services", "ecosystem"]
updated_at: 2026-06-23
keywords:
  [
    "component recipes",
    "ecosystem component",
    "scene recipe",
    "named service recipe",
    "pinboard recipe",
    "chat recipe",
    "app as service provider",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
---
# Component Recipes

These recipes are short implementation entry points. They should not duplicate
the full architecture.

Read order for app builders:

```text
1. Architecture Of What You Build
2. Components Ecosystem Architecture
3. Ecosystem Component Contract
4. Namespace Services, only if the app should expose a provider realm
5. The specific recipe below
```

## Recipes

| Recipe | Use when |
| --- | --- |
| [Your Component](your-component-README.md) | You are adding a new app/widget and need to choose which ecosystem planes it uses. |
| [Named-Service App](named-service-README.md) | You want a regular app/domain realm to become an agent-usable service provider. |
| [Scene](scene-README.md) | You are composing multiple widgets/surfaces into one browser scene. |
| [Scene Surface Command](scene-surface-command-README.md) | One widget should summon and direct another (declare the contract, receive + ack, emit with fallback). |
| [Canvas Pinboard](pinboard-README.md) | You want objects from different realms pinned, searched, moved, opened, and reused as context. |
| [Chat Widget](chat-README.md) | You are embedding a ReAct chat surface that attaches, emits, and consumes context. |
| [Chat With A ReAct Agent](chat-with-react-agent-README.md) | You are declaring the agent behind the chat — per-agent config incl. `supported_models` and the tools/skills inventory — and letting users customize it via the composer menu. |

## Planes Checklist

```text
Does your component need:
  API / REST                 -> expose @api
  MCP                         -> expose @mcp
  Event Bus / SSE             -> emit/claim service events
  Data Bus                    -> implement durable @data_bus_handler
  Cron / scheduled jobs       -> use @cron for due scan, @on_job for work
  Named services              -> own namespace/object refs and provider ops
  UI scene                    -> expose widget route + target_surface commands
  ReAct visibility            -> object.get + block.produce/render policies
  Pinboard compatibility      -> object.resolve/action + presentation config
```

Use named services when the agent or generic UI should understand a domain
realm without hardcoding it. Use API/MCP/Data Bus/cron directly when the app
only needs normal service integration.
