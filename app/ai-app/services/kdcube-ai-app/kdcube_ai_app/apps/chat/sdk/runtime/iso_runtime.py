# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py
import contextvars
import io
import json
import os, sys
import asyncio
import time
import pathlib
import tokenize
from typing import Dict, Any, List, Tuple, Optional, Literal

from kdcube_ai_app.apps.chat.sdk.util import strip_lone_surrogates
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

def _run_in_executor_with_ctx(loop, fn, *args, **kwargs):
    ctx = contextvars.copy_context()
    return loop.run_in_executor(None, lambda: ctx.run(fn, *args, **kwargs))

def build_current_tool_imports(alias_map: dict[str, str]) -> str:
    """
    alias_map: {"io_tools":"io_tools","ctx_tools":"ctx_tools","generic_tools":"generic_tools","llm_tools":"llm_tools", ...}
    Returns a Markdown code block with python import lines.
    """
    # Always show the wrapper import first
    lines = ["from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools  # wrapper for tool_call/save_ret"]
    seen = set()

    for alias, module in (alias_map or {}).items():
        if not alias or not module:
            continue
        # Avoid duplicating the wrapper line
        line = f"from {module} import tools as {alias}"
        if line not in seen:
            lines.append(line)
            seen.add(line)

    return "```python\n" + "\n".join(lines) + "\n```"

def build_packages_installed_block() -> str:
    return (
        "[AVAILABLE PACKAGES]\n"
        "- Data: pandas, numpy, openpyxl, xlsxwriter\n"
        "- Files: python-docx, python-pptx, pymupdf, pypdf, reportlab, Pillow\n"
        "- Web: requests, aiohttp, httpx, playwright, beautifulsoup4, lxml\n"
        "- Viz: matplotlib, seaborn, plotly, networkx, graphviz, diagrams, folium\n"
        "- Text: markdown-it-py, pygments, jinja2, python-dateutil\n"
        "- Utils: pydantic, orjson, python-dotenv, PyJWT, geopy\n"
    )

