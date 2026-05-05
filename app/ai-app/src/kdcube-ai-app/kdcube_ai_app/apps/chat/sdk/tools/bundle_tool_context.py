from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Dict, List

from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec

_TOOL_SUBSYSTEM: Any = None
_LOG = logging.getLogger("kdcube.bundle.tools")

__all__ = [
    "ScopedBundleConfig",
    "bind_integrations",
    "error",
    "host_files",
    "log_tool_error",
    "log_tool_start",
    "log_tool_success",
    "ok",
    "scope",
]


class ScopedBundleConfig:
    def __init__(self, props: Dict[str, Any] | None = None):
        self.bundle_props = props or {}

    def bundle_prop(self, path: str, default: Any = None) -> Any:
        cursor: Any = self.bundle_props
        for part in str(path or "").split("."):
            if not part:
                continue
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor


def bind_integrations(integrations: Dict[str, Any]) -> None:
    global _TOOL_SUBSYSTEM
    _TOOL_SUBSYSTEM = (integrations or {}).get("tool_subsystem")


def _caller_tool_subsystem() -> Any:
    import inspect
    import sys

    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        this_module = sys.modules.get(__name__)
        while frame is not None:
            module = sys.modules.get(str(frame.f_globals.get("__name__") or ""))
            if module is not None and module is not this_module:
                tool_subsystem = (
                    getattr(module, "_TOOL_SUBSYSTEM", None)
                    or getattr(module, "TOOL_SUBSYSTEM", None)
                )
                if tool_subsystem is not None:
                    return tool_subsystem
            frame = frame.f_back
    finally:
        del frame
    return None


def _current_tool_subsystem() -> Any:
    return _TOOL_SUBSYSTEM or _caller_tool_subsystem()


def _current_bundle_call_context(tool_subsystem: Any) -> Dict[str, Any]:
    value = getattr(tool_subsystem, "bundle_call_context", None)
    if isinstance(value, dict):
        return dict(value)
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_current_bundle_call_context,
            get_current_request_context,
        )

        context = get_current_bundle_call_context()
        if context:
            return context
        request_context = get_current_request_context()
        request_value = getattr(request_context, "bundle_call_context", None)
        return dict(request_value or {}) if isinstance(request_value, dict) else {}
    except Exception:
        return {}


def ok(ret: Any) -> Dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "ret": None}


def scope() -> Dict[str, Any]:
    tool_subsystem = _current_tool_subsystem()
    if tool_subsystem is None:
        raise RuntimeError("tools are not bound to the current tool subsystem")
    comm = tool_subsystem.comm
    spec = tool_subsystem.bundle_spec
    user_id = (
        getattr(comm, "user_id", None)
        or getattr(comm, "fingerprint", None)
        or "anonymous"
    )
    tenant = getattr(comm, "tenant", None) or "unknown"
    project = getattr(comm, "project", None) or "unknown"
    user_type = getattr(comm, "user_type", None) or "registered"
    storage_root = storage_for_spec(
        spec=spec,
        tenant=tenant,
        project=project,
        ensure=True,
    )
    if storage_root is None:
        raise RuntimeError("bundle storage root is unavailable")
    bundle_props = getattr(tool_subsystem, "bundle_props", None)
    if not isinstance(bundle_props, dict):
        bundle_props = {}
    conversation = getattr(comm, "conversation", None)
    conversation_id = ""
    turn_id = ""
    session_id = ""
    if isinstance(conversation, dict):
        conversation_id = str(conversation.get("conversation_id") or "").strip()
        turn_id = str(conversation.get("turn_id") or "").strip()
        session_id = str(conversation.get("session_id") or "").strip()
    outdir = ""
    workdir = ""
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV

        outdir = str(OUTDIR_CV.get("") or "").strip()
        workdir = str(WORKDIR_CV.get("") or "").strip()
    except Exception:
        pass
    outdir = outdir or os.environ.get("OUTPUT_DIR", "")
    workdir = workdir or os.environ.get("WORKDIR", "")
    return {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "user_type": user_type,
        "bundle_id": getattr(spec, "id", None) or "",
        "conversation_id": conversation_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "outdir": outdir,
        "workdir": workdir,
        "storage_root": storage_root,
        "bundle_props": bundle_props,
        "bundle_call_context": _current_bundle_call_context(tool_subsystem),
        "comm": comm,
        "entrypoint": ScopedBundleConfig(bundle_props),
    }


def _file_row_to_host_artifact(row: Dict[str, Any]) -> Dict[str, Any]:
    path = str(
        row.get("physical_path")
        or row.get("path")
        or row.get("local_path")
        or row.get("artifact_path")
        or row.get("logical_path")
        or ""
    ).strip()
    filename = str(row.get("filename") or pathlib.PurePosixPath(path).name or "file").strip()
    mime = str(row.get("mime") or row.get("mime_type") or "application/octet-stream").strip()
    artifact_id = str(row.get("artifact_id") or row.get("slot") or filename).strip()
    description = str(row.get("description") or row.get("summary") or filename).strip()
    return {
        "type": "file",
        "artifact_id": artifact_id,
        "resource_id": artifact_id,
        "slot": artifact_id,
        "tool_id": str(row.get("tool_id") or "").strip(),
        "mime": mime,
        "description": description,
        "output": {
            "type": "file",
            "path": path,
            "filename": filename,
            "mime": mime,
            "text": row.get("text") if isinstance(row.get("text"), str) else "",
        },
    }


