---
id: ks:docs/sdk/tools/sdk-tools-README.md
title: "SDK Tools"
summary: "Overview of built-in SDK tool families, including rendering tools, web research, multimodal flows, and accounting-aware runtime integration."
tags: ["sdk", "tools", "runtime", "rendering", "web", "multimodal", "accounting", "citations", "sources"]
keywords: ["sdk tools", "rendering_tools", "web_tools", "sources_pool", "citations", "trusted tools", "multimodal", "accounting", "tool families"]
see_also:
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/citations-system.md
  - ks:docs/sdk/agents/react/source-pool-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
---
# SDK Tools

This document is the high-level map of built-in SDK tool families.

Scope:
- This page explains what the built-in tools are for and how they fit the runtime model.
- For bundle-local authoring and registration, see [Custom Tools](./custom-tools-README.md).
- For descriptor wiring, alias resolution, and isolated supervisor execution, see [Tool Subsystem](./tool-subsystem-README.md).

## What SDK tools are

SDK tools are the built-in callable capabilities exposed through the tool subsystem.

They are part of the trusted runtime surface:
- ReAct and other workflows can call them directly.
- Generated code can reach them only through the supervisor/tool bridge.
- Tool calls remain observable and can participate in provenance, artifacts, and citations.

In practice, the built-in tools are how the runtime exposes common capabilities without forcing each app to reimplement them.

## Main built-in tool families

Common built-in families include:
- `io_tools`
  - file and path operations inside the runtime workdir
- `ctx_tools`
  - context and memory access helpers
- `exec_tools`
  - isolated code execution entrypoints
- `web_tools`
  - web search and web fetch
- `rendering_tools`
  - rendering and artifact generation

Apps can use these directly, combine them with MCP tools, or add their own app-local tools.

## Multimodal runtime integration

The SDK tool model sits inside a runtime that already understands attachments and hosted files.

That matters because tools do not operate only on plain text:
- user attachments can be ingested into the conversation runtime
- hosted files and produced artifacts can be reused later in the same turn or a later turn
- multimodal-capable flows can work with images and PDFs as first-class inputs
- tool instructions can fetch original attachments and pass them forward instead of forcing the model to inline binary payloads

In practice, this means the tool layer can participate in:
- image-aware and document-aware assistant turns
- artifact generation from previously uploaded or generated files
- conversation-scoped provenance where hosted files, attachments, and cited web sources all remain part of the same runtime record

See:
- [Attachments System](../../hosting/attachments-system.md)
- [Source Pool](../agents/react/source-pool-README.md)

## Rendering tools

`rendering_tools` is the built-in artifact rendering family.

Important outputs:
- PDF via `rendering_tools.write_pdf`
- PNG via `rendering_tools.write_png`
- PPTX via `rendering_tools.write_pptx`
- DOCX via `rendering_tools.write_docx`
- HTML via `rendering_tools.write_html`

These tools are important because they turn app output into final deliverables, not just plain text.

### Rendering skills that pair with the tools

The SDK also ships public authoring skills that are designed to work with these renderers:
- `public.pdf-press`
- `public.docx-press`
- `public.pptx-press`
- `public.png-press`
- `public.mermaid`

The split is:
- skills teach the model how to author good input for the renderer
- `rendering_tools.write_*` produces the final artifact

This is especially useful in ReAct flows, where the model can:
1. plan the artifact,
2. draft the content with the right structure,
3. call the rendering tool,
4. return a downloadable artifact tied to the turn timeline.

### Citations and rendering

Rendering tools participate in provenance:
- they extract citation SIDs from the content they render
- they persist `sources_used` for the produced artifact
- clients can later rehydrate citations against the current per-conversation `sources_pool`

See:
- [Citations System](../../citations-system.md)
- [Source Pool](../agents/react/source-pool-README.md)

## Web search and web fetch tools

`web_tools` is the built-in research family.

Main entrypoints:
- `web_tools.web_search`
- `web_tools.web_fetch`

These tools are not just raw HTTP helpers.

### Search / fetch behavior

The built-in web path includes:
- search backend normalization
- URL dedupe and ranking
- adaptive extraction/fetch behavior
- optional objective-aware external refinement

For fetch:
- `web_fetch` can run objective-aware refinement modes such as `balanced`, `recall`, and `precision`
- fetched pages are not simply dumped back unchanged when refinement is enabled

For search:
- `web_search` can fetch pages and run content-based refinement over the fetched set
- the external refinement path can keep the best pages and segment only the relevant spans

### Model-assisted filtering and segmentation

The web research path includes a model-assisted filter/segmenter flow.

That flow is designed to:
- drop clearly irrelevant pages
- reduce boilerplate and chrome
- keep the useful spans for the current objective
- preserve enough context for recall-oriented modes

Implementation-wise, this is the path built around:
- `content_filters_fast.py`
- `filter_segmenter_fast.py`
- adaptive extraction in `web_extractors.py`

The result is that the assistant can work with cleaner evidence instead of raw page dumps.

### Sources pool integration

Research results are captured in the per-conversation `sources_pool`.

That is a core runtime property, not an optional afterthought:
- `web_search` returns canonical source rows that enter the pool
- `web_fetch` returns canonical fetched rows that enter the pool
- the pool is deduped and SID-stable within the conversation
- citations and later reads refer back to those SIDs

This is why web research in KDCube is more than “search plus scrape”:
- the fetched evidence becomes part of the conversation-scoped provenance model
- later outputs, citations, and artifacts can refer back to the same source IDs

See:
- [Citations System](../../citations-system.md)
- [Source Pool](../agents/react/source-pool-README.md)

## Accounting-aware tool execution

Built-in tools participate in the same accounting and economics model as the rest of the platform.

That is important because usage is not reconstructed later from best-effort logs:
- LLM calls are tracked
- embedding calls are tracked
- web search usage is tracked
- turn-level accounting scopes can be attached around higher-level runtime steps

For tool-backed research and generation flows, that means:
- metered service usage stays attached to real turns, users, and conversations
- economics limits can act on current usage instead of delayed reporting
- operators can inspect usage with the same accounting data model used by the runtime

This is one reason KDCube tools fit the broader product runtime story:
- trusted capabilities are exposed through the tool layer
- multimodal inputs and hosted artifacts stay in the same runtime record
- accounting and economics stay attached to the same turn and conversation lifecycle

See:
- [Accounting](../../accounting/accounting-README.md)
- [Economics](../../economics/economic-README.md)

## Trusted tools and isolated generated code

The built-in tools belong to the trusted tool layer.

That matters for execution:
- generated code runs in isolated execution paths
- generated code does not get arbitrary network or secret access
- when generated code needs a trusted capability, it reaches it through the tool bridge

This is the same runtime boundary used for:
- web research
- artifact rendering
- app-local modular tools
- MCP tools

## When to read the other docs

Read [Custom Tools](./custom-tools-README.md) when you want to:
- add app-local tool modules
- register tools in `tools_descriptor.py`
- set per-tool runtime overrides

Read [Tool Subsystem](./tool-subsystem-README.md) when you want to:
- understand `TOOLS_SPECS`, `MCP_TOOL_SPECS`, and `TOOL_RUNTIME`
- understand how tools stay available in isolated runtimes
- understand supervisor routing and tool IDs

Read [ReAct Tools](../agents/react/react-tools-README.md) when you want to:
- understand how tools are surfaced to the ReAct runtime
- understand planner/generator usage patterns
