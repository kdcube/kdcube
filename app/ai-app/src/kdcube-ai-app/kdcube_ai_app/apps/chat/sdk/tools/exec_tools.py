# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/exec_tools.py
from __future__ import annotations

import asyncio
import json
import re
import pathlib
import time
import traceback
import uuid
import tarfile
import zipfile
from collections.abc import Mapping, MutableMapping
from typing import Any, Dict, Optional, Annotated, Tuple, List

import semantic_kernel as sk

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import touch_current_task_activity
from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime, build_packages_installed_block
from kdcube_ai_app.apps.chat.sdk.runtime.diagnose import (
    read_log_tail,
    extract_exec_segment,
    extract_error_lines,
    extract_traceback_blocks,
    merge_infra_logs,
    find_user_code_start_line,
    remap_traceback_line_numbers,
)
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import (
    artifact_outdir_for,
    resolve_artifact_path,
    snapshot_outdir,
    diff_snapshots,
    format_diff,
    build_items_from_diff,
    build_deleted_notices,
)
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import resolve_exec_runtime_profile
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.events import event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_tool_result_error_block,
    error_block_details,
    physical_path_to_logical_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import (
    block_production_policy,
    default_tool_event_policies,
    tool_call_validation_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import extract_code_file_paths
from kdcube_ai_app.apps.chat.sdk.util import (
    LINE_NUMBERS_DISABLED,
    LINE_NUMBERS_LINES,
    count_text_lines,
    count_text_symbols,
    guess_mime_type,
    format_visible_line_window,
    line_number_text,
    normalize_line_numbers_mode,
    normalize_artifact_visibility,
    visible_line_window,
)
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


def _split_turn_artifact_path(path: str) -> Optional[Tuple[str, str, str]]:
    safe = _safe_relpath(path)
    if not safe:
        return None
    parts = safe.split("/", 2)
    if len(parts) != 3:
        return None
    turn_id, namespace, rel = parts
    if not turn_id or namespace not in {"files", "outputs", "attachments"} or not rel:
        return None
    return turn_id, namespace, rel

EXEC_TEXT_PREVIEW_MAX_SYMBOLS = 8000
EXEC_ACTIVITY_TOUCH_INTERVAL_SEC = 30.0
INFRA_LOG_TAIL_CHARS = 12000
USER_LOG_TAIL_CHARS = 4000
TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/csv",
    "text/csv",
}
EXEC_USER_CODE_FILENAME = "user_code.py"
ARCHIVE_MIME_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
}
ARCHIVE_SUFFIXES = {
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
}
INTERNAL_ARCHIVE_TOP_LEVEL = {
    ".kdcube",
    "_etc",
    "_exec-workspace",
    "_home",
    "_kdcube-supervisor",
    "_opt",
    "_proc",
    "_root",
    "_run",
    "_sys",
    "_tmp",
    "_usr",
    "_var",
    "_workspace",
    "_workspace_out",
    "bin",
    "boot",
    "dev",
    "etc",
    "exec-workspace",
    "home",
    "kdcube-supervisor",
    "lib",
    "lib64",
    "logs",
    "opt",
    "proc",
    "root",
    "run",
    "sbin",
    "sys",
    "tmp",
    "usr",
    "var",
    "workspace",
    "workspace_out",
}
INTERNAL_ARCHIVE_FILENAMES = {
    "docker.err.log",
    "docker.out.log",
    "infra.log",
    "runtime.err.log",
    "supervisor.log",
}


def _is_text_mime(mime: str) -> bool:
    if not mime:
        return False
    if mime.startswith("text/"):
        return True
    return mime in TEXT_MIME_TYPES


def _is_archive_path(path: pathlib.Path, mime: str) -> bool:
    suffixes = "".join(path.suffixes[-2:]).lower()
    return (
        (mime or "").lower() in ARCHIVE_MIME_TYPES
        or path.suffix.lower() in ARCHIVE_SUFFIXES
        or suffixes in ARCHIVE_SUFFIXES
    )


def _archive_entry_violation(name: str) -> Optional[str]:
    raw = str(name or "").replace("\\", "/").strip()
    if not raw:
        return "empty archive entry name"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw) or raw.startswith("//"):
        return f"absolute archive entry is not allowed: {raw}"
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return f"path traversal archive entry is not allowed: {raw}"
    if not parts:
        return "empty archive entry name"
    lowered = [part.lower() for part in parts]
    if lowered[0] in INTERNAL_ARCHIVE_TOP_LEVEL:
        return f"archive appears to contain internal runtime path: {raw}"
    if any(part in INTERNAL_ARCHIVE_FILENAMES for part in lowered):
        return f"archive appears to contain internal runtime log: {raw}"
    if any(part in {"kdcube-supervisor", ".kdcube"} for part in lowered):
        return f"archive appears to contain internal runtime path: {raw}"
    return None


def _validate_archive_egress(path: pathlib.Path, *, mime: str = "") -> Optional[str]:
    mime_l = (mime or "").strip().lower()
    suffixes = "".join(path.suffixes[-2:]).lower()
    expects_zip = (
        mime_l in {"application/zip", "application/x-zip-compressed"}
        or path.suffix.lower() == ".zip"
        or suffixes.endswith(".zip")
    )
    if expects_zip and not zipfile.is_zipfile(path):
        return "archive validation failed: not a readable zip archive"
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if not infos:
                    return "archive validation failed: zip archive has no files"
                for info in infos:
                    violation = _archive_entry_violation(info.filename)
                    if violation:
                        return violation
            return None
    except Exception as exc:
        return f"archive validation failed: {exc}"

    try:
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    violation = _archive_entry_violation(member.name)
                    if violation:
                        return violation
            return None
    except Exception as exc:
        return f"archive validation failed: {exc}"
    return None


def _validate_contract_artifact_egress(
    *,
    path: pathlib.Path,
    outdir: pathlib.Path,
    rel: str,
    mime: str,
) -> Optional[Dict[str, str]]:
    try:
        if path.is_symlink():
            return {
                "code": "artifact_symlink_blocked",
                "message": "contracted output must be a regular file, not a symlink",
            }
        resolved_outdir = outdir.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_outdir)
        except ValueError:
            return {
                "code": "artifact_path_escape_blocked",
                "message": "contracted output resolves outside the execution output directory",
            }
        if not path.is_file():
            return {
                "code": "artifact_not_regular_file",
                "message": "contracted output must be a regular file",
            }
    except Exception as exc:
        return {
            "code": "artifact_path_validation_failed",
            "message": f"contracted output path validation failed: {exc}",
        }

    if _is_archive_path(pathlib.Path(rel), mime):
        violation = _validate_archive_egress(path, mime=mime)
        if violation:
            return {
                "code": "artifact_internal_path_blocked",
                "message": violation,
            }
    return None


