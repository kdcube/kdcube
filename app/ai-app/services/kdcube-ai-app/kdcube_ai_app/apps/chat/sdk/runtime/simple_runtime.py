# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/simple_runtime.py
import io
import os, sys, re
import asyncio
import pathlib
import runpy
import tokenize
from typing import Dict, Any, List, Tuple
from textwrap import dedent

from kdcube_ai_app.apps.chat.sdk.inventory import AgentLogger

def _inject_header_after_future(src: str, header: str) -> str:
    lines = src.splitlines(True)
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("from __future__ import"):
        i += 1
    # idempotent
    if header.strip() in src:
        return src
    return "".join(lines[:i] + [header] + lines[i:])

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

class _InProcessRuntime:
    def __init__(self, logger: AgentLogger):
        self.log = logger or AgentLogger("tool_runtime")

    def _ensure_modules_on_sys_modules(self, modules: List[Tuple[str, object]]):
        """Make sure codegen can 'from <name> import tools as <alias>' for each module."""
        for name, mod in modules or []:
            if name and name not in sys.modules:
                sys.modules[name] = mod

    async def run_snippet(
        self,
        *,
        code: str,
        output_dir: pathlib.Path,
        tool_modules: List[Tuple[str, object]],
        globals: Dict[str, Any] = None,
        timeout_s: int = 90,
    ) -> Dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)

        def _runner():
            old_env = dict(os.environ)

            os.environ["OUTPUT_DIR"] = str(output_dir)
            # Align snippet runtime context with package runner
            from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, SOURCE_ID_CV
            t_out = OUTDIR_CV.set(str(output_dir))
            last_sid = _max_sid_from_context(output_dir)
            t_sid = SOURCE_ID_CV.set({"next": int(last_sid) + 1})

            try:
                self._ensure_modules_on_sys_modules(tool_modules)

                # Prepare source: fix JSON booleans/null and inject the same runtime header
                globals_src = ""
                if globals:
                    for k, v in globals.items():
                        if k and (k != "__name__"):
                            globals_src += f"\n{k} = {repr(v)}\n"

                injected_header = dedent('''\
# === AGENT-RUNTIME HEADER (auto-injected, do not edit) ===
from pathlib import Path
import json as _json
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV
from io_tools import tools as agent_io_tools

OUTPUT_DIR = OUTDIR_CV.get()
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)
<GLOBALS_SRC>
async def fail(description: str,
               where: str = "",
               error: str = "",
               details: str = "",
               managed: bool = True,
               out_dyn: dict | None = None):
    """
    Runtime failure helper.
    - Writes result.json with a normalized failure envelope.
    - Pulls CONTRACT/objective from main globals if present (else falls back).
    """
    try:
        g = globals()
        contract = g.get("CONTRACT", {}) or {}
        objective = g.get("objective") or g.get("OBJECTIVE") or ""
    except Exception:
        contract, objective = {}, ""
    payload = {
        "ok": False,
        "objective": str(objective or description),
        "contract": contract,
        "out_dyn": (out_dyn or {}),
        "error": {
            "where": (where or "runtime"),
            "details": str(details or ""),
            "error": str(error or ""),
            "description": description,
            "managed": bool(managed),
         }
    }
    return await agent_io_tools.save_ret(data=_json.dumps(payload), filename="result.json")
# === END HEADER ===
''').replace('<GLOBALS_SRC>', globals_src)
                src = _fix_json_bools(code)
                src = _inject_header_after_future(src, injected_header)

                glb = {"__name__": "__main__"}
                exec(compile(src, "<solver_snippet>", "exec"), glb, glb)
            finally:
                # reset context vars and env
                try:
                    OUTDIR_CV.reset(t_out)
                    SOURCE_ID_CV.reset(t_sid)
                except Exception:
                    pass
                os.environ.clear(); os.environ.update(old_env)
        try:
            await asyncio.wait_for(asyncio.to_thread(_runner), timeout=timeout_s)
            return {"ok": True}
        except asyncio.TimeoutError:
            return {"error": "timeout", "seconds": timeout_s}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    async def run_main_py(
        self,
        *,
        workdir: pathlib.Path,
        output_dir: pathlib.Path,
        tool_modules: List[Tuple[str, object]],
        globals: Dict[str, Any] = None,
        timeout_s: int = 90,
    ) -> Dict[str, Any]:
        workdir.mkdir(parents=True, exist_ok=True)

        from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV, SOURCE_ID_CV
        def _runner():
            old_env = dict(os.environ)
            old_path = list(sys.path)

            t_out = OUTDIR_CV.set(str(output_dir))
            t_wrk = WORKDIR_CV.set(str(workdir))

            last_sid = _max_sid_from_context(output_dir)
            t_sid = SOURCE_ID_CV.set({"next": int(last_sid) + 1})

            try:
                sys.path.insert(0, str(workdir))
                self._ensure_modules_on_sys_modules(tool_modules)

                src = (workdir / "main.py").read_text(encoding="utf-8")

                # 1) Fix JSON booleans/null FIRST (no exceptions here)
                src = _fix_json_bools(src)

                # 2) Inject the OUTPUT_DIR header (after any __future__)

                globals_src = ""
                if globals:
                    for k, v in globals.items():
                        if k and (k != "__name__"):
                            globals_src += f"\n{k} = {repr(v)}\n"

                injected_header = dedent('''\
# === AGENT-RUNTIME HEADER (auto-injected, do not edit) ===
from pathlib import Path
import json as _json
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV
from io_tools import tools as agent_io_tools

OUTPUT_DIR = OUTDIR_CV.get()
if not OUTPUT_DIR:
    raise RuntimeError("OUTPUT_DIR missing in run context")
OUTPUT = Path(OUTPUT_DIR)
<GLOBALS_SRC>
async def fail(description: str,
               where: str = "",
               error: str = "",
               details: str = "",
               managed: bool = True,
               out_dyn: dict | None = None):
    """
    Runtime failure helper.
    - Writes result.json with a normalized failure envelope.
    - Pulls CONTRACT/objective from main globals if present (else falls back).
    """
    try:
        g = globals()
        contract = g.get("CONTRACT", {}) or {}
        objective = g.get("objective") or g.get("OBJECTIVE") or ""
    except Exception:
        contract, objective = {}, ""
    payload = {
        "ok": False,
        "objective": str(objective or description),
        "contract": contract,
        "out_dyn": (out_dyn or {}),
        "error": {
            "where": (where or "runtime"),
            "details": str(details or ""),
            "error": str(error or ""),
            "description": description,
            "managed": bool(managed),
         }
    }
    return await agent_io_tools.save_ret(data=_json.dumps(payload), filename="result.json")
# === END HEADER ===
''').replace('<GLOBALS_SRC>', globals_src)
                src = _inject_header_after_future(src, injected_header)

                # 3) Persist the rewritten file and run it
                (workdir / "main.py").write_text(src, encoding="utf-8")
                runpy.run_path(str(workdir / "main.py"), run_name="__main__")
            finally:
                OUTDIR_CV.reset(t_out); WORKDIR_CV.reset(t_wrk)
                SOURCE_ID_CV.reset(t_sid)
                sys.path[:] = old_path
                os.environ.clear(); os.environ.update(old_env)

        try:
            await asyncio.wait_for(asyncio.to_thread(_runner), timeout=timeout_s)
            return {"ok": True}
        except asyncio.TimeoutError:
            return {"error": "timeout", "seconds": timeout_s}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

