# **Isolated Code Execution Architecture**

## **Diagram 1: Detailed Execution Flow (Docker Mode)**

```
┌─────────────────────────────────────────────────────────────────────────┐
│ HOST MACHINE                                                            │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Agent Service (Python)                                        │    │
│  │                                                                │    │
│  │  1. Codegen produces main.py with tool calls                  │    │
│  │  2. Prepares runtime_globals:                                 │    │
│  │     - PORTABLE_SPEC_JSON (ModelService, KB, Redis config)     │    │
│  │     - TOOL_ALIAS_MAP (io_tools → dyn_io_tools_abc123)        │    │
│  │     - TOOL_MODULE_FILES (paths to tool .py files)            │    │
│  │  3. Calls run_py_in_docker()                                  │    │
│  └───────────────────────────────────┬─────────────────────────────┘    │
│                                      │                                  │
│                                      ▼                                  │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ docker run --network host --cap-add=SYS_ADMIN                 │    │
│  │   -v /host/workdir:/workspace/work:rw                         │    │
│  │   -v /host/outdir:/workspace/out:rw                           │    │
│  │   -e RUNTIME_GLOBALS_JSON='{"PORTABLE_SPEC_JSON":{...},...}'  │    │
│  │   py-code-exec:latest                                         │    │
│  └───────────────────────────────────┬─────────────────────────────┘    │
│                                      │                                  │
└──────────────────────────────────────┼──────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ DOCKER CONTAINER (py-code-exec)                                         │
│ ENV: PYTHONPATH=/opt/app (from Dockerfile)                              │
│      --network host (can reach localhost:6379 Redis, etc.)              │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ ENTRYPOINT: py_code_exec_entry.py (PID 1, UID 0 root)        │    │
│  │                                                                │    │
│  │  1. Parse RUNTIME_GLOBALS_JSON                                │    │
│  │  2. _bootstrap_supervisor_runtime():                          │    │
│  │     - Load dynamic tool modules (dyn_io_tools_abc123.py)     │    │
│  │     - Call bootstrap_bind_all(bootstrap_env=False)            │    │
│  │       • DON'T apply env_passthrough (keeps PYTHONPATH clean)  │    │
│  │       • Initialize ModelService, KB client, Redis comm        │    │
│  │       • Bind into all tool modules                            │    │
│  │  3. Start PrivilegedSupervisor on /tmp/supervisor.sock        │    │
│  │  4. Launch run_py_code() for executor subprocess              │    │
│  └───────────────────────┬──────────────────────────────────┬─────────┘    │
│                          │                                  │             │
│         ┌────────────────┴─────────┐          ┌────────────┴──────────┐  │
│         │                          │          │                       │  │
│         ▼                          │          ▼                       │  │
│  ┌──────────────────────┐          │   ┌──────────────────────────┐  │  │
│  │ SUPERVISOR           │          │   │ EXECUTOR SUBPROCESS      │  │  │
│  │ (async server)       │◄─────────┼───│ (main.py)                │  │  │
│  │                      │  Unix    │   │                          │  │  │
│  │ - Port 0 (no listen) │  Socket  │   │ preexec_fn():            │  │  │
│  │ - UID 0 (root)       │          │   │  1. unshare(CLONE_NEWNET)│  │  │
│  │ - Full network       │          │   │  2. setuid(1001)         │  │  │
│  │ - Has secrets        │          │   │                          │  │  │
│  │ - ModelService       │          │   │ Result:                  │  │  │
│  │ - KB client          │          │   │ - UID 1001 (unprivileged)│  │  │
│  │ - Redis comm         │          │   │ - NO network namespace   │  │  │
│  │                      │          │   │ - Cannot reach Redis     │  │  │
│  │ Executes:            │          │   │ - Cannot reach internet  │  │  │
│  │ • web_search()       │          │   │                          │  │  │
│  │ • web_fetch()        │          │   │ ENV:                     │  │  │
│  │ • kb_client.search() │          │   │ - PYTHONPATH=/opt/app ✅ │  │  │
│  │ • write_file()       │          │   │ - AGENT_IO_CONTEXT=      │  │  │
│  │ • generate_content() │          │   │   limited                │  │  │
│  └──────────────────────┘          │   │                          │  │  │
│         ▲                          │   │ Executes:                │  │  │
│         │                          │   │ • User logic             │  │  │
│         │ Tool call via socket     │   │ • Calculations           │  │  │
│         │                          │   │ • Data transformations   │  │  │
│         │                          │   │                          │  │  │
│         │                          │   │ Tool calls proxied:      │  │  │
│         │                          │   │   await agent_io_tools   │  │  │
│         │                          │   │     .tool_call(          │  │  │
│         │                          │   │       fn=web_search,     │  │  │
│         │                          │   │       params={"query":   │  │  │
│         │                          │   │         "..."}           │  │  │
│         │                          │   │     )                    │  │  │
│         └──────────────────────────────┤                          │  │  │
│                                        │ ToolStub detects         │  │  │
│                                        │ AGENT_IO_CONTEXT=limited │  │  │
│                                        │ and proxies to socket    │  │  │
│                                        └──────────────────────────┘  │  │
│                                                                       │  │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Unix Socket Protocol (/tmp/supervisor.sock, chmod 0666)        │  │
│  │                                                                 │  │
│  │ Request (Executor → Supervisor):                               │  │
│  │ {                                                               │  │
│  │   "tool_id": "generic_tools.web_search",                       │  │
│  │   "params": {                                                   │  │
│  │     "query": "...",              ← Strings passed directly      │  │
│  │     "image_data": {              ← Bytes encoded as base64     │  │
│  │       "__type__": "bytes",                                      │  │
│  │       "__data__": "base64..."                                   │  │
│  │     }                                                            │  │
│  │   },                                                            │  │
│  │   "reason": "Search for information"                            │  │
│  │ }                                                               │  │
│  │                                                                 │  │
│  │ Response (Supervisor → Executor):                              │  │
│  │ {                                                               │  │
│  │   "ok": true,                                                   │  │
│  │   "result": {                                                   │  │
│  │     "items": [...],              ← Strings/dicts/lists         │  │
│  │     "binary": {                  ← Bytes decoded from base64   │  │
│  │       "__type__": "bytes",                                      │  │
│  │       "__data__": "base64..."                                   │  │
│  │     }                                                            │  │
│  │   }                                                             │  │
│  │ }                                                               │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │  │
│  Results written to: /workspace/out/result.json                      │  │
│  Tool calls logged:  /workspace/out/web_search-0.json                │  │
│  Runtime logs:       /workspace/out/runtime.out.log                  │  │
│                      /workspace/out/runtime.err.log                  │  │
└─────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ HOST MACHINE                                                            │
│                                                                         │
│  Host can read:                                                         │
│  - /host/outdir/result.json          (final output)                    │
│  - /host/outdir/web_search-0.json    (tool call audit trail)           │
│  - /host/outdir/transcription_providers_comparison.xlsx (created file) │
│  - /host/outdir/runtime.out.log      (stdout)                          │
│  - /host/outdir/runtime.err.log      (stderr)                          │
│                                                                         │
│  Container exits, docker run --rm cleans up                            │
└─────────────────────────────────────────────────────────────────────────┘
```

