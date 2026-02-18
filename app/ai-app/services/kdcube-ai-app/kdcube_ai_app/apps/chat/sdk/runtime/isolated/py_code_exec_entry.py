# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py

"""
What this expects from the outside

Your existing run_py_in_docker(...) already sets:

WORKDIR=/workspace/work
OUTPUT_DIR=/workspace/out
RUNTIME_GLOBALS_JSON=...
RUNTIME_TOOL_MODULES=...

plus mounts host_workdir → /workspace/work and host_outdir → /workspace/out
and sets the image’s entrypoint (see below)

Optionally, in base_env in docker.run_py_in_docker:
base_env["SUPERVISOR_SOCKET_PATH"] = "/tmp/supervisor.sock"
But our Python entrypoint already uses that as default, so you can skip it.
"""

# TOOL_ALIAS_MAP, TOOL_MODULE_FILES, BUNDLE_SPEC, RAW_TOOL_SPECS
import asyncio
import json
import os
import pathlib
import signal
import importlib
import sys
import time
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec import run_py_code
from kdcube_ai_app.apps.chat.sdk.runtime.isolated.supervisor_entry import PrivilegedSupervisor
import kdcube_ai_app.apps.utils.logging_config as logging_config

# Optional central registry; if you don’t have it yet, dynamic resolution will be used
try:
    from kdcube_ai_app.apps.chat.sdk.runtime import tool_registry
except ImportError:  # pragma: no cover - optional
    tool_registry = None  # type: ignore[assignment]

# Optional bootstrap for supervisor-side tools (same PORTABLE_SPEC / modules story)
try:
    from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import bootstrap_bind_all
except ImportError:  # pragma: no cover
    bootstrap_bind_all = None  # type: ignore[assignment]

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm, get_comm
from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import bootstrap_bind_all, bootstrap_from_spec  # type: ignore[assignment]
from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import (
    restore_zip_to_dir,
    rewrite_runtime_globals_for_bundle,
    write_dir_zip_to_uri,
    build_manifest,
)

def _append_errors_log(message: str) -> None:
    try:
        log_dir = pathlib.Path(os.environ.get("LOG_DIR", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        prefix = os.environ.get("LOG_FILE_PREFIX", "executor")
        errlog_path = log_dir / f"{prefix}.log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        exec_id = os.environ.get("EXECUTION_ID") or "unknown"
        header = f"\n===== EXECUTION {exec_id} START {ts} =====\n"
        with open(errlog_path, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(message)
            if not message.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def _load_runtime_globals() -> Dict[str, Any]:
    raw = os.environ.get("RUNTIME_GLOBALS_JSON") or "{}"
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    skills_desc = data.get("SKILLS_DESCRIPTOR")
    if isinstance(skills_desc, dict) and skills_desc:
        try:
            os.environ["SKILLS_DESCRIPTOR_JSON"] = json.dumps(skills_desc, ensure_ascii=False)
        except Exception:
            pass
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_skills_descriptor
            set_skills_descriptor(skills_desc)
        except Exception:
            pass
    return data


def _restore_snapshot_if_present(
    runtime_globals: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    logger: AgentLogger,
) -> None:
    snap = runtime_globals.get("EXEC_SNAPSHOT") or {}
    if not isinstance(snap, dict):
        return
    input_work = snap.get("input_work_uri")
    input_out = snap.get("input_out_uri")
    if input_work:
        try:
            restore_zip_to_dir(input_work, workdir)
            logger.log(f"[exec.snapshot] Restored workdir from {input_work}", "INFO")
        except Exception as e:
            logger.log(f"[exec.snapshot] Failed to restore workdir: {e}", "ERROR")
    if input_out:
        try:
            restore_zip_to_dir(input_out, outdir)
            logger.log(f"[exec.snapshot] Restored outdir from {input_out}", "INFO")
        except Exception as e:
            logger.log(f"[exec.snapshot] Failed to restore outdir: {e}", "ERROR")


def _restore_bundle_if_present(
    runtime_globals: Dict[str, Any],
    logger: AgentLogger,
) -> Dict[str, Any]:
    uri = runtime_globals.get("BUNDLE_SNAPSHOT_URI")
    if not uri:
        return _maybe_restore_bundle_from_git(runtime_globals, logger)
    bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
    bundle_id = None
    if isinstance(bundle_spec, dict):
        bundle_id = bundle_spec.get("id")
    bundle_id = bundle_id or "bundle"
    dest_root = pathlib.Path(os.environ.get("EXEC_BUNDLE_ROOT", f"/workspace/bundles/{bundle_id}"))
    try:
        restore_zip_to_dir(str(uri), dest_root)
        logger.log(f"[exec.bundle] Restored bundle to {dest_root} from {uri}", "INFO")
        return rewrite_runtime_globals_for_bundle(runtime_globals, new_bundle_root=dest_root)
    except Exception as e:
        logger.log(f"[exec.bundle] Failed to restore bundle: {e}", "ERROR")
        return runtime_globals


def _maybe_restore_bundle_from_git(
    runtime_globals: Dict[str, Any],
    logger: AgentLogger,
) -> Dict[str, Any]:
    """
    Optional fallback for future use: restore bundle from Git if snapshot URI is absent.
    Uses git clone/fetch if git fields are provided in runtime_globals or env.
    """
    bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
    git_url = (
        runtime_globals.get("BUNDLE_GIT_URL")
        or (bundle_spec.get("git_url") if isinstance(bundle_spec, dict) else None)
        or os.environ.get("BUNDLE_GIT_URL")
    )
    if not git_url:
        return runtime_globals
    git_ref = (
        runtime_globals.get("BUNDLE_GIT_REF")
        or (bundle_spec.get("git_ref") if isinstance(bundle_spec, dict) else None)
        or os.environ.get("BUNDLE_GIT_REF")
        or ""
    )
    git_subdir = (
        runtime_globals.get("BUNDLE_GIT_SUBDIR")
        or (bundle_spec.get("git_subdir") if isinstance(bundle_spec, dict) else None)
        or os.environ.get("BUNDLE_GIT_SUBDIR")
        or ""
    )

    def _needs_bundle(globals_obj: Dict[str, Any]) -> bool:
        tool_files = globals_obj.get("TOOL_MODULE_FILES") or {}
        if isinstance(tool_files, dict) and any(v for v in tool_files.values()):
            return True
        raw_specs = globals_obj.get("RAW_TOOL_SPECS") or []
        for spec in raw_specs:
            if isinstance(spec, dict) and spec.get("ref"):
                return True
        return False

    if not _needs_bundle(runtime_globals):
        logger.log("[exec.bundle] Git bundle configured but no bundle tools requested; skipping git restore.", "INFO")
        return runtime_globals

    try:
        from kdcube_ai_app.infra.plugin.git_bundle import ensure_git_bundle
        bundle_id = bundle_spec.get("id") if isinstance(bundle_spec, dict) else None
        bundle_dir = runtime_globals.get("BUNDLE_DIR") or bundle_id
        exec_root_env = os.environ.get("EXEC_BUNDLE_ROOT")
        bundles_root = pathlib.Path(exec_root_env).parent if exec_root_env else None
        paths = ensure_git_bundle(
            bundle_id=bundle_dir,
            git_url=git_url,
            git_ref=git_ref or None,
            git_subdir=git_subdir or None,
            bundles_root=bundles_root,
            logger=logger,
        )
        logger.log(f"[exec.bundle] Restored bundle from git to {paths.bundle_root}", "INFO")
        return rewrite_runtime_globals_for_bundle(runtime_globals, new_bundle_root=paths.bundle_root)
    except Exception as e:
        logger.log(f"[exec.bundle] Git restore failed: {e}", "ERROR")
        return runtime_globals


def _upload_snapshot_outputs(
    runtime_globals: Dict[str, Any],
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    logger: AgentLogger,
    baseline_work: Optional[Dict[str, Dict[str, Any]]] = None,
    baseline_out: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    snap = runtime_globals.get("EXEC_SNAPSHOT") or {}
    if not isinstance(snap, dict):
        return
    output_work = snap.get("output_work_uri")
    output_out = snap.get("output_out_uri")
    if output_work:
        try:
            write_dir_zip_to_uri(str(output_work), workdir, baseline=baseline_work)
            logger.log(f"[exec.snapshot] Uploaded workdir to {output_work}", "INFO")
        except Exception as e:
            logger.log(f"[exec.snapshot] Failed to upload workdir: {e}", "ERROR")
    if output_out:
        try:
            write_dir_zip_to_uri(str(output_out), outdir, baseline=baseline_out)
            logger.log(f"[exec.snapshot] Uploaded outdir to {output_out}", "INFO")
        except Exception as e:
            logger.log(f"[exec.snapshot] Failed to upload outdir: {e}", "ERROR")


def _load_tool_module_names() -> List[str]:
    raw = os.environ.get("RUNTIME_TOOL_MODULES") or "[]"
    try:
        arr = json.loads(raw)
        if not isinstance(arr, list):
            return []
        return [str(x) for x in arr]
    except Exception:
        return []


def _portable_spec_str(runtime_globals: Dict[str, Any]) -> Optional[str]:
    ps = runtime_globals.get("PORTABLE_SPEC_JSON") or runtime_globals.get("PORTABLE_SPEC")
    if ps is None:
        return None
    if isinstance(ps, str):
        return ps
    try:
        return json.dumps(ps, ensure_ascii=False)
    except Exception:
        return None

def _bootstrap_supervisor_runtime(
        runtime_globals: Dict[str, Any],
        tool_module_names: list[str],
        logger: AgentLogger,
        outdir: pathlib.Path,
) -> None:
    """
    Supervisor-side bootstrap: load dynamic modules, then let bootstrap_bind_all
    handle all the heavy lifting (CVs, ModelService, registry, communicator).

    This runs once in the supervisor process before accepting tool calls.
    """
    import traceback
    import json as _json
    import importlib.util as _importlib_util

    # ---------- Get portable spec ----------
    ps = runtime_globals.get("PORTABLE_SPEC_JSON")
    if not ps:
        logger.log("[supervisor.bootstrap] ERROR: PORTABLE_SPEC_JSON missing", "ERROR")
        raise ValueError("PORTABLE_SPEC_JSON required but not found in runtime_globals")

    ps_str = ps if isinstance(ps, str) else _json.dumps(ps, ensure_ascii=False)

    # ---------- Load dynamic modules FIRST (before bootstrap tries to import them) ----------
    TOOL_ALIAS_MAP = runtime_globals.get("TOOL_ALIAS_MAP") or {}
    TOOL_MODULE_FILES = runtime_globals.get("TOOL_MODULE_FILES") or {}
    RAW_TOOL_SPECS = runtime_globals.get("RAW_TOOL_SPECS") or []

    logger.log(f"[supervisor.bootstrap] TOOL_ALIAS_MAP: {TOOL_ALIAS_MAP}", "INFO")
    logger.log(f"[supervisor.bootstrap] TOOL_MODULE_FILES keys: {list(TOOL_MODULE_FILES.keys())}", "INFO")

    # Build alias → module name map for library modules
    alias_to_module: Dict[str, str] = {}
    for spec in RAW_TOOL_SPECS:
        if "module" in spec and spec.get("alias"):
            alias_to_module[spec["alias"]] = spec["module"]

    logger.log(f"[supervisor.bootstrap] alias_to_module: {alias_to_module}", "INFO")

    # Load all dynamic alias modules
    loaded_modules = []
    failed_modules = []

    for alias, dyn_name in (TOOL_ALIAS_MAP or {}).items():
        path = (TOOL_MODULE_FILES or {}).get(alias)

        # Resolve library modules from RAW_TOOL_SPECS if no explicit path
        if not path and alias in alias_to_module:
            module_name = alias_to_module[alias]
            logger.log(f"[supervisor.bootstrap] resolving {alias} from library module {module_name}", "INFO")
            try:
                spec_obj = _importlib_util.find_spec(module_name)
                if spec_obj and spec_obj.origin:
                    path = spec_obj.origin
                    logger.log(
                        f"[supervisor.bootstrap] resolved library module: "
                        f"{alias} → {module_name} → {path}",
                        "INFO"
                    )
                else:
                    logger.log(f"[supervisor.bootstrap] find_spec returned None or no origin for {module_name}", "WARNING")
            except Exception as e:
                logger.log(
                    f"[supervisor.bootstrap] find_spec failed for {module_name}: {e}\n{traceback.format_exc()}",
                    "ERROR"
                )
                failed_modules.append((alias, dyn_name, f"find_spec failed: {e}"))
                continue

        if not path:
            logger.log(f"[supervisor.bootstrap] no path for alias {alias} -> {dyn_name}, skipping", "WARNING")
            failed_modules.append((alias, dyn_name, "no path"))
            continue

        logger.log(f"[supervisor.bootstrap] loading {dyn_name} from {path}", "INFO")

        try:
            spec = _importlib_util.spec_from_file_location(dyn_name, path)
            if spec is None or spec.loader is None:
                error = f"spec_from_file_location returned None or no loader"
                logger.log(f"[supervisor.bootstrap] {error} for {dyn_name}", "ERROR")
                failed_modules.append((alias, dyn_name, error))
                continue

            mod = _importlib_util.module_from_spec(spec)
            sys.modules[dyn_name] = mod
            logger.log(f"[supervisor.bootstrap] added {dyn_name} to sys.modules, executing...", "INFO")

            spec.loader.exec_module(mod)  # type: ignore[union-attr]

            logger.log(f"[supervisor.bootstrap] ✅ loaded dyn module {dyn_name} from {path}", "INFO")
            loaded_modules.append((alias, dyn_name, path))

            # Verify it's actually importable now
            try:
                test_import = importlib.import_module(dyn_name)
                logger.log(f"[supervisor.bootstrap] ✅ verified {dyn_name} is importable", "INFO")
            except Exception as e:
                logger.log(f"[supervisor.bootstrap] ⚠️  {dyn_name} in sys.modules but not importable: {e}", "WARNING")

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.log(
                f"[supervisor.bootstrap] ❌ failed to load {dyn_name} from {path}:\n{traceback.format_exc()}",
                "ERROR"
            )
            failed_modules.append((alias, dyn_name, error_msg))

    # Log summary
    logger.log(f"[supervisor.bootstrap] Module loading summary:", "INFO")
    logger.log(f"[supervisor.bootstrap]   Loaded: {len(loaded_modules)}/{len(TOOL_ALIAS_MAP)}", "INFO")
    for alias, dyn_name, path in loaded_modules:
        logger.log(f"[supervisor.bootstrap]     ✅ {alias} → {dyn_name}", "INFO")
    if failed_modules:
        logger.log(f"[supervisor.bootstrap]   Failed: {len(failed_modules)}", "ERROR")
        for alias, dyn_name, error in failed_modules:
            logger.log(f"[supervisor.bootstrap]     ❌ {alias} → {dyn_name}: {error}", "ERROR")

    # ---------- Build full module list for bootstrap ----------
    bind_targets = list(tool_module_names or [])
    for dyn_name in (TOOL_ALIAS_MAP or {}).values():
        if dyn_name and dyn_name not in bind_targets:
            bind_targets.append(dyn_name)
    try:
        if "kdcube_ai_app.apps.chat.sdk.tools.io_tools" not in bind_targets:
            bind_targets.append("kdcube_ai_app.apps.chat.sdk.tools.io_tools")
    except Exception:
        pass

    logger.log(f"[supervisor.bootstrap] bind_targets: {bind_targets}", "INFO")

    # ---------- Single bootstrap call handles everything ----------
    if bootstrap_bind_all is not None:
        try:
            logger.log(f"[supervisor.bootstrap] calling bootstrap_bind_all for {len(bind_targets)} modules", "INFO")
            bootstrap_bind_all(ps_str, module_names=bind_targets, bootstrap_env=False)
            logger.log("[supervisor.bootstrap] ✅ bootstrap_bind_all completed successfully", "INFO")

            # Store comm reference for delta cache dumping
            try:
                comm = get_comm()
                if comm:
                    globals()["_comm_obj"] = comm
                    logger.log("[supervisor.bootstrap] stored comm reference for delta cache", "INFO")
            except Exception as e:
                logger.log(f"[supervisor.bootstrap] could not get comm: {e}", "WARNING")

        except Exception as e:
            logger.log(f"[supervisor.bootstrap] ❌ bootstrap_bind_all failed: {e}", "ERROR")
            logger.log(f"[supervisor.bootstrap] traceback:\n{traceback.format_exc()}", "ERROR")
            marker = outdir / "bootstrap_failed_supervisor.txt"
            try:
                marker.write_text(f"Bootstrap error: {e}\n{traceback.format_exc()}", encoding="utf-8")
            except Exception:
                pass
            raise  # Re-raise to make failure obvious
    else:
        logger.log("[supervisor.bootstrap] ❌ bootstrap_bind_all not available", "ERROR")
        raise ImportError("bootstrap_bind_all not available")

def _dump_delta_cache_file(outdir: pathlib.Path, logger: AgentLogger) -> None:
    """
    Supervisor-side dump of communicator delta cache.

    All deltas are collected in the supervisor process (tools run there), so we must
    dump from here, NOT from the executor header.
    """
    try:
        comm = None
        try:
            # Prefer comm from comm_ctx
            comm = get_comm()
        except Exception:
            # Fallback: the one we stashed in globals during bootstrap
            comm = globals().get("_comm_obj")

        if not comm:
            return

        dest = outdir / "delta_aggregates.json"
        try:
            ok = comm.dump_delta_cache(dest)
            if not ok:
                # fallback: dump inline
                aggs = comm.export_delta_cache(merge_text=False)
                dest.write_text(
                    json.dumps({"items": aggs}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.log(f"[entry] delta cache dump failed: {e}", level="ERROR")
    except Exception:
        # best-effort only
        pass

async def _shutdown_supervisor_modules(tool_module_names: list[str], logger: AgentLogger) -> None:
    """
    Best-effort async shutdown of modules that might hold resources (KB clients, etc.).
    We mirror the old _sync_shutdown_all in the header, but now on the supervisor side.
    """
    import asyncio as _asyncio

    names: set[str] = set(tool_module_names or [])
    # Include KB client by default, like before
    names.add("kdcube_ai_app.apps.chat.sdk.retrieval.kb_client")

    mods = []
    for name in names:
        try:
            mod = importlib.import_module(name)
            mods.append(mod)
        except Exception:
            # Not fatal; module may not exist in this context
            continue

    async def _async_shutdown_mod(mod):
        try:
            if hasattr(mod, "shutdown") and callable(mod.shutdown):
                maybe = mod.shutdown()
                if _asyncio.iscoroutine(maybe):
                    await maybe
            elif hasattr(mod, "close") and callable(mod.close):
                maybe = mod.close()
                if _asyncio.iscoroutine(maybe):
                    await maybe
        except Exception as e:
            logger.log(f"[entry] module {getattr(mod, '__name__', mod)} shutdown failed: {e}", level="WARNING")

    for m in mods:
        await _async_shutdown_mod(m)

async def _supervisor_loop(sup: PrivilegedSupervisor, stop_event: asyncio.Event, log: AgentLogger) -> None:
    """
    Main accept loop for the PrivilegedSupervisor, running in the same process
    as the driver.

    - Blocks on sup.handle_request() in a thread-pool (one request at a time).
    - Exits when stop_event is set or when the process terminates.
    """
    loop = asyncio.get_running_loop()
    log.log("[supervisor.entry] entering main accept loop", level="INFO")

    while not stop_event.is_set():
        # handle_request() blocks on accept(); we run it in a worker thread
        await loop.run_in_executor(None, sup.handle_request)

    log.log("[supervisor.entry] stop_event set, leaving accept loop", level="INFO")


async def _async_main() -> int:
    """
    Container-side main:

      1. Read WORKDIR / OUTPUT_DIR / RUNTIME_GLOBALS_JSON / RUNTIME_TOOL_MODULES from env.
      2. Bootstrap tool modules on the supervisor side (ModelService, registry, etc.).
      3. Start PrivilegedSupervisor on a Unix socket.
      4. In parallel, run iso_runtime.run_py_code(...) which:
           - injects the executor header into workdir/main.py
           - runs user code in a **networkless** subprocess
      5. When user code finishes:
           - dump communicator delta cache (from supervisor),
           - stop supervisor server,
           - shutdown modules,
           - exit with the same return code.
    """
    logger = AgentLogger("py_code_exec_entry")
    exec_id = os.environ.get("EXECUTION_ID") or "unknown"
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    logger.log(f"[entry] ===== EXECUTION {exec_id} START {ts} =====", level="INFO")
    # Ensure group-writable files across shared volumes (chat user is gid 1000)
    os.umask(0o002)

    workdir = pathlib.Path(os.environ.get("WORKDIR", "/workspace/work")).resolve()
    outdir = pathlib.Path(os.environ.get("OUTPUT_DIR", "/workspace/out")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    runtime_globals = _load_runtime_globals()
    runtime_globals = _restore_bundle_if_present(runtime_globals, logger)
    tool_module_names = _load_tool_module_names()
    spec_str = _portable_spec_str(runtime_globals)

    # Debug helper: dump rewritten TOOL_MODULE_FILES after bundle restore
    try:
        tmf = runtime_globals.get("TOOL_MODULE_FILES") or {}
        if isinstance(tmf, dict) and tmf:
            items = list(tmf.items())
            preview = {k: (str(v) if v is not None else None) for k, v in items[:50]}
            more = f" (+{len(items) - len(preview)} more)" if len(items) > len(preview) else ""
            logger.log(f"[entry] TOOL_MODULE_FILES={preview}{more}", level="INFO")
    except Exception:
        pass

    logger.log(
        f"[entry] workdir={workdir}, outdir={outdir}, "
        f"tool_modules={tool_module_names}, "
        f"has_portable_spec={bool(spec_str)}",
        level="INFO",
    )

    # 🔹 1. Bootstrap the *supervisor* runtime before doing anything else
    logger.log(
        f"[supervisor.bootstrap] pre OUTDIR_CV={OUTDIR_CV.get('')}; WORKDIR_CV={WORKDIR_CV.get('')}; "
        f"ENV OUTPUT_DIR={os.environ.get('OUTPUT_DIR','')}; WORKDIR={os.environ.get('WORKDIR','')}",
        level="INFO",
    )
    _restore_snapshot_if_present(runtime_globals, workdir, outdir, logger)
    baseline_work = build_manifest(workdir)
    baseline_out = build_manifest(outdir)
    _bootstrap_supervisor_runtime(runtime_globals, tool_module_names, logger, outdir)
    try:
        logger.log(
            f"[supervisor.bootstrap] post-bootstrap OUTDIR_CV={OUTDIR_CV.get('')}; WORKDIR_CV={WORKDIR_CV.get('')}",
            level="INFO",
        )
        OUTDIR_CV.set(os.environ.get("OUTPUT_DIR", "") or "")
        WORKDIR_CV.set(os.environ.get("WORKDIR", "") or "")
        logger.log(
            f"[supervisor.bootstrap] OUTDIR_CV={OUTDIR_CV.get('')}; WORKDIR_CV={WORKDIR_CV.get('')}",
            level="INFO",
        )
    except Exception as e:
        logger.log(f"[supervisor.bootstrap] Failed to set OUTDIR/WORKDIR CVs: {e}", level="WARNING")

    # 🔹 2. Prepare supervisor object & Unix server
    socket_path = os.environ.get("SUPERVISOR_SOCKET_PATH", "/tmp/supervisor.sock")
    # Remove old socket
    try:
        os.unlink(socket_path)
    except OSError:
        pass

    sup = PrivilegedSupervisor(socket_path=socket_path, logger=logger)
    alias_map = runtime_globals.get("TOOL_ALIAS_MAP") or {}
    sup.set_alias_map(alias_map)
    logger.log(f"[entry] Set alias map with {len(alias_map)} entries: {list(alias_map.keys())}", level="INFO")

    # Async Unix server so all requests share the same ContextVars
    server = await asyncio.start_unix_server(
        sup.handle_stream,
        path=socket_path,
        backlog=8,
    )
    os.chmod(socket_path, 0o666)

    logger.log(f"[entry] supervisor listening on {socket_path}", level="INFO")

    stop_event = asyncio.Event()

    async def _wait_server_stop():
        await stop_event.wait()
        server.close()
        await server.wait_closed()
        logger.log("[entry] supervisor server shut down", level="INFO")

    server_task = asyncio.create_task(_wait_server_stop())

    loop = asyncio.get_running_loop()

    def _on_sig(*_args: Any) -> None:
        if not stop_event.is_set():
            stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_sig)
        except NotImplementedError:
            pass

    # 🔹 4. Run executor (networkless, no PORTABLE_SPEC) in parallel
    timeout_env = os.environ.get("PY_CODE_EXEC_TIMEOUT")
    try:
        timeout_s = int(timeout_env) if timeout_env else None
    except ValueError:
        timeout_s = None

    # After supervisor bootstrap, drop PORTABLE_SPEC so executor never sees it
    exec_globals = dict(runtime_globals)
    exec_globals.pop("PORTABLE_SPEC", None)
    exec_globals.pop("PORTABLE_SPEC_JSON", None)

    logger.log("[entry] starting run_py_code()", level="INFO")
    res = await run_py_code(
        workdir=workdir,
        output_dir=outdir,
        globals=exec_globals,
        logger=logger,
        timeout_s=timeout_s or 600,
    )

    if isinstance(res, dict):
        logger.log(
            f"[entry] run_py_code() finished: ok={res.get('ok', True)} "
            f"returncode={res.get('returncode')} error={res.get('error')}",
            level="INFO",
        )
    else:
        logger.log("[entry] run_py_code() finished", level="INFO")
    if isinstance(res, dict):
        ok = res.get("ok", True)
        error = res.get("error")
        if not ok or error:
            summary = res.get("error_summary") or (str(error) if error else "")
            if not summary and res.get("returncode") is not None:
                summary = f"subprocess failed (returncode={res.get('returncode')})"
            if summary:
                logger.log(f"[entry] ERROR: {summary}", level="ERROR")
                _append_errors_log(f"[entry] ERROR: {summary}")

    # 🔹 4.5 Dump communicator delta cache from supervisor side
    _dump_delta_cache_file(outdir, logger)

    # 🔹 5. Stop supervisor server
    stop_event.set()
    try:
        await asyncio.wait_for(server_task, timeout=1.0)
    except asyncio.TimeoutError:
        logger.log("[entry] supervisor server did not exit in time", level="WARNING")
    except Exception as e:
        logger.log(f"[entry] supervisor server error on shutdown: {e}", level="ERROR")

    # 🔹 6. Best-effort shutdown of tool modules (KB, etc.)
    try:
        await _shutdown_supervisor_modules(tool_module_names, logger)
    except Exception as e:
        logger.log(f"[entry] module shutdown failed: {e}", level="ERROR")

    # 🔹 7. Upload output snapshots if requested
    _upload_snapshot_outputs(runtime_globals, workdir, outdir, logger, baseline_work, baseline_out)

    # Map run_py_code() result to exit code
    if isinstance(res, dict):
        if res.get("error") == "timeout":
            return 124  # typical timeout exit code

        rc = res.get("returncode")
        if isinstance(rc, int):
            return rc

    # Unknown failure shape → generic error
    return 1


def main() -> None:
    try:
        os.environ.setdefault("EXECUTION_SANDBOX", "docker")
        # Single, global logging setup for this process
        logging_config.configure_logging()
        rc = asyncio.run(_async_main())
    except Exception as e:
        # last-resort log to stderr; Inventory logger may not be available
        print(f"[py_code_exec_entry] fatal error: {e}", file=sys.stderr)
        _append_errors_log(f"[py_code_exec_entry] fatal error: {e}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
