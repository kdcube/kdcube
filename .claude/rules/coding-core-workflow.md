# Coding-Core MCP Workflow Rules

## Rule 1: Ping Neo4j First

**BEFORE ANY MCP CALL:** Call `mcp__coding-core__ping` to check Neo4j status.

Only need to ping once per session. If it returned "ok" or "warming" earlier, proceed without re-ping.

If status is "down": remind user to start Neo4j Desktop (bolt port 7690, database `code-core`).

---

## Rule 2: Graph-First Exploration

When asked about code structure, relationships, or "how does X work?":

1. **Start with the graph** — do NOT grep/glob first
2. Use `show_architecture` to orient in a package area
3. Use `class_footprint` to understand a class (inheritance, methods, callers, docs, tests)
4. Use `find_references` to find who uses a symbol
5. Use `trace_call_chain` to follow execution flow
6. Use `code_search` (hybrid mode) for conceptual/keyword questions
7. **Only fall back** to Grep/Glob/Read when graph results are insufficient or you need actual source code

Graph queries return in <200ms vs dozens of tool calls for manual exploration.

---

## Rule 3: Before Modifying Any Public Symbol

Before renaming, deleting, moving, or changing the signature of a class/method/function:

1. Run `impact_analysis` to find all affected code
2. Run `find_references` for full reference picture (callers, subclasses, overrides, tests)
3. Present the impact summary to the user before proceeding
4. If >5 callers are affected, confirm with the user before making changes

---

## Rule 4: Include Documentation Context

When explaining code or answering questions about a class:

1. Use `find_docs_for_code` to find linked documentation
2. KDCube has 2000+ doc sections under `app/ai-app/docs/` — leverage them
3. Include doc references in explanations (the "why" behind the "what")
4. Key doc areas: `docs/sdk/agents/react/` (ReAct agent), `docs/sdk/bundle/` (bundles), `docs/sdk/tools/` (tools/MCP), `docs/service/` (platform services)

---

## Rule 5: Understand Before Editing

When asked to modify code in the KDCube codebase:

1. Run `class_footprint` on the target class first
2. Check `find_siblings` to see if similar classes follow a pattern
3. Check `show_contract` if implementing/extending an abstract class or protocol
4. Read the actual source file only after understanding the graph context

This prevents breaking patterns, missing required interface methods, or duplicating existing functionality.

---

## Rule 6: Re-index After Significant Changes

After creating new classes, moving files, or changing inheritance:

1. Run `index_codebase` (without force_reindex for speed)
2. Use `force_reindex=true` only if graph seems stale or inconsistent
3. Do NOT rely on stale graph data for structural queries after code changes

---

## Rule 7: KDCube Project Structure Awareness

The graph indexes two source roots:
- `app/ai-app/services/kdcube-ai-app/` — main backend (722 classes, 21 infra modules, 8 apps)
- `libs/kdcube-comm/src/` — communication library

Key package prefixes for `show_architecture` filtering:
- `kdcube_ai_app.apps.chat` — chat processing & SDK (ReAct agent, bundles, skills, tools)
- `kdcube_ai_app.apps.middleware` — auth, accounting, gateway
- `kdcube_ai_app.infra` — infrastructure (LLM, Redis, Postgres, channels, config, metrics)
- `kdcube_ai_app.tools` — tool implementations
- `kdcube_ai_app.storage` — storage abstraction
- `kdcube_ai_app.auth` — authentication

---

## Rule 8: Use Graph for Code Reviews and Debugging

When debugging or reviewing:
- `trace_call_chain` to follow the execution path from an entry point
- `find_entry_points` to find HTTP routes and handlers
- `find_references` to check if a suspect method is called from unexpected places
- `class_footprint` to see the full picture of a class including its tests