def _hosted_row_to_file_row(
    *,
    hosted: Dict[str, Any],
    source: Dict[str, Any],
    emitted: bool,
) -> Dict[str, Any]:
    filename = str(hosted.get("filename") or source.get("filename") or "").strip()
    mime = str(hosted.get("mime") or source.get("mime") or source.get("mime_type") or "application/octet-stream").strip()
    size = hosted.get("size")
    out = dict(source)
    out.update(
        {
            "type": "file",
            "visibility": str(source.get("visibility") or "external").strip() or "external",
            "hosted": True,
            "emitted": bool(emitted),
            "filename": filename,
            "mime": mime,
            "mime_type": mime,
            "hosted_uri": hosted.get("hosted_uri") or source.get("hosted_uri") or "",
            "key": hosted.get("key") or source.get("key") or "",
            "rn": hosted.get("rn") or source.get("rn") or "",
            "physical_path": hosted.get("physical_path") or source.get("physical_path") or source.get("path") or "",
        }
    )
    if size is not None:
        out["size"] = size
        out["size_bytes"] = size
    return out


async def host_files(
    files: List[Dict[str, Any]],
    *,
    emit: bool = True,
    outdir: str | os.PathLike[str] | None = None,
    turn_id: str | None = None,
) -> Dict[str, Any]:
    """
    Host current-turn files from inside a trusted bundle tool.

    Returns a `ret` payload ready to pass to `ok(...)`:
      {"artifact_type": "files", "files": [...]}
    """
    tool_subsystem = _current_tool_subsystem()
    if tool_subsystem is None:
        raise RuntimeError("tools are not bound to the current tool subsystem")
    hosting_service = getattr(tool_subsystem, "hosting_service", None)
    if hosting_service is None:
        raise RuntimeError("tool hosting service is unavailable")

    sc = scope()
    comm = sc.get("comm")
    if comm is None:
        raise RuntimeError("tool communicator is unavailable")
    svc = getattr(comm, "service", None) or {}
    outdir_value = str(outdir or sc.get("outdir") or "").strip()
    turn_value = str(turn_id or sc.get("turn_id") or "").strip()
    source_rows = [dict(row) for row in (files or []) if isinstance(row, dict)]
    artifacts = [_file_row_to_host_artifact(row) for row in source_rows]
    hosted = await hosting_service.host_files_to_conversation(
        rid=svc.get("request_id") or "",
        files=artifacts,
        outdir=outdir_value,
        tenant=svc.get("tenant") or sc.get("tenant") or "",
        project=svc.get("project") or sc.get("project") or "",
        user=svc.get("user") or sc.get("user_id") or getattr(comm, "user_id", None) or "",
        conversation_id=svc.get("conversation_id") or sc.get("conversation_id") or "",
        user_type=svc.get("user_type") or sc.get("user_type") or getattr(comm, "user_type", None) or "",
        turn_id=turn_value,
    )
    if emit and hosted:
        await hosting_service.emit_solver_artifacts(files=hosted, citations=[])

    hosted_files: List[Dict[str, Any]] = []
    for idx, item in enumerate(hosted or []):
        source = source_rows[idx] if idx < len(source_rows) else {}
        hosted_files.append(_hosted_row_to_file_row(hosted=item, source=source, emitted=bool(emit)))
    return {
        "artifact_type": "files",
        "files": hosted_files,
        "hosted_count": len(hosted_files),
        "emitted": bool(emit and hosted_files),
    }


def _scope_log_fields(sc: Dict[str, Any] | None) -> Dict[str, Any]:
    sc = sc or {}
    return {
        "tenant": sc.get("tenant"),
        "project": sc.get("project"),
        "bundle_id": sc.get("bundle_id"),
        "user_id": sc.get("user_id"),
        "user_type": sc.get("user_type"),
        "conversation_id": sc.get("conversation_id"),
        "turn_id": sc.get("turn_id"),
    }


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:300] + "...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(key): _safe_value(child)
            for key, child in list(value.items())[:20]
            if "token" not in str(key).lower() and "secret" not in str(key).lower()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in list(value)[:10]]
    return str(value)


def log_tool_start(tool_name: str, sc: Dict[str, Any] | None = None, **params: Any) -> None:
    _LOG.info(
        "[bundle.tool.start] tool=%s scope=%s params=%s",
        tool_name,
        _scope_log_fields(sc),
        _safe_value(params),
    )


def log_tool_success(tool_name: str, sc: Dict[str, Any] | None = None, **result: Any) -> None:
    _LOG.info(
        "[bundle.tool.success] tool=%s scope=%s result=%s",
        tool_name,
        _scope_log_fields(sc),
        _safe_value(result),
    )


def log_tool_error(tool_name: str, exc: BaseException, sc: Dict[str, Any] | None = None, **params: Any) -> None:
    _LOG.exception(
        "[bundle.tool.error] tool=%s scope=%s params=%s error=%s",
        tool_name,
        _scope_log_fields(sc),
        _safe_value(params),
        str(exc),
    )
