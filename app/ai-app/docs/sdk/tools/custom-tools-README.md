---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
title: "Custom Tools"
summary: "How to author bundle-local tools and expose them through agent-scoped surfaces.as_consumer config with module/ref entries and runtime wiring."
tags: ["sdk", "tools", "custom", "bundle", "semantic-kernel", "authoring", "runtime"]
keywords: ["surfaces.as_consumer", "agent tool config", "module", "ref", "alias", "kernel_function", "create_tool_subsystem_with_mcp", "tool_call", "_SERVICE", "_INTEGRATIONS", "KV_CACHE", "get_comm"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/online-strategic-governance-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/streaming/governed-streaming-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/block-production-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-index-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-runtime-modes-builtin-tools.md
---
# Custom Tools (Bundle-Local)

This guide covers authoring and registration of bundle-local tools.

For runtime internals (config resolution, supervisor flow, isolated execution), see [Tool Subsystem](./tool-subsystem-README.md).

## 1) Write a tool module

Tools are regular Python callables (usually async) with Semantic Kernel metadata:

```python
from typing import Annotated
import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

@kernel_function(name="search", description="Search bundle knowledge")
async def search(query: Annotated[str, "Search query"], n: Annotated[int, "Max results"] = 5):
    ...
```

## 2) Connect tools to an agent

Declare the model-facing tool connection under bundle props:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: docs
            kind: python
            ref: tools/local_tools.py
            alias: doc
            discovery: semantic_kernel
            allowed:
              - search
```

For a bundle with multiple agents, give each agent its own list:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: automations
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.solutions.automations.tools
            alias: automations
            allowed: [list_automations, search_automations, get_automation, create_automation]
      automation_job:
        tools:
          - id: automation_job
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.solutions.automations.job_tools
            alias: automation_job
            allowed: [get_current_automation, update_execution_journal]
```

Notes:
- `module` points to installed Python modules.
- `ref` is relative to bundle root (portable across host and isolated runtimes).
- `alias` becomes the tool ID prefix.
- `allowed` is the exact callable allow-list for that agent.
- `discovery` defaults to `semantic_kernel`; only `@kernel_function` tools
  should be published to the model catalog for Python sources.

## 2.1) Make ReAct tools governable with strategy traits

If a tool is visible to a ReAct agent, document its strategic role with the
`strategy` trait. This is not only catalog decoration. The ReAct harness uses
the trait during online stream governance to decide whether same-round moves
can safely share a round.

Governed tools reduce invalid multi-action rounds:

```text
tool has strategy trait
  -> catalog shows the trait
  -> stream overseer can classify the move early
  -> compatible moves pass
  -> incompatible later moves are interrupted before large wrong payloads finish
```

If a tool has no strategy trait, the runtime treats it as `unknown`. Unknown
tools run alone because the harness cannot prove same-round compatibility.
That is safe, but it causes more dropped actions and retries for reactive
agents.

Use the detailed policy docs for the full matrix and stream interruption path:

- [Tool Strategy Traits](../solutions/multi-action/tool-strategy-traits-README.md)
- [ReAct Online Strategic Governance](../agents/react/online-strategic-governance-README.md)

### Strategy values

```text
exploration   reads, searches, fetches, inspects, pulls
exploitation  writes, patches, renders, upserts, deletes, hosts files
neutral       bookkeeping that does not change the answer premise
unknown       omitted or undiscovered strategy; runs alone
```

### Mark a bundle-local Python tool in code

Use `@tool_trait(...)` when the tool has an intrinsic strategy independent of
deployment:

```python
from typing import Annotated

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import tool_trait

@tool_trait(strategy=["exploration"])
@kernel_function(name="search", description="Search bundle knowledge")
async def search(query: Annotated[str, "Search query"], n: Annotated[int, "Max results"] = 5):
    ...
```

Decorator traits travel with the discovered tool metadata.

### Override or assign traits in the consumer config

Use `tool_traits` under the agent tool connection when the strategy is a
deployment/agent policy or when the source is external:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: docs
            kind: python
            ref: tools/local_tools.py
            alias: doc
            discovery: semantic_kernel
            allowed: [search, export_report]
            tool_traits:
              search:
                strategy: [exploration]
              export_report:
                strategy: [exploitation]
```

Consumer config is authoritative for the active agent. It can add traits to
tools that cannot be decorated and can override decorator-provided traits.

### Mark connected MCP tools

MCP tools cannot use Python decorators. Mark them on the MCP connection:

```yaml
- id: knowledge
  kind: mcp
  server_id: knowledge
  alias: knowledge
  allowed: ["*"]
  tool_traits:
    "*":
      strategy: [exploration]

- id: browser
  kind: mcp
  server_id: browser
  alias: browser
  allowed: [open_page, click, close]
  tool_traits:
    open_page:
      strategy: [exploration]
    click:
      strategy: [exploration, exploitation]
    close:
      strategy: [neutral]
```

`"*"` applies to all tools from that connection. Prefer concrete tool names
when the server exposes mixed read/write behavior.

### Mark named-service tools

Named-service `allowed` entries use provider operation ids, but `tool_traits`
uses the concrete ReAct-facing tool callable names:

```yaml
- id: task_service
  kind: named_service
  alias: named_services
  namespaces:
    task:
      allowed:
        - provider.about
        - object.search
        - object.schema
        - object.upsert
        - object.host_file
  tool_traits:
    provider_about:
      strategy: [exploration]
    search_objects:
      strategy: [exploration]
    object_schema:
      strategy: [exploration]
    upsert_object:
      strategy: [exploitation]
    host_file:
      strategy: [exploitation]
```

See [Named Services Tools](named-services-tools-README.md) for the operation
to callable-name mapping.

Runtime code resolves this config with the SDK helper. The config remains the
policy source for which tools an agent sees:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    agent_tool_config_from_bundle_props,
)

tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    agent_id,
    bundle_root=BUNDLE_ROOT,
    default_agent_id="main",
)
```

Code defaults may seed `surfaces.as_consumer` when no deployment config exists.
Keep those defaults in the bundle, because they are bundle policy. SDK helpers
only merge defaults and adapt the effective config into runtime specs. Legacy
`tools.agents` is still read for old bundles only.

### Bundle-local imports from `ref` tools

Bundle-local tool modules may import same-bundle helpers with package-relative
imports:

```python
# tools/local_tools.py
from ..services.storage import MemoryStore
from ..resources.prompts import SEARCH_PROMPT
```

Do not use top-level bundle-local imports such as `from services...`,
`from tools...`, or `import resources`. Proc loads multiple bundles in one
Python process, so those top-level names are process-global and can collide
with another bundle.

Keep the bundle root and package directories importable by including
`__init__.py` files, for example:

```text
my-bundle@1-0/
  __init__.py
  tools/
    __init__.py
    local_tools.py
  services/
    __init__.py
    storage.py
```

The `ref` loader preserves this package context in normal in-process execution
and in isolated supervisor execution. Use `module` only for installed Python
modules outside the bundle.

## 3) Wire resolved config in workflow

Your workflow must resolve the active agent config, pass specs into subsystem
creation, and pass both alias and per-tool allow-lists to ReAct:

```python
tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    agent_id,
    bundle_root=BUNDLE_ROOT,
    default_agent_id="main",
)

tool_subsystem, _ = create_tool_subsystem_with_mcp(
    service=self.model_service,
    comm=self.comm,
    logger=self.logger,
    bundle_spec=self.config.ai_bundle_spec,
    context_rag_client=self.ctx_client,
    registry={"kb_client": self.kb},
    raw_tool_specs=tool_config.tool_specs,
    tool_runtime=tool_config.tool_runtime,
    mcp_tool_specs=tool_config.mcp_tool_specs,
    mcp_env_json=os.environ.get("MCP_SERVICES") or "",
)

sr = await react.run(
    allowed_plugins=tool_config.allowed_plugins,
    allowed_tool_names_by_alias=tool_config.allowed_tool_names_by_alias,
)
```

## 4) Tool IDs and generated-code calls

Tool IDs are `<alias>.<function_name>`.

Generated code should execute tools through `agent_io_tools.tool_call(...)`:

```python
resp = await agent_io_tools.tool_call(
    fn=doc.search,
    params={"query": "KDCube architecture", "n": 5},
    call_reason="Find product architecture details",
    tool_id="doc.search",
)
```

## 4.1) Tool Result Shape

Custom tools can return either a plain structured result or the managed
`ret` wrapper. Do not force every domain result into `ret`.

Plain structured result:

```json
{
  "ok": true,
  "object_ref": "mem:mem_123",
  "memory": {"id": "mem_123", "memory": "Use matplotlib PNGs inside xlsx charts."}
}
```

Managed wrapper:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "results": [
      {"object_ref": "doc:123", "title": "Document 123"}
    ]
  }
}
```

ReAct unwraps only explicit wrappers that contain `ret`.

Protocol rule:

```text
plain result body   = the top-level returned dict
wrapped result body = the dict stored under ret
```

A plain structured result that contains `ok` remains the result body seen by
event-source policies. This matters for domain tools such as memory, task,
canvas, and search tools where fields like `object_ref`, `memory`, `rows`,
`results`, or `attachments` are part of the tool's own schema.

Use a plain structured result when the tool owns a domain schema:

```json
{"ok": true, "object_ref": "task:issues/issue_2026...", "issue": {...}}
```

Use the managed wrapper when a runtime helper already returns it, or when the
tool intentionally needs a generic `ret` container separate from the domain
schema.

Failure results should be explicit. Either shape is valid:

```json
{"ok": false, "error": "memory_not_found", "message": "Memory was not found"}
```

```json
{"ok": false, "error": {"code": "upstream_timeout", "message": "Timed out"}, "ret": null}
```

The important rule is that the event-source policies for the tool must know
which result schema they consume.

## 4.2) Declaring Files For React Hosting

If a custom tool intentionally produces files that should be delivered to the
user, use the declared-file result protocol.

Protocol rule:

```text
artifact_type is not a multi-value artifact taxonomy.
artifact_type has one supported declared-file value: "files".
artifact_type: "files" means: this result body declares files[] for React hosting.
```

With the managed wrapper this means `ret.artifact_type == "files"`:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "path": "turn_123/outputs/invoices/invoice.pdf",
        "physical_path": "turn_123/outputs/invoices/invoice.pdf",
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12345,
        "visibility": "external"
      }
    ]
  }
}
```