def _merge_timeout_result(path: pathlib.Path, *, objective: str, seconds: int):
    reason = f"Solver runtime exceeded {seconds}s and was terminated."
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        # preserve existing fields, only enforce error shape and ok=False
        data["ok"] = False
        err = (data.get("error") or {})
        err.update({
            "where": "runtime",
            "error": "timeout",
            "description": reason,
            "managed": True,
        })
        data["error"] = err
        if not data.get("objective"):
            data["objective"] = objective
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except UnicodeEncodeError:
            safe_json = strip_lone_surrogates(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            path.write_text(safe_json, encoding="utf-8")

        return

    # no prior file — write minimal
    payload = {
        "ok": False,
        "objective": objective,
        "out": [],
        "error": {
            "where": "runtime",
            "error": "timeout",
            "description": reason,
            "managed": True
        }
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except UnicodeEncodeError:
        safe_json = strip_lone_surrogates(
            json.dumps(payload, ensure_ascii=False, indent=2)
        )
        path.write_text(safe_json, encoding="utf-8")

def _inject_header_after_future(src: str, header: str) -> str:
    lines = src.splitlines(True)
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("from __future__ import"):
        i += 1
    # idempotent
    if header.strip() in src:
        return src
    return "".join(lines[:i] + [header] + lines[i:])

def _validate_and_report_fstring_issues(src: str, workdir: pathlib.Path) -> str:
    """
    Detect common f-string issues and log warnings.
    This is a safety net - codegen should generate correct code.
    """
    issues = []

    # Check for potential unescaped braces in f-strings
    import re
    # Pattern: f''' or f""" followed by content with { that might be problematic
    pattern = r"f['\"]{{3}}.*?\{[^{].*?['\"]{{3}}"

    matches = re.findall(pattern, src, re.DOTALL)
    for match in matches:
        # Check if there are single braces that look like JSON
        if re.search(r'\{["\w]+:', match) and not re.search(r'\{\{', match):
            issues.append(f"Potential unescaped brace in f-string: {match[:100]}...")

    if issues:
        warning_file = workdir / "codegen_warnings.txt"
        warning_file.write_text("\n".join(issues))
        print(f"[Runtime] Detected {len(issues)} potential f-string issues", file=sys.stderr)

    return src

def _fix_json_bools(src: str) -> str:
    """
    Replace NAME tokens 'true'/'false'/'null' with Python's True/False/None,
    preserving all original spacing and positions, and skipping strings/comments.
    Includes a fast path that returns src unchanged if none of the literals occur.
    """
    # Fast path: avoid tokenization if none of the JSON literals appear
    if ("true" not in src) and ("false" not in src) and ("null" not in src):
        return src

    mapping = {"true": "True", "false": "False", "null": "None"}
    out = []

    # Use TokenInfo objects to preserve exact spacing via start/end positions
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NAME and tok.string in mapping:
            tok = tok._replace(string=mapping[tok.string])
        out.append(tok)

    return tokenize.untokenize(out)

def _max_sid_from_context(outdir: pathlib.Path) -> int:
    try:
        p = outdir / "context.json"
        if not p.exists():
            return 0
        import json as _json
        data = _json.loads(p.read_text(encoding="utf-8"))
        ph = data.get("program_history") or []
        mx = 0
        for entry in ph:
            if not isinstance(entry, dict):
                continue
            rec = next(iter(entry.values()), {})  # {"<exec_id>": {...}}
            items = (((rec or {}).get("web_links_citations") or {}).get("items") or [])
            for it in items:
                try:
                    mx = max(mx, int(it.get("sid") or 0))
                except Exception:
                    pass
        return mx
    except Exception:
        return 0

def _module_parent_dirs(tool_modules: List[Tuple[str, object]]) -> List[str]:
    paths = []
    for name, mod in tool_modules or []:
        try:
            p = pathlib.Path(getattr(mod, "__file__", "")).resolve()
            if p.exists():
                paths.append(str(p.parent))
        except Exception:
            pass
    # de-dup while preserving order
    seen = set(); uniq = []
    for d in paths:
        if d not in seen:
            uniq.append(d); seen.add(d)
    return uniq

async def _run_subprocess(entry_path: pathlib.Path, *,
                          cwd: pathlib.Path,
                          env: dict,
                          timeout_s: int,
                          outdir: pathlib.Path,
                          allow_network: bool = True,
                          exec_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Environment toggles (per-call via env, or global via os.environ):
      - allow_network: True (default) -> network allowed
                       False          -> network disabled (--unshare-net)
    """

    import logging
    import ctypes
    log = logging.getLogger("agent.runtime")

    CLONE_NEWNET = 0x40000000
    EXECUTOR_UID = 1001
    EXECUTOR_GID = 1001

    def preexec_fn():
        """This runs in the child process BEFORE exec.
        We're still root here, so we can create namespace and drop privileges."""
        try:
            # 1. Create isolated network namespace (requires root)
            libc = ctypes.CDLL("libc.so.6")
            if libc.unshare(CLONE_NEWNET) != 0:
                raise OSError(f"unshare(CLONE_NEWNET) failed")

            # 2. Drop to unprivileged user
            os.setgid(EXECUTOR_GID)
            os.setuid(EXECUTOR_UID)

            log.info(f"[executor] Network isolated and dropped to UID {EXECUTOR_UID}")
        except Exception as e:
            log.error(f"[executor] Isolation failed: {e}")
            raise
    if not allow_network:
        # Original behavior
        log.info(f"[_run_subprocess] Starting isolated executor (root→uid 1001, no network)")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(entry_path),
            cwd=str(cwd),
            env=env,
            preexec_fn=preexec_fn,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        # Original behavior
        log.info(f"[_run_subprocess] Using plain subprocess for {entry_path}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(entry_path),
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    out: bytes = b""
    err: bytes = b""
    timed_out = False

    try:
        # Read and wait at the same time; avoids pipe deadlock
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

        # Give it a moment to shut down gracefully
        try:
            out2, err2 = await asyncio.wait_for(proc.communicate(), timeout=5)
            # Append any extra output we got after terminate()
            out += out2
            err += err2
        except asyncio.TimeoutError:
            # Force kill if it still doesn’t exit
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out2, err2 = await proc.communicate()
            out += out2
            err += err2
    finally:
        try:
            log_dir = outdir / "logs"
            out_path = log_dir / "runtime.out.log"
            err_path = log_dir / "runtime.err.log"
            errlog_path = log_dir / "errors.log"
            log_dir.mkdir(parents=True, exist_ok=True)

            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            eid = (exec_id or env.get("EXECUTION_ID") or "unknown")
            header = f"\n===== EXECUTION {eid} START {ts} =====\n".encode("utf-8")

            with open(out_path, "ab") as f:
                f.write(header)
                if out:
                    f.write(out)
                    if not out.endswith(b"\n"):
                        f.write(b"\n")
            with open(err_path, "ab") as f:
                f.write(header)
                if err:
                    f.write(err)
                    if not err.endswith(b"\n"):
                        f.write(b"\n")

            if timed_out or proc.returncode != 0:
                reason = "timeout" if timed_out else f"returncode={proc.returncode}"
                err_txt = err.decode("utf-8", errors="ignore")
                tail = err_txt[-4000:] if err_txt else ""
                with open(errlog_path, "ab") as f:
                    f.write(header)
                    f.write(f"[runtime] {reason}\n".encode("utf-8"))
                    if tail:
                        f.write(tail.encode("utf-8", errors="ignore"))
                        if not tail.endswith("\n"):
                            f.write(b"\n")
        except Exception:
            pass

    if timed_out:
        return {"error": "timeout", "seconds": timeout_s}

    return {"ok": proc.returncode == 0, "returncode": proc.returncode}

def _build_injected_header(*, globals_src: str, imports_src: str) -> str:
    from textwrap import dedent
    return dedent('''\
# === AGENT-RUNTIME HEADER (auto-injected, do not edit) ===
from pathlib import Path
import json as _json
import os, importlib, asyncio, atexit, signal, sys, traceback
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

import logging
import kdcube_ai_app.apps.utils.logging_config as logging_config

logging_config.configure_logging()
logger = logging.getLogger("agent.runtime")

# --- Directories / CV fallbacks ---
OUTPUT_DIR = OUTDIR_CV.get() or os.environ.get("OUTPUT_DIR")
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)
_EXEC_ID = os.environ.get("EXECUTION_ID") or "unknown"
logger.info(f"===== EXECUTION {_EXEC_ID} START =====")

# Ensure ContextVars are set even if only env was provided
try:
    if not OUTDIR_CV.get(""):
        od = os.environ.get("OUTPUT_DIR")
        if od:
            OUTDIR_CV.set(od)
    if not WORKDIR_CV.get(""):
        wd = os.environ.get("WORKDIR")
        if wd:
            WORKDIR_CV.set(wd)
except Exception:
    pass

# --- Portable spec handoff (context vars + model service + registry + communicator) ---
_PORTABLE_SPEC = os.environ.get("PORTABLE_SPEC")
# print(f"[Runtime Header] PORTABLE_SPEC {_PORTABLE_SPEC}", file=sys.stderr)
_TOOL_MODULES = _json.loads(os.environ.get("RUNTIME_TOOL_MODULES") or "[]")
_SHUTDOWN_MODULES = _json.loads(os.environ.get("RUNTIME_SHUTDOWN_MODULES") or "[]")

def _bootstrap_child():
    """
    - Apply env passthrough
    - Restore ALL ContextVars captured in the parent snapshot
    - Initialize ModelService/registry
    - Bind into every tool module
    - Rebuild ChatCommunicator if present
    """
    # Build the complete list of modules to bind into
    _BIND_TARGETS = list(_TOOL_MODULES or [])
    try:
        _ALIAS_TO_DYN = globals().get("TOOL_ALIAS_MAP", {}) or {}
        for _dyn in _ALIAS_TO_DYN.values():
            if _dyn and _dyn not in _BIND_TARGETS:
                _BIND_TARGETS.append(_dyn)
    except Exception as e:
        logger.error(f"Failed to build bind targets: {e}", exc_info=True)
    
    # Perform a single, idempotent bootstrap and bind into all modules
    try:
        if _PORTABLE_SPEC and _BIND_TARGETS:
            from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import bootstrap_bind_all as _bootstrap_all
            _bootstrap_all(_PORTABLE_SPEC, module_names=_BIND_TARGETS, bootstrap_env=True)
            logger.info(f"Bootstrap completed successfully for {len(_BIND_TARGETS)} modules")
    except Exception as e:
        # Log with full traceback
        logger.error(f"Bootstrap failed: {e}", exc_info=True)
        # Write error marker so we know bootstrap failed
        try:
            marker_file = Path(os.environ.get("OUTPUT_DIR", ".")) / "bootstrap_failed.txt"
            marker_file.write_text(f"Bootstrap error: {e}\\n{traceback.format_exc()}")
            logger.info(f"Bootstrap failure marker written to {marker_file}")
        except Exception as write_err:
            logger.error(f"Could not write bootstrap failure marker: {write_err}", exc_info=True)
            
_bootstrap_child()

# --- Globals provided by parent (must come before alias imports) ---
<GLOBALS_SRC>

result_filename = (
    globals().get("RESULT_FILENAME")
    or os.environ.get("RESULT_FILENAME")
    or "result.json"
)

# --- Dyn tool modules pre-registration (by file path) ---
# The parent passes:
#   TOOL_ALIAS_MAP   : {"io_tools": "dyn_io_tools_<hash>", ...}
#   TOOL_MODULE_FILES: {"io_tools": "/abs/path/to/io_tools.py", ...}
_ALIAS_TO_DYN  = globals().get("TOOL_ALIAS_MAP", {}) or {}
_ALIAS_TO_FILE = globals().get("TOOL_MODULE_FILES", {}) or {}
for _alias, _dyn_name in (_ALIAS_TO_DYN or {}).items():
    _path = (_ALIAS_TO_FILE or {}).get(_alias)
    if not _path:
        continue
    try:
        _spec = importlib.util.spec_from_file_location(_dyn_name, _path)
        _mod  = importlib.util.module_from_spec(_spec)
        sys.modules[_dyn_name] = _mod
        _spec.loader.exec_module(_mod)
    except Exception as e:
        # Don't silently fail - this will cause import errors later!
        logger.error(f"Failed to load {_dyn_name} from {_path}: {e}", exc_info=True)
        print(f"[ERROR] Module load failed: {_alias} -> {_dyn_name}: {e}", file=sys.stderr)

# Bind services into alias modules as well (idempotent)
try:
    if _PORTABLE_SPEC:
        from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import bootstrap_from_spec as _bs
        for _dyn_name in (_ALIAS_TO_DYN or {}).values():
            try:
                _m = importlib.import_module(_dyn_name)
                _bs(_PORTABLE_SPEC, tool_module=_m)
            except Exception:
                pass
except Exception:
    pass
    
# --- Tool alias imports (auto-injected) ---
<TOOL_IMPORTS_SRC>

# -------- Live progress cache (safe, in-process) --------
_PROGRESS = {
    "objective": "",
    "status": "In progress",
    "story": [],          # list[str]
    "out_dyn": {},        # slot_name -> slot dict (inline/file)
}
_FINALIZED = False  # prevents late checkpoints from overwriting the final result

def _build_project_log_md() -> str:
    lines = []
    lines.append("# Project Log")
    lines.append("")
    lines.append("## Objective")
    lines.append(str(_PROGRESS.get("objective", "")))
    lines.append("")
    lines.append("## Status")
    lines.append(str(_PROGRESS.get("status", "")))
    lines.append("")
    lines.append("## Story")
    story = " ".join(_PROGRESS.get("story") or [])
    lines.append(story)
    lines.append("")
    lines.append("## Produced Slots")

    for name, data in (_PROGRESS.get("out_dyn") or {}).items():
        if name == "project_log":
            continue
        t = (data.get("type") or "inline")
        desc = data.get("description", "")
        is_draft = data.get("draft", False)
        draft_marker = " [DRAFT]" if is_draft else ""
        
        lines.append(f"### {name} ({t}){draft_marker}")
        if desc:
            lines.append(desc)
        if t == "file":
            mime = data.get("mime", "")
            path = data.get("path", "")
            if mime:
                lines.append(f"**Mime:** {mime}")
            if path:
                lines.append(f"**Filename:** {path}")
        else:
            fmt = data.get("format", "")
            if fmt:
                lines.append(f"**Format:** {fmt}")

    return "\\n".join(lines).strip()

def _refresh_project_log_slot():
    """Keep 'project_log' as a first-class slot in _PROGRESS['out_dyn']."""
    od = _PROGRESS["out_dyn"]
    md = _build_project_log_md()
    od["project_log"] = {
        "type": "inline",
        "format": "markdown",
        "description": "Live run log",
        "value": md,
    }

def _dump_delta_cache_file():
    """
    Best-effort: write communicator delta cache to OUTPUT/delta_aggregates.json.
    Safe to call multiple times; last write wins.
    """
    try:
        comm = None
        try:
            # prefer explicitly bound communicator if available
            comm = globals().get("_comm_obj") or get_comm()
        except Exception:
            pass
        if not comm:
            return
        dest = OUTPUT / "delta_aggregates.json"
        try:
            ok = comm.dump_delta_cache(dest)
            if not ok:
                # fallback: inline export + write
                aggs = comm.export_delta_cache(merge_text=False)
                dest.write_text(_json.dumps({"items": aggs}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass
        
async def set_progress(*, objective=None, status=None, story_append=None, out_dyn_patch=None, flush=False):
    if objective is not None:
        _PROGRESS["objective"] = str(objective)
    if status is not None:
        _PROGRESS["status"] = str(status)
    if story_append:
        if isinstance(story_append, (list, tuple)):
            _PROGRESS["story"].extend([str(s) for s in story_append])
        else:
            _PROGRESS["story"].append(str(story_append))
    if out_dyn_patch:
        od = _PROGRESS["out_dyn"]
        for k, v in (out_dyn_patch or {}).items():
            od[k] = v

    _refresh_project_log_slot()

    if flush and not globals().get("_FINALIZED", False):
        await _write_checkpoint(reason="progress", managed=True)
        
# initialize with an empty log so it's present even before first set_progress()
_refresh_project_log_slot()

async def _write_checkpoint(reason: str = "checkpoint", managed: bool = True):
    if globals().get("_FINALIZED", False):
        return
    try:
        # ensure log is up-to-date at checkpoint time
        _refresh_project_log_slot()
        g = globals()
        payload = {
            "ok": False,
            "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
            "contract": (g.get("CONTRACT") or {}),
            "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
            "error": {
                "where": "runtime",
                "details": "",
                "error": reason,
                "description": reason,
                "managed": bool(managed),
            },
        }
        await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
        )
    except Exception:
        # best-effort only
        pass

async def done():
    # ensure latest log
    _refresh_project_log_slot()
    g = globals()
    # normalize status
    status = (_PROGRESS.get("status") or "Completed")
    if status.lower().startswith("in progress"):
        _PROGRESS["status"] = "Completed"
        _refresh_project_log_slot()
    payload = {
        "ok": True,
        "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
    }
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        _dump_delta_cache_file()
    finally:
        pass
    return await agent_io_tools.save_ret(data=_json.dumps(payload, ensure_ascii=False), filename=result_filename)

async def fail(description: str,
               where: str = "",
               error: str = "",
               details: str = "",
               managed: bool = True,
               out_dyn: dict | None = None):
    """
    Managed failure helper. Always writes result.json with a normalized envelope.
    Uses the canonical _PROGRESS['out_dyn'] which already contains 'project_log'.
    """
    # update status for the log and refresh slot
    _PROGRESS["status"] = "Failed"
    _refresh_project_log_slot()

    g = globals()
    payload = {
        "ok": False,
        "objective": str(_PROGRESS.get("objective") or description),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
        "error": {
            "where": (where or "runtime"),
            "details": str(details or ""),
            "error": str(error or ""),
            "description": description,
            "managed": bool(managed),
        },
    }
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        _dump_delta_cache_file()
    finally:
        pass
    return await agent_io_tools.save_ret(data=_json.dumps(payload, ensure_ascii=False), filename=result_filename)

def _on_term(signum, frame):
    """Handle SIGTERM/SIGINT by checkpointing and exiting."""
    if globals().get("_FINALIZED", False):
        # Already finalized, just exit
        sys.exit(0)
    
    try:
        # Try to persist deltas first (best-effort)
        try:
            _dump_delta_cache_file()
        except Exception:
            pass
        # Try to checkpoint synchronously if possible
        try:
            loop = asyncio.get_running_loop()
            # Schedule checkpoint but don't wait
            loop.create_task(_write_checkpoint(reason=f"signal:{signum}", managed=True))
            # Give it a moment to write
            # loop.run_until_complete(asyncio.sleep(0.1))
        except RuntimeError:
            # No loop running - create one just for checkpoint
            try:
                asyncio.run(_write_checkpoint(reason=f"signal:{signum}", managed=True))
            except:
                pass
    except Exception:
        pass
    finally:
        # Exit with appropriate signal code
        # Don't use os._exit in development; use sys.exit for clean shutdown
        sys.exit(128 + signum)  # Standard Unix convention

# --- Module shutdown on exit (KB, tool modules etc.) ---
async def _async_shutdown_mod(mod):
    try:
        if hasattr(mod, "shutdown") and callable(mod.shutdown):
            maybe = mod.shutdown()
            if asyncio.iscoroutine(maybe):
                await maybe
        elif hasattr(mod, "close") and callable(mod.close):
            maybe = mod.close()
            if asyncio.iscoroutine(maybe):
                await maybe
    except Exception:
        pass

def _sync_shutdown_all():
    try:
        import importlib
        mods = []
        for name in set(_SHUTDOWN_MODULES or []):
            try:
                mods.append(importlib.import_module(name))
            except Exception:
                pass
        async def _run():
            for m in mods:
                await _async_shutdown_mod(m)
        asyncio.run(_run())
    except Exception:
        pass

def _on_atexit():
    """
    Atexit marker. Should only run if done()/fail() wasn't called.
    DO NOT do async operations here - event loop is closed.
    """
    try:
        if os.environ.get("EXEC_NO_UNEXPECTED_EXIT") == "1":
            return
        # Persist deltas even if we didn't call done()/fail() (e.g., tool-only exec)
        try:
            _dump_delta_cache_file()
        except Exception:
            pass
        if not globals().get("_FINALIZED", False):
            import sys
            import traceback
            
            # Try to understand why we're here
            marker_path = Path(os.environ.get("OUTPUT_DIR", ".")) / "unexpected_exit.txt"
            exc_info = sys.exc_info()
            
            details = [
                "Process exiting without explicit done()/fail() call",
                f"_FINALIZED={globals().get('_FINALIZED', 'not set')}",
                f"Exception info: {exc_info}",
            ]
            
            # Check if there was an uncaught exception
            if exc_info[0] is not None:
                details.append(f"Uncaught exception: {exc_info[0].__name__}: {exc_info[1]}")
                details.append(traceback.format_exc())
            
            marker_path.write_text("\\n".join(details))
            print("[Runtime] " + details[0], file=sys.stderr)
    except Exception as e:
        print(f"[Runtime] atexit handler error: {e}", file=sys.stderr)

try:
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
except Exception:
    pass
# atexit.register(lambda: asyncio.run(_write_checkpoint(reason="atexit", managed=True)))
atexit.register(_on_atexit)
atexit.register(_sync_shutdown_all)

# === END HEADER ===
# === CHAT COMMUNICATOR RECONSTRUCTION ===
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatEnvelope, ServiceCtx, ConversationCtx
)
from kdcube_ai_app.apps.chat.emitters import (ChatRelayCommunicator, ChatCommunicator)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm, get_comm

def _rebuild_communicator_from_spec(spec: dict) -> ChatCommunicator:
    REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
    REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
    REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"
    # redis_url = (spec or {}).get("redis_url") or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_url = REDIS_URL
    if redis_url.startswith('"') and redis_url.endswith('"'):
        redis_url = redis_url[1:-1]
    redis_url_safe = redis_url.replace(REDIS_PASSWORD, "REDIS_PASSWORD") if REDIS_PASSWORD else redis_url 
    logger.info(f"Redis url: {redis_url_safe}")
    print(f"Redis url: {redis_url_safe}")
    channel   = (spec or {}).get("channel")   or "chat.events"
    relay = ChatRelayCommunicator(redis_url=redis_url, channel=channel)
    svc = ServiceCtx(**(spec.get("service") or {}))
    
    conv = ConversationCtx(**(spec.get("conversation") or {}))
    comm = ChatCommunicator(
        emitter=relay,
        service=svc.model_dump() if hasattr(svc, "model_dump") else dict(svc),
        conversation=conv.model_dump() if hasattr(conv, "model_dump") else dict(conv),
        room=spec.get("room"),
        target_sid=spec.get("target_sid"),
        # tenant=svc.tenant,
        # project=svc.project,
        user_id=spec.get("user_id"),
        user_type=spec.get("user_type"),
        tenant=spec.get("tenant"),
        project=spec.get("project"),
    )
    return comm

try:
    _COMM_SPEC = globals().get("COMM_SPEC") or {}
    if _COMM_SPEC:
        _comm_obj = _rebuild_communicator_from_spec(_COMM_SPEC)
        set_comm(_comm_obj)
        globals()["_comm_obj"] = _comm_obj
        logger.info("ChatCommunicator initialized successfully")
except Exception as _comm_err:
    logger.error(f"Communicator setup failed: {_comm_err}", exc_info=True)
    print(f"[ERROR] ChatCommunicator failed: {_comm_err}", file=sys.stderr)
    
# === END COMMUNICATOR SETUP ===
''').replace('<GLOBALS_SRC>', globals_src).replace('<TOOL_IMPORTS_SRC>', imports_src)

def _build_iso_injected_header(*, globals_src: str, imports_src: str) -> str:
    """
    Build the executor-side runtime header.

    IMPORTANT:
    - No bootstrap of ModelService / KB / communicator here.
    - No _dump_delta_cache_file here.
      All privileged state (COMM, KB, secrets) lives in the supervisor.
    - Executor owns only:
        * OUTDIR / WORKDIR (via ContextVars or env)
        * _PROGRESS + project_log
        * set_progress / done / fail that proxy to supervisor via io_tools.save_ret.
    """
    from textwrap import dedent
    return dedent('''\
# === AGENT-RUNTIME HEADER (auto-injected, do not edit) ===
from pathlib import Path
import json as _json
import os, asyncio, atexit, signal, sys, importlib
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

import logging
import kdcube_ai_app.apps.utils.logging_config as logging_config

logging_config.configure_logging()
logger = logging.getLogger("agent.runtime")

# --- Directories / CV fallbacks ---
OUTPUT_DIR = OUTDIR_CV.get() or os.environ.get("OUTPUT_DIR")
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)

# Ensure ContextVars are set even if only env was provided
try:
    if not OUTDIR_CV.get(""):
        od = os.environ.get("OUTPUT_DIR")
        if od:
            OUTDIR_CV.set(od)
    if not WORKDIR_CV.get(""):
        wd = os.environ.get("WORKDIR")
        if wd:
            WORKDIR_CV.set(wd)
except Exception:
    pass

# --- Globals provided by parent (must come before alias imports) ---
<GLOBALS_SRC>

result_filename = (
    globals().get("RESULT_FILENAME")
    or os.environ.get("RESULT_FILENAME")
    or "result.json"
)

# --- Dyn tool modules pre-registration (by file path) ---
# CRITICAL: Load dynamic modules before import statements try to use them
_ALIAS_TO_DYN  = globals().get("TOOL_ALIAS_MAP", {}) or {}
_ALIAS_TO_FILE = globals().get("TOOL_MODULE_FILES", {}) or {}
_RAW_TOOL_SPECS = globals().get("RAW_TOOL_SPECS", []) or []

# Build a map from alias → module name for library modules
_alias_to_module: dict[str, str] = {}
for _spec in _RAW_TOOL_SPECS:
    if "module" in _spec and _spec.get("alias"):
        _alias_to_module[_spec["alias"]] = _spec["module"]

# Preload dyn alias modules
for _alias, _dyn_name in (_ALIAS_TO_DYN or {}).items():
    _path = (_ALIAS_TO_FILE or {}).get(_alias)
    
    # If path is None, try to resolve from RAW_TOOL_SPECS module name
    if not _path and _alias in _alias_to_module:
        _module_name = _alias_to_module[_alias]
        try:
            _spec_obj = importlib.util.find_spec(_module_name)
            if _spec_obj and _spec_obj.origin:
                _path = _spec_obj.origin
                logger.info(
                    f"[executor] resolved library module: "
                    f"{_alias} → {_module_name} → {_path}"
                )
        except Exception as _e:
            logger.error(
                f"[executor] failed to resolve module {_module_name}: {_e}",
                exc_info=True
            )
            continue
    
    if not _path:
        logger.warning(f"[executor] no path for alias {_alias}, skipping dyn load")
        continue
        
    try:
        _spec = importlib.util.spec_from_file_location(_dyn_name, _path)
        if _spec is None or _spec.loader is None:
            logger.error(f"[executor] Could not create spec for {_dyn_name} from {_path}")
            continue
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_dyn_name] = _mod
        _spec.loader.exec_module(_mod)
        logger.info(f"[executor] Loaded dynamic module {_dyn_name} from {_path}")
    except Exception as e:
        logger.error(f"[executor] Failed to load {_dyn_name} from {_path}: {e}", exc_info=True)
        print(f"[ERROR] Module load failed: {_alias} -> {_dyn_name}: {e}", file=sys.stderr)

# --- Tool alias imports (auto-injected) ---
<TOOL_IMPORTS_SRC>

# -------- Live progress cache (executor-local) --------
_PROGRESS = {
    "objective": "",
    "status": "In progress",
    "story": [],          # list[str]
    "out_dyn": {},        # slot_name -> slot dict (inline/file)
}
_FINALIZED = False  # prevents late checkpoints from overwriting the final result

def _build_project_log_md() -> str:
    lines = []
    lines.append("# Project Log")
    lines.append("")
    lines.append("## Objective")
    lines.append(str(_PROGRESS.get("objective", "")))
    lines.append("")
    lines.append("## Status")
    lines.append(str(_PROGRESS.get("status", "")))
    lines.append("")
    lines.append("## Story")
    story = " ".join(_PROGRESS.get("story") or [])
    lines.append(story)
    lines.append("")
    lines.append("## Produced Slots")

    for name, data in (_PROGRESS.get("out_dyn") or {}).items():
        if name == "project_log":
            continue
        t = (data.get("type") or "inline")
        desc = data.get("description", "")
        is_draft = data.get("draft", False)
        draft_marker = " [DRAFT]" if is_draft else ""

        lines.append(f"### {name} ({t}){draft_marker}")
        if desc:
            lines.append(desc)
        if t == "file":
            mime = data.get("mime", "")
            path = data.get("path", "")
            if mime:
                lines.append(f"**Mime:** {mime}")
            if path:
                lines.append(f"**Filename:** {path}")
        else:
            fmt = data.get("format", "")
            if fmt:
                lines.append(f"**Format:** {fmt}")

    return "\\n".join(lines).strip()

def _refresh_project_log_slot():
    """Keep 'project_log' as a first-class slot in _PROGRESS['out_dyn']."""
    od = _PROGRESS["out_dyn"]
    md = _build_project_log_md()
    od["project_log"] = {
        "type": "inline",
        "format": "markdown",
        "description": "Live run log",
        "value": md,
    }

async def set_progress(*, objective=None, status=None, story_append=None, out_dyn_patch=None, flush=False):
    if objective is not None:
        _PROGRESS["objective"] = str(objective)
    if status is not None:
        _PROGRESS["status"] = str(status)
    if story_append:
        if isinstance(story_append, (list, tuple)):
            _PROGRESS["story"].extend([str(s) for s in story_append])
        else:
            _PROGRESS["story"].append(str(story_append))
    if out_dyn_patch:
        od = _PROGRESS["out_dyn"]
        for k, v in (out_dyn_patch or {}).items():
            od[k] = v

    _refresh_project_log_slot()

    if flush and not globals().get("_FINALIZED", False):
        await _write_checkpoint(reason="progress", managed=True)

# initialize with an empty log so it's present even before first set_progress()
_refresh_project_log_slot()

async def _write_checkpoint(reason: str = "checkpoint", managed: bool = True):
    if globals().get("_FINALIZED", False):
        return
    try:
        _refresh_project_log_slot()
        g = globals()
        payload = {
            "ok": False,
            "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
            "contract": (g.get("CONTRACT") or {}),
            "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
            "error": {
                "where": "runtime",
                "details": "",
                "error": reason,
                "description": reason,
                "managed": bool(managed),
            },
        }
        await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
        )
    except Exception:
        # best-effort only
        pass

async def done():
    # ensure latest log
    _refresh_project_log_slot()
    g = globals()
    status = (_PROGRESS.get("status") or "Completed")
    if status.lower().startswith("in progress"):
        _PROGRESS["status"] = "Completed"
        _refresh_project_log_slot()
    payload = {
        "ok": True,
        "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
    }
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        return await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
        )
    except Exception:
        # nothing more we can do here
        return None

async def fail(description: str,
               where: str = "",
               error: str = "",
               details: str = "",
               managed: bool = True,
               out_dyn: dict | None = None):
    """
    Managed failure helper. Always writes result.json with a normalized envelope.
    Uses the canonical _PROGRESS['out_dyn'] which already contains 'project_log'.
    """
    _PROGRESS["status"] = "Failed"
    _refresh_project_log_slot()

    g = globals()
    payload = {
        "ok": False,
        "objective": str(_PROGRESS.get("objective") or description),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
        "error": {
            "where": (where or "runtime"),
            "details": str(details or ""),
            "error": str(error or ""),
            "description": description,
            "managed": bool(managed),
        },
    }
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        return await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
        )
    except Exception:
        return None

def _on_term(signum, frame):
    """
    Handle SIGTERM/SIGINT by best-effort checkpoint and exit.
    Done/Fail remain executor responsibilities; supervisor is not involved here.
    """
    try:
        if globals().get("_FINALIZED", False):
            sys.exit(0)

        try:
            asyncio.run(_write_checkpoint(reason=f"signal:{signum}", managed=True))
        except Exception:
            pass
    finally:
        # standard Unix convention for "terminated by signal"
        sys.exit(128 + signum)

def _on_atexit():
    """
    Atexit marker. Should only run if done()/fail() wasn't called.
    DO NOT do async operations here - event loop may be closed.
    """
    try:
        if os.environ.get("EXEC_NO_UNEXPECTED_EXIT") == "1":
            return
        if globals().get("_FINALIZED", False):
            return

        marker_path = Path(os.environ.get("OUTPUT_DIR", ".")) / "unexpected_exit.txt"
        details = [
            "Process exiting without explicit done()/fail() call",
            f"_FINALIZED={globals().get('_FINALIZED', 'not set')}",
        ]
        marker_path.write_text("\\n".join(details), encoding="utf-8")
        print("[Runtime] " + details[0], file=sys.stderr)
    except Exception:
        # avoid raising from atexit
        pass

try:
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
except Exception:
    # e.g., not allowed in some embedding environments
    pass

atexit.register(_on_atexit)

# === END HEADER ===
''').replace('<GLOBALS_SRC>', globals_src).replace('<TOOL_IMPORTS_SRC>', imports_src)

def _build_iso_injected_header_step_artifacts(*, globals_src: str, imports_src: str) -> str:
    """
    Build the executor-side runtime header.

    IMPORTANT:
    - No bootstrap of ModelService / KB / communicator here.
    - No _dump_delta_cache_file here.
      All privileged state (COMM, KB, secrets) lives in the supervisor.
    - Executor owns only:
        * OUTDIR / WORKDIR (via ContextVars or env)
        * _PROGRESS + project_log
        * set_progress / done / fail that proxy to supervisor via io_tools.save_ret.
    """
    from textwrap import dedent
    return dedent('''\
# === AGENT-RUNTIME HEADER (auto-injected, do not edit) ===
from pathlib import Path
import json as _json
import os, asyncio, atexit, signal, sys, importlib
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

import logging
import kdcube_ai_app.apps.utils.logging_config as logging_config

logging_config.configure_logging()
logger = logging.getLogger("agent.runtime")

# --- Directories / CV fallbacks ---
OUTPUT_DIR = OUTDIR_CV.get() or os.environ.get("OUTPUT_DIR")
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)

# Ensure ContextVars are set even if only env was provided
try:
    if not OUTDIR_CV.get(""):
        od = os.environ.get("OUTPUT_DIR")
        if od:
            OUTDIR_CV.set(od)
    if not WORKDIR_CV.get(""):
        wd = os.environ.get("WORKDIR")
        if wd:
            WORKDIR_CV.set(wd)
except Exception:
    pass

# --- Globals provided by parent (must come before alias imports) ---
<GLOBALS_SRC>

result_filename = (
    globals().get("RESULT_FILENAME")
    or os.environ.get("RESULT_FILENAME")
    or "result.json"
)
print(f"Effective result filename: {result_filename}")

# --- Dyn tool modules pre-registration (by file path) ---
# CRITICAL: Load dynamic modules before import statements try to use them
_ALIAS_TO_DYN  = globals().get("TOOL_ALIAS_MAP", {}) or {}
_ALIAS_TO_FILE = globals().get("TOOL_MODULE_FILES", {}) or {}
_RAW_TOOL_SPECS = globals().get("RAW_TOOL_SPECS", []) or []

# Build alias -> module map for library modules (optional)
_alias_to_module: dict[str, str] = {}
for _spec in _RAW_TOOL_SPECS:
    if isinstance(_spec, dict) and _spec.get("alias") and _spec.get("module"):
        _alias_to_module[_spec["alias"]] = _spec["module"]

# Preload dyn alias modules
for _alias, _dyn_name in (_ALIAS_TO_DYN or {}).items():
    _path = (_ALIAS_TO_FILE or {}).get(_alias)
    
    # If path is None, try to resolve from RAW_TOOL_SPECS module name
    if not _path and _alias in _alias_to_module:
        _module_name = _alias_to_module[_alias]
        try:
            _spec_obj = importlib.util.find_spec(_module_name)
            if _spec_obj and _spec_obj.origin:
                _path = _spec_obj.origin
                logger.info(
                    f"[executor] resolved library module: "
                    f"{_alias} → {_module_name} → {_path}"
                )
        except Exception as _e:
            logger.error(
                f"[executor] failed to resolve module {_module_name}: {_e}",
                exc_info=True
            )
            continue
    
    if not _path:
        logger.warning(f"[executor] no path for alias {_alias}, skipping dyn load")
        continue
        
    try:
        _spec = importlib.util.spec_from_file_location(_dyn_name, _path)
        if _spec is None or _spec.loader is None:
            logger.error(f"[executor] Could not create spec for {_dyn_name} from {_path}")
            continue
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_dyn_name] = _mod
        _spec.loader.exec_module(_mod)
        logger.info(f"[executor] Loaded dynamic module {_dyn_name} from {_path}")
    except Exception as e:
        logger.error(f"[executor] Failed to load {_dyn_name} from {_path}: {e}", exc_info=True)
        print(f"[ERROR] Module load failed: {_alias} -> {_dyn_name}: {e}", file=sys.stderr)

# --- Tool alias imports (auto-injected) ---
<TOOL_IMPORTS_SRC>

# -------- Live progress cache (executor-local) --------
_PROGRESS = {
    "objective": "",
    "status": "In progress",
    "story": [],          # list[str]
    "out_dyn": {},        # artifact_id -> artifact payload (inline/file)
}
_FINALIZED = False  # prevents late checkpoints from overwriting the final result

def _build_project_log_md() -> str:
    lines = []
    lines.append("# Project Log")
    lines.append("")
    lines.append("## Objective")
    lines.append(str(_PROGRESS.get("objective", "")))
    lines.append("")
    lines.append("## Status")
    lines.append(str(_PROGRESS.get("status", "")))
    lines.append("")
    lines.append("## Story")
    story = " ".join(_PROGRESS.get("story") or [])
    lines.append(story)
    lines.append("")
    lines.append("## Produced Artifacts")

    for name, data in (_PROGRESS.get("out_dyn") or {}).items():
        if name == "project_log":
            continue
        t = (data.get("type") or "inline")
        desc = data.get("description", "")
        is_draft = bool(data.get("draft", False))
        draft_marker = " [DRAFT]" if is_draft else ""

        lines.append(f"### {name} ({t}){draft_marker}")
        if desc:
            lines.append(desc)
        if t == "file":
            mime = data.get("mime", "")
            path = data.get("path", "")
            if not isinstance(path, str) or not path:
                path = data.get("filename", "")
                if not isinstance(path, str):
                    path = ""
            if mime:
                lines.append(f"**Mime:** {mime}")
            if path:
                lines.append(f"**Filename:** {path}")
        else:
            fmt = data.get("format", "")
            if fmt:
                lines.append(f"**Format:** {fmt}")

    return "\\n".join(lines).strip()

def _refresh_project_log_slot():
    """Keep 'project_log' as a first-class slot in _PROGRESS['out_dyn']."""
    od = _PROGRESS["out_dyn"]
    md = _build_project_log_md()
    od["project_log"] = {
        "type": "inline",
        "format": "markdown",
        "description": "Live run log",
        "value": md,
    }

def _apply_artifact_update(artifact):
    """
    Accept either:
      A) Single artifact dict with ('name' or 'artifact_id') and 'type'
         -> stored under that id (without the name fields)
      B) Mapping dict: {artifact_id: artifact_payload, ...}
         -> each stored as-is
    """
    if artifact is None:
        return

    if not isinstance(artifact, dict):
        raise TypeError("artifact must be a dict")

    def _coerce_file_path(val):
        if isinstance(val, Path):
            return str(val)
        return val

    od = _PROGRESS["out_dyn"]

    # Case A: looks like a single artifact object
    if ("type" in artifact) and (("name" in artifact) or ("artifact_id" in artifact)):
        aid = artifact.get("name") or artifact.get("artifact_id")
        if not aid:
            raise ValueError("artifact missing name/artifact_id")
        obj = dict(artifact)
        obj.pop("name", None)
        obj.pop("artifact_id", None)
        if obj.get("type") == "file" and "path" in obj:
            obj["path"] = _coerce_file_path(obj.get("path"))
        od[str(aid)] = obj
        return

    # Case B: treat as mapping patch
    for k, v in artifact.items():
        if isinstance(v, dict) and v.get("type") == "file" and "path" in v:
            v = dict(v)
            v["path"] = _coerce_file_path(v.get("path"))
        od[str(k)] = v
    print(f"[Runtime] Applied artifact update: {artifact}")

async def _write_checkpoint(reason: str = "checkpoint", managed: bool = True):
    if globals().get("_FINALIZED", False):
        return
    try:
        _refresh_project_log_slot()
        g = globals()
        payload = {
            "ok": False,
            "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
            "contract": (g.get("CONTRACT") or {}),
            "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
            "error": {
                "where": "runtime",
                "details": "",
                "error": reason,
                "description": reason,
                "managed": bool(managed),
            },
        }
        out_dyn = dict(_PROGRESS.get("out_dyn") or {})
        print(f"[Runtime] Writing checkpoint.out_dyn={out_dyn}")
        await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
            artifact_lvl="artifact",
        )
    except Exception:
        # best-effort only
        pass

async def set_progress(*, objective=None, status=None, story_append=None, artifact=None, flush: bool = False):
    if objective is not None:
        _PROGRESS["objective"] = str(objective)
    if status is not None:
        print(f"[Runtime] Progress status updated: {status}")
        _PROGRESS["status"] = str(status)

    if story_append:
        if isinstance(story_append, (list, tuple)):
            _PROGRESS["story"].extend([str(s) for s in story_append])
        else:
            _PROGRESS["story"].append(str(story_append))

    if artifact is not None:
        print(f"[Runtime] artifact update: {artifact}")
        _apply_artifact_update(artifact)
        print(f"[Runtime] artifact update completed: {artifact}")

    _refresh_project_log_slot()

    if flush and not globals().get("_FINALIZED", False):
        await _write_checkpoint(reason="progress", managed=True)

# ensure project_log exists from the start
_refresh_project_log_slot()

async def done():
    print("[Runtime] done() called, finalizing result")
    # ensure latest log
    _refresh_project_log_slot()
    g = globals()
    status = (_PROGRESS.get("status") or "Completed")
    if status.lower().startswith("in progress"):
        _PROGRESS["status"] = "Completed"
        _refresh_project_log_slot()
    out_dyn = dict(_PROGRESS.get("out_dyn") or {})
    payload = {
        "ok": True,
        "objective": str(_PROGRESS.get("objective") or g.get("objective") or g.get("OBJECTIVE") or ""),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": out_dyn,
    }
    print(f"[Runtime] done(). out_dyn={out_dyn}")
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        return await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
            artifact_lvl="artifact",
        )
        print(f"[Runtime] done(). ret saved to {result_filename}")
    except Exception:
        # nothing more we can do here
        import traceback
        print(traceback.format_exc(), file=sys.stderr)
        return None

async def fail(description: str,
               where: str = "",
               error: str = "",
               details: str = "",
               managed: bool = True):
    """
    Managed failure helper. Always writes result.json with a normalized envelope.
    Uses the canonical _PROGRESS['out_dyn'] which already contains 'project_log'.
    """
    _PROGRESS["status"] = "Failed"
    _refresh_project_log_slot()

    g = globals()
    payload = {
        "ok": False,
        "objective": str(_PROGRESS.get("objective") or description),
        "contract": (g.get("CONTRACT") or {}),
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {}),
        "error": {
            "where": (where or "runtime"),
            "details": str(details or ""),
            "error": str(error or ""),
            "description": description,
            "managed": bool(managed),
        },
    }
    globals()["_FINALIZED"] = True  # set BEFORE writing final file
    try:
        return await agent_io_tools.save_ret(
            data=_json.dumps(payload, ensure_ascii=False),
            filename=result_filename,
            artifact_lvl="artifact",
        )
    except Exception:
        return None

def _on_term(signum, frame):
    """
    Handle SIGTERM/SIGINT by best-effort checkpoint and exit.
    Done/Fail remain executor responsibilities; supervisor is not involved here.
    """
    try:
        if globals().get("_FINALIZED", False):
            sys.exit(0)

        try:
            asyncio.run(_write_checkpoint(reason=f"signal:{signum}", managed=True))
        except Exception:
            pass
    finally:
        # standard Unix convention for "terminated by signal"
        sys.exit(128 + signum)

def _on_atexit():
    """
    Atexit marker. Should only run if done()/fail() wasn't called.
    DO NOT do async operations here - event loop may be closed.
    """
    try:
        if os.environ.get("EXEC_NO_UNEXPECTED_EXIT") == "1":
            return
        if globals().get("_FINALIZED", False):
            return

        marker_path = Path(os.environ.get("OUTPUT_DIR", ".")) / "unexpected_exit.txt"
        details = [
            "Process exiting without explicit done()/fail() call",
            f"_FINALIZED={globals().get('_FINALIZED', 'not set')}",
        ]
        marker_path.write_text("\\n".join(details), encoding="utf-8")
        print("[Runtime] " + details[0], file=sys.stderr)
    except Exception:
        # avoid raising from atexit
        pass

try:
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
except Exception:
    # e.g., not allowed in some embedding environments
    pass

atexit.register(_on_atexit)

# === END HEADER ===
''').replace('<GLOBALS_SRC>', globals_src).replace('<TOOL_IMPORTS_SRC>', imports_src)

class _InProcessRuntime:
    def __init__(self, logger: AgentLogger):
        self.log = logger or AgentLogger("tool_runtime")

    def _ensure_modules_on_sys_modules(self, modules: List[Tuple[str, object]]):
        """Make sure codegen can 'from <name> import tools as <alias>' for each module."""
        for name, mod in modules or []:
            if name and name not in sys.modules:
                sys.modules[name] = mod

    async def execute_py_code(
            self,
            *,
            workdir: pathlib.Path,
            output_dir: pathlib.Path,
            bundle_root: str|None,
            tool_modules: List[Tuple[str, object]],
            globals: Dict[str, Any] | None = None,
            isolation: Optional[Literal["none", "local_network", "docker", "local"]] = "none",
            timeout_s: int = 90,
            extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute workdir/main.py in an isolated runtime."""

        # Make a working copy so we don't mutate caller's dict
        g = dict(globals or {})
        exec_id = (extra_env or {}).get("EXECUTION_ID") or g.get("EXECUTION_ID") or g.get("RESULT_FILENAME")
        if not exec_id:
            exec_id = f"run-{int(time.time() * 1000)}"
        if extra_env is None:
            extra_env = {}
        extra_env.setdefault("EXECUTION_ID", exec_id)

        # Compute tool module names (needed by both docker and local)
        alias_map = g.get("TOOL_ALIAS_MAP") or {}
        tool_module_names: List[str] = [name for name, _ in (tool_modules or []) if name]
        for dyn_name in alias_map.values():
            if dyn_name and dyn_name not in tool_module_names:
                tool_module_names.append(dyn_name)

        # --- DOCKER BRANCH: Just delegate, don't touch files ---
        if isolation == "docker":
            from kdcube_ai_app.apps.chat.sdk.runtime.docker import docker as docker_runtime
            network_mode = os.environ.get("PY_CODE_EXEC_NETWORK_MODE", "host")
            # Docker will handle everything - just pass globals as-is
            # (including PORTABLE_SPEC which supervisor needs)
            return await docker_runtime.run_py_in_docker(
                workdir=workdir,
                outdir=output_dir,
                runtime_globals=g,  # Keep PORTABLE_SPEC in here!
                tool_module_names=tool_module_names,
                logger=self.log,
                timeout_s=timeout_s,
                bundle_root=pathlib.Path(bundle_root).resolve() if bundle_root else None,
                extra_env=extra_env,  # propagate explicit env (e.g., EXECUTION_MODE)
                network_mode=network_mode
            )

        # --- LOCAL BRANCH: Prepare file and run subprocess ---
        workdir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        main_path = workdir / "main.py"
        if not main_path.exists():
            raise FileNotFoundError(f"main.py not found in workdir: {workdir}")

        # Read & transform main.py
        src = main_path.read_text(encoding="utf-8")
        src = _fix_json_bools(src)
        src = _validate_and_report_fstring_issues(src, workdir)

        # Build imports for header
        imports_src = ""
        for alias, mod_name in (alias_map or {}).items():
            imports_src += f"\nfrom {mod_name} import tools as {alias}\n"

        # Build globals for header (exclude PORTABLE_SPEC from injected code)
        globals_src = ""
        for k, v in g.items():
            if k and k != "__name__" and k not in {"PORTABLE_SPEC", "PORTABLE_SPEC_JSON"}:
                globals_src += f"\n{k} = {repr(v)}\n"

        # Inject header
        injected_header = _build_injected_header(globals_src=globals_src, imports_src=imports_src)
        src = _inject_header_after_future(src, injected_header)
        main_path.write_text(src, encoding="utf-8")

        # Build subprocess env
        child_env = os.environ.copy()
        child_env["OUTPUT_DIR"] = str(output_dir)
        child_env["WORKDIR"] = str(workdir)
        child_env["LOG_DIR"] = str(output_dir / "logs")
        child_env["LOG_FILE_PREFIX"] = "executor"
        if extra_env:
            for k, v in extra_env.items():
                if k in {"WORKDIR", "OUTPUT_DIR"}:
                    continue
                child_env[k] = v

        # Serialize runtime_globals (KEEP PORTABLE_SPEC in it for bootstrap to use)
        ps = g.get("PORTABLE_SPEC_JSON") or g.get("PORTABLE_SPEC")
        if ps is not None:
            portable_spec_str = ps if isinstance(ps, str) else json.dumps(ps, ensure_ascii=False)
            child_env["PORTABLE_SPEC"] = portable_spec_str

        child_env["RUNTIME_TOOL_MODULES"] = json.dumps(tool_module_names, ensure_ascii=False)
        shutdown_candidates = list(tool_module_names) + ["kdcube_ai_app.apps.chat.sdk.retrieval.kb_client"]
        child_env["RUNTIME_SHUTDOWN_MODULES"] = json.dumps(shutdown_candidates, ensure_ascii=False)

        # Augment PYTHONPATH
        parents = _module_parent_dirs(tool_modules)
        file_parents: List[str] = []
        alias_files = g.get("TOOL_MODULE_FILES") or {}
        for _p in (alias_files or {}).values():
            if _p:
                file_parents.append(str(pathlib.Path(_p).resolve().parent))

        seen = set()
        extra_paths: List[str] = []
        for d in [str(workdir)] + file_parents + parents:
            if d and d not in seen:
                extra_paths.append(d)
                seen.add(d)

        child_env["PYTHONPATH"] = os.pathsep.join(extra_paths + [child_env.get("PYTHONPATH", "")])

        # Run subprocess
        return await _run_subprocess(
            entry_path=main_path,
            cwd=workdir,
            env=child_env,
            timeout_s=timeout_s,
            outdir=output_dir,
            allow_network=isolation != "local_network",
            exec_id=exec_id,
        )

    async def run_tool_in_isolation(
            self,
            *,
            workdir: pathlib.Path,
            output_dir: pathlib.Path,
            bundle_root: str,
            tool_modules: List[Tuple[str, object]],
            tool_id: str,
            params: Dict[str, Any],
            call_reason: Optional[str] = None,
            globals: Dict[str, Any] | None = None,
            isolation: Optional[Literal["none", "docker", "local_network", "local"]] = "none",
            timeout_s: int = 90,
    ) -> Dict[str, Any]:
        """
        Wrap a single tool call into main.py and execute it via execute_py_code.

        docker=False → local subprocess
        docker=True  → py-code-exec docker container
        """
        workdir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        params_json = json.dumps(params, ensure_ascii=False)

        main_py = f"""
import os, json, importlib, asyncio, sys
from pathlib import Path
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools

TOOL_ID  = {tool_id!r}
PARAMS   = json.loads({params_json!r})
REASON   = {call_reason!r} or f"ReAct: {{TOOL_ID}}"

async def _main():
    alias, func_name = TOOL_ID.split('.', 1)

    # prefer alias symbol injected by the runtime header
    owner = globals().get(alias)

    # fallback 1: import alias module if available
    if owner is None:
        try:
            mod = importlib.import_module(alias)
            owner = getattr(mod, "tools", None) or mod
        except Exception:
            owner = None

    if owner is None:
        raise ImportError(f"Could not resolve tool owner for alias '{{alias}}'")

    fn = getattr(owner, func_name, None)
    if fn is None:
        raise AttributeError(f"Function '{{func_name}}' not found on alias '{{alias}}'")

    # Execute via io_tools so the call is persisted to <sanitized>-<idx>.json and indexed
    await agent_io_tools.tool_call(
        fn=fn,
        params=PARAMS,
        call_reason=REASON,
        tool_id=TOOL_ID
    )

    # Mark this tool-only run as finalized so atexit doesn't warn about missing done()/fail()
    globals()["_FINALIZED"] = True

asyncio.run(_main())
"""
        (workdir / "main.py").write_text(main_py, encoding="utf-8")

        # keep the same subprocess plumbing as execute_py_code (header injection, bootstrap, alias maps, etc.)
        g = dict(globals or {})
        res = await self.execute_py_code(
            workdir=workdir,
            output_dir=output_dir,
            bundle_root=bundle_root,
            tool_modules=tool_modules,
            globals=g,
            isolation=isolation,
            timeout_s=timeout_s,
        )
        return res
