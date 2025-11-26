# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py
import contextvars
import io
import json
import os, sys
import asyncio
import pathlib
import tokenize
from typing import Dict, Any, List, Tuple, Optional

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
    lines = ["from io_tools import tools as agent_io_tools  # wrapper for tool_call/save_ret"]
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
        "## Available Packages\n"
        "- Data: pandas, numpy, openpyxl, xlsxwriter\n"
        "- Files: python-docx, python-pptx, pymupdf, pypdf, reportlab, Pillow\n"
        "- Web: requests, aiohttp, httpx, playwright, beautifulsoup4, lxml\n"
        "- Viz: matplotlib, seaborn, plotly, networkx, graphviz, diagrams\n"
        "- Text: markdown-it-py, pygments, jinja2, python-dateutil\n"
        "- Utils: pydantic, orjson, python-dotenv, PyJWT\n"
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

# async def _run_subprocess(entry_path: pathlib.Path, *, cwd: pathlib.Path, env: dict, timeout_s: int, outdir: pathlib.Path):
#     proc = await asyncio.create_subprocess_exec(
#         sys.executable, "-u", str(entry_path),
#         cwd=str(cwd),
#         env=env,
#         stdout=asyncio.subprocess.PIPE,
#         stderr=asyncio.subprocess.PIPE,
#     )
#
#     out: bytes = b""
#     err: bytes = b""
#     timed_out = False
#
#     try:
#         # Read and wait at the same time; avoids pipe deadlock
#         out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
#     except asyncio.TimeoutError:
#         timed_out = True
#         try:
#             proc.terminate()
#         except ProcessLookupError:
#             pass
#
#         # Give it a moment to shut down gracefully
#         try:
#             out2, err2 = await asyncio.wait_for(proc.communicate(), timeout=5)
#             # Append any extra output we got after terminate()
#             out += out2
#             err += err2
#         except asyncio.TimeoutError:
#             # Force kill if it still doesn’t exit
#             try:
#                 proc.kill()
#             except ProcessLookupError:
#                 pass
#             out2, err2 = await proc.communicate()
#             out += out2
#             err += err2
#     finally:
#         # append logs (do not overwrite)
#         try:
#             out_path = outdir / "runtime.out.log"
#             err_path = outdir / "runtime.err.log"
#             out_path.parent.mkdir(parents=True, exist_ok=True)
#             with open(out_path, "ab") as f:
#                 if out:
#                     f.write(out)
#                     f.write(b"\n")
#             with open(err_path, "ab") as f:
#                 if err:
#                     f.write(err)
#                     f.write(b"\n")
#         except Exception:
#             pass
#
#     if timed_out:
#         return {"error": "timeout", "seconds": timeout_s}
#
#     return {"ok": proc.returncode == 0, "returncode": proc.returncode}


async def _run_subprocess(entry_path: pathlib.Path, *, cwd: pathlib.Path, env: dict, timeout_s: int, outdir: pathlib.Path, sandbox_fs: bool = True, sandbox_net: bool = True) -> Dict[str, Any]:
    """
    Environment toggles (per-call via env, or global via os.environ):
      - sandbox_fs: True -> enable FS sandbox if bwrap present
                    False (default)          -> disable sandbox
      - sandbox_net: True (default) -> network allowed
                     False          -> network disabled (--unshare-net)
    """
    from kdcube_ai_app.apps.chat.sdk.runtime import bubblewrap as bwrap
    use_sandbox = sandbox_fs and bwrap.bwrap_available()

    if use_sandbox:
        proc = await bwrap.run_proc(entry_path=entry_path,
                                    cwd=cwd,
                                    env=env,
                                    outdir=outdir,
                                    sandbox_net=sandbox_net) # Child env as seen inside sandbox
    else:
        # Original behavior
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
            out_path = outdir / "runtime.out.log"
            err_path = outdir / "runtime.err.log"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "ab") as f:
                if out:
                    f.write(out)
                    f.write(b"\n")
            with open(err_path, "ab") as f:
                if err:
                    f.write(err)
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
from io_tools import tools as agent_io_tools