With a plain structured result, put the same marker on the top-level result
body:

```json
{
  "ok": true,
  "artifact_type": "files",
  "files": [
    {
      "type": "file",
      "path": "turn_123/outputs/invoices/invoice.pdf",
      "physical_path": "turn_123/outputs/invoices/invoice.pdf",
      "filename": "invoice.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 12345,
      "visibility": "external"
    }
  ]
}
```

The `react.block_production.declared_file_items` policy recognizes this exact
shape on the result body. Each declared file is copied into the conversation
store and receives hosted artifact metadata for transport. User-facing object
identity should remain the logical artifact ref (`fi:...`), not transport
handles such as renderer names or download URLs.

This contract works for one file or many files. The marker and container are
strict: use `artifact_type: "files"` and `files[]` on the result body.

Use this only for deliberate file-producing tools. For example, a tool that
materializes a requested email attachment can declare that attachment for
hosting. A tool that merely returns message metadata should not do so.

Do not use this shape for a tool that has several independent result surfaces
such as files plus search rows plus snapshot refs. For that case, use a
composite result and declare the event-source policies that consume each field.

### File vs Artifact Surfaces

Client placement is driven by explicit event/artifact surfaces, not by
namespace names and not by incidental fields inside a row. Client reducers
project incoming packages into artifact objects with one of these UI surfaces:
`files`, `artifacts`, `links`, or `timeline`. A row with `object_ref`, `mime`,
or `filename` is not a downloadable file unless it came through a file surface.
Anything written only to the timeline remains `surface="timeline"` and must not
be counted in the Artifacts tab.

