## External execution notes (Fargate / distributed)

### Snapshot + merge flow
- Host creates input snapshot via `snapshot_exec_input`  
  `kdcube_ai_app/apps/chat/sdk/runtime/external/distributed_snapshot.py`
- Snapshot layout:  
  `cb/tenants/<tenant>/projects/<project>/executions/<user_type>/<user_or_fp>/<conversation>/<turn>/<run_id>/<exec_id>/`
    - `input/work.zip`, `input/out.zip`
    - `output/work.zip`, `output/out.zip`
- Input snapshot uses a **lightweight exec workspace**:
  - `work/` copied fully
  - `out/` contains filtered `timeline.json` + only referenced files (from code + fetch_ctx)
  - Manifest: `exec_snapshot_manifest.json`
- Remote executor restores `input/*` before supervisor bootstrap in  
  `kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`
- Remote executor uploads `output/*` after execution (delta-aware).
- Host (Fargate runner) restores `output/*` into local `workdir/outdir` after task finishes.
  - Outdir merge is **selective**: only `turn_*` outputs copied, `logs/` appended.
  - `timeline.json` / `sources_pool.json` are preserved.

### Delta packaging
- Baseline manifests taken after restore (`build_manifest`) for work/out.
- `write_dir_zip_to_uri(..., baseline=manifest)` includes only changed files.
- Implemented in `distributed_snapshot.py` and used in `py_code_exec_entry.py`.

### Bundle snapshot + caching
- Bundle version is computed from bundle content (SHA‑256 prefix) at load time in
  `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`.
- Snapshot path:
  `cb/tenants/<tenant>/projects/<project>/ai-bundle-snapshots/<bundle_id>.<version>.zip`
  and `.sha256`
- If version is missing, fallback uses SHA256 prefix as version.
- Cached if object already exists (no re-upload).
- Implemented in `ensure_bundle_snapshot` in `distributed_snapshot.py`.

### Bundle restore in exec
- `BUNDLE_SNAPSHOT_URI` is restored to `/workspace/bundles/<bundle_id>` in exec entrypoint.
- Runtime globals are rewritten so tool module paths point at restored bundle root.
- Implemented in `py_code_exec_entry.py` via `rewrite_runtime_globals_for_bundle`.

### Fargate runner (current)
- `FARGATE_EXEC_ENABLED=1` enables execution.
- Required env:
  `FARGATE_CLUSTER`, `FARGATE_TASK_DEFINITION`, `FARGATE_CONTAINER_NAME`,
  `FARGATE_SUBNETS` (comma list), `FARGATE_SECURITY_GROUPS` (comma list),
  `FARGATE_ASSIGN_PUBLIC_IP`.
- Runner is implemented in `kdcube_ai_app/apps/chat/sdk/runtime/external/fargate.py`.

### Output merge rules
- `logs/*` are appended into existing log files.
- `turn_*/*` outputs are copied into the live outdir.
- `timeline.json` and `sources_pool.json` are never overwritten.

### Exec context propagation
- `EXEC_CONTEXT` contains tenant/project/user/conversation/turn/session/run_id.
- Set in `kdcube_ai_app/apps/chat/sdk/runtime/solution/react/execution.py`.
