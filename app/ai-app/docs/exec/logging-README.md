## Executor log streams

The isolated executor produces **two distinct log streams**:

1) **Program/user logs**  
   - `out/logs/user.log`  
   - Contains **all program output in order** (stdout, stderr, and optionally `logging.*`
     calls from user code).
   - Each execution is prefixed with:
     ```
     ===== EXECUTION <exec_id> START <timestamp> =====
     ```

2) **Runtime/infra logs**  
   - `out/logs/infra.log` (merged)
   - Component logs (source inputs) that get merged:
     - `runtime.err.log`, `docker.err.log`, `py_code_exec_entry.log`, etc.
   - `infra.log` is appended per execution by merging the component logs that
     contain the matching execution banner.

### Separation rules
- In the executor header, `sys.stdout` is redirected to `user.log`.
- `sys.stderr` is redirected to `user.log`.
- The **root logger** is rewired to stream to stdout (→ `user.log`) when
  `EXEC_USER_LOG_MODE=include_logging`, so `logging.getLogger(__name__)`
  in user code lands in `user.log`.
- Runtime loggers are bound to their own handlers, so infra noise does not
  leak into `user.log`.

### Config: program log mode
`EXEC_USER_LOG_MODE` controls whether logging is included in the program log:
- `include_logging` (default):  
  - `user.log` includes stdout, stderr, and `logging.*` output.
- `print_only`:  
  - `user.log` includes stdout/stderr only.  
  - `logging.*` stays in infra logs (via file handler), so it does not
    pollute `user.log`.

### Dedicated program logger
The executor also configures `logging.getLogger("user")` with a handler that
always writes to `user.log` (even when `EXEC_USER_LOG_MODE=print_only`), so
user code can explicitly log to the program stream when desired.

Example:
```python
import logging

log = logging.getLogger("user")
log.info("starting batch job")
log.warning("row 42 skipped")
```

### Traceback line remap
By default, traceback lines referencing `main.py` are **remapped** to point to the
original user snippet (before the injected runtime header). To disable this:
- `EXEC_TRACEBACK_REMAP=0`

This only affects `main.py` line numbers; other files are untouched.

### Error detection
- **Program errors** are detected by scanning `user.log` for:
  - `ERROR` lines (case-insensitive)
  - `Traceback`
- **Infra errors** are detected by scanning `infra.log` for:
  - `ERROR` lines (case-insensitive)
  - `Traceback`

Diagnostics always slice logs by the most recent execution banner:
`===== EXECUTION <exec_id> START ... =====`, so per-run log extraction is reliable.

### Execution banner
The banner is written only when `EXECUTION_SANDBOX` is set (e.g., `docker` or
`fargate`) and `EXECUTION_ID` is present.

### Infra log merging
Infra logs are merged into `infra.log` by the library helper:
```
from kdcube_ai_app.apps.chat.sdk.runtime.diagnose import merge_infra_logs

merge_infra_logs(log_dir=Path(outdir) / "logs", exec_id=exec_id)
```
This is invoked by `exec_tools.run_exec_tool(...)` and `collect_exec_diagnostics(...)`.
Ordering between different component logs is preserved *within* each component,
but interleaving across components is best-effort (by per-exec segment).

### Log filtering helper
Library utility (for programmatic use):
```
from kdcube_ai_app.apps.chat.sdk.runtime.diagnose import filter_log_by_level

errors_only = filter_log_by_level(text, level="ERROR", min_level=True)
```
