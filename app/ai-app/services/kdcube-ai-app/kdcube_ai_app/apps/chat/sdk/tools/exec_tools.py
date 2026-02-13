# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/exec_tools.py
from __future__ import annotations

import json
import re
import pathlib
import uuid
import textwrap
import mimetypes
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

EXEC_TEXT_PREVIEW_MAX_BYTES = 20000
TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/csv",
    "text/csv",
}


def _is_text_mime(mime: str) -> bool:
    if not mime:
        return False
    if mime.startswith("text/"):
        return True
    return mime in TEXT_MIME_TYPES


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
    seen_names: Dict[str, int] = {}
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        raw_filename = item.get("filename")
        filename = raw_filename.strip() if isinstance(raw_filename, str) else ""
        description = (item.get("description") or "").strip()
        if not filename or not description:
            return None, {
                "code": "invalid_artifact_spec",
                "message": "Each artifact requires filename and description",
            }
        safe_filename = _safe_relpath(filename)
        if not safe_filename:
            return None, {
                "code": "invalid_filename",
                "message": f"Invalid filename path: {filename}",
            }
        if "/attachments/" in safe_filename:
            return None, {
                "code": "invalid_filename",
                "message": "Contract filename must be under turn_<id>/files/ (attachments not allowed)",
            }
        if not re.match(r"^turn_[^/]+/files/", safe_filename):
            return None, {
                "code": "invalid_filename",
                "message": (
                    "filename must be OUT_DIR-relative and start with "
                    "'turn_<id>/files/': "
                    f"{filename}"
                ),
            }
        leaf = pathlib.Path(safe_filename).name
        name = pathlib.Path(leaf).stem or leaf
        if not name:
            name = f"artifact_{len(normalized) + 1}"
        if name in seen_names:
            seen_names[name] += 1
            name = f"{name}_{seen_names[name]}"
        else:
            seen_names[name] = 1
        mime = mimetypes.guess_type(leaf)[0] or "application/octet-stream"
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


