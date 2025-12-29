# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/exec_tools.py
from __future__ import annotations

import json
import pathlib
import uuid
import textwrap
from typing import Any, Dict, Optional, Annotated, Tuple, List

import semantic_kernel as sk

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime, build_packages_installed_block
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function


def _safe_relpath(path: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    p = pathlib.Path(path)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    return str(p)


def _normalize_artifacts_spec(artifacts: Any) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    if artifacts is None:
        return None, {"code": "missing_artifacts", "message": "artifacts list is required"}
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except Exception:
            return None, {"code": "invalid_artifacts_json", "message": "artifacts must be a JSON array or list"}
    if not isinstance(artifacts, list) or not artifacts:
        return None, {"code": "invalid_artifacts", "message": "artifacts must be a non-empty list"}

    normalized: List[Dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("artifact_name") or item.get("artifact_id") or "").strip()
        filename = (item.get("filename") or item.get("artifact_filename") or item.get("path") or "").strip()
        mime = (item.get("mime") or "").strip()
        description = (item.get("description") or item.get("content_guidance") or item.get("inventorization") or "").strip()
        if not name or not filename or not mime or not description:
            return None, {
                "code": "invalid_artifact_spec",
                "message": "Each artifact requires name, filename, mime, description",
            }
        safe_filename = _safe_relpath(filename)
        if not safe_filename:
            return None, {
                "code": "invalid_filename",
                "message": f"Invalid filename path: {filename}",
            }
        normalized.append(
            {
                "name": name,
                "filename": safe_filename,
                "mime": mime,
                "description": description,
            }
        )
    if not normalized:
        return None, {"code": "invalid_artifacts", "message": "No valid artifacts found"}
    return normalized, None


def build_exec_output_contract(
    artifacts: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    normalized, err = _normalize_artifacts_spec(artifacts)
    if err:
        return None, None, err
    contract: Dict[str, Any] = {}
    for a in normalized or []:
        contract[a["name"]] = {
            "type": "file",
            "filename": a["filename"],
            "mime": a["mime"],
            "description": a["description"],
            "content_guidance": a["description"],
        }
    return contract, normalized, None


class ExecTools:
    @kernel_function(
        name="execute_code_python",
        description=(
            "Execute a ready (pre-written) Python 3.11 program in the sandbox using the same\n"
            "runtime mechanism as `codegen_tools.codegen_python`, but WITHOUT code generation.\n"
            "\n"
            "WHEN TO USE\n"
            "- Use this tool ONLY when you already have the code and need to run it.\n"
            "- The code you pass is a SNIPPET that is inserted inside an async main() wrapper.\n"
            "- The snippet SHOULD use async operations (await where needed).\n"
            "\n"
            "RUNTIME BEHAVIOR\n"
            "- The executor wraps your snippet into an async main() and runs it.\n"
            "- After execution, the executor checks for the requested output files.\n"
            "- For each requested file that exists and is non-empty, an artifact is produced.\n"
            "\n"
            "INPUTS (REQUIRED)\n"
            "1) `code` (string): Python code snippet to run (inserted into async main()).\n"
            "2) `artifacts` (list or JSON string): list of artifact specs with fields:\n"
            "   - name (artifact id)\n"
            "   - filename (relative path in OUTPUT_DIR)\n"
            "   - mime\n"
            "   - description (text surrogate / promise of content)\n"
            "   Each artifact is ALWAYS a file.\n"
            "\n"
            "FILES & PATHS\n"
            "- Input artifacts from context are available by their filenames under OUTPUT_DIR.\n"
            "- Write your outputs to the provided `filename` paths under OUTPUT_DIR.\n"
            "- `OUTPUT_DIR` is a global string path in the runtime; build paths like:\n"
            "  `os.path.join(OUTPUT_DIR, \"my_file.ext\")` or `Path(OUTPUT_DIR) / \"my_file.ext\"`.\n"
            "- Network access is disabled in the sandbox; any network calls will fail.\n"
            "- Read/write outside OUTPUT_DIR or the current workdir is not permitted.\n"
            "\n"
            "AVAILABLE PACKAGES\n"
            f"{build_packages_installed_block()}\n"
            "\n"
            "OUTPUT\n"
            "- A status dict indicating success/error and the produced file artifacts.\n"
        ),
    )
    async def execute_code_python(
        self,
        code: Annotated[str, "Python code snippet (string). Inserted into async main()."],
        artifacts: Annotated[Any, "List or JSON string of artifact specs (name, filename, mime, description)."],
        timeout_s: Annotated[Optional[int], "Execution timeout seconds (default: 600)."] = None,
    ) -> Annotated[dict, "Envelope: ok/out_dyn/out/error/summary."]:
        pass


async def run_exec_tool(
    *,
    tool_manager: Any,
    output_contract: Dict[str, Any],
    code: str,
    artifacts: List[Dict[str, Any]],
    timeout_s: int,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
    exec_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute pre-written code using the same runtime as codegen.
    Returns an envelope similar to run_codegen_tool().
    """
    log = logger or AgentLogger("exec.tool")

    # 1) unique per invocation
    result_filename = f"exec_result_{exec_id}.json" if exec_id else f"exec_result_{uuid.uuid4().hex[:10]}.json"

    # 2) prepare workspace
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)

    snippet = textwrap.indent(code or "", "        ")
    wrapper = "\n".join([
        "import asyncio",
        "import traceback",
        "import sys",
        "",
        "async def _main():",
        "    try:",
        snippet or "        pass",
        "    except Exception as e:",
        "        tb = traceback.format_exc()",
        "        try:",
        "            await fail(\"Unhandled error\", where=\"main\", error=f\"{type(e).__name__}: {e}\", details=tb, managed=False)",
        "        except Exception:",
        "            pass",
        "        print(tb, file=sys.stderr)",
        "        raise",
        "",
        "if __name__ == '__main__':",
        "    asyncio.run(_main())",
        "",
    ])
    (workdir / "main.py").write_text(wrapper, encoding="utf-8")

    # 3) execute in sandbox (same as codegen)
    runtime = _InProcessRuntime(log)
    runtime_globals = tool_manager.export_runtime_globals()
    spec = build_portable_spec(svc=tool_manager.svc, chat_comm=tool_manager.comm)
    comm_spec = getattr(tool_manager.comm, "_export_comm_spec_for_runtime", lambda: {})()

    globals_for_runtime = {
        "CONTRACT": output_contract,
        "COMM_SPEC": comm_spec,
        "PORTABLE_SPEC_JSON": spec.to_json(),
        "RESULT_FILENAME": result_filename,
        **({"EXECUTION_ID": exec_id} if exec_id else {}),
        **runtime_globals,
    }

    try:
        run_res = await runtime.execute_py_code(
            workdir=workdir,
            output_dir=outdir,
            tool_modules=tool_manager.tool_modules_tuple_list(),
            globals=globals_for_runtime,
            timeout_s=timeout_s,
            isolation="docker",
            bundle_root=tool_manager.bundle_root,
            extra_env={
                "EXECUTION_MODE": "TOOL",
                "EXEC_NO_UNEXPECTED_EXIT": "1",
                "RESULT_FILENAME": result_filename,
                **({"EXECUTION_ID": exec_id} if exec_id else {}),
            },
        )
    except Exception as e:
        return {
            "ok": False,
            "error": {
                "where": "exec.tool",
                "error": "execution_error",
                "description": str(e),
                "managed": True,
            },
        }

    # 4) build artifacts by checking requested files
    out_dyn: Dict[str, Any] = {}
    missing: List[str] = []
    for a in artifacts or []:
        rel = a["filename"]
        p = outdir / rel
        if not p.exists() or p.stat().st_size <= 0:
            missing.append(rel)
            continue
        out_dyn[a["name"]] = {
            "type": "file",
            "path": rel,
            "filename": pathlib.Path(rel).name,
            "mime": a["mime"],
            "text": a["description"],
            "description": a["description"],
            "content_inventorization": {"promise": a["description"]},
        }

    stderr_tail = ""
    try:
        err_path = outdir / "runtime.err.log"
        if err_path.exists():
            txt = err_path.read_text(encoding="utf-8", errors="ignore")
            stderr_tail = txt[-2000:] if len(txt) > 2000 else txt
    except Exception:
        pass

    runtime_ok = bool(run_res.get("ok", True))
    ok = len(missing) == 0 and runtime_ok
    error = None
    if not ok:
        err_code = "missing_output_files" if missing else "execution_failed"
        desc = (
            f"Missing output files: {', '.join(missing)}"
            if missing else "Execution failed (non-zero exit)"
        )
        error = {
            "where": "exec.tool",
            "error": err_code,
            "description": desc,
            "managed": True,
            "details": {"missing": missing, "run": run_res, "stderr_tail": stderr_tail},
        }

    payload = {
        "ok": ok,
        "objective": "",
        "contract": output_contract,
        "out_dyn": out_dyn,
        "error": error,
    }
    result_path = outdir / result_filename
    try:
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    artifact_lvl = "artifact"
    artifacts_list = [
        {
            "resource_id": f"{artifact_lvl}:{name}",
            "output": artifact,
            "type": artifact.get("type"),
            "mime": artifact.get("mime"),
            "format": artifact.get("format"),
            "description": artifact.get("description"),
            "sources_used": artifact.get("sources_used"),
            "sources_used_sids": artifact.get("sources_used_sids"),
            "draft": artifact.get("draft"),
            "content_inventorization": artifact.get("content_inventorization"),
        }
        for name, artifact in out_dyn.items()
        if isinstance(artifact, dict)
    ]

    return {
        "ok": ok,
        "result_filename": result_filename,
        "workdir": str(workdir),
        "outdir": str(outdir),
        "artifacts": artifacts_list,
        "canonical_sources": [],
        "error": error,
        "project_log": None,
    }


# module-level exports
kernel = sk.Kernel()
tools = ExecTools()
kernel.add_plugin(tools, "exec_tools")
