# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec.py

import pathlib, os, json
from typing import Optional, Dict, Any

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _fix_json_bools, _validate_and_report_fstring_issues, \
    _build_iso_injected_header, _inject_header_after_future, _run_subprocess, _build_iso_injected_header_step_artifacts
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


def _prepare_workspace_for_executor(
        output_dir: pathlib.Path,
        executor_uid: int = 1001,
        logger: Optional[AgentLogger] = None
) -> None:
    """
    Ensure output directories are writable by executor user.
    Must be called BEFORE _run_subprocess drops privileges.

    This runs while we're still privileged (root in Docker, or regular user on host).
    """
    import subprocess

    log = logger or AgentLogger("workspace_prep")

    # Ensure logs directory exists
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Try to chown to executor user (requires root)
    try:
        subprocess.run(
            ["chown", "-R", f"{executor_uid}:{executor_uid}", str(output_dir)],
            check=True,
            capture_output=True,
            timeout=5
        )
        log.log(
            f"[workspace_prep] Set ownership of {output_dir} to UID {executor_uid}",
            level="INFO"
        )
    except subprocess.CalledProcessError as e:
        # Not root - try fallback (chmod)
        log.log(
            f"[workspace_prep] chown failed (not root?): {e.stderr.decode()}. "
            f"Falling back to chmod.",
            level="WARNING"
        )
        try:
            os.chmod(output_dir, 0o777)
            os.chmod(logs_dir, 0o777)
            log.log(
                f"[workspace_prep] Made {output_dir} world-writable (fallback)",
                level="WARNING"
            )
        except Exception as chmod_err:
            log.log(
                f"[workspace_prep] Failed to fix permissions: {chmod_err}. "
                f"Executor may fail to write logs!",
                level="ERROR"
            )
    except FileNotFoundError:
        # chown command not available (unlikely in Docker)
        log.log(
            "[workspace_prep] chown command not found. Using chmod fallback.",
            level="WARNING"
        )
        try:
            os.chmod(output_dir, 0o777)
            os.chmod(logs_dir, 0o777)
        except Exception as chmod_err:
            log.log(
                f"[workspace_prep] chmod fallback failed: {chmod_err}",
                level="ERROR"
            )
    except subprocess.TimeoutExpired:
        log.log(
            "[workspace_prep] chown timed out",
            level="ERROR"
        )
    except Exception as e:
        log.log(
            f"[workspace_prep] Unexpected error: {e}",
            level="ERROR"
        )

# --- Docker entry runtime: run_py_code --------------------------------------

