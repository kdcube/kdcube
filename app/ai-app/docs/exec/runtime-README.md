---
id: ks:docs/exec/runtime-README.md
title: "Runtime"
summary: "Architecture of the isolated execution runtime (Docker + external modes)."
tags: ["exec", "runtime", "architecture", "docker", "supervisor"]
keywords: ["execution architecture", "runtime supervisor", "executor", "external modes", "isolation"]
see_also:
  - ks:docs/exec/README-iso-runtime.md
  - ks:docs/exec/README-runtime-modes-builtin-tools.md
  - ks:docs/exec/operations.md
---
# **Isolated Code Execution Architecture (Docker + External Modes)**

Important distinction used throughout this document:

- **bundle code root** = bundle-local Python/code files, for example a path like `tools/react_tools.py` under the bundle root
- **bundle readonly data** = prepared local bundle data such as built knowledge indexes, cloned docs repos, and other per-bundle cached assets

These are transported separately in external exec.

## **Diagram 1: Detailed Execution Flow (Docker Mode)**

Docker mode supports two container strategies:

- `combined`: historical layout. One `py-code-exec` container hosts the
  supervisor and the UID-dropped generated-code executor subprocess.
- `split`: stronger filesystem isolation. The proc starts separate supervisor
  and executor sibling containers. The supervisor receives descriptors,
  runtime storage, bundle code, and network. The executor receives only the
  work mount, artifact output mount, executor log mount, and supervisor socket.

The diagram below shows the `combined` strategy because it is the baseline
entrypoint flow. The split filesystem tree is documented in
[README-iso-runtime.md](README-iso-runtime.md).