Full surface routing:

```text
Tool/provider result
  |
  |-- ret.artifact_type == "files" + ret.files[]
  |     -> declared-file block production
  |     -> hosted file metadata
  |     -> chat.files event
  |     -> client FileArtifact
  |     -> Files tab / transport download handling
  |
  |-- named_service.search_results subsystem artifact
  |     -> client NamedServiceSearchArtifact
  |     -> Artifacts tab / timeline search block
  |     -> click/drag routes object_ref through object.action
  |
  |-- canvas/write artifact blocks
  |     -> client CanvasArtifact with surface="artifacts"
  |     -> Artifacts tab, plus a separate timeline entry when streamed
  |
  |-- marker="timeline_text" / answer notes / final answers
  |     -> client TimelineArtifact with surface="timeline"
  |     -> Timeline/chat feed only
  |
  |-- citation/source rows with public URLs
        -> citation/link artifacts
        -> Links tab / citation UI
```

Compact version:

```text
ret.artifact_type="files"       -> FileArtifact(surface=files)               -> Files
named_service.search_results    -> NamedServiceSearchArtifact(surface=artifacts) -> Artifacts
canvas/write artifact           -> CanvasArtifact(surface=artifacts)         -> Artifacts
marker="timeline_text"/answers  -> TimelineArtifact(surface=timeline)        -> Timeline/chat
public cited URL rows           -> LinkArtifact(surface=links)               -> Links
```