async def run_py_code(
        *,
        workdir: pathlib.Path,
        output_dir: pathlib.Path,
        globals: Dict[str, Any] | None = None,
        logger: Optional[AgentLogger] = None,
        timeout_s: int = 600,
) -> Dict[str, Any]:
    """
    Execute workdir/main.py *inside the current container*.

    This is intended to be called from the py-code-exec Docker entrypoint:
      - WORKDIR / OUTPUT_DIR are container paths (/workspace, /output)
      - RUNTIME_GLOBALS_JSON and RUNTIME_TOOL_MODULES have been provided via env

    It mirrors the behavior of _InProcessRuntime.execute_py_code, but:
      - assumes we are already in an isolated Docker container
      - optionally disables bwrap (if SANDBOX_FS is 0 in globals)
      - writes runtime.out.log / runtime.err.log into OUTPUT_DIR
    """
    log = logger or AgentLogger("py_code_exec")

    workdir = workdir.resolve()
    output_dir = output_dir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    _prepare_workspace_for_executor(output_dir, executor_uid=1001, logger=log)

    main_path = workdir / "main.py"
    if not main_path.exists():
        raise FileNotFoundError(f"main.py not found in workdir: {workdir}")

    # --- Read & transform main.py (JSON bool fix + f-string lint + header injection) ---
    src = main_path.read_text(encoding="utf-8")
    src = _fix_json_bools(src)
    src = _validate_and_report_fstring_issues(src, workdir)

    # ⚠️ DO NOT leak full os.environ into untrusted code.
    # Start from an empty env and selectively copy safe variables.
    base_env = os.environ
    child_env: dict[str, str] = {}

    # Minimal runtime stuff the interpreter actually needs
    SAFE_KEYS = {
        "PATH",
        "PYTHONPATH",      # so imports from /opt/app etc. continue to work
        "LANG",
        "LC_ALL",
        "TZ",
        "PYTHONUNBUFFERED",
        # if you care about consistent home expansion in user code:
        "HOME",
        # custom socket path, so executor can reach supervisor:
        "SUPERVISOR_SOCKET_PATH",
        # Logging configuration (ADDED)
        "LOG_DIR",
        "LOG_LEVEL",
        "LOG_MAX_MB",
        "LOG_BACKUP_COUNT",
        "RESULT_FILENAME",
        "EXECUTION_MODE",
        "EXEC_NO_UNEXPECTED_EXIT"
    }
    for k in SAFE_KEYS:
        v = base_env.get(k)
        if v is not None:
            child_env[k] = v

    g = globals or {}

    # ❌ IMPORTANT: do NOT propagate PORTABLE_SPEC into executor
    # (we already used it in the supervisor, and py_code_exec_entry
    # should have popped it from runtime_globals before calling run_py_code).
    # So: no PORTABLE_SPEC, no env_passthrough, no secrets here.

    # Build globals prelude (simple assignments injected into header)
    globals_src = ""
    for k, v in g.items():
        if k and k != "__name__":
            globals_src += f"\n{k} = {repr(v)}\n"

    # # Tool alias imports (must match TOOL_ALIAS_MAP)
    imports_src = ""
    alias_map = (g.get("TOOL_ALIAS_MAP") or {}) if g else {}
    for alias, mod_name in (alias_map or {}).items():
        imports_src += f"\nfrom {mod_name} import tools as {alias}\n"

    mode = child_env.get("EXECUTION_MODE") or "STANDALONE"
    if mode == "STANDALONE":
        injected_header = _build_iso_injected_header(globals_src=globals_src, imports_src=imports_src)
    else:
        injected_header = _build_iso_injected_header_step_artifacts(globals_src=globals_src, imports_src=imports_src)

    src = _inject_header_after_future(src, injected_header)
    main_path.write_text(src, encoding="utf-8")

    # OUTPUT_DIR / WORKDIR inside this container
    child_env["OUTPUT_DIR"] = str(output_dir)
    child_env["WORKDIR"] = str(workdir)
    child_env["AGENT_IO_CONTEXT"] = "limited"
    child_env["LOG_FILE_PREFIX"] = "executor"
    # RUNTIME_TOOL_MODULES:
    # - Start from env (if any), then augment with dynamic alias modules from TOOL_ALIAS_MAP
    tool_module_names: list[str] = []
    raw_modules = child_env.get("RUNTIME_TOOL_MODULES") or ""
    try:
        if raw_modules:
            tool_module_names = list(json.loads(raw_modules) or [])
    except Exception:
        tool_module_names = []

    for dyn_name in (alias_map or {}).values():
        if dyn_name and dyn_name not in tool_module_names:
            tool_module_names.append(dyn_name)

    child_env["RUNTIME_TOOL_MODULES"] = json.dumps(tool_module_names, ensure_ascii=False)
    # Modules to shutdown on exit (KB client included)
    shutdown_candidates = list(tool_module_names) + [
        "kdcube_ai_app.apps.chat.sdk.retrieval.kb_client"
    ]
    child_env["RUNTIME_SHUTDOWN_MODULES"] = json.dumps(shutdown_candidates, ensure_ascii=False)

    # We rely on the container image having all required modules installed on sys.path,
    # so we do NOT need to modify PYTHONPATH here (unlike host-based iso_runtime).

    log.log(
        f"[py_code_exec] Running main.py in-container: workdir={workdir}, outdir={output_dir}, "
        f"run_mode={mode}; runtime_modules={tool_module_names}",
        level="INFO",
    )
    log.log(f"[py_code_exec] About to call _run_subprocess", level="INFO")
    # Execute the rewritten main.py as a child process inside this container.
    log.log(f"[py_code_exec] base_env PYTHONPATH: {base_env.get('PYTHONPATH')}", level="INFO")
    log.log(f"[py_code_exec] child_env PYTHONPATH: {child_env.get('PYTHONPATH')}", level="INFO")
    log.log(f"[py_code_exec] child_env keys: {sorted(child_env.keys())}", level="INFO")
    res = await _run_subprocess(
        entry_path=main_path,
        cwd=workdir,
        env=child_env,
        timeout_s=timeout_s,
        outdir=output_dir,
        allow_network=False,
    )
    log.log(f"[py_code_exec] _run_subprocess returned: {res}", level="INFO")
    return res