```
┌─────────────────────────────────────────────────────────────────────────┐
│ HOST MACHINE                                                            │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Agent Service (Python)                                        │    │
│  │                                                                │    │
│  │  1. Codegen produces main.py loader + user_code.py            │    │
│  │  2. Prepares runtime_globals:                                 │    │
│  │     - PORTABLE_SPEC_JSON (ModelService, KB, Redis config)     │    │
│  │     - TOOL_ALIAS_MAP (io_tools → dyn_io_tools_abc123)        │    │
│  │     - TOOL_MODULE_FILES (paths to tool .py files)            │    │
│  │  3. Calls run_py_in_docker()                                  │    │
│  └───────────────────────────────────┬─────────────────────────────┘    │
│                                      │                                  │
│                                      ▼                                  │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ docker run (combined strategy)                                │    │
│  │   --network host --cap-add=SYS_ADMIN                          │    │
│  │   -v /host/workdir:/workspace/work:rw                         │    │
│  │   -v /host/outdir:/workspace/out:rw                           │    │
│  │   -v /host/bundles/<bundle_id>:/bundles/<bundle_id>:ro        │    │
│  │   -v /host/bundle-storage/...:/bundle-storage/...:ro          │    │
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
│  │  3. Start PrivilegedSupervisor on Unix socket                 │    │
│  │  4. Launch run_py_code() for executor subprocess              │    │
│  └───────────────────────┬──────────────────────────────────┬─────────┘    │
│                          │                                  │             │
│         ┌────────────────┴─────────┐          ┌────────────┴──────────┐  │
│         │                          │          │                       │  │
│         ▼                          │          ▼                       │  │
│  ┌──────────────────────┐          │   ┌──────────────────────────┐  │  │
│  │ SUPERVISOR           │          │   │ EXECUTOR SUBPROCESS      │  │  │
│  │ (async server)       │◄─────────┼───│ (main.py loader)         │  │  │
│  │                      │  Unix    │   │                          │  │  │
│  │ - Port 0 (no listen) │  Socket  │   │ sandbox launcher:        │  │  │
│  │ - UID 0 (root)       │          │   │  1. unshare network      │  │  │
│  │ - Full network       │          │   │  2. clear groups         │  │  │
│  │ - Has secrets        │          │   │  3. setgid(1000)         │  │  │
│  │ - Runtime storage    │          │   │  4. setuid(1001)         │  │  │
│  │ - ModelService       │          │   │ Result:                  │  │  │
│  │ - KB client          │          │   │ - UID 1001 (unprivileged)│  │  │
│  │ - Redis comm         │          │   │ - NO network namespace   │  │  │
│  │                      │          │   │ - Cannot reach Redis     │  │  │
│  │ Executes:            │          │   │ - Cannot reach internet  │  │  │
│  │                      │          │   │ - Minimal safe env       │  │  │
│  │ • web_search()       │          │   │                          │  │  │
│  │ • web_fetch()        │          │   │ ENV:                     │  │  │
│  │ • kb_client.search() │          │   │ - PYTHONPATH=/opt/app ✅ │  │  │
│  │ • write_file()       │          │   │ - AGENT_IO_CONTEXT=      │  │  │
│  │ • generate_content() │          │   │   limited                │  │  │
│  └──────────────────────┘          │   │                          │  │  │
│         ▲                          │   │ Executes:                │  │  │
│         │                          │   │ • main.py loader         │  │  │
│         │ Tool call via socket     │   │ • user_code.py logic     │  │  │
│         │                          │   │ • Calculations           │  │  │
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
│  │ Unix Socket Protocol                                           │  │
│  │ combined: /tmp/supervisor.sock                                 │  │
│  │ split:    /supervisor-socket/supervisor.sock                   │  │
│  │                                                                 │  │
│  │ Request (Executor → Supervisor):                               │  │
│  │ {                                                               │  │
│  │   "tool_id": "web_tools.web_search",                            │  │
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
│  Artifact writes:    /workspace/out/...                              │  │
│  Result files:       /workspace/out/exec_result_*.json               │  │
│  Runtime logs:       proc-side out/logs/...                           │  │
│  Readonly bundle data: BUNDLE_STORAGE_DIR                            │  │
└─────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ HOST MACHINE                                                            │
│                                                                         │
│  Host can read the proc-side runtime output tree:                     │
│  - /host/outdir/workdir/turn_<id>/...      (generated artifacts)       │
│  - /host/outdir/workdir/exec_result_*.json (execution result)          │
│  - /host/outdir/executed_programs/...      (preserved sources)         │
│  - /host/outdir/logs/infra.log             (merged infra diagnostics)  │
│  - /host/outdir/logs/supervisor/...        (supervisor logs)           │
│  - /host/outdir/logs/executor/...          (executor logs)             │
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
│  2. LOCAL SUBPROCESS (isolation="local")                               │
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
│  │ Docker runtime → Supervisor + Executor                         │    │
│  │                                                                │    │
│  │  Supervisor (root, network, secrets):                         │    │
│  │  • Executes privileged tools                                  │    │
│  │  • Has ModelService, KB, Redis                                │    │
│  │  • Full network access                                        │    │
│  │                                                                │    │
│  │  Executor (UID 1001/GID 1000, network isolated):              │    │
│  │  • Runs untrusted user code                                   │    │
│  │  • NO network (unshare CLONE_NEWNET)                          │    │
│  │  • Read-only FS except work/artifact/log mounts               │    │
│  │  • Max generated file/workspace size limits are enforced      │    │
│  │  • Tools proxied to supervisor via Unix socket                │    │
│  │                                                                │    │
│  │  Used for: Production, untrusted code execution               │    │
│  └───────────────────────────────────────────────────────────────┘    │
│                                                                         │
│                                                                         │
│  4. EXTERNAL / FARGATE (isolation="fargate"/"external")                │
│  ┌───────────────────────────────────────────────────────────────┐    │
│  │ Remote task (ECS/Fargate/other)                                │    │
│  │                                                                │    │
│  │  • Host snapshots workdir/runtime outdir to storage            │    │
│  │  • Host snapshots bundle code root when bundle-local tools     │    │
│  │    are needed                                                   │    │
│  │  • Host snapshots per-bundle readonly data when bundle-local   │    │
│  │    prepared data is needed                                      │    │
│  │  • Remote restores input snapshot                              │    │
│  │  • Executes same supervisor+executor entrypoint                │    │
│  │  • Uploads output snapshots                                    │    │
│  │  • Host merges output into local workdir/runtime outdir        │    │
│  └───────────────────────────────────────────────────────────────┘    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ TOOL EXECUTION BY TYPE (Docker mode)                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ BUILT-IN SAFE TOOLS (Executed by Supervisor in Docker)          │   │
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
│  • combined: one py-code-exec container with UID-dropped child         │
│  • split: separate supervisor and executor sibling containers          │
│  • split executor receives only work, artifact output, executor logs,  │
│    and supervisor socket mounts                                        │
│  • split executor uses --network none, --cap-drop=ALL,                 │
│    no-new-privileges, and narrow UID/ownership capability add-backs    │
│                                                                         │
│  Layer 2: Network Isolation                                            │
│  • Supervisor keeps configured network path for approved tools         │
│  • Executor child runs in an isolated no-network namespace             │
│  • Creates isolated network namespace with no interfaces               │
│  • Untrusted code cannot reach network                                 │
│                                                                         │
│  Layer 3: Privilege Isolation                                          │
│  • Supervisor: trusted component with runtime access                   │
│  • Executor: UID 1001 / GID 1000 - untrusted code                      │
│  • setgroups([1000]), setgid(1000), setuid(1001) after isolation       │
│  • Executor should not retain supplementary root group                 │
│                                                                         │
│  Layer 4: Tool Proxying                                                │
│  • Untrusted code cannot import real tool modules                      │
│  • All tool calls go through ToolStub → Unix socket                    │
│  • Supervisor validates & executes with full privileges                │
│  • Results sanitized (path normalization, etc.)                        │
│                                                                         │
│  Layer 5: Resource Limits                                              │
│  • Per-file size limit via RLIMIT_FSIZE                                │
│  • Net-new workspace/output byte monitor                               │
│  • Timeout enforcement                                                 │
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
│  Executor (main.py loader subprocess):                                 │
│  • Run loader-owned main.py, which executes user_code.py               │
│  • Execute pure computation                                            │
│  • Proxy tool calls to supervisor                                      │
│  • Write exec_result_*.json and generated artifacts                    │
│                                                                         │
│  Tool Modules:                                                         │
│  • io_tools: Wrapper for tool_call(), save_ret()                       │
│  • web_tools: searc/fetch                            │
│  • rendering_tools: rendering to pptx, docx, pdf, png, etc.                            │
│  • llm_tools: LLM generation (via ModelService)                        │
│  • ctx_tools: Context/state management                                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## **Key Insights (Docker mode)**

1. **Untrusted code CAN execute arbitrary logic** (loops, calculations, pandas, etc.) - it just **cannot** directly access network/secrets
2. **Tool calls are transparent** - user code calls `await web_search(...)` like normal, but it's proxied
3. **Bytes are supported** - images, PDFs, Excel files can flow through socket via base64 encoding
4. **Supervisor is the trust boundary** - it has all privileges and validates all tool calls
5. **Filesystem isolation is applied to the executor child, not the supervisor** - this preserves tool functionality while preventing untrusted code from browsing runtime internals
6. **Network isolation doesn't break functionality** - tools that need network run in supervisor
7. **`combined` vs `split` is configurable** - `split` keeps the same tool-call contract while removing supervisor runtime roots, descriptors, bundle mounts, and supervisor logs from the executor filesystem

---

## **Local Subprocess Mode (no supervisor)**

When isolation is set to `local`, tools run in a standalone subprocess on the host:

- No supervisor/executor split.
- No Unix socket proxying.
- Crash containment only (process boundary).
- Use this mode when you want safety from native crashes but don’t need Docker sandboxing.

---

## **External / Fargate Mode (snapshot-based)**

### Snapshot layout

```
cb/tenants/<tenant>/projects/<project>/executions/<user_type>/<user_or_fp>/<conversation>/<turn>/<run_id>/<exec_id>/
  input/
    work.zip
    out.zip
  output/
    work.zip
    out.zip