def _strip_exec_banner(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if lines and lines[0].startswith("===== EXECUTION "):
        return "\n".join(lines[1:]).lstrip()
    return text


def _build_exec_loader_wrapper(*, user_code_filename: str = EXEC_USER_CODE_FILENAME) -> str:
    return "\n".join([
        "import asyncio",
        "import ast",
        "import inspect",
        "import pathlib",
        "import sys",
        "import traceback",
        "",
        f"USER_CODE_PATH = pathlib.Path(__file__).with_name({user_code_filename!r})",
        "",
        "async def _run_user_code():",
        "    source = USER_CODE_PATH.read_text(encoding='utf-8')",
        "    scope = dict(globals())",
        "    scope['__file__'] = str(USER_CODE_PATH)",
        "    scope['__name__'] = '__kdcube_exec_user_code__'",
        "    code_obj = compile(",
        "        source,",
        "        str(USER_CODE_PATH),",
        "        'exec',",
        "        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,",
        "        dont_inherit=True,",
        "    )",
        "    result = eval(code_obj, scope, scope)",
        "    if inspect.isawaitable(result):",
        "        await result",
        "",
        "async def _main():",
        "    try:",
        "        await _run_user_code()",
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


def _runtime_result_error_tail(payload: Any) -> Tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    err = payload.get("error")
    if not isinstance(err, dict):
        return "", ""

    parts: List[str] = []
    for key in ("where", "error", "code", "description", "message", "details"):
        val = err.get(key)
        if val is None or val == "":
            continue
        parts.append(f"{key}: {val}")
    text = "\n".join(parts).strip()
    summary = str(err.get("description") or err.get("message") or err.get("error") or "").strip()
    return summary, text[-8000:] if len(text) > 8000 else text


def _load_runtime_result_error(result_path: pathlib.Path) -> Tuple[str, str]:
    try:
        if not result_path.exists() or not result_path.is_file():
            return "", ""
        payload = json.loads(result_path.read_text(encoding="utf-8", errors="ignore"))
        return _runtime_result_error_tail(payload)
    except Exception:
        return "", ""


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
        raw_filepath = item.get("filepath")
        filename = raw_filepath.strip() if isinstance(raw_filepath, str) else ""
        description = (item.get("description") or "").strip()
        if not filename or not description:
            return None, {
                "code": "invalid_artifact_spec",
                "message": "Each artifact requires filepath (the full OUTPUT_DIR-relative path) and description",
            }
        visibility = normalize_artifact_visibility(item.get("visibility"), default="")
        if item.get("visibility") is not None and not visibility:
            return None, {
                "code": "invalid_artifact_spec",
                "message": "visibility must be either 'external' or 'internal'",
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
                "message": "Contract filepath must be under the current turn files/ or outputs/ namespace (attachments not allowed)",
            }
        qualified = _split_turn_artifact_path(safe_filename)
        if not qualified or qualified[1] not in {"files", "outputs"}:
            return None, {
                "code": "invalid_filename",
                "message": (
                    "filepath must be OUTPUT_DIR-relative and start with "
                    "'turn_<current>/files/' or 'turn_<current>/outputs/': "
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
        mime = guess_mime_type(leaf)
        normalized.append(
            {
                "name": name,
                "filename": safe_filename,
                "mime": mime,
                "description": description,
                "visibility": visibility or "external",
            }
        )
    if not normalized:
        return None, {"code": "invalid_artifacts", "message": "No valid artifacts found"}
    return normalized, None


def _build_exec_context_from_comm_spec(
    *,
    comm_spec: Dict[str, Any],
    runtime_globals: Dict[str, Any],
    exec_id: Optional[str],
    exec_runtime: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    service = comm_spec.get("service") if isinstance(comm_spec.get("service"), dict) else {}
    conversation = comm_spec.get("conversation") if isinstance(comm_spec.get("conversation"), dict) else {}
    bundle_spec = runtime_globals.get("BUNDLE_SPEC") or {}
    bundle_id = bundle_spec.get("id") if isinstance(bundle_spec, dict) else None
    return {
        "tenant": comm_spec.get("tenant"),
        "project": comm_spec.get("project"),
        "user_id": comm_spec.get("user_id"),
        "user_type": comm_spec.get("user_type"),
        "conversation_id": conversation.get("conversation_id"),
        "turn_id": conversation.get("turn_id"),
        "session_id": conversation.get("session_id"),
        "request_id": service.get("request_id"),
        "bundle_id": bundle_id,
        "exec_id": exec_id,
        "codegen_run_id": exec_id,
        "exec_runtime": dict(exec_runtime or {}),
    }


def normalize_exec_contract_for_turn(
    artifacts: Any,
    *,
    turn_id: str,
) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, str]], Optional[Dict[str, Any]]]:
    """
    Normalize exec contract to current turn:
    - contract entries must target turn_<current>/files/<name> or turn_<current>/outputs/<name>
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
        raw_filepath = item.get("filepath")
        filename = raw_filepath.strip() if isinstance(raw_filepath, str) else ""
        description = (item.get("description") or "").strip()
        if not filename or not description:
            return None, [], {
                "code": "invalid_artifact_spec",
                "message": "Each artifact requires filepath (the full OUTPUT_DIR-relative path) and description",
            }
        visibility = normalize_artifact_visibility(item.get("visibility"), default="")
        if item.get("visibility") is not None and not visibility:
            return None, [], {
                "code": "invalid_artifact_spec",
                "message": "visibility must be either 'external' or 'internal'",
            }
        safe_filename = _safe_relpath(filename)
        if not safe_filename:
            return None, [], {
                "code": "invalid_filename",
                "message": f"Invalid filename path: {filename}",
            }
        qualified = _split_turn_artifact_path(safe_filename)
        if (
            "/attachments/" in safe_filename
            or safe_filename.startswith("attachments/")
            or safe_filename.startswith(f"{turn_id}/attachments/")
            or (qualified and qualified[1] == "attachments")
        ):
            return None, [], {
                "code": "invalid_filename",
                "message": "Contract filepath must be under the current turn files/ or outputs/ namespace (attachments not allowed)",
            }
        rewritten = None
        if qualified:
            qualified_turn_id, namespace, _rel = qualified
            if not (
                qualified_turn_id == turn_id
                and namespace in {"files", "outputs"}
            ):
                return None, [], {
                    "code": "invalid_filename",
                    "message": "Contract filepath must use current turn_id and files/ or outputs/ path",
                }
            filename = safe_filename
        elif safe_filename.startswith("files/"):
            rel = safe_filename[len("files/") :]
            rewritten = f"{turn_id}/files/{rel}"
        elif safe_filename.startswith("outputs/"):
            rel = safe_filename[len("outputs/") :]
            rewritten = f"{turn_id}/outputs/{rel}"
        else:
            rewritten = f"{turn_id}/outputs/{safe_filename}"
        if rewritten:
            rewrites.append({"original": filename, "rewritten": rewritten})
            filename = rewritten

        updated.append(
            {
                # _normalize_artifacts_spec reads the input under `filepath`; this
                # is the internal handoff, not a second accepted input alias.
                "filepath": filename,
                "description": description,
                "visibility": visibility or "external",
            }
        )

    normalized, err = _normalize_artifacts_spec(updated)
    if err:
        return None, rewrites, err
    return normalized, rewrites, None


_PATH_TOKEN_RE = re.compile(r"[^\s'\"\)\];,]+")
_UNQUALIFIED_ARTIFACT_PREFIXES = ("files/", "outputs/", "attachments/")


def _is_unqualified_artifact_path_token(token: str) -> bool:
    return any(str(token or "").startswith(prefix) for prefix in _UNQUALIFIED_ARTIFACT_PREFIXES)


def rewrite_exec_code_paths(
    code: str,
    *,
    turn_id: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Best-effort recovery for legacy current-turn artifact paths in generated code.
    Leaves already qualified turn_<id>/files|outputs|attachments paths intact.
    Returns (rewritten_code, rewrites).
    """
    if not isinstance(code, str) or not code.strip() or not turn_id:
        return code or "", []
    rewrites: List[Dict[str, str]] = []
    out_parts: List[str] = []
    last = 0
    for m in _PATH_TOKEN_RE.finditer(code):
        orig = m.group(0)
        if not _is_unqualified_artifact_path_token(orig):
            continue
        repl = f"{turn_id}/{orig}"
        out_parts.append(code[last:m.start()] + repl)
        last = m.end()
        rewrites.append({"original": orig, "rewritten": repl})
    out_parts.append(code[last:])
    return "".join(out_parts), rewrites


EXEC_TOOL_CALL_VALIDATION_POLICY_ID = "exec_tools.tool_call_validation.exec_preflight"


def _validation_notices(target: MutableMapping[str, Any]) -> List[Dict[str, Any]]:
    rows = target.setdefault("notice_rows", [])
    return rows if isinstance(rows, list) else []


def _validation_blocks(target: MutableMapping[str, Any]) -> List[Dict[str, Any]]:
    rows = target.setdefault("blocks", [])
    return rows if isinstance(rows, list) else []


def _validation_state_updates(target: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    updates = target.setdefault("state_updates", {})
    return updates if isinstance(updates, MutableMapping) else {}


def _add_validation_notice(
    target: MutableMapping[str, Any],
    *,
    code: str,
    message: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    _validation_notices(target).append(
        {
            "code": str(code or "").strip(),
            "message": str(message or "").strip(),
            "extra": dict(extra or {}),
        }
    )


def _stop_exec_validation(
    target: MutableMapping[str, Any],
    *,
    error_code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    last_tool_result: List[Dict[str, Any]] | None = None,
) -> None:
    tool_id = str(target.get("tool_id") or target.get("event_source_id") or "").strip()
    tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
    turn_id = str(target.get("turn_id") or "").strip()
    _validation_blocks(target).append(
        build_tool_result_error_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            code=error_code,
            message=message,
            details=dict(details or {}),
        )
    )
    target["retry_decision"] = True
    target["stop"] = True
    updates = _validation_state_updates(target)
    updates["retry_decision"] = True
    updates["last_tool_id"] = tool_id
    updates["last_tool_result"] = list(last_tool_result or [])


def _exec_validation_extract_code(state: Mapping[str, Any]) -> str:
    exec_streamer = state.get("exec_code_streamer")
    if exec_streamer:
        try:
            code_txt = exec_streamer.get_code() or ""
            if isinstance(code_txt, str) and code_txt:
                return code_txt
        except Exception:
            pass

    packet = state.get("last_decision_raw")
    if isinstance(packet, Mapping):
        channels = packet.get("channels") or {}
        if isinstance(channels, Mapping):
            code = channels.get("code") or {}
            if isinstance(code, Mapping):
                text = code.get("text")
                if isinstance(text, str) and text:
                    return text

    return ""


def detect_exec_code_contamination(code: str) -> Dict[str, Any] | None:
    """Return a managed validation error when an exec code channel is contaminated.

    Exec accepts raw Python only. This detector rejects channel tags, thinking
    markup, and markdown fences before the code reaches any runtime. It is used
    by the exec tool-call validation policy and by the legacy external-tool
    fallback while the policy pipeline is feature-flagged.
    """
    text = code or ""
    if not text.strip():
        return None
    markers = [
        "<channel:",
        "</channel:",
        "<thinking>",
        "</thinking>",
        "<channel:thinking>",
        "</channel:thinking>",
        "<channel:action>",
        "</channel:action>",
    ]
    lower = text.lower()
    marker = next((m for m in markers if m.lower() in lower), "")
    first_nonempty = ""
    for line in text.splitlines():
        if line.strip():
            first_nonempty = line.strip()
            break
    if not marker and not first_nonempty.startswith(("```", "`")):
        return None
    line_no = 0
    offending = first_nonempty
    if marker:
        marker_l = marker.lower()
        for idx, line in enumerate(text.splitlines(), start=1):
            if marker_l in line.lower():
                line_no = idx
                offending = line.strip()
                break
    return {
        "code": "exec_code_contaminated",
        "message": "Exec code channel contained non-code text or channel tags; no code was executed.",
        "where": "react.exec_code_validation",
        "marker": marker or "markdown_fence_or_backtick",
        "line": line_no or 1,
        "excerpt": offending[:500],
    }


@tool_call_validation_policy(event_policy_id=EXEC_TOOL_CALL_VALIDATION_POLICY_ID)
def exec_tool_call_validation_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Validate and normalize an exec ReAct tool call before execution.

    This policy owns the exec-specific preflight behavior that used to live in
    the shared external-tool handler:

    - normalize the output contract to the current turn;
    - require code in the code channel and reject contaminated code;
    - rewrite current-turn relative artifact refs inside code;
    - emit the model-visible `react.tool.code` block;
    - require `react.pull` for historical files that are not materialized.

    The policy mutates the call-validation target. It never executes code.
    """
    if not isinstance(target, MutableMapping):
        return target
    final_params = target.get("final_params")
    if not isinstance(final_params, MutableMapping):
        final_params = {}
        target["final_params"] = final_params
    state = target.get("state") if isinstance(target.get("state"), Mapping) else {}
    tool_id = str(target.get("tool_id") or target.get("event_source_id") or "").strip()
    tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
    turn_id = str(target.get("turn_id") or "").strip()

    base_contract = final_params.get("contract")
    normalized_contract, contract_rewrites, contract_err = normalize_exec_contract_for_turn(
        base_contract,
        turn_id=turn_id,
    )
    if contract_err:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_contract_invalid",
            message=contract_err.get("message") or "Invalid exec contract",
            extra={"error": contract_err, "tool_id": tool_id, "protocol_violation": True},
        )
        target["retry_decision"] = True
        target["stop"] = True
        _validation_state_updates(target)["retry_decision"] = True
        return target
    if contract_rewrites:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_contract_rewritten",
            message="Exec contract filenames were rewritten to current turn files/ paths.",
            extra={"rewritten": contract_rewrites, "tool_id": tool_id},
        )
    if normalized_contract is not None:
        final_params["contract"] = normalized_contract

    code_txt = _exec_validation_extract_code(state)
    if not code_txt:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_missing_code",
            message="Exec tool requires code in <channel:code>; no code was received.",
            extra={"tool_id": tool_id},
        )
        error_payload = {
            "tool_id": tool_id,
            "reason": "missing_channel.code",
            "recovery": (
                "Use exec only when raw Python is emitted in channel:code. "
                "For ordinary PDF/PPTX/DOCX rendering, call rendering_tools.write_* directly."
            ),
        }
        target["decision_raw_reason"] = "missing_channel.code"
        _stop_exec_validation(
            target,
            error_code="exec_missing_code",
            message="Exec tool requires raw Python in channel:code; no code was received.",
            details=error_payload,
            last_tool_result=[{
                "artifact_id": tool_id,
                "output": None,
                "summary": "",
                "error": {
                    "code": "exec_missing_code",
                    "message": "Exec tool requires raw Python in channel:code; no code was received.",
                    "details": error_payload,
                },
            }],
        )
        return target

    contamination = detect_exec_code_contamination(code_txt)
    if contamination:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_code_contaminated",
            message=contamination["message"],
            extra={**contamination, "tool_id": tool_id},
        )
        _stop_exec_validation(
            target,
            error_code=contamination["code"],
            message=contamination["message"],
            details=contamination,
            last_tool_result=[{
                "artifact_id": tool_id,
                "output": None,
                "summary": "",
                "error": contamination,
            }],
        )
        return target

    rewritten_code, code_rewrites = rewrite_exec_code_paths(code_txt, turn_id=turn_id)
    if rewritten_code != code_txt:
        code_txt = rewritten_code
        final_params["code"] = rewritten_code
        exec_streamer = target.get("exec_streamer")
        if exec_streamer:
            try:
                exec_streamer.set_code(rewritten_code)
            except Exception:
                pass
    elif code_txt:
        final_params["code"] = code_txt
    if code_rewrites:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_code_rewritten",
            message="Exec code contained relative files/ or attachments/ paths; rewritten to current turn.",
            extra={"rewritten": code_rewrites, "tool_id": tool_id},
        )

    if code_txt:
        lang = ""
        exec_streamer = target.get("exec_streamer")
        if exec_streamer:
            try:
                lang = (exec_streamer.subsystem_language or "").strip()
            except Exception:
                lang = ""
        mime = "text/x-python" if (lang or "").lower() in {"python", "py"} else "text/plain"
        _validation_blocks(target).append(
            {
                "type": "react.tool.code",
                "call_id": tool_call_id,
                "tool_id": tool_id,
                "mime": mime,
                "path": f"fi:{turn_id}.code.{tool_call_id}" if turn_id else "",
                "text": code_txt,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "meta": {
                    "lang": lang or "python",
                    "kind": "file",
                    "tool_call_id": tool_call_id,
                    "tool_id": tool_id,
                },
            }
        )

    paths, rewritten_paths = extract_code_file_paths(code_txt, turn_id=turn_id) if code_txt else ([], [])
    target["write_timeline_local"] = True
    if rewritten_paths:
        _add_validation_notice(
            target,
            code="protocol_violation.exec_path_rewritten",
            message="Exec code referenced relative files/… paths; rewritten to current turn.",
            extra={"rewritten": rewritten_paths},
        )
        target.setdefault("log_rows", []).append(
            {
                "level": "WARNING",
                "message": f"[react] exec_path_rewritten: {rewritten_paths}",
            }
        )
    if paths:
        outdir = pathlib.Path(str(target.get("outdir") or ""))
        missing_local = [p for p in paths if not resolve_artifact_path(outdir, p).exists()]
        if missing_local:
            logical_missing = [physical_path_to_logical_path(p) or p for p in missing_local]
            pull_hint = f"react.pull(paths={json.dumps(logical_missing, ensure_ascii=False)})"
            _stop_exec_validation(
                target,
                error_code="pre_exec_pull_required",
                message="Exec code referenced historical files that are not materialized locally. Use react.pull(paths=[...]) first.",
                details={
                    "missing": missing_local,
                    "logical_missing": logical_missing,
                    "pull_hint": pull_hint,
                },
                last_tool_result=[],
            )
    return target


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
            "visibility": a.get("visibility") or "external",
        }
    return contract, normalized, None


def _runtime_error_details(run_res: Dict[str, Any] | None) -> Tuple[bool, str, str]:
    run_res = run_res or {}
    runtime_ok = bool(run_res.get("ok", True))
    raw = str(run_res.get("error") or "").strip()
    summary = str(run_res.get("error_summary") or "").strip()
    stderr_tail = str(run_res.get("stderr_tail") or "").strip()

    def _stderr_summary(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        for line in lines:
            lowered = line.lower()
            if "error" in lowered or "exception" in lowered:
                return line
        return lines[0]

    code = "execution_failed"
    message = ""
    if raw:
        if ":" in raw:
            head, tail = raw.split(":", 1)
            code = head.strip() or code
            if tail.strip():
                message = tail.strip()
        else:
            code = raw
    if summary:
        message = summary
    if not message and stderr_tail:
        message = _stderr_summary(stderr_tail)
    if not message:
        message = raw or "Execution failed (non-zero exit)"
    return runtime_ok, code, message


def _build_exec_error_payload(
    *,
    missing: List[str],
    errors: List[Dict[str, Any]],
    run_res: Dict[str, Any] | None,
    infra_text: str,
) -> Optional[Dict[str, Any]]:
    runtime_ok, runtime_code, runtime_message = _runtime_error_details(run_res)
    if runtime_ok and not missing and not errors:
        return None

    if not runtime_ok:
        desc = runtime_message
        if missing:
            desc = f"{desc}. Missing output files: {', '.join(missing)}"
        return {
            "where": "exec.tool",
            "code": runtime_code,
            "message": desc,
            "error": runtime_code,
            "description": desc,
            "managed": True,
            "details": {
                "missing": missing,
                "errors": errors,
                "run": run_res or {},
                "stderr_tail": infra_text,
                "runtime_code": runtime_code,
                "runtime_message": runtime_message,
            },
        }

    if missing:
        desc = f"Missing output files: {', '.join(missing)}"
        return {
            "where": "exec.tool",
            "code": "missing_output_files",
            "message": desc,
            "error": "missing_output_files",
            "description": desc,
            "managed": True,
            "details": {"missing": missing, "errors": errors, "run": run_res or {}, "stderr_tail": infra_text},
        }

    desc = "; ".join(
        f"{e.get('filename') or e.get('artifact_id') or 'unknown'}: {e.get('message') or e.get('code') or 'error'}"
        for e in errors
    ).strip() or "Artifact validation failed"
    return {
        "where": "exec.tool",
        "code": "artifact_validation_failed",
        "message": desc,
        "error": "artifact_validation_failed",
        "description": desc,
        "managed": True,
        "details": {"missing": missing, "errors": errors, "run": run_res or {}, "stderr_tail": infra_text},
    }


EXEC_RESULT_BLOCK_PRODUCTION_POLICY_ID = "exec_tools.block_production.exec_result"


def exec_tool_event_policies() -> list[dict[str, Any]]:
    """Return ReAct event policies for the Python exec tool.

    Exec is not an exploration tool by default. Its tool result has an
    exec-specific shape: `raw.report_text` is a human-readable execution report
    and `raw.items` are artifact rows produced by the isolated runtime.
    """
    return [
        *default_tool_event_policies(),
        {
            "react_phase": "tool_call_validation",
            "event_policy_id": EXEC_TOOL_CALL_VALIDATION_POLICY_ID,
        },
        {
            "react_phase": "block_production",
            "event_policy_id": EXEC_RESULT_BLOCK_PRODUCTION_POLICY_ID,
        },
    ]


@block_production_policy(event_policy_id=EXEC_RESULT_BLOCK_PRODUCTION_POLICY_ID)
def exec_result_block_production_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    """Project exec raw result surfaces into the production accumulator.

    The generic `react.tool.call` block is already produced by the harness before
    execution. This policy only handles the result side:

    - `raw.report_text` becomes a `react.tool.result` markdown block candidate;
    - `raw.items` become `artifact_rows`, preserving the current exec artifact
      loop shape used by `external.py`;
    - no exploration/source-pool rows are produced by default.
    """
    if not isinstance(target, MutableMapping):
        return target
    raw = target.get("raw") if isinstance(target.get("raw"), Mapping) else {}
    target.setdefault("blocks", [])
    target.setdefault("artifact_rows", [])

    report_text = str(raw.get("report_text") or "").strip()
    if report_text and isinstance(target.get("blocks"), list):
        turn_id = str(target.get("turn_id") or "").strip()
        tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
        path = str(target.get("tool_result_path") or "").strip()
        target["blocks"].append({
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "text/markdown",
            "path": path,
            "text": report_text,
            "meta": {"tool_call_id": tool_call_id},
        })
    elif isinstance(target.get("blocks"), list):
        # No human-readable report_text — the sandbox failed to produce one
        # (typically an infra/harness failure before the code ran). Emit a
        # STRUCTURED error result block (application/json with an `error` field)
        # so the timeline registers it as an error, not a plain result, and the
        # agent sees the failure instead of assuming success.
        err = target.get("tool_error") or target.get("error") or target.get("call_error")
        err = err if isinstance(err, dict) else ({"message": str(err)} if err else None)
        if err:
            turn_id = str(target.get("turn_id") or "").strip()
            tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
            target["blocks"].append(build_tool_result_error_block(
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                code=str(err.get("code") or "sandbox_execution_failed"),
                message=str(err.get("message") or err.get("description") or "Execution failed."),
                details=error_block_details(err),
            ))

    raw_items = raw.get("items")
    if isinstance(raw_items, list) and isinstance(target.get("artifact_rows"), list):
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            row.setdefault("visibility", "external")
            row.setdefault("emit_hosted_file", True)
            row.setdefault("resolve_file_path", True)
            target["artifact_rows"].append(row)
    return target


class ExecTools:
    @event_source(
        event_source_id="{alias}.execute_code_python",
        policies=exec_tool_event_policies(),
        description="Execute generated Python in the isolated runtime and produce existing ReAct execution report/artifact blocks.",
        kind="react.tool",
    )
    @kernel_function(
        name="execute_code_python",
        description=(
            "Registers the sanbdbox to execute a Python 3.11 program in this sandbox.\n"
            "Will wait for code to be mounted to start execution. You generate the code to execute in the dedicated channel called <channel:code>.\n"
            "You cannot provide the code in the call of this function directly.\n"
            "\n"
            "[Requirements to code which can be executed by this tool]:\n"
            "- Must be a Python module body / snippet.\n"
            "- Top-level await is allowed.\n"
            "\n"
            "RUNTIME BEHAVIOR\n"
            "- The executor runs your snippet verbatim from a separate user_code.py module.\n"
            "- The executor supports top-level await and does not indent or rewrite your program body.\n"
            "- After execution, the harness collects ONLY the files named in `contract`. It iterates the\n"
            "  contract (NOT the workdir): each contracted `filepath` that exists and is non-empty is hosted,\n"
            "  and the exec workdir is then discarded.\n"
            "- CONSEQUENCE (the #1 cause of 'my file is gone next turn'): `contract` is the EXHAUSTIVE list of\n"
            "  what survives. Any file your code writes that is NOT in the contract is thrown away — it cannot\n"
            "  be pulled, read, shared, or reused this turn or in any later turn. There is no 'keep everything'\n"
            "  scan. So contract EVERY file you want to keep: deliverables AND intermediates you will reuse\n"
            "  (e.g. chart PNGs you embed into an xlsx and want to pull later) — mark such intermediates\n"
            "  visibility='internal'. The contract `filepath` MUST be byte-identical to the path your code\n"
            "  writes to, or the file is reported as missing and its bytes are lost.\n"
            "\n"
            "[INPUTS]\n"
            "- When called from React decision, the code is provided in <channel:code> (not in params).\n"
            "1) `contract` (list or JSON string, REQUIRED): list of output files specs with fields:\n"
            "   - filepath (the FULL OUTPUT_DIR-relative path, NOT a bare name; MUST equal the path your code\n"
            "     writes to). The files/ vs outputs/ choice IS this prefix:\n"
            "     turn_<current>/files/<scope>/... = durable workspace/project state;\n"
            "     turn_<current>/outputs/<scope>/... = produced deliverables / reports / one-off artifacts.\n"
            "   - description (what this file contains / why it was produced)\n"
            "   - visibility (optional: `external` or `internal`; default `external`).\n"
            "       · external = hosted AND delivered to the user.\n"
            "       · internal = hosted and pullable/readable by you in later turns, but NOT shown to the user.\n"
            "   These are outputs of this program that it promises to produce; anything not listed is discarded.\n"
            "2) `prog_name` (string, optional): short name of the program for UI labeling.\n"
            "\n"
            "FETCH_CTX (ADVANCED)\n"
            "- If your snippet needs to load the text data for the artifact you see on timeline, you may call\n"
            "  ctx_tools.fetch_ctx inside the snippet using agent_io_tools.tool_call.\n"
            "- The paths allowed with this tool are only logical ar: so: tc:\n"
            "- This is for computation or for producing smaller derived artifacts. It is not an uncapped way\n"
            "  to put large content into model-visible context; exec stdout and previews are capped too.\n"
            "- Only execution-enabled runtime tool handles are available inside snippets. Orchestration/job tools\n"
            "  such as automation_job.* must be called as direct ReAct tool calls, not from generated Python.\n"
            "- Do NOT rely on fetch_ctx unless you are the code author for this run.\n"
            "\n"
            "Example:\n"
            "  import json\n"
            "  resp = await agent_io_tools.tool_call(\n"
            "      fn=ctx_tools.fetch_ctx,\n"
            "      params={\"path\": \"ar:turn_<id>.user.prompt\"},\n"
            "      call_reason=\"Load user message for turn_<id>\",\n"
            "      tool_id=\"ctx_tools.fetch_ctx\"\n"
            "  )\n"
            "  if resp.get(\"err\"):\n"
            "      raise RuntimeError(resp[\"err\"])\n"
            "  artifact = resp[\"ret\"]\n"
            "  payload = artifact.get(\"payload\")\n"
            "  if payload is None:\n"
            "      payload = json.loads(artifact[\"text\"])\n"
            "\n"
            "FILES & PATHS\n"
            "- `OUTPUT_DIR` is the output data/artifact root.\n"
            "- `OUT_DIR` is also available as `Path(OUTPUT_DIR)` if you prefer Path operations.\n"
            "- Input workspace artifacts from context are available by their filenames under OUTPUT_DIR/turn_<id>/files/<scope>/.\n"
            "- Historical or generated non-workspace artifacts may also be under OUTPUT_DIR/turn_<id>/outputs/<scope>/.\n"
            "- User attachments are available under OUTPUT_DIR/turn_<id>/attachments/.\n"
            "- Write durable project/workspace state to OUTPUT_DIR/turn_<current>/files/<scope>/.\n"
            "- Write reports, test results, and other non-workspace deliverables to OUTPUT_DIR/turn_<current>/outputs/<scope>/.\n"
            "- Build paths like:\n"
            "  `Path(OUTPUT_DIR) / \"turn_<current>/files/app/my_file.ext\"` or `Path(OUTPUT_DIR) / \"turn_<current>/outputs/report/report.txt\"`.\n"
            "- Use the exact current turn id shown in the runtime context.\n"
            "- Network access is disabled in the sandbox; any network calls will fail.\n"
            "- Read/write outside OUTPUT_DIR or the current workdir is not permitted.\n"
            "- The runtime filesystem view is restricted; do not inspect, list, copy, archive, or report system/runtime paths.\n"
            "- Contracted archive outputs are rejected if they contain internal/system path snapshots.\n"
            "- File MIME is inferred from filename extension (no mime in contract).\n"
            "\n"
            "AGENT GUIDANCE FOR RESULTS\n"
            "- The final tool result includes `Program log (tail)` from user.log, not the full program log.\n"
            "- Large text artifacts may also be preview-truncated in the immediate tool result.\n"
            "- Therefore, if you need the authoritative result to be available to the model, write it to contracted files.\n"
            "- Use `print(...)` or `logging.getLogger(\"user\")` only for short progress lines, counts, and pointers.\n"
            "- For large outputs, split them into multiple contracted files instead of dumping everything to stdout.\n"
            "- For filesystem/search tasks, prefer structured outputs such as `listing.json`, `matches.json`, or `summary.txt`.\n"
            "- For patch/edit tasks, prefer a `.diff`/`.patch` file plus a small summary file if helpful.\n"
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
        contract: Annotated[Any, "List or JSON string of artifact specs (filepath, description, optional visibility=external|internal) that you plan your future code to produce. filepath is the full OUTPUT_DIR-relative path your code writes to."],
        prog_name: Annotated[Optional[str], "Short name of the program for UI labeling."] = None,
        timeout_s: Annotated[Optional[int], "Execution timeout seconds (default: 600)."] = None,
    ) -> Annotated[dict, "Envelope: ok/artifacts/items/error/report_text."]:
        pass

    # @kernel_function(
    #     name="execute_code_python_side_effect",
    #     description=(
    #         "Registers the sanbdbox to execute a Python 3.11 program in this sandbox.\n"
    #         "Will wait for code to be mounted to start execution. You generate the code to execute in the dedicated channel called <channel:code>.\n"
    #         "You cannot provide the code in the call of this function directly.\n"
    #         "\n"
    #         "[Requirements to code which can be executed by this tool]:\n"
    #         "- Must be SNIPPET that is inserted inside an async main() wrapper.\n"
    #         "- The snippet SHOULD use async operations (await where needed).\n"
    #         "\n"
    #         "RUNTIME BEHAVIOR\n"
    #         "- The executor wraps your snippet into an async main() and runs it.\n"
    #         "- After execution, outputs are inferred by diffing out/ (side-effects).\n"
    #         "\n"
    #         "[INPUTS]\n"
    #         "- When called from React decision, the code is provided in <channel:code> (not in params).\n"
    #         "1) `prog_name` (string, optional): short name of the program for UI labeling.\n"
    #         "\n"
    #         "FETCH_CTX (ADVANCED)\n"
    #         "- If your snippet needs to load the text data for the artifact you see on timeline, you may call\n"
    #         "  ctx_tools.fetch_ctx inside the snippet using agent_io_tools.tool_call.\n"
    #         "- The paths allowed with this tool are only logical ar: so: tc:\n"
    #         "- Do NOT rely on fetch_ctx unless you are the code author for this run.\n"
    #         "\n"
    #         "Example:\n"
    #         "  resp = await agent_io_tools.tool_call(\n"
    #         "      fn=ctx_tools.fetch_ctx,\n"
    #         "      params={\"path\": \"ar:turn_<id>.user.prompt\"},\n"
    #         "      call_reason=\"Load user message for turn_<id>\",\n"
    #         "      tool_id=\"ctx_tools.fetch_ctx\"\n"
    #         "  )\n"
    #         "  if resp.get(\"err\"):\n"
    #         "      raise RuntimeError(resp[\"err\"])\n"
    #         "\n"
    #         "FILES & PATHS\n"
    #         "- Input artifacts from context are available by their filenames under OUTPUT_DIR/turn_<id>/files/.\n"
    #         "- User attachments are available under OUTPUT_DIR/turn_<id>/attachments/.\n"
    #         "- Write your outputs to OUTPUT_DIR/turn_<current>/files/ or OUTPUT_DIR/turn_<current>/outputs/.\n"
    #         "- `OUTPUT_DIR` is a global string path in the runtime; build paths like:\n"
    #         "  `os.path.join(OUTPUT_DIR, \"turn_<current>/files/app/my_file.ext\")` or `Path(OUTPUT_DIR) / \"turn_<id>/attachments/user_file.ext\"`.\n"
    #         "- Network access is disabled in the sandbox; any network calls will fail.\n"
    #         "- Read/write outside OUTPUT_DIR or the current workdir is not permitted.\n"
    #         "\n"
    #         "AVAILABLE PACKAGES\n"
    #         f"{build_packages_installed_block()}\n"
    #         "\n"
    #         "OUTPUT\n"
    #         "- A status dict indicating success/error and the produced file artifacts.\n"
    #     ),
    # )
    async def execute_code_python_side_effect(
        self,
        prog_name: Annotated[Optional[str], "Short name of the program for UI labeling."] = None,
        timeout_s: Annotated[Optional[int], "Execution timeout seconds (default: 600)."] = None,
    ) -> Annotated[dict, "Envelope: ok/artifacts/items/error/report_text."]:
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
    exec_runtime: Optional[Dict[str, Any]] = None,
    bundle_storage_dir: Optional[str] = None,
    text_preview_max_symbols: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Execute pre-written code using the same runtime as codegen.
    Returns an envelope similar to run_codegen_tool().
    """
    log = logger or AgentLogger("exec.tool")

    # 1) unique per invocation
    result_filename = f"exec_result_{exec_id}.json" if exec_id else f"exec_result_{uuid.uuid4().hex[:10]}.json"
    activity_exec_id = (exec_id or result_filename).strip()

    async def _touch_running_runtime() -> None:
        while True:
            await asyncio.sleep(EXEC_ACTIVITY_TOUCH_INTERVAL_SEC)
            touch_current_task_activity(f"exec_tool.runtime.running:{activity_exec_id}")

    # 2) prepare workspace
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    artifact_outdir = artifact_outdir_for(outdir)

    (workdir / EXEC_USER_CODE_FILENAME).write_text(code or "", encoding="utf-8")
    (workdir / "main.py").write_text(_build_exec_loader_wrapper(), encoding="utf-8")

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
    spec = tool_manager.build_portable_spec()
    comm_spec = getattr(tool_manager.comm, "_export_comm_spec_for_runtime", lambda: {})()
    exec_context = _build_exec_context_from_comm_spec(
        comm_spec=comm_spec if isinstance(comm_spec, dict) else {},
        runtime_globals=runtime_globals,
        exec_id=exec_id,
        exec_runtime=exec_runtime,
    )

    globals_for_runtime = {
        "CONTRACT": output_contract,
        "COMM_SPEC": comm_spec,
        "PORTABLE_SPEC_JSON": spec.to_json(),
        "EXEC_CONTEXT": exec_context,
        "RESULT_FILENAME": result_filename,
        **({"EXECUTION_ID": exec_id} if exec_id else {}),
        **runtime_globals,
    }
    if isinstance(bundle_storage_dir, str) and bundle_storage_dir.strip():
        globals_for_runtime["BUNDLE_STORAGE_DIR"] = bundle_storage_dir.strip()
    if exec_runtime:
        globals_for_runtime["EXEC_RUNTIME_CONFIG"] = resolve_exec_runtime_profile(
            runtime=exec_runtime,
            profile=None,
        )
        mode = globals_for_runtime["EXEC_RUNTIME_CONFIG"].get("mode")
        if isinstance(mode, str) and mode.strip():
            log.log(f"[exec.tool] runtime override active: mode={mode.strip()}", level="INFO")

    touch_current_task_activity(f"exec_tool.runtime.started:{activity_exec_id}")
    activity_task = asyncio.create_task(_touch_running_runtime())
    try:
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
        finally:
            activity_task.cancel()
            try:
                await activity_task
            except asyncio.CancelledError:
                pass
            touch_current_task_activity(f"exec_tool.runtime.finished:{activity_exec_id}")
    except Exception as e:
        # An exception here comes from the sandbox HARNESS (launching the docker
        # runtime, subprocess/IPC, result handling) — never from the user's code,
        # which runs inside the sandbox and reports its own errors as a result.
        # Say so explicitly so the agent does not mistake it for a code defect.
        log.log(f"[exec.tool] runtime.execute_py_code failed: {type(e).__name__}: {e}", level="ERROR")
        log.log(traceback.format_exc(), level="ERROR")
        return {
            "ok": False,
            "error": {
                "where": "exec.tool.harness",
                "code": "sandbox_execution_failed",
                "message": (
                    "The code execution sandbox failed to run — a platform/harness error, NOT a defect in your "
                    f"code; your code did not execute. Underlying error: {type(e).__name__}: {e}. A single retry is "
                    "reasonable in case it was a transient blip, but if the SAME failure repeats do NOT resend the "
                    "same request: send a minimal probe (e.g. print('ok')) to check whether the sandbox is alive, "
                    "and if that also fails, report the infrastructure failure to the user or finish without code "
                    "execution instead of retrying further."
                ),
                "error": "sandbox_execution_failed",
                "description": str(e),
                "managed": True,
                "retryable": True,
                "retry_guidance": "retry_once_then_probe_then_stop",
            },
        }

    runtime_result_path = artifact_outdir / result_filename
    if not runtime_result_path.exists():
        runtime_result_path = outdir / result_filename
    runtime_result_summary, runtime_result_tail = _load_runtime_result_error(runtime_result_path)
    if isinstance(run_res, dict) and (runtime_result_summary or runtime_result_tail):
        if runtime_result_summary and not str(run_res.get("error_summary") or "").strip():
            run_res["error_summary"] = runtime_result_summary
        if runtime_result_tail:
            existing_tail = str(run_res.get("stderr_tail") or "").strip()
            if runtime_result_tail not in existing_tail:
                run_res["stderr_tail"] = (
                    existing_tail + "\n" + runtime_result_tail
                ).strip() if existing_tail else runtime_result_tail

    # 4) build artifacts by checking requested files
    out_dyn: Dict[str, Any] = {}
    missing: List[str] = []
    errors: List[Dict[str, Any]] = []
    succeeded: List[Dict[str, Any]] = []
    try:
        preview_max_symbols = int(text_preview_max_symbols or EXEC_TEXT_PREVIEW_MAX_SYMBOLS)
    except Exception:
        preview_max_symbols = EXEC_TEXT_PREVIEW_MAX_SYMBOLS
    preview_max_symbols = max(0, preview_max_symbols)
    for a in contract or []:
        rel = a["filename"]
        p = resolve_artifact_path(outdir, rel)
        if not p.exists() or p.stat().st_size <= 0:
            missing.append(rel)
            errors.append({
                "artifact_id": a["name"],
                "filename": rel,
                "code": "missing_file" if not p.exists() else "empty_file",
                "message": "file not produced" if not p.exists() else "file is empty",
            })
            continue
        egress_root = artifact_outdir
        try:
            p.resolve().relative_to(artifact_outdir.resolve())
        except Exception:
            egress_root = outdir
        egress_error = _validate_contract_artifact_egress(
            path=p,
            outdir=egress_root,
            rel=rel,
            mime=a.get("mime") or "",
        )
        if egress_error:
            errors.append({
                "artifact_id": a["name"],
                "filename": rel,
                **egress_error,
            })
            continue
        # Validate produced file with heuristics
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.artifact_analysis import analyze_write_tool_output
            stats = analyze_write_tool_output(
                file_path=str(rel),
                mime=a.get("mime") or "",
                output_dir=artifact_outdir,
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
        text_preview = ""
        text_truncated = False
        text_symbols = None
        line_count = None
        text_preview_symbols = 0
        text_preview_line_start = None
        text_preview_line_end = None
        is_text = _is_text_mime(a.get("mime") or "")
        if is_text:
            raw_preview = ""
            try:
                with p.open("r", encoding="utf-8", errors="ignore") as fh:
                    raw_preview = fh.read(preview_max_symbols + 1) if preview_max_symbols else ""
                if preview_max_symbols:
                    text_content = raw_preview[:preview_max_symbols]
                text_preview_symbols = len(text_content)
                text_symbols = count_text_symbols(p)
                if text_symbols is not None:
                    text_truncated = text_symbols > preview_max_symbols
                else:
                    text_truncated = len(raw_preview) > preview_max_symbols
                line_count = count_text_lines(p)
                if text_content:
                    line_numbers_mode = normalize_line_numbers_mode(
                        getattr(get_settings(), "AI_REACT_LINE_NUMBERS_MODE", LINE_NUMBERS_LINES),
                        default=LINE_NUMBERS_LINES,
                    )
                    line_window = visible_line_window(
                        text_content,
                        source_truncated=bool(text_truncated),
                        total_line_count=line_count,
                    )
                    start = line_window.get("line_start")
                    end = line_window.get("line_end")
                    text_preview_line_start = start
                    text_preview_line_end = end
                    numbered = (
                        line_number_text(text_content, line_numbers=line_numbers_mode)
                        if line_numbers_mode != LINE_NUMBERS_DISABLED
                        else text_content
                    )
                    header = [
                        "[TEXT FILE PREVIEW]",
                        f"path: {rel}",
                    ]
                    header.append(f"lines: {format_visible_line_window(line_window)}")
                    if line_window.get("partial_line") is not None:
                        header.append(f"partial_line: {line_window.get('partial_line')}")
                    if line_count is not None:
                        header.append(f"line_count: {line_count}")
                    if text_symbols is not None:
                        header.append(f"text_symbols: {text_symbols}")
                    header.append(f"visible_text_symbols: {len(text_content)}")
                    header.append(f"preview_cap_text_symbols: {preview_max_symbols}")
                    header.append(f"line_numbers: {line_numbers_mode if line_count else LINE_NUMBERS_DISABLED}")
                    text_preview = "\n".join(header + ["", numbered]).strip()
                if text_truncated:
                    if text_preview:
                        text_preview = (text_preview.rstrip() + "\n\n[TEXT FILE PREVIEW TRUNCATED]").strip()
            except Exception:
                text_content = ""
                text_preview = ""
                text_symbols = None
                line_count = None
        out_dyn[a["name"]] = {
            "type": "file",
            "path": rel,
            "filename": pathlib.Path(rel).name,
            "mime": a["mime"],
            "text_preview": text_preview if is_text else "",
            "description": a["description"],
            "visibility": a.get("visibility") or "external",
            "size_bytes": stats.get("size_bytes") if isinstance(stats, dict) else None,
            "text_symbols": text_symbols if is_text else None,
            "line_count": line_count if is_text else None,
            "text_preview_symbols": text_preview_symbols if is_text else 0,
            "text_preview_max_symbols": preview_max_symbols if is_text else 0,
            "text_is_preview": bool(is_text),
            "text_preview_line_start": text_preview_line_start if is_text else None,
            "text_preview_line_end": text_preview_line_end if is_text else None,
            "text_preview_line_numbers": bool(text_content) if is_text else False,
            "text_truncated": bool(text_truncated) if is_text else False,
            "write_warning": stats.get("write_warning") if isinstance(stats, dict) else None,
        }
        succeeded.append({
            "artifact_id": a["name"],
            "filename": rel,
        })

    infra_tail = ""
    user_log_tail = ""
    user_code_start_line = None
    try:
        user_code_start_line = find_user_code_start_line(workdir / "main.py")
        merged_infra = merge_infra_logs(
            log_dir=outdir / "logs",
            exec_id=exec_id,
            max_chars=INFRA_LOG_TAIL_CHARS,
        )
        infra_path = outdir / "logs" / "infra.log"
        runtime_path = infra_path if infra_path.exists() else (outdir / "logs" / "runtime.err.log")
        if merged_infra and merged_infra.strip():
            infra_tail = merged_infra
        else:
            infra_tail = extract_exec_segment(
                read_log_tail(runtime_path, max_chars=INFRA_LOG_TAIL_CHARS),
                exec_id,
            )
        user_log_text = ""
        for user_log_path in (
                outdir / "logs" / "executor" / "user.log",
                outdir / "logs" / "user.log",
                artifact_outdir / "logs" / "user.log",
        ):
            user_log_text = read_log_tail(user_log_path, max_chars=USER_LOG_TAIL_CHARS)
            if user_log_text:
                break
        user_log_tail = extract_exec_segment(user_log_text, exec_id)
    except Exception:
        pass

    if user_code_start_line:
        infra_tail = remap_traceback_line_numbers(infra_tail, user_code_start_line)
        user_log_tail = remap_traceback_line_numbers(user_log_tail, user_code_start_line)

    user_log_error_lines = extract_error_lines(user_log_tail)
    user_error_lines = user_log_error_lines
    user_tracebacks = extract_traceback_blocks(user_log_tail)
    if user_tracebacks:
        blocks = [b.strip() for b in user_tracebacks.split("\n\n") if b.strip()]
        seen = set()
        uniq = []
        for b in blocks:
            if b in seen:
                continue
            seen.add(b)
            uniq.append(b)
        user_tracebacks = "\n\n".join(uniq)
    program_err_in_log = bool(user_log_tail and (user_log_error_lines or "Traceback" in user_log_tail))
    program_err_extra = False

    infra_text = infra_tail or ""
    if isinstance(run_res, dict):
        run_stderr = (run_res.get("stderr_tail") or "").strip()
        if run_stderr and run_stderr not in infra_text:
            infra_text = (infra_text + "\n" + run_stderr).strip()
        run_summary = (run_res.get("error_summary") or "").strip()
        if run_summary and run_summary not in infra_text:
            infra_text = (infra_text + "\n" + run_summary).strip()

    infra_error_lines = extract_error_lines(infra_text)
    infra_tracebacks = extract_traceback_blocks(infra_text)
    if infra_tracebacks:
        blocks = [b.strip() for b in infra_tracebacks.split("\n\n") if b.strip()]
        seen = set()
        uniq = []
        for b in blocks:
            if b in seen:
                continue
            seen.add(b)
            uniq.append(b)
        infra_tracebacks = "\n\n".join(uniq)
    infra_has_err = bool(infra_error_lines or infra_tracebacks or (infra_text.strip() and not bool((run_res or {}).get("ok", True))))

    runtime_ok, runtime_code, runtime_message = _runtime_error_details(run_res if isinstance(run_res, dict) else {})
    ok = len(missing) == 0 and len(errors) == 0 and runtime_ok
    error = _build_exec_error_payload(
        missing=missing,
        errors=errors,
        run_res=run_res if isinstance(run_res, dict) else {},
        infra_text=infra_text,
    )

    if logger is not None and not runtime_ok:
        try:
            logger.log(
                "[exec.tool] runtime failure "
                f"code={runtime_code} summary={runtime_message}",
                level="ERROR",
            )
            if infra_text.strip():
                logger.log(f"[exec.tool] runtime diagnostics tail\n{infra_text[-4000:]}", level="ERROR")
        except Exception:
            pass

    # Build human-readable report text
    lines: List[str] = []
    has_file_errors = bool(errors)
    err_msg = (error.get("description") or error.get("message") or "").strip() if error else ""
    err_code = (error.get("error") or error.get("code") or "exec_error") if error else "exec_error"
    if ok:
        lines.append("Status: success")
    else:
        lines.append(f"Status: error — {err_code}: {err_msg}".strip())
        if not runtime_ok and runtime_message and runtime_message != err_msg:
            lines.append(f"Runtime failure: {runtime_message}")

    if errors:
        lines.append("File errors:")
        for e in errors:
            fname = e.get("filename") or e.get("artifact_id") or "unknown"
            try:
                fname = pathlib.Path(fname).name
            except Exception:
                pass
            msg = e.get("message") or e.get("code") or "error"
            lines.append(f"- {fname}: {msg}")
    if missing and not (error and (error.get("code") == "missing_output_files")):
        lines.append("Missing contracted outputs:")
        for rel in missing:
            try:
                rel = pathlib.Path(rel).name
            except Exception:
                pass
            lines.append(f"- {rel}")
    if succeeded:
        lines.append("Succeeded:")
        for s in succeeded:
            fname = s.get("filename") or s.get("artifact_id") or "unknown"
            try:
                fname = pathlib.Path(fname).name
            except Exception:
                pass
            lines.append(f"- {fname}")

    if infra_has_err:
        lines.append("Infra errors (infra.log):")
        if infra_error_lines:
            lines.append(infra_error_lines.strip())
        if infra_tracebacks:
            lines.append(infra_tracebacks.strip())
        if not infra_error_lines and not infra_tracebacks:
            infra_display = _strip_exec_banner(infra_text).strip()
            if infra_display:
                lines.append(infra_display[-2000:])

    user_log_display = _strip_exec_banner(user_log_tail)
    if user_log_display:
        lines.append("Program log (tail):")
        lines.append(user_log_display.strip())
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
                "visibility": artifact.get("visibility") or "external",
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
    result_path = artifact_outdir / result_filename
    try:
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if logger is not None:
        try:
            preview = report_text
            if len(preview) > 4000:
                preview = preview[:4000] + "\n...[truncated]"
            level = "INFO" if ok else "ERROR"
            logger.log(f"[exec.tool] final report\n{preview}", level=level)
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
            "visibility": artifact.get("visibility") or "external",
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
        "artifact_outdir": str(artifact_outdir),
        "artifacts": artifacts_list,
        "sources_pool": [],
        "error": error,
        "report_text": report_text,
        "items": items_list,
        "errors": errors,
        "succeeded": succeeded,
        "user_out_tail": user_log_tail,
        "user_err_tail": "",
        "runtime_err_tail": infra_text,
        "user_error_lines": user_error_lines,
        "user_tracebacks": user_tracebacks,
        "runtime_error_lines": infra_error_lines,
        "runtime_tracebacks": infra_tracebacks,
        "project_log": None,
    }


async def run_exec_tool_no_contract(
    *,
    tool_manager: Any,
    code: str,
    timeout_s: int,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
    exec_id: Optional[str] = None,
    exec_runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute code without an output contract (side-effects mode).
    This still runs in the same isolated runtime and returns logs/summary.
    """
    return await run_exec_tool(
        tool_manager=tool_manager,
        output_contract={},
        code=code,
        contract=[],
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        logger=logger,
        exec_id=exec_id,
        exec_runtime=exec_runtime,
    )


async def run_exec_tool_side_effects(
    *,
    tool_manager: Any,
    code: str,
    timeout_s: int,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
    exec_id: Optional[str] = None,
    exec_runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute code without a contract and report side-effects by diffing outdir.
    """
    artifact_outdir = artifact_outdir_for(outdir)
    before = snapshot_outdir(artifact_outdir)
    envelope = await run_exec_tool_no_contract(
        tool_manager=tool_manager,
        logger=logger,
        code=code,
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        exec_id=exec_id,
        exec_runtime=exec_runtime,
    )
    after = snapshot_outdir(artifact_outdir)
    diff = diff_snapshots(before, after)
    diff_text = format_diff(diff)

    items = build_items_from_diff(artifact_outdir, diff)
    items.extend(build_deleted_notices(diff))

    report_text = (envelope.get("report_text") or "").strip()
    side_effects_hdr = "Side-effects (out/ diff):"
    if report_text:
        report_text = f"{report_text}\n{side_effects_hdr}\n{diff_text}"
    else:
        report_text = f"{side_effects_hdr}\n{diff_text}"
    envelope["report_text"] = report_text
    envelope["workspace_diff"] = diff
    envelope["items"] = items
    return envelope


# module-level exports
kernel = sk.Kernel()
tools = ExecTools()
kernel.add_plugin(tools, "exec_tools")