## **Diagram 2: High-Level System Architecture**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      CODE EXECUTION SYSTEM                              │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ EXECUTION MODES                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. IN-MEMORY EXECUTION (isolation="none")                             │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Same Process                                                   │    │
│  │                                                                │    │
│  │  • Tools execute directly in host process                     │    │
│  │  • No network isolation                                        │    │
│  │  • No filesystem isolation                                     │    │
│  │  • Fast, for trusted code only                                │    │
│  │  • Used for: Quick tests, internal tool dev                   │    │
│  └───────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  2. LOCAL SUBPROCESS (isolation="local" or "local_network")            │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Separate Process (on host)                                     │    │
│  │                                                                │    │
│  │  • Tools execute in subprocess                                │    │
│  │  • Optional network isolation (unshare on Linux)              │    │
│  │  • Limited filesystem isolation (CWD restrictions)            │    │
│  │  • Used for: Development, light isolation                     │    │
│  └───────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  3. DOCKER ISOLATED (isolation="docker") ⭐ PRODUCTION                 │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Docker Container → Supervisor + Executor                       │    │
│  │                                                                │    │
│  │  Supervisor (root, network, secrets):                         │    │
│  │  • Executes privileged tools                                  │    │
│  │  • Has ModelService, KB, Redis                                │    │
│  │  • Full network access                                        │    │
│  │                                                                │    │
│  │  Executor (UID 1001, network isolated):                       │    │
│  │  • Runs untrusted user code                                   │    │
│  │  • NO network (unshare CLONE_NEWNET)                          │    │
│  │  • Read-only FS except /workspace/{work,out}                  │    │
│  │  • Tools proxied to supervisor via Unix socket                │    │
│  │                                                                │    │
│  │  Used for: Production, untrusted code execution               │    │
│  └───────────────────────────────────────────────────────────────┘    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ TOOL EXECUTION BY TYPE                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ BUILT-IN SAFE TOOLS (Always in Supervisor)                     │   │
│  ├────────────────────────────────────────────────────────────────┤   │
│  │ Tool              │ Requires    │ Execution Location          │   │
│  ├───────────────────┼─────────────┼─────────────────────────────┤   │
│  │ web_search        │ Network     │ Supervisor (has network)    │   │
│  │ web_fetch         │ Network     │ Supervisor                  │   │
│  │ kb_client.search  │ Postgres    │ Supervisor (has DB conn)    │   │
│  │ write_file        │ FS write    │ Supervisor (writes to /out) │   │
│  │ read_file         │ FS read     │ Supervisor (reads /out)     │   │
│  │ generate_content  │ LLM API     │ Supervisor (ModelService)   │   │
│  │ send_message      │ Redis       │ Supervisor (has comm)       │   │
│  └───────────────────┴─────────────┴─────────────────────────────┘   │
│                                                                         │
│  Data Transfer (Executor ↔ Supervisor):                                │
│  • Strings: Passed directly in JSON                                    │
│  • Bytes: Base64-encoded with marker {"__type__": "bytes", ...}        │
│  • Dicts/Lists: JSON-serialized                                        │
│  • Large files: Written to /workspace/out, path returned               │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ COMPUTATION TOOLS (Can run in Executor)                        │   │
│  ├────────────────────────────────────────────────────────────────┤   │
│  │ Tool              │ Requires    │ Execution Location          │   │
│  ├───────────────────┼─────────────┼─────────────────────────────┤   │
│  │ Pure computation  │ CPU only    │ Executor (safe in sandbox)  │   │
│  │ pandas/numpy      │ Memory      │ Executor                    │   │
│  │ Local file ops    │ /workspace  │ Executor (restricted mount) │   │
│  │ Data transforms   │ None        │ Executor                    │   │
│  └───────────────────┴─────────────┴─────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ SECURITY BOUNDARIES                                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Layer 1: Container Isolation                                          │
│  • Docker --read-only filesystem                                       │
│  • Only /workspace/work and /workspace/out writable                    │
│  • No access to host filesystem                                        │
│  • --cap-add=SYS_ADMIN (needed for unshare)                            │
│                                                                         │
│  Layer 2: Network Isolation                                            │
│  • Container runs with --network host (for supervisor)                 │
│  • Executor subprocess calls unshare(CLONE_NEWNET)                     │
│  • Creates isolated network namespace with no interfaces               │
│  • Untrusted code cannot reach network                                 │
│                                                                         │
│  Layer 3: Privilege Isolation                                          │
│  • Supervisor: UID 0 (root) - trusted component                        │
│  • Executor: UID 1001 (unprivileged) - untrusted code                  │
│  • setuid() called after unshare()                                     │
│                                                                         │
│  Layer 4: Tool Proxying                                                │
│  • Untrusted code cannot import real tool modules                      │
│  • All tool calls go through ToolStub → Unix socket                    │
│  • Supervisor validates & executes with full privileges                │
│  • Results sanitized (path normalization, etc.)                        │
│                                                                         │
│  Layer 5: Resource Limits (TODO)                                       │
│  • CPU limits (docker --cpus)                                          │
│  • Memory limits (docker --memory)                                     │
│  • Timeout enforcement (asyncio.wait_for)                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ COMPONENT RESPONSIBILITIES                                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Host (Agent Service):                                                 │
│  • Generate code with codegen                                          │
│  • Prepare PORTABLE_SPEC (ModelService config, secrets)                │
│  • Launch docker container                                             │
│  • Collect results from /host/outdir                                   │
│                                                                         │
│  Supervisor (py_code_exec_entry.py):                                   │
│  • Bootstrap runtime (ModelService, KB, Redis)                         │
│  • Start Unix socket server                                            │
│  • Execute privileged tool calls                                       │
│  • Manage tool accounting & audit trail                                │
│  • Dump delta cache & cleanup                                          │
│                                                                         │
│  Executor (main.py subprocess):                                        │
│  • Run user-generated code                                             │
│  • Execute pure computation                                            │
│  • Proxy tool calls to supervisor                                      │
│  • Write result.json                                                   │
│                                                                         │
│  Tool Modules:                                                         │
│  • io_tools: Wrapper for tool_call(), save_ret()                       │
│  • generic_tools: File ops, basic utilities                            │
│  • llm_tools: LLM generation (via ModelService)                        │
│  • ctx_tools: Context/state management                                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## **Key Insights**

1. **Untrusted code CAN execute arbitrary logic** (loops, calculations, pandas, etc.) - it just **cannot** directly access network/secrets
2. **Tool calls are transparent** - user code calls `await web_search(...)` like normal, but it's proxied
3. **Bytes are supported** - images, PDFs, Excel files can flow through socket via base64 encoding
4. **Supervisor is the trust boundary** - it has all privileges and validates all tool calls
5. **Network isolation doesn't break functionality** - tools that need network run in supervisor
6. **`--network host` mode** allows supervisor to reach Redis/Postgres on `localhost` while executor remains isolated