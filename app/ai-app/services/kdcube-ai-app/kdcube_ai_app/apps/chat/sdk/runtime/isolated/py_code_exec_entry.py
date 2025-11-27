# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/exec/py_code_exec_entry.py

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
base_env["SUPERVISOR_SOCKET"] = "/tmp/supervisor.sock"
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
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import run_py_code
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


def _load_runtime_globals() -> Dict[str, Any]:
    raw = os.environ.get("RUNTIME_GLOBALS_JSON") or "{}"
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # Enforce defaults for sandbox flags inside container:
    # - FS is already controlled by docker (--read-only + mounts), so disable FS sandbox.
    # - NET: make sure user-code subprocess runs with no network.
    data.setdefault("SANDBOX_FS", "0")
    data.setdefault("SANDBOX_NET", "0")
    return data


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
    This function runs in the supervisor process once, before we accept any tool calls.

    It mirrors what the injected header used to do inside user code (in the non-isolated mode):
      - Restores ContextVars (OUTDIR / WORKDIR).
      - Bootstraps ModelService / registry / KB for tool modules.
      - Rebuilds ChatCommunicator from COMM_SPEC and registers it via set_comm().
    """
    import logging
    import traceback
    import json as _json
    from pathlib import Path
    from kdcube_ai_app.apps.chat.sdk.protocol import (
        ChatEnvelope,
        ServiceCtx,
        ConversationCtx,
    )
    from kdcube_ai_app.apps.chat.emitters import (
        ChatRelayCommunicator,
        ChatCommunicator,
        _RelayEmitterAdapter,
    )

    log = logging.getLogger("agent.runtime")

    # ---------- OUTDIR_CV / WORKDIR_CV ----------
    outdir_s = os.environ.get("OUTPUT_DIR")
    workdir_s = os.environ.get("WORKDIR")
    if outdir_s:
        OUTDIR_CV.set(outdir_s)
    if workdir_s:
        WORKDIR_CV.set(workdir_s)

    # ---------- Portable Spec & modules ----------
    ps = runtime_globals.get("PORTABLE_SPEC_JSON") or runtime_globals.get("PORTABLE_SPEC")
    if isinstance(ps, dict):
        ps_str = _json.dumps(ps, ensure_ascii=False)
    else:
        ps_str = ps

    TOOL_ALIAS_MAP = runtime_globals.get("TOOL_ALIAS_MAP") or {}
    TOOL_MODULE_FILES = runtime_globals.get("TOOL_MODULE_FILES") or {}

    # Preload dyn alias modules from TOOL_MODULE_FILES (same as old header)
    import importlib.util as _importlib_util
    for alias, dyn_name in (TOOL_ALIAS_MAP or {}).items():
        path = (TOOL_MODULE_FILES or {}).get(alias)
        if not path:
            continue
        try:
            spec = _importlib_util.spec_from_file_location(dyn_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = _importlib_util.module_from_spec(spec)
            sys.modules[dyn_name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            log.info(f"[supervisor.bootstrap] loaded dyn module {dyn_name} from {path}")
        except Exception as e:
            log.error(f"[supervisor.bootstrap] failed to load {dyn_name} from {path}: {e}", exc_info=True)

    # Build full module list for bootstrap_bind_all
    bind_targets = list(tool_module_names or [])
    for dyn_name in (TOOL_ALIAS_MAP or {}).values():
        if dyn_name and dyn_name not in bind_targets:
            bind_targets.append(dyn_name)

    if ps_str and bind_targets and bootstrap_bind_all is not None:
        try:
            bootstrap_bind_all(ps_str, module_names=bind_targets)
            log.info(f"[supervisor.bootstrap] bootstrap_bind_all completed for {len(bind_targets)} modules")
        except Exception as e:
            log.error(f"[supervisor.bootstrap] bootstrap_bind_all failed: {e}", exc_info=True)
            marker = outdir / "bootstrap_failed_supervisor.txt"
            try:
                marker.write_text(f"Bootstrap error: {e}\n{traceback.format_exc()}", encoding="utf-8")
            except Exception:
                pass

    # ---------- Communicator from COMM_SPEC ----------
    COMM_SPEC = runtime_globals.get("COMM_SPEC") or {}
    if COMM_SPEC:
        try:
            REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
            REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
            REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
            REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"
            redis_url = REDIS_URL
            if redis_url.startswith('"') and redis_url.endswith('"'):
                redis_url = redis_url[1:-1]
            redis_url_safe = redis_url.replace(REDIS_PASSWORD, "REDIS_PASSWORD") if REDIS_PASSWORD else redis_url
            log.info(f"[supervisor.bootstrap] Redis url: {redis_url_safe}")

            channel = (COMM_SPEC or {}).get("channel") or "chat.events"
            relay = ChatRelayCommunicator(redis_url=redis_url, channel=channel)
            emitter = _RelayEmitterAdapter(relay)
            svc = ServiceCtx(**(COMM_SPEC.get("service") or {}))
            conv = ConversationCtx(**(COMM_SPEC.get("conversation") or {}))
            comm = ChatCommunicator(
                emitter=emitter,
                service=svc.model_dump() if hasattr(svc, "model_dump") else dict(svc),
                conversation=conv.model_dump() if hasattr(conv, "model_dump") else dict(conv),
                room=COMM_SPEC.get("room"),
                target_sid=COMM_SPEC.get("target_sid"),
            )
            set_comm(comm)
            globals()["_comm_obj"] = comm  # for delta cache dumping
            log.info("[supervisor.bootstrap] ChatCommunicator initialized successfully")
        except Exception as e:
            log.error(f"[supervisor.bootstrap] Communicator setup failed: {e}", exc_info=True)


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

    workdir = pathlib.Path(os.environ.get("WORKDIR", "/workspace/work")).resolve()
    outdir = pathlib.Path(os.environ.get("OUTPUT_DIR", "/workspace/out")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    runtime_globals = _load_runtime_globals()
    tool_module_names = _load_tool_module_names()
    spec_str = _portable_spec_str(runtime_globals)

    logger.log(
        f"[entry] workdir={workdir}, outdir={outdir}, "
        f"tool_modules={tool_module_names}, "
        f"has_portable_spec={bool(spec_str)}",
        level="INFO",
    )

    # 🔹 1. Bootstrap the *supervisor* runtime before doing anything else
    _bootstrap_supervisor_runtime(runtime_globals, tool_module_names, logger, outdir)

    # 🔹 2. Prepare supervisor object & Unix server
    socket_path = os.environ.get("SUPERVISOR_SOCKET", "/tmp/supervisor.sock")
    # Remove old socket
    try:
        os.unlink(socket_path)
    except OSError:
        pass

    sup = PrivilegedSupervisor(socket_path=socket_path, logger=logger)

    # Register allowed tools / callables from registry
    if tool_registry is not None:
        try:
            tool_ids = tool_registry.list_tool_ids()
            sup.set_allowed_tools(tool_ids)
            tool_map = tool_registry.build_tool_map()  # id -> callable
            sup.register_tool_callables(tool_map)
            logger.log(
                f"[entry] supervisor registered {len(tool_ids)} allowed tools", level="INFO"
            )
        except Exception as e:
            logger.log(f"[entry] tool_registry wiring failed: {e}", level="ERROR")
    else:
        logger.log(
            "[entry] no tool_registry module; supervisor will rely on dynamic resolution",
            level="WARNING",
        )

    # Async Unix server so all requests share the same ContextVars
    server = await asyncio.start_unix_server(
        sup.handle_stream,
        path=socket_path,
        backlog=8,
    )
    os.chmod(socket_path, 0o600)

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

    logger.log(f"[entry] run_py_code() finished with result={res}", level="INFO")

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
        # Single, global logging setup for this process
        logging_config.configure_logging()
        rc = asyncio.run(_async_main())
    except Exception as e:
        # last-resort log to stderr; Inventory logger may not be available
        print(f"[py_code_exec_entry] fatal error: {e}", file=sys.stderr)
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