This is why search results, memory records, task issues, and other provider
objects must stay object artifacts. They may be clickable or draggable in a
capable client, but they are not downloadable files unless a separate file
surface explicitly declares or hosts bytes.

## 4.3) Multi-Surface Tool Results

There is no protocol where `artifact_type` contains multiple values such as
`["files", "snapshot"]`. Multi-surface tools return a structured result body
with named fields, and event-source policies decide which fields are consumed.

Current standard block-production policies consume these fields:

| Policy | Result body fields consumed |
| --- | --- |
| `react.block_production.declared_file_items` | `artifact_type: "files"` plus `files[]` |
| `react.block_production.hosted_artifacts` | `hosted_artifacts[]`, `artifact_rows[]`, `files[]`; also accepts `artifact_type: "files"` plus `files[]` |
| `react.block_production.snapshot_refs` | `snapshot_ref`, `snapshot_refs[]`, `snapshots[]` |
| `react.block_production.announce_candidates` | `announce_candidate`, `announce_entry`, `announce_candidates[]`, `announce_entries[]` |
| `react.block_production.exploration_results` | `exploration_results[]`, `source_rows[]`, `items[]`, `results[]` when rows are source-like |

Policy pack behavior is fixed:

| Policy pack | Included standard policies |
| --- | --- |
| `structured_result_source_policies()` | default tool block, generic result item, declared file items |
| `exploration_source_policies()` | default tool block, exploration result rows, generic result item |
| `write_tool_source_policies()` | rendering-tool input validation, write-tool result, declared file items |
| `composite_artifact_source_policies()` | default tool block, hosted artifacts, snapshot refs, announce candidates |

If a tool needs a combination that is not in one policy pack, declare the exact
policy list for that event source. For example, a tool that returns search rows
and also snapshot refs must include both:

```python
@event_source(
    event_source_id="{alias}.inspect",
    policies=[
        {"react_phase": "block_production", "event_policy_id": "react.block_production.tool_default"},
        {"react_phase": "block_production", "event_policy_id": "react.block_production.exploration_results"},
        {"react_phase": "block_production", "event_policy_id": "react.block_production.snapshot_refs"},
        {"react_phase": "block_production", "event_policy_id": "react.block_production.generic_result_item"},
        {"react_phase": "timeline_projection", "event_policy_id": "react.timeline_projection.identity"},
        {"react_phase": "compaction_projection", "event_policy_id": "react.compaction_projection.identity"},
    ],
    kind="react.tool",
)
```

Example plain multi-surface result body:

```json
{
  "ok": true,
  "results": [
    {"url": "https://example.test/a", "title": "A", "content": "Excerpt"}
  ],
  "snapshot_refs": [
    "fi:turn_2026-06-09-12-00-00-000.snapshots/report/current.json"
  ],
  "announce_candidates": [
    {"title": "Report state", "summary": "Snapshot was refreshed."}
  ]
}
```

Example wrapped multi-surface result body:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "hosted_artifacts": [
      {"filename": "report.pdf", "mime": "application/pdf", "hosted_uri": "..."}
    ],
    "snapshot_refs": [
      "fi:turn_2026-06-09-12-00-00-000.snapshots/report/current.json"
    ]
  }
}
```

Example bundle-local tool:

```python
from pathlib import Path
from typing import Annotated

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import ok, scope


@kernel_function(name="export_report", description="Create a report file")
async def export_report(
    filename: Annotated[str, "Output filename"] = "report.txt",
):
    sc = scope()
    turn_id = sc["turn_id"]
    outdir = Path(sc["outdir"])
    rel = Path(turn_id) / "outputs" / "reports" / filename
    target = outdir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello\n", encoding="utf-8")

    return ok({
        "artifact_type": "files",
        "files": [{
            "type": "file",
            "path": rel.as_posix(),
            "physical_path": rel.as_posix(),
            "filename": filename,
            "mime_type": "text/plain",
            "size_bytes": target.stat().st_size,
            "visibility": "external",
        }],
    })
```

Path rules:
- `path` / `physical_path` should be relative to the current React `OUT_DIR`.
- Use `turn_<id>/outputs/...` for tool-generated outputs.
- Use `visibility: "external"` only when the file is intended for user delivery.
- Do not inline large binary payloads in `ret`; write the file and return a path.

### Hosting From The Tool

Trusted bundle tools can host current-turn files themselves through
`bundle_tool_context.host_files`. The helper uses the active conversation scope
and returns a declared-file result body with hosted fields already filled in:

```python
from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import host_files, ok


@kernel_function(name="export_report", description="Create and host a report")
async def export_report():
    # Write the file under the current OUT_DIR first.
    hosted = await host_files([
        {
            "path": "turn_123/outputs/reports/report.pdf",
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "visibility": "external",
        }
    ])
    return ok(hosted)
```

`host_files(...)` returns:

```json
{
  "artifact_type": "files",
  "files": [
    {
      "type": "file",
      "hosted": true,
      "emitted": true,
      "hosted_uri": "...",
      "key": "...",
      "rn": "...",
      "filename": "report.pdf",
      "mime_type": "application/pdf"
    }
  ],
  "hosted_count": 1,
  "emitted": true
}
```

React records those rows as declared files and does not host them again.

Runtime availability:
- normal React tool calls run with the workflow `ToolSubsystem`, so
  `host_files(...)` can use the workflow hosting service directly.
- in-memory tool execution uses the same binding contract.
- isolated tool execution rebuilds the communicator, conversation store, and
  hosting-capable `ToolSubsystem` in the trusted supervisor/runtime bootstrap,
  so a catalog tool running there can also call `host_files(...)`.
- generated executor code reaches file hosting by calling a catalog tool through
  `agent_io_tools.tool_call(...)`; the catalog tool is the trusted boundary that
  writes files and calls `host_files(...)`.

Runtime context required:
- `host_files(...)` requires a bound `ToolSubsystem` with a hosting service.
- the bound communicator must carry enough conversation scope to place the
  artifact: tenant, project, user id, conversation id, turn id, and usually
  user type.
- the runtime must also provide conversation storage and an output directory
  where the declared file path is readable.
- the model should not pass those runtime ids as tool parameters; they must be
  prepared by the SDK runtime before the tool runs.

Normal ReAct workflows get this preparation when `BaseWorkflow.build_react(...)`
creates the `ToolSubsystem` with the workflow `ApplicationHostingService`.
Cached workflows refresh the request-bound communicator through
`BaseWorkflow.rebind_request_context(...)`.

Isolated execution gets the same preparation from
`kdcube_ai_app.apps.chat.sdk.runtime.bootstrap.bootstrap_bind_all(...)`. That
bootstrap restores runtime context, builds the communicator, recreates the
conversation hosting service, creates the tool subsystem, and binds the tool
modules. A custom isolated runner must call the SDK bootstrap or perform the
same preparation before a trusted tool can call `host_files(...)`.

If this preparation is missing, `host_files(...)` fails fast. Typical failures
are:
- `RuntimeError("tools are not bound to the current tool subsystem")`
- `RuntimeError("tool hosting service is unavailable")`
- `RuntimeError("tool communicator is unavailable")`
- `RuntimeError("bundle storage root is unavailable")`

Missing tenant, project, user id, conversation id, or turn id should be treated
as a runtime-preparation bug. Do not ask the LLM to invent those values.

Direct returned declarations and tool-side hosting are equivalent from React's
point of view. A tool may either return `artifact_type: "files"` and `files[]`
on the result body with local paths, or call `host_files(...)` and return a
result body with already-hosted file rows.

## 4.4) ReAct Event-Source Policies

ReAct can run an event-source policy pipeline for tool results. The pipeline is
controlled by `RuntimeCtx.event_source_pipeline_enabled` and can be enabled per
bundle:

```yaml
config:
  react:
    event_source_pipeline:
      enabled: true
```

A custom tool is a tool-backed event source when it runs inside ReAct. It is
still implemented and called as a normal tool; the event-source declaration
adds policy metadata for validation, block production, timeline projection,
ANNOUNCE production, and compaction projection.

The event-source policy owns how the result becomes ReAct context:

```text
tool call
  -> result body: top-level dict, or ret dict when the return is a ret wrapper
  -> block production policy
  -> timeline / ANNOUNCE / compaction policies
```

This is how tools produce search rows, canvas-aware payloads, task/story
context, memory facts, file artifacts, and other structured surfaces without
hard-coding those shapes into the generic runtime.

When the flag is enabled, custom tools continue to work even when they do not
declare event-source metadata. The default fallback is the structured-result
policy pack:

```text
react.block_production.tool_default
react.block_production.generic_result_item
react.block_production.declared_file_items
react.timeline_projection.identity
react.compaction_projection.identity
```

That fallback mirrors the old `external.py` behavior for ordinary custom tools:
- JSON/text results are rendered as ordinary `tc:<turn>.<call>.result`
  `react.tool.result` blocks.
- Errors become ordinary tool-result/error notices.
- `artifact_type: "files"` plus `files[]` on the result body still produces
  declared file artifacts.
- Generic JSON results are not treated as files just because the artifact id is
  the tool id.
- No source-pool rows, snapshots, or ANNOUNCE entries are produced unless the
  tool declares policies that produce those surfaces.

### Declaring a custom event source

If a tool needs custom ReAct behavior, decorate the tool-backed event source
with `@event_source`:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import structured_result_source_policies

@event_source(
    event_source_id="{alias}.search",
    policies=structured_result_source_policies(),
    description="Search bundle records and return structured result rows.",
    kind="react.tool",
)
@kernel_function(name="search", description="Search bundle records")
async def search(query: Annotated[str, "Search query"], n: int = 5):
    ...
```