cb/tenants/<tenant>/projects/<project>/ai-bundle-snapshots/
  <bundle_id>.<version>.zip
  <bundle_id>.<version>.sha256

cb/tenants/<tenant>/projects/<project>/ai-bundle-storage-snapshots/
  <bundle_id>.<sha>.zip
  <bundle_id>.<sha>.sha256
```

### Flow summary
- Host creates input snapshots (workdir + runtime outdir).
- If bundle-local tools are used, host snapshots the bundle code root.
- If bundle-local readonly data is used, host snapshots the per-bundle readonly storage dir.
- Remote executor restores input zips before supervisor bootstrap.
- Remote executor restores bundle code snapshot and bundle readonly data snapshot before running user code.
- After execution, remote uploads output zips (delta-only by default).
- Host downloads output zips and merges into local workdir/runtime outdir.

### Notes
- Tool call files are timestamp-suffixed; index file is still used for grouping.
- Bundle code snapshot is restored when tools are bundle-local.
- Bundle readonly data snapshot is restored to `BUNDLE_STORAGE_DIR` when the bundle needs prepared local data.
- Example: `kdcube.copilot` keeps its built knowledge space in per-bundle storage and reads it in isolated exec via `BUNDLE_STORAGE_DIR`.
- Additional implementation notes: [external-exec-README.md](../sdk/agents/react/external-exec-README.md)