import logging, sys, warnings
class _MaxLevelFilter(logging.Filter):
    def __init__(self, level: int):
        super().__init__()
        self.level = level
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level

def _setup_runtime_logging():
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()

    # Python 3.8+: this clears existing handlers on root and configures anew
    try:
        logging.basicConfig(level=logging.INFO, handlers=[], force=True)
    except TypeError:
        # Fallback if force= not available: clear handlers manually
        for h in list(root.handlers):
            root.removeHandler(h)

    # Ensure a clean slate for all known (already created) loggers to avoid double-prints
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()
            logger.propagate = True  # bubble to root

    # stdout handler for DEBUG/INFO
    h_out = logging.StreamHandler(sys.stdout)
    h_out.setLevel(logging.DEBUG)
    h_out.addFilter(_MaxLevelFilter(logging.INFO))
    h_out.setFormatter(logging.Formatter(fmt, datefmt))

    # stderr handler for WARNING+
    h_err = logging.StreamHandler(sys.stderr)
    h_err.setLevel(logging.WARNING)
    h_err.setFormatter(logging.Formatter(fmt, datefmt))

    root.setLevel(logging.INFO)
    root.addHandler(h_out)
    root.addHandler(h_err)

    # Route warnings.warn(...) into the logging system
    logging.captureWarnings(True)
    warnings.simplefilter("default")  # ensure they actually fire

    # Make uncaught exceptions show up as formatted ERROR logs
    def _excepthook(exc_type, exc, tb):
        logging.getLogger("runtime").error("Unhandled exception", exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook

_setup_runtime_logging()
logger = logging.getLogger("agent.runtime")

# --- Directories / CV fallbacks ---
OUTPUT_DIR = OUTDIR_CV.get() or os.environ.get("OUTPUT_DIR")
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)

# ✅ Ensure child process has ContextVars set even when only env is present
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
            _bootstrap_all(_PORTABLE_SPEC, module_names=_BIND_TARGETS)
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