`{alias}` resolves to the alias from the active Python tool spec, so a tool
connection such as `ref: tools/local_tools.py` with `alias: doc` produces the
event source id `doc.search`.

The event source id is the semantic policy key. For tool-backed events:

```text
event_source_id == tool_id
event_id        == tool_call_id
```

### Declaring policy handlers

Policy handlers are normal Python functions registered with a ReAct phase:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import block_production_policy

@block_production_policy(event_policy_id="doc.block_production.search_results")
def search_results_policy(target, **context):
    rows = (target.get("ret") or {}).get("results") or []
    target.setdefault("source_rows", []).extend(rows)
    target["source_rows_merge"] = True
    return target
```

Then bind the policy from the event-source declaration:

```python
@event_source(
    event_source_id="{alias}.search",
    policies=[
        {"react_phase": "block_production", "event_policy_id": "react.block_production.tool_default"},
        {"react_phase": "block_production", "event_policy_id": "doc.block_production.search_results"},
        {"react_phase": "block_production", "event_policy_id": "react.block_production.generic_result_item"},
        {"react_phase": "timeline_projection", "event_policy_id": "react.timeline_projection.identity"},
        {"react_phase": "compaction_projection", "event_policy_id": "react.compaction_projection.identity"},
    ],
    kind="react.tool",
)
```

Use the built-in policy packs when they fit:
- `structured_result_source_policies()` for ordinary JSON/text result tools and
  declared file rows.
- `exploration_source_policies()` for search/fetch-like tools that should merge
  rows into `sources_pool`.
- `write_tool_source_policies()` for rendering/write tools where `params.path`
  is the produced artifact.
- `composite_artifact_source_policies()` for tools that return multiple
  surfaces such as hosted artifacts, snapshot refs, and announce candidates.

### Event modules without decorating the tool

Policies can also live in a separate event-source module. This is useful when
the callable tool is remote, generated, or owned by another package. Pass the
module through `event_source_specs` when calling `BaseWorkflow.build_react(...)`,
or through `event_specs` when creating a `ToolSubsystem` directly. The module
can return declarations from `list_event_sources()`.

```python
def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="doc.search",
            policies=[...],
            kind="react.tool",
        )
    ]
```

The source id must still match the runtime tool id if the policy is meant to
handle that tool's result.

Do not use tool visibility for event-only needs. Agent tool config grants the
model permission to call functions. Event-source specs grant runtime event
visibility: policies, event-source readers, and namespace rehosters. For
example, if a bundle only needs `react.pull(paths=["cnv:..."])` to materialize
canvas-owned refs, pass the canvas event resolver through `event_source_specs`;
do not add the canvas tool module to the agent tool config unless the model
should be able to call `canvas.patch`.

## 5) What a tool module receives at runtime

Tool modules are bound centrally by the runtime before use.

That happens in both:
- the normal in-process tool subsystem
- the isolated execution bootstrap path

Tool code should treat that runtime context as already prepared. Do not try to
construct it yourself.

### Optional bind hooks

If the module defines these hooks, the runtime calls them during binding:
- `bind_service(svc)`
- `bind_registry(registry)`
- `bind_integrations(integrations)`

These are optional convenience hooks. The runtime also stamps canonical globals
onto the module so tools can read the same names in both proc and isolated
execution.

### Canonical globals available in tool modules

| Name | Meaning |
| --- | --- |
| `_SERVICE` / `SERVICE` | The current `ModelServiceBase` for model/router access |
| `model_service` | Same service, under a more explicit name |
| `_INTEGRATIONS` / `INTEGRATIONS` | Small integration map prepared by the runtime |
| `_TOOL_SUBSYSTEM` / `TOOL_SUBSYSTEM` | Current `ToolSubsystem` instance when available |
| `_COMMUNICATOR` / `COMMUNICATOR` | Current `ChatCommunicator` when request context exists |
| `_KV_CACHE` / `KV_CACHE` | Shared KV cache handle when configured |
| `_CTX_CLIENT` / `CTX_CLIENT` | Current `ContextRAGClient` when present |
| `REGISTRY` | Registry objects explicitly provided by the workflow |

Current `INTEGRATIONS` content is intentionally small:
- `ctx_client`
- `kv_cache`
- `tool_subsystem`

Do not assume arbitrary host objects are present there unless your workflow
explicitly provides them.

### Communicator inside tools

Tools can emit progress or user-facing events through the normal chat
communicator.

The preferred read paths are:
- `_COMMUNICATOR` / `COMMUNICATOR` if your module wants a direct bound global
- `from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm`

Important current behavior:
- when a tool is used inside a chat turn, communicator usually has request and
  session context
- when the originating request carried an exact socket/stream target, direct
  peer delivery is possible
- when the originating path only carried `session_id`, emits fan out to the
  whole session room

For the request-path details, see [Bundle Runtime](../bundle/bundle-runtime-README.md).

### Shared browser and other importable runtime services

Some facilities are not stamped as globals, but are still safe to use from tool
modules as normal SDK services. For example:

```python
from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

