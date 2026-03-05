---
id: ks:docs/sdk/tools/custom-tools-README.md
title: "Custom Tools"
summary: "How to author bundle-local tools and register them in tools_descriptor.py with module/ref entries and consumer runtime wiring."
tags: ["sdk", "tools", "custom", "bundle", "descriptor", "semantic-kernel", "authoring"]
keywords: ["tools_descriptor.py", "TOOLS_SPECS", "module", "ref", "alias", "kernel_function", "create_tool_subsystem_with_mcp", "tool_call", "TOOL_RUNTIME", "MCP_TOOL_SPECS"]
see_also:
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/tools/mcp-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/exec/README-runtime-modes-builtin-tools.md
---
# Custom Tools (Bundle-Local)

This guide covers authoring and registration of bundle-local tools.

For runtime internals (descriptor resolution, supervisor flow, isolated execution), see [Tool Subsystem](./tool-subsystem-README.md).

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

## 2) Register tools in `tools_descriptor.py`

```python
TOOLS_SPECS = [
    {"module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools", "alias": "web_tools", "use_sk": True},
    {"ref": "tools/local_tools.py", "alias": "doc", "use_sk": True},
]
```

Notes:
- `module` points to installed Python modules.
- `ref` is relative to bundle root (portable across host and isolated runtimes).
- `alias` becomes the tool ID prefix.

## 3) Wire descriptor in workflow

Your workflow must pass descriptor values into subsystem creation:

```python
tool_subsystem, _ = create_tool_subsystem_with_mcp(
    service=self.model_service,
    comm=self.comm,
    logger=self.logger,
    bundle_spec=self.config.ai_bundle_spec,
    context_rag_client=self.ctx_client,
    registry={"kb_client": self.kb},
    raw_tool_specs=tools_descriptor.TOOLS_SPECS,
    tool_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
    mcp_tool_specs=getattr(tools_descriptor, "MCP_TOOL_SPECS", []),
    mcp_env_json=os.environ.get("MCP_SERVICES") or "",
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

## 5) Optional per-tool runtime overrides

```python
TOOL_RUNTIME = {
    "doc.search": "local",
    "web_tools.web_search": "local",
    "rendering_tools.write_pdf": "docker",
    "exec_tools.python_exec": "fargate",
}
```

If a tool is not listed, default runtime policy applies.

## 6) Optional MCP tool sources

```python
MCP_TOOL_SPECS = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
]
```

For MCP transport/auth/runtime details, see [MCP Integration](./mcp-README.md).

## Example references

- [with-isoruntime bundle](../../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00)
- [with-isoruntime `tools_descriptor.py`](../../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools_descriptor.py)
- [with-isoruntime `tools/local_tools.py`](../../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools/local_tools.py)
- [react.doc `tools_descriptor.py`](../../../services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react.doc@2026-03-02-22-10/tools_descriptor.py)
