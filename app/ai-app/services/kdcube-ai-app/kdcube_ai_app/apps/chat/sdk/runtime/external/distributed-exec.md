# Distributed Execution (Fargate/External)

This document defines the plan to support **distributed code execution** (Fargate, Prefect, etc.)
when Docker‑on‑node is not available. It complements the current docker runtime.

## Goals

- Run isolated exec outside the chat node (Fargate / external worker)
- Snapshot current workdir + outdir, upload to shared storage (S3)
- Bootstrap exec with downloaded snapshot
- Upload result deltas back to S3
- Merge deltas into the host outdir after completion

## Non‑Goals (for phase 1)

- Parallel tool calls in the same workdir (future: distributed tool index)
- Full bundle shipping for every run (only required if tools are bundle‑local)

---

## Current runtime (baseline)

- **Local docker runtime** executes within node and shares workdir/outdir
- Tool calls are indexed in `tool_calls_index.json`
- Context + artifacts are stored in outdir

---

## Required changes (phased)

### Phase 1 — Tool index updates (safe change)

- Replace index suffix from numeric counter to **timestamp suffix**
- Make index writes **thread/process‑safe**
- Keep `tool_calls_index.json` format (tool_id → list of files)

### Phase 2 — Distributed exec abstraction

- Keep `runtime/external/docker` as the local implementation
- Add `runtime/external/fargate`
- Introduce a runtime interface (`ExternalRuntime`) and a shared snapshot helper:
  - `runtime/external/distributed_snapshot.py`

### Phase 3 — Snapshot format + S3 layout

**Snapshot rules**

- Input snapshot = workdir + minimal outdir state
- Exclude:
  - `logs/`
  - executed programs folder
  - `sources_pool.json`, `sources_used.json`
  - `tool_calls_index.json` (replaced by timestamped suffix list)

**Storage layout** (per execution)

```
.../executions/<role>/<session>/<conversation>/<turn>/<react-id>/<execution_id>/
  input/
    work.zip
    out.zip
  output/
    work.zip
    out.zip
```

Example paths:
- `.../<execution_id>/input/work.zip`
- `.../<execution_id>/input/out.zip`
- `.../<execution_id>/output/work.zip`
- `.../<execution_id>/output/out.zip`

### Phase 4 — Exec bootstrap + result merge

**Remote exec bootstrap**
- Use `EXEC_SNAPSHOT` (in `runtime_globals`) with `input_work_uri`/`input_out_uri`
- Download + unzip into local workdir/outdir before supervisor bootstrap
- Ensure bundle is available (see next section)

**Result collection**
- On completion, zip workdir/outdir (delta support optional)
- Upload to `output/work.zip` + `output/out.zip`

**Host merge**
- Download output zip, merge into local outdir
- Extract executed program and save with exec ID

---

## Bundle availability (remote exec)

If tools are bundle‑local, the remote executor must resolve them:

Options:
1) **Bake bundle into exec image** (preferred for Fargate)
2) **Ship bundle snapshot** alongside workdir (zip + unpack). Uses `AIBundleStorage` under
   `cb/tenants/{tenant}/projects/{project}/ai-bundle-storage/{bundle_id}/bundle.zip`
3) **Mount bundles from shared storage** (S3/efs) if available

**Decision point**: pick 1 for production, 2 for dev flexibility.

---

## Fargate runner (current env contract)

Required env:
- `FARGATE_EXEC_ENABLED=1`
- `FARGATE_CLUSTER`
- `FARGATE_TASK_DEFINITION`
- `FARGATE_CONTAINER_NAME`
- `FARGATE_SUBNETS` (comma list)
- `FARGATE_SECURITY_GROUPS` (comma list, optional)
- `FARGATE_ASSIGN_PUBLIC_IP` (`ENABLED`/`DISABLED`)

Optional env:
- `FARGATE_LAUNCH_TYPE` (default: `FARGATE`)
- `FARGATE_PLATFORM_VERSION`

Container receives:
- `RUNTIME_GLOBALS_JSON`
- `RUNTIME_TOOL_MODULES`
- `EXECUTION_ID`, `WORKDIR=/workspace/work`, `OUTPUT_DIR=/workspace/out`
- `EXEC_BUNDLE_ROOT` if bundle snapshot is used

---

## React integration points

- `solution/widgets/exec.py` orchestrates iso runtime
- Both react v1 and v2 set workdir/outdir — do not duplicate logic there
- Move new snapshot + merge behavior into the exec layer (not react)

---

## Open questions

- Should `tool_calls_index.json` remain authoritative, or move to event logs only?
- How to reconcile parallel tool runs when distributed?
- Should remote exec upload only deltas or full snapshot?