browser = await get_shared_browser()
```

This is the same shared browser service used by rendering tools such as
`rendering_tools` / `md2pdf_async`.

### Example pattern

```python
_SERVICE = None
_INTEGRATIONS = None

def bind_service(svc):
    global _SERVICE
    _SERVICE = svc

def bind_integrations(integrations):
    global _INTEGRATIONS
    _INTEGRATIONS = integrations or {}

async def my_tool(...):
    cache = (_INTEGRATIONS or {}).get("kv_cache")
    comm = (_INTEGRATIONS or {}).get("tool_subsystem").comm if (_INTEGRATIONS or {}).get("tool_subsystem") else None
    ...
```

If you prefer not to read from `_INTEGRATIONS`, reading `_KV_CACHE`,
`_COMMUNICATOR`, or `get_comm()` is also valid.

## 6) Optional per-tool runtime overrides

```python
TOOL_RUNTIME = {
    "doc.search": "local",
    "web_tools.web_search": "local",
    "rendering_tools.write_pdf": "docker",
    "exec_tools.python_exec": "fargate",
}
```

If a tool is not listed, default runtime policy applies.

## 6.1 Custom dependencies for bundle-local tools

Bundle-local tools do not currently get their own `requirements.txt` install
step from the tool subsystem.

Current behavior:
- a custom tool module is imported into the current execution runtime
- for in-process execution, that means the proc interpreter
- for isolated execution, that means the isolated runtime / supervisor
- therefore any third-party package imported directly by the tool module must
  already be installed in that runtime

What is supported today:
- if the tool needs a package-heavy leaf operation, keep the tool function
  lightweight and call a bundle-local helper marked with `@venv(...)`
- that helper can then use a bundle-managed cached subprocess venv derived from
  the runtime plus the bundle's `requirements.txt`

What is **not** supported today:
- automatic per-tool dependency installation from agent tool config
- automatic bundle-tool `requirements.txt` resolution separate from
  `@venv(...)`

Practical recommendation:
- direct imports in the tool module only for dependencies already present in
  the runtime image
- use `@venv(...)` only for dependency-heavy leaf work
- keep request-bound objects such as communicator, DB pools, and Redis clients
  outside that helper boundary

## 7) Optional MCP Tool Sources

Expose MCP tools under the same agent-scoped config surface:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: docs
            kind: mcp
            server_id: docs
            alias: docs
            allowed: ["*"]
```

For MCP transport/auth/runtime details, see [MCP Integration](./mcp-README.md).

## Example references

- [with-isoruntime bundle](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00)
- [with-isoruntime `tools/local_tools.py`](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools/local_tools.py)
