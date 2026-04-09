---
id: ks:docs/sdk/tools/custom-tools-README.md
title: "Custom Tools"
summary: "How to author bundle-local tools and register them in tools_descriptor.py with module/ref entries and consumer runtime wiring."
tags: ["sdk", "tools", "custom", "bundle", "descriptor", "semantic-kernel", "authoring", "runtime"]
keywords: ["tools_descriptor.py", "TOOLS_SPECS", "module", "ref", "alias", "kernel_function", "create_tool_subsystem_with_mcp", "tool_call", "TOOL_RUNTIME", "MCP_TOOL_SPECS", "_SERVICE", "_INTEGRATIONS", "KV_CACHE", "get_comm"]
see_also:
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/tools/mcp-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
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
- automatic per-tool dependency installation from `tools_descriptor.py`
- automatic bundle-tool `requirements.txt` resolution separate from
  `@venv(...)`

Practical recommendation:
- direct imports in the tool module only for dependencies already present in
  the runtime image
- use `@venv(...)` only for dependency-heavy leaf work
- keep request-bound objects such as communicator, DB pools, and Redis clients
  outside that helper boundary

## 7) Optional MCP tool sources

```python
MCP_TOOL_SPECS = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
]
```

For MCP transport/auth/runtime details, see [MCP Integration](./mcp-README.md).

## Example references

- [with-isoruntime bundle](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00)
- [with-isoruntime `tools_descriptor.py`](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools_descriptor.py)
- [with-isoruntime `tools/local_tools.py`](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools/local_tools.py)
- [kdcube.copilot `tools_descriptor.py`](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/tools_descriptor.py)