# --- Globals provided by parent (must come before dyn pre-registration) ---
<GLOBALS_SRC>

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
            "error": {"where": "runtime", "details": "", "error": reason, "description": reason, "managed": bool(managed)}
        }
        await agent_io_tools.save_ret(data=_json.dumps(payload, ensure_ascii=False), filename="result.json")
    except Exception:
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
        "out_dyn": dict(_PROGRESS.get("out_dyn") or {})
    }
    globals()["_FINALIZED"] = True  # ← set BEFORE writing final file
    try:
        _dump_delta_cache_file()
    finally:
        pass
    return await agent_io_tools.save_ret(data=_json.dumps(payload, ensure_ascii=False), filename="result.json")

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
        }
    }
    globals()["_FINALIZED"] = True  # ← set BEFORE writing final file
    try:
        _dump_delta_cache_file()
    finally:
        pass
    return await agent_io_tools.save_ret(data=_json.dumps(payload, ensure_ascii=False), filename="result.json")

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
from kdcube_ai_app.apps.chat.emitters import (ChatRelayCommunicator, ChatCommunicator, _RelayEmitterAdapter)
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
    emitter = _RelayEmitterAdapter(relay)
    svc = ServiceCtx(**(spec.get("service") or {}))
    conv = ConversationCtx(**(spec.get("conversation") or {}))
    comm = ChatCommunicator(
        emitter=emitter,
        service=svc.model_dump() if hasattr(svc, "model_dump") else dict(svc),
        conversation=conv.model_dump() if hasattr(conv, "model_dump") else dict(conv),
        room=spec.get("room"),
        target_sid=spec.get("target_sid"),
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

class _InProcessRuntime:
    def __init__(self, logger: AgentLogger):
        self.log = logger or AgentLogger("tool_runtime")

    def _ensure_modules_on_sys_modules(self, modules: List[Tuple[str, object]]):
        """Make sure codegen can 'from <name> import tools as <alias>' for each module."""
        for name, mod in modules or []:
            if name and name not in sys.modules:
                sys.modules[name] = mod

    async def run_main_py_subprocess(
        self,
        *,
        workdir: pathlib.Path,
        output_dir: pathlib.Path,
        bundle_root: str|None,
        tool_modules: List[Tuple[str, object]],
        globals: Dict[str, Any] | None = None,
        docker: bool = False,
        timeout_s: int = 90,
    ) -> Dict[str, Any]:
        """
        Execute workdir/main.py in an isolated runtime.

        - docker=False: spawn a local subprocess (optionally bwrap-sandboxed).
        - docker=True : delegate to docker.run_py_in_docker(), which will run
                        iso_runtime.run_py_code() inside the py-code-exec container.

        In both modes we:
          - fix JSON booleans
          - scan f-strings
          - inject the runtime header (globals + TOOL_ALIAS_MAP imports)
          - compute the tool module list (RUNTIME_TOOL_MODULES / SHUTDOWN_MODULES)
          - compute the same runtime env (OUTPUT_DIR, WORKDIR, etc.).
        """
        workdir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        main_path = workdir / "main.py"
        if not main_path.exists():
            raise FileNotFoundError(f"main.py not found in workdir: {workdir}")

        # --- Read & transform main.py (host-side) ---------------------------
        src = main_path.read_text(encoding="utf-8")
        src = _fix_json_bools(src)
        src = _validate_and_report_fstring_issues(src, workdir)

        # Make a working copy of globals so we don't mutate the caller dict
        g = dict(globals or {})

        # Build globals prelude (simple assignments)
        globals_src = ""
        for k, v in g.items():
            if k and (k != "__name__"):
                globals_src += f"\n{k} = {repr(v)}\n"

        # Build alias import block from TOOL_ALIAS_MAP provided by the caller
        imports_src = ""
        alias_map = (g.get("TOOL_ALIAS_MAP") or {}) if g else {}
        for alias, mod_name in (alias_map or {}).items():
            imports_src += f"\nfrom {mod_name} import tools as {alias}\n"

        injected_header = _build_injected_header(globals_src=globals_src, imports_src=imports_src)
        src = _inject_header_after_future(src, injected_header)
        main_path.write_text(src, encoding="utf-8")

        # --- Compute tool module names (for bootstrap + shutdown) -----------
        tool_module_names: List[str] = [name for name, _ in (tool_modules or []) if name]

        # ALSO bind into dynamic alias modules so bootstrap reaches them
        for dyn_name in (alias_map or {}).values():
            if dyn_name and dyn_name not in tool_module_names:
                tool_module_names.append(dyn_name)

        # --- Sandbox flags (same semantics for both backends) ---------------
        sandbox_fs = False
        sandbox_net = True
        if "SANDBOX_FS" in g:
            sandbox_fs = str(g["SANDBOX_FS"]) != "0"
        if "SANDBOX_NET" in g:
            sandbox_net = str(g["SANDBOX_NET"]) != "0"

        # --- PortableSpec handling (shared) ---------------------------------
        # Accept either PORTABLE_SPEC_JSON (dict/string) or PORTABLE_SPEC (string)
        ps = g.get("PORTABLE_SPEC_JSON") or g.get("PORTABLE_SPEC")
        portable_spec_str: str | None = None
        if ps is not None:
            portable_spec_str = ps if isinstance(ps, str) else json.dumps(ps, ensure_ascii=False)
            # don't leak these back into user code
            g.pop("PORTABLE_SPEC", None)
            g.pop("PORTABLE_SPEC_JSON", None)

        # --- Runtime env that both backends see (before adaptation) ---------
        runtime_env: Dict[str, str] = {}

        # flags
        runtime_env["SANDBOX_FS"] = "1" if sandbox_fs else "0"
        runtime_env["SANDBOX_NET"] = "1" if sandbox_net else "0"

        # dirs (host paths; docker backend will rewrite them to /workspace,/output)
        runtime_env["OUTPUT_DIR"] = str(output_dir)
        runtime_env["WORKDIR"] = str(workdir)

        # spec + runtime modules
        if portable_spec_str is not None:
            runtime_env["PORTABLE_SPEC"] = portable_spec_str

        runtime_env["RUNTIME_TOOL_MODULES"] = json.dumps(tool_module_names, ensure_ascii=False)

        shutdown_candidates = list(tool_module_names) + [
            "kdcube_ai_app.apps.chat.sdk.retrieval.kb_client"
        ]
        runtime_env["RUNTIME_SHUTDOWN_MODULES"] = json.dumps(shutdown_candidates, ensure_ascii=False)

        # --- Docker backend --------------------------------------------------
        if docker:
            # Pass the same env + globals into the docker runtime.
            # docker.run_py_in_docker is responsible for:
            #   - mapping host paths -> container paths
            #   - exporting runtime_env as container env
            from kdcube_ai_app.apps.chat.sdk.runtime import docker as docker_runtime

            # Build the *same* env we’d use for a local subprocess
            host_env = os.environ.copy()
            host_env.update(runtime_env)

            return await docker_runtime.run_py_in_docker(
                workdir=workdir,
                outdir=output_dir,
                runtime_globals=g,               # includes SANDBOX_* & TOOL_* etc.
                tool_module_names=tool_module_names,
                logger=self.log,
                timeout_s=timeout_s,
                bundle_root=pathlib.Path(bundle_root).resolve() if bundle_root else None,
                extra_env=host_env,
            )

        # --- Local subprocess backend (original behavior) -------------------
        child_env = os.environ.copy()
        child_env.update(runtime_env)

        # Augment PYTHONPATH so alias imports (from {mod} import tools as {alias}) resolve
        parents = _module_parent_dirs(tool_modules)
        file_parents: List[str] = []
        try:
            alias_files = (g.get("TOOL_MODULE_FILES") or {}) if g else {}
            for _p in (alias_files or {}).values():
                if _p:
                    file_parents.append(str(pathlib.Path(_p).resolve().parent))
        except Exception:
            pass

        # de-duplicate while preserving order
        seen = set()
        extra_paths: List[str] = []
        for d in [str(workdir)] + file_parents + parents:
            if d and d not in seen:
                extra_paths.append(d)
                seen.add(d)

        child_env["PYTHONPATH"] = os.pathsep.join(
            extra_paths + [child_env.get("PYTHONPATH", "")]
        )

        # --- Run as subprocess (capture stdout/err to files beside OUTPUT_DIR) ---
        return await _run_subprocess(
            entry_path=main_path,
            cwd=workdir,
            env=child_env,
            timeout_s=timeout_s,
            outdir=output_dir,
            sandbox_fs=sandbox_fs,
            sandbox_net=sandbox_net,
        )

    async def run_tool_once_subprocess(
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
            docker: bool = False,
            timeout_s: int = 90,
    ) -> Dict[str, Any]:
        """
        Wrap a single tool call into main.py and execute it via run_main_py_subprocess.

        docker=False → local subprocess
        docker=True  → py-code-exec docker container
        """
        workdir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        params_json = json.dumps(params, ensure_ascii=False)

        main_py = f"""
import os, json, importlib, asyncio, sys
from pathlib import Path
from io_tools import tools as agent_io_tools

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
        params_json=json.dumps(PARAMS, ensure_ascii=False),
        call_reason=REASON,
        tool_id=TOOL_ID
    )

    # Mark this tool-only run as finalized so atexit doesn't warn about missing done()/fail()
    globals()["_FINALIZED"] = True

asyncio.run(_main())
"""
        (workdir / "main.py").write_text(main_py, encoding="utf-8")

        # keep the same subprocess plumbing as run_main_py_subprocess (header injection, bootstrap, alias maps, etc.)
        g = dict(globals or {})
        res = await self.run_main_py_subprocess(
            workdir=workdir,
            output_dir=output_dir,
            bundle_root=bundle_root,
            tool_modules=tool_modules,
            globals=g,
            docker=docker,
            timeout_s=timeout_s,
        )
        return res

    async def run_finalize_result_subprocess(
            self,
            *,
            workdir: pathlib.Path,
            output_dir: pathlib.Path,
            tool_modules: List[Tuple[str, object]],
            payload: Dict[str, Any],  # {ok, objective, contract, out_dyn}
            globals: Dict[str, Any] | None = None,
            timeout_s: int = 90,
    ) -> Dict[str, Any]:
        """
        Write `result.json` in OUTDIR via io_tools.AgentIO.save_ret to ensure canonical
        citations and promotion of tool call artifacts.
        """
        workdir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        payload_s = json.dumps(payload, ensure_ascii=False)
        main_py = f"""
import json, asyncio
from io_tools import tools as agent_io_tools

DATA = json.loads({payload_s!r})

async def _main():
    await agent_io_tools.save_ret(data=json.dumps(DATA, ensure_ascii=False), filename='result.json')

asyncio.run(_main())
"""
        (workdir / "main.py").write_text(main_py, encoding="utf-8")
        res = await self.run_main_py_subprocess(
            workdir=workdir,
            output_dir=output_dir,
            bundle_root=None,
            tool_modules=tool_modules,
            globals=dict(globals or {}),
            timeout_s=timeout_s,
        )
        return res


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

    It mirrors the behavior of _InProcessRuntime.run_main_py_subprocess, but:
      - assumes we are already in an isolated Docker container
      - optionally disables bwrap (if SANDBOX_FS is 0 in globals)
      - writes runtime.out.log / runtime.err.log into OUTPUT_DIR
    """
    log = logger or AgentLogger("py_code_exec")

    workdir = workdir.resolve()
    output_dir = output_dir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    main_path = workdir / "main.py"
    if not main_path.exists():
        raise FileNotFoundError(f"main.py not found in workdir: {workdir}")

    # --- Read & transform main.py (JSON bool fix + f-string lint + header injection) ---
    src = main_path.read_text(encoding="utf-8")
    src = _fix_json_bools(src)
    src = _validate_and_report_fstring_issues(src, workdir)

    # --- Build child environment (inside this container) ---
    child_env = os.environ.copy()

    # SANDBOX flags (inside Docker, FS sandbox is usually not needed)
    sandbox_fs = False
    sandbox_net = True
    g = globals or {}

    if "SANDBOX_FS" in g:
        sandbox_fs = str(g["SANDBOX_FS"]) != "0"
        child_env["SANDBOX_FS"] = str(g["SANDBOX_FS"])
    else:
        # default: disable bwrap in container
        child_env["SANDBOX_FS"] = "0"

    if "SANDBOX_NET" in g:
        sandbox_net = str(g["SANDBOX_NET"]) != "0"
        child_env["SANDBOX_NET"] = str(g["SANDBOX_NET"])

    # PORTABLE_SPEC from globals (JSON or string)
    ps = g.get("PORTABLE_SPEC_JSON") or g.get("PORTABLE_SPEC")
    if ps is not None:
        child_env["PORTABLE_SPEC"] = ps if isinstance(ps, str) else json.dumps(ps, ensure_ascii=False)

    # Build globals prelude (simple assignments injected into header)
    globals_src = ""
    for k, v in g.items():
        if k and k != "__name__":
            globals_src += f"\n{k} = {repr(v)}\n"

    # Tool alias imports (must match TOOL_ALIAS_MAP)
    imports_src = ""
    alias_map = (g.get("TOOL_ALIAS_MAP") or {}) if g else {}
    for alias, mod_name in (alias_map or {}).items():
        imports_src += f"\nfrom {mod_name} import tools as {alias}\n"

    injected_header = _build_injected_header(globals_src=globals_src, imports_src=imports_src)
    src = _inject_header_after_future(src, injected_header)
    main_path.write_text(src, encoding="utf-8")

    # OUTPUT_DIR / WORKDIR inside this container
    child_env["OUTPUT_DIR"] = str(output_dir)
    child_env["WORKDIR"] = str(workdir)

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
        f"runtime_modules={tool_module_names}",
        level="INFO",
    )

    # Execute the rewritten main.py as a child process inside this container.
    res = await _run_subprocess(
        entry_path=main_path,
        cwd=workdir,
        env=child_env,
        timeout_s=timeout_s,
        outdir=output_dir,
        sandbox_fs=sandbox_fs,
        sandbox_net=sandbox_net,
    )
    return res