def normalize_exec_contract_for_turn(
    artifacts: Any,
    *,
    turn_id: str,
) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, str]], Optional[Dict[str, Any]]]:
    """
    Normalize exec contract to current turn:
    - contract entries must target turn_<id>/files/<name>
    - if turn_id is missing in filename, rewrite to current turn
    - attachments are forbidden in contract
    Returns (normalized_list, rewrites, error)
    """
    if not turn_id:
        return None, [], {"code": "missing_turn_id", "message": "turn_id is required to normalize exec contract"}
    if artifacts is None:
        return None, [], {"code": "missing_artifacts", "message": "artifacts list is required"}
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except Exception:
            return None, [], {"code": "invalid_artifacts_json", "message": "artifacts must be a JSON array or list"}
    if not isinstance(artifacts, list) or not artifacts:
        return None, [], {"code": "invalid_artifacts", "message": "artifacts must be a non-empty list"}

    rewrites: List[Dict[str, str]] = []
    updated: List[Dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        raw_filename = item.get("filename")
        filename = raw_filename.strip() if isinstance(raw_filename, str) else ""
        description = (item.get("description") or "").strip()
        if not filename or not description:
            return None, [], {
                "code": "invalid_artifact_spec",
                "message": "Each artifact requires filename and description",
            }
        if "/attachments/" in filename or filename.startswith("attachments/") or filename.startswith(f"{turn_id}/attachments/"):
            return None, [], {
                "code": "invalid_filename",
                "message": "Contract filename must be under turn_<id>/files/ (attachments not allowed)",
            }
        rewritten = None
        if filename.startswith("turn_"):
            if not filename.startswith(f"{turn_id}/files/"):
                return None, [], {
                    "code": "invalid_filename",
                    "message": "Contract filename must use current turn_id and files/ path",
                }
        elif filename.startswith("files/"):
            rel = filename[len("files/") :]
            rewritten = f"{turn_id}/files/{rel}"
        else:
            rewritten = f"{turn_id}/files/{filename}"
        if rewritten:
            rewrites.append({"original": filename, "rewritten": rewritten})
            filename = rewritten

        updated.append({"filename": filename, "description": description})

    normalized, err = _normalize_artifacts_spec(updated)
    if err:
        return None, rewrites, err
    return normalized, rewrites, None


_QUALIFIED_PATH_RE = re.compile(r"turn_[A-Za-z0-9_]+/(files|attachments)/[A-Za-z0-9_./\\-]+")
_UNQUALIFIED_PATH_RE = re.compile(r"(files|attachments)/[A-Za-z0-9_./\\-]+")


def rewrite_exec_code_paths(
    code: str,
    *,
    turn_id: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Rewrite unqualified files/ or attachments/ paths in code to current turn_id.
    Leaves already qualified turn_<id>/files|attachments paths intact.
    Returns (rewritten_code, rewrites).
    """
    if not isinstance(code, str) or not code.strip() or not turn_id:
        return code or "", []
    qualified_spans = [(m.start(), m.end()) for m in _QUALIFIED_PATH_RE.finditer(code)]

    def _inside_qualified(idx: int) -> bool:
        for s, e in qualified_spans:
            if s <= idx < e:
                return True
        return False

    rewrites: List[Dict[str, str]] = []
    out_parts: List[str] = []
    last = 0
    for m in _UNQUALIFIED_PATH_RE.finditer(code):
        if _inside_qualified(m.start()):
            continue
        if m.start() > 0 and re.match(r"[A-Za-z0-9_]", code[m.start() - 1]):
            continue
        orig = m.group(0)
        repl = f"{turn_id}/{orig}"
        out_parts.append(code[last:m.start()] + repl)
        last = m.end()
        rewrites.append({"original": orig, "rewritten": repl})
    out_parts.append(code[last:])
    return "".join(out_parts), rewrites


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
            "- Each requested file that exists and is non-empty is considered.\n"
            "- Expected as a result of this snippet files are described in contract.\n"
            "\n"
            "INPUTS\n"
            "1) `contract` (list or JSON string, REQUIRED): list of output files specs with fields:\n"
            "   - filename (OUT_DIR‑relative; MUST start with turn_<id>/files/)\n"
            "   - description (what this file contains / why it was produced)\n"
            "   These are outputs of this program that it promises to produce.\n"
            "2) `code` (string, REQUIRED): Python code snippet to run (inserted into async main()).\n"
            "3) `prog_name` (string, optional): short name of the program for UI labeling.\n"
            "\n"
            "FETCH_CTX (ADVANCED)\n"
            "- If your snippet needs to load the text data for the artifact you see on timeline, you may call\n"
            "  ctx_tools.fetch_ctx inside the snippet using agent_io_tools.tool_call.\n"
            "- This is ONLY allowed when the current generator is writing the code (when you act as a codegenerator and in your generated code).\n"
            "- The paths allowed with this tool are only logical ar: so: tc:\n"
            "- Do NOT rely on fetch_ctx unless you are the code author for this run.\n"
            "\n"
            "Example:\n"
            "  resp = await agent_io_tools.tool_call(\n"
            "      fn=ctx_tools.fetch_ctx,\n"
            "      params={\"path\": \"ar:turn_123.user.prompt\"},\n"
            "      call_reason=\"Load user message for turn_123\",\n"
            "      tool_id=\"ctx_tools.fetch_ctx\"\n"
            "  )\n"
            "  if resp.get(\"err\"):\n"
            "      raise RuntimeError(resp[\"err\"])\n"
            "\n"
            "FILES & PATHS\n"
            "- Input artifacts from context are available by their filenames under OUTPUT_DIR/<turn_id>/files/.\n"
            "- User attachments are available under OUTPUT_DIR/<turn_id>/attachments/.\n"
            "- Write your outputs to the provided `filename` paths under OUTPUT_DIR/<turn_id>/files/.\n"
            "- `OUTPUT_DIR` is a global string path in the runtime; build paths like:\n"
            "  `os.path.join(OUTPUT_DIR, \"<turn_id>/files/my_file.ext\")` or `Path(OUTPUT_DIR) / \"<turn_id>/attachments/user_file.ext\"`.\n"
            "- Network access is disabled in the sandbox; any network calls will fail.\n"
            "- Read/write outside OUTPUT_DIR or the current workdir is not permitted.\n"
            "- File MIME is inferred from filename extension (no mime in contract).\n"
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
        contract: Annotated[Any, "List or JSON string of artifact specs (filename, description)."],
        prog_name: Annotated[Optional[str], "Short name of the program for UI labeling."] = None,
        timeout_s: Annotated[Optional[int], "Execution timeout seconds (default: 600)."] = None,
    ) -> Annotated[dict, "Envelope: ok/out_dyn/out/error/summary."]:
        pass


async def run_exec_tool(
    *,
    tool_manager: Any,
    output_contract: Dict[str, Any],
    code: str,
    contract: List[Dict[str, Any]],
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
    try:
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import get_active_skills_subsystem
        runtime_globals = {
            **runtime_globals,
            **get_active_skills_subsystem().export_runtime_globals(),
        }
    except Exception:
        pass
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
    errors: List[Dict[str, Any]] = []
    succeeded: List[Dict[str, Any]] = []
    for a in contract or []:
        rel = a["filename"]
        p = outdir / rel
        if not p.exists() or p.stat().st_size <= 0:
            missing.append(rel)
            errors.append({
                "artifact_id": a["name"],
                "filename": rel,
                "code": "missing_file" if not p.exists() else "empty_file",
                "message": "file not produced" if not p.exists() else "file is empty",
            })
            continue
        # Validate produced file with heuristics
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import analyze_write_tool_output
            stats = analyze_write_tool_output(
                file_path=str(rel),
                mime=a.get("mime") or "",
                output_dir=outdir,
                artifact_id=a.get("name"),
            )
        except Exception:
            stats = {}
        write_error = (stats or {}).get("write_error")
        if write_error:
            errors.append({
                "artifact_id": a["name"],
                "filename": rel,
                "code": "artifact_invalid",
                "message": write_error,
            })
            continue
        if (stats or {}).get("write_warning") == "file_unusually_small":
            errors.append({
                "artifact_id": a["name"],
                "filename": rel,
                "code": "file_unusually_small",
                "message": "file unusually small",
            })
            continue
        text_content = ""
        if _is_text_mime(a.get("mime") or ""):
            try:
                with p.open("rb") as fh:
                    data = fh.read(EXEC_TEXT_PREVIEW_MAX_BYTES + 1)
                truncated = len(data) > EXEC_TEXT_PREVIEW_MAX_BYTES
                if truncated:
                    data = data[:EXEC_TEXT_PREVIEW_MAX_BYTES]
                text_content = data.decode("utf-8", errors="ignore")
                if truncated:
                    text_content = (text_content.rstrip() + "\n...[truncated]").strip()
            except Exception:
                text_content = ""
        out_dyn[a["name"]] = {
            "type": "file",
            "path": rel,
            "filename": pathlib.Path(rel).name,
            "mime": a["mime"],
            "text": text_content or a["description"],
            "description": a["description"],
            "size_bytes": stats.get("size_bytes") if isinstance(stats, dict) else None,
            "write_warning": stats.get("write_warning") if isinstance(stats, dict) else None,
        }
        succeeded.append({
            "artifact_id": a["name"],
            "filename": rel,
        })

    stderr_tail = ""
    try:
        err_path = outdir / "logs/runtime.err.log"
        if err_path.exists():
            txt = err_path.read_text(encoding="utf-8", errors="ignore")
            stderr_tail = txt[-2000:] if len(txt) > 2000 else txt
    except Exception:
        pass

    runtime_ok = bool(run_res.get("ok", True))
    ok = len(missing) == 0 and len(errors) == 0 and runtime_ok
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

    # Build human-readable report text
    lines: List[str] = []
    has_file_errors = bool(errors)
    if (not runtime_ok) or has_file_errors:
        if runtime_ok:
            lines.append("Runtime error: none")
        else:
            err_msg = (error.get("description") or error.get("message") or "").strip() if error else ""
            err_code = (error.get("error") or error.get("code") or "exec_error") if error else "exec_error"
            lines.append(f"Runtime error: {err_code} — {err_msg}".strip())
            if stderr_tail:
                lines.append("Runtime stderr (tail):")
                lines.append(stderr_tail.strip())
        if errors:
            lines.append("File errors:")
            for e in errors:
                fname = e.get("filename") or e.get("artifact_id") or "unknown"
                msg = e.get("message") or e.get("code") or "error"
                lines.append(f"- {fname}: {msg}")
        if succeeded:
            lines.append("Succeeded:")
            for s in succeeded:
                fname = s.get("filename") or s.get("artifact_id") or "unknown"
                lines.append(f"- {fname}")
    else:
        lines.append("Produced files:")
        for s in succeeded:
            fname = s.get("filename") or s.get("artifact_id") or "unknown"
            lines.append(f"- {fname}")
    report_text = "\n".join(lines).strip()

    items_list = []
    try:
        for name, artifact in out_dyn.items():
            if not isinstance(artifact, dict):
                continue
            items_list.append({
                "artifact_id": name,
                "output": artifact,
                "artifact_kind": artifact.get("type") or "file",
                "summary": "",
                "filepath": artifact.get("path") or "",
            })
    except Exception:
        items_list = []

    payload = {
        "ok": ok,
        "objective": "",
        "contract": output_contract,
        "out_dyn": out_dyn,
        "error": error,
        "report_text": report_text,
        "items": items_list,
        "errors": errors,
        "succeeded": succeeded,
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
            "draft": artifact.get("draft"),
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
        "sources_pool": [],
        "error": error,
        "report_text": report_text,
        "items": items_list,
        "errors": errors,
        "succeeded": succeeded,
        "project_log": None,
    }


# module-level exports
kernel = sk.Kernel()
tools = ExecTools()
kernel.add_plugin(tools, "exec_tools")
