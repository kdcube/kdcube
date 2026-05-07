from __future__ import annotations

import asyncio
import json
import mimetypes
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable
from urllib.parse import quote, unquote, urlparse


def execution_completed_at(execution: Dict[str, Any]) -> str:
    return str(
        execution.get("finished_at")
        or execution.get("updated_at")
        or execution.get("started_at")
        or execution.get("created_at")
        or ""
    ).strip()


def _safe_filename(value: str, fallback: str = "artifact.bin") -> str:
    name = pathlib.PurePosixPath(str(value or "").strip()).name
    name = re.sub(r"[^A-Za-z0-9._@ -]+", "_", name).strip(" ._")
    return name or fallback


def _filename_from_artifact(artifact: Dict[str, Any]) -> str:
    for key in ("filename", "logical_path", "source_physical_path", "stored_path", "hosted_uri"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return _safe_filename(value)
    return "artifact.bin"


def _artifact_logical_path(artifact: Dict[str, Any]) -> str:
    return str(artifact.get("logical_path") or artifact.get("artifact_path") or "").strip()


def _artifact_hosted_uri(artifact: Dict[str, Any]) -> str:
    return str(artifact.get("hosted_uri") or artifact.get("url") or artifact.get("key") or artifact.get("rn") or "").strip()


def _artifact_visibility(artifact: Dict[str, Any]) -> str:
    return str(artifact.get("visibility") or "").strip().lower()


def _is_user_visible_file_artifact(artifact: Dict[str, Any]) -> bool:
    kind = str(artifact.get("kind") or "file").strip().lower() or "file"
    visibility = _artifact_visibility(artifact)
    if kind != "file":
        return False
    if visibility not in {"visible", "user", "external", "public", "shared"}:
        return False
    return bool(
        _artifact_logical_path(artifact)
        or str(artifact.get("source_physical_path") or artifact.get("physical_path") or artifact.get("local_path") or "").strip()
        or str(artifact.get("stored_path") or "").strip()
        or _artifact_hosted_uri(artifact)
    )


def _artifact_selector(artifact: Dict[str, Any], *, index: int) -> tuple[str, str]:
    artifact_id = str(artifact.get("id") or "").strip()
    if artifact_id:
        return "id", artifact_id
    logical_path = _artifact_logical_path(artifact)
    if logical_path:
        return "logical", logical_path
    hosted_uri = _artifact_hosted_uri(artifact)
    if hosted_uri:
        return "hosted", hosted_uri
    for key in ("stored_path", "source_physical_path", "physical_path", "local_path"):
        value = str(artifact.get(key) or "").strip()
        if value:
            return key, value
    filename = _filename_from_artifact(artifact)
    if filename:
        return "filename", filename
    return "index", str(index)


def _artifact_ref(execution: Dict[str, Any], artifact: Dict[str, Any], *, index: int) -> str:
    execution_id = str(execution.get("id") or "").strip()
    selector_kind, selector_value = _artifact_selector(artifact, index=index)
    return (
        f"task-artifact://{quote(execution_id, safe='')}/"
        f"{quote(selector_kind, safe='')}/{quote(selector_value, safe='')}"
    )


def artifact_ref_for_execution_artifact(execution: Dict[str, Any], artifact: Dict[str, Any], *, index: int = 0) -> str:
    return _artifact_ref(execution, artifact, index=index)


def _parse_artifact_ref(artifact_ref: str) -> tuple[str, str, str]:
    parsed = urlparse(str(artifact_ref or "").strip())
    if parsed.scheme != "task-artifact" or not parsed.netloc:
        raise ValueError("artifact_ref must use task-artifact://<execution_id>/<selector-kind>/<selector>")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("artifact_ref must include selector kind and selector value")
    return unquote(parsed.netloc), unquote(parts[0]), unquote(parts[1])


def execution_id_from_artifact_ref(artifact_ref: str) -> str:
    execution_id, _selector_kind, _selector_value = _parse_artifact_ref(artifact_ref)
    return execution_id


def _select_artifact_by_ref(
    artifacts: list[Dict[str, Any]],
    *,
    selector_kind: str,
    selector_value: str,
) -> Dict[str, Any] | None:
    if selector_kind == "index":
        try:
            return artifacts[int(selector_value)]
        except Exception:
            return None
    for artifact in artifacts:
        if selector_kind == "id" and str(artifact.get("id") or "").strip() == selector_value:
            return artifact
        if selector_kind == "logical" and _artifact_logical_path(artifact) == selector_value:
            return artifact
        if selector_kind == "hosted" and _artifact_hosted_uri(artifact) == selector_value:
            return artifact
        if selector_kind in {"stored_path", "source_physical_path", "physical_path", "local_path"}:
            if str(artifact.get(selector_kind) or "").strip() == selector_value:
                return artifact
        if selector_kind == "filename" and _filename_from_artifact(artifact) == _safe_filename(selector_value):
            return artifact
    return None


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _json_from_text(value: str) -> Any:
    raw = str(value or "").strip()
    if not raw or raw[0] not in "[{":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _artifact_from_timeline_dict(item: Dict[str, Any], *, fallback_id: str) -> Dict[str, Any] | None:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    nested_file_item = item.get("file_item") if isinstance(item.get("file_item"), dict) else {}
    source = {**nested_file_item, **payload, **meta, **item}
    logical_path = str(
        source.get("artifact_path")
        or source.get("logical_path")
        or source.get("path")
        or ""
    ).strip()
    if logical_path and not logical_path.startswith("fi:"):
        logical_path = ""
    physical_path = str(
        source.get("physical_path")
        or source.get("source_physical_path")
        or source.get("local_path")
        or source.get("filepath")
        or ""
    ).strip()
    hosted_uri = _artifact_hosted_uri(source)
    filename = str(source.get("filename") or pathlib.PurePosixPath(physical_path or logical_path or hosted_uri).name or "").strip()
    if not (logical_path or physical_path or (hosted_uri and filename)):
        return None
    artifact_id = str(source.get("id") or source.get("artifact_id") or fallback_id).strip()
    return {
        "id": artifact_id,
        "kind": str(source.get("kind") or "file").strip() or "file",
        "logical_path": logical_path,
        "source_physical_path": physical_path,
        "hosted_uri": hosted_uri,
        "mime_type": str(source.get("mime_type") or source.get("mime") or "").strip(),
        "filename": filename,
        "visibility": str(source.get("visibility") or "").strip().lower(),
        "description": str(source.get("description") or source.get("title") or "").strip(),
        "delivery_target": str(source.get("delivery_target") or "").strip(),
        "source": str(source.get("source") or "job_turn_timeline").strip() or "job_turn_timeline",
    }


def _artifacts_from_timeline_record(record: Dict[str, Any], *, index_prefix: str) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    values: list[Any] = [record]
    parsed_text = _json_from_text(str(record.get("text") or ""))
    if parsed_text is not None:
        values.append(parsed_text)
    for value in values:
        for idx, item in enumerate(_iter_dicts(value)):
            artifact = _artifact_from_timeline_dict(item, fallback_id=f"{index_prefix}_{idx + 1}")
            if not artifact:
                continue
            key = str(
                artifact.get("logical_path")
                or artifact.get("hosted_uri")
                or artifact.get("source_physical_path")
                or artifact.get("filename")
                or ""
            )
            if key in seen:
                continue
            seen.add(key)
            out.append({k: v for k, v in artifact.items() if v not in ("", None)})
    return out


def _dedupe_artifacts(items: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(
            item.get("logical_path")
            or item.get("hosted_uri")
            or item.get("source_physical_path")
            or item.get("stored_path")
            or item.get("id")
            or item.get("filename")
            or ""
        )
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(item)
    return out


def _timeline_artifacts_from_execution(execution: Dict[str, Any]) -> list[Dict[str, Any]]:
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    timeline = result.get("timeline") if isinstance(result.get("timeline"), dict) else {}
    blocks = timeline.get("blocks") if isinstance(timeline.get("blocks"), list) else []
    out: list[Dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if isinstance(block, dict):
            for artifact in _artifacts_from_timeline_record(block, index_prefix=f"timeline_{index + 1}"):
                artifact.setdefault("source", "execution_result_timeline")
                out.append({k: v for k, v in artifact.items() if v not in ("", None)})
    return _dedupe_artifacts(out)


def _metadata_execution_artifacts(execution: Dict[str, Any]) -> list[Dict[str, Any]]:
    artifacts = execution.get("artifacts") if isinstance(execution.get("artifacts"), list) else []
    out: list[Dict[str, Any]] = [dict(item) for item in artifacts if isinstance(item, dict)]
    out.extend(_timeline_artifacts_from_execution(execution))
    return _dedupe_artifacts(out)


async def _job_turn_artifacts_from_store(execution: Dict[str, Any], *, sc: Dict[str, Any]) -> list[Dict[str, Any]]:
    conversation_id = str(execution.get("conversation_id") or "").strip()
    turn_id = str(execution.get("turn_id") or "").strip()
    if not conversation_id or not turn_id:
        return []
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

        store = ConversationStore(get_settings().STORAGE_PATH)
        records = await asyncio.to_thread(
            store.list_conversation,
            tenant=sc["tenant"],
            project=sc["project"],
            user_type=sc.get("user_type") or "registered",
            user_or_fp=sc["user_id"],
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
    except Exception:
        return []
    out: list[Dict[str, Any]] = []
    for idx, record in enumerate(records):
        if isinstance(record, dict):
            out.extend(_artifacts_from_timeline_record(record, index_prefix=f"job_timeline_{idx + 1}"))
    return _dedupe_artifacts(out)


async def execution_artifacts(execution: Dict[str, Any], *, sc: Dict[str, Any]) -> list[Dict[str, Any]]:
    return _dedupe_artifacts([*_metadata_execution_artifacts(execution), *await _job_turn_artifacts_from_store(execution, sc=sc)])


async def downloadable_execution_artifacts(execution: Dict[str, Any], *, sc: Dict[str, Any]) -> list[Dict[str, Any]]:
    return [artifact for artifact in await execution_artifacts(execution, sc=sc) if _is_user_visible_file_artifact(artifact)]


def _artifact_access_payload(
    *,
    artifact: Dict[str, Any],
    artifact_ref: str,
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    logical_path = _artifact_logical_path(artifact)
    return {
        "artifact_ref": artifact_ref,
        "job_conversation_id": str(execution.get("conversation_id") or "").strip(),
        "job_turn_id": execution.get("turn_id"),
        "materialize": {
            "tool": "tasks.materialize_execution_artifact",
            "params": {"artifact_ref": artifact_ref},
            "then": (
                "Use the returned current_turn.logical_path with react.read, or the returned "
                "current_turn.physical_path from code/rendering tools."
            ),
        },
        "artifact_kind": "output" if ".outputs/" in logical_path else ("workspace_file" if ".files/" in logical_path else "file"),
    }


def _artifact_for_agent(
    artifact: Dict[str, Any],
    *,
    artifact_ref: str,
    access: Dict[str, Any],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "artifact_ref": artifact_ref,
        "filename": _filename_from_artifact(artifact),
        "mime_type": str(artifact.get("mime_type") or artifact.get("mime") or "").strip(),
        "description": str(artifact.get("description") or "").strip(),
        "kind": str(artifact.get("kind") or access.get("artifact_kind") or "file").strip(),
        "access": access,
        "source": {
            "kind": str(artifact.get("source") or "execution_artifact").strip(),
            "job_conversation_id": access.get("job_conversation_id"),
            "job_turn_id": access.get("job_turn_id"),
            "has_job_fi_ref": bool(_artifact_logical_path(artifact)),
            "has_hosted_uri": bool(_artifact_hosted_uri(artifact)),
        },
    }
    artifact_id = str(artifact.get("id") or "").strip()
    if artifact_id:
        out["id"] = artifact_id
    return {k: v for k, v in out.items() if v not in ("", None)}


async def execution_for_agent(execution: Dict[str, Any], *, sc: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = await execution_artifacts(execution, sc=sc)
    result = {
        "execution_id": execution.get("id"),
        "task_id": execution.get("task_id"),
        "task_title": execution.get("task_title"),
        "status": execution.get("status"),
        "trigger": execution.get("trigger"),
        "completed_at": execution_completed_at(execution),
        "summary": execution.get("summary") or "",
        "job_conversation_id": execution.get("conversation_id"),
        "job_turn_id": execution.get("turn_id"),
        "artifact_count": len(artifacts),
        "artifacts": [],
        "result": execution.get("result") if isinstance(execution.get("result"), dict) else {},
    }
    for index, artifact in enumerate(artifacts):
        ref = _artifact_ref(execution, artifact, index=index)
        access = _artifact_access_payload(artifact=artifact, artifact_ref=ref, execution=execution)
        result["artifacts"].append(_artifact_for_agent(artifact, artifact_ref=ref, access=access))
    return result


async def _read_artifact_bytes(artifact: Dict[str, Any], *, storage_root: pathlib.Path) -> bytes:
    async def _is_file(path: pathlib.Path) -> bool:
        return await asyncio.to_thread(lambda: path.exists() and path.is_file())

    for key in ("source_physical_path", "physical_path", "local_path"):
        value = str(artifact.get(key) or "").strip()
        if not value:
            continue
        path = pathlib.Path(value)
        if not path.is_absolute():
            path = storage_root / value
        if await _is_file(path):
            return await asyncio.to_thread(path.read_bytes)

    value = str(artifact.get("stored_path") or "").strip()
    if value:
        path = storage_root / value
        if await _is_file(path):
            return await asyncio.to_thread(path.read_bytes)

    hosted = _artifact_hosted_uri(artifact)
    if hosted:
        parsed = urlparse(hosted)
        if parsed.scheme == "file":
            path = pathlib.Path(unquote(parsed.path))
            if await _is_file(path):
                return await asyncio.to_thread(path.read_bytes)
        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            return await ConversationStore(get_settings().STORAGE_PATH).get_blob_bytes(hosted)
        except Exception as exc:
            raise FileNotFoundError(f"Cannot read hosted artifact {hosted!r}: {exc}") from exc

    raise FileNotFoundError("artifact has no readable source path or hosted uri")


async def materialize_execution_artifact_for_current_turn(
    *,
    artifact_ref: str,
    execution: Dict[str, Any],
    sc: Dict[str, Any],
) -> Dict[str, Any]:
    turn_id = str(sc.get("turn_id") or "").strip()
    outdir_raw = str(sc.get("outdir") or "").strip()
    if not turn_id or not outdir_raw:
        raise RuntimeError("Current React turn id or output directory is missing.")
    execution_id, selector_kind, selector_value = _parse_artifact_ref(artifact_ref)
    if execution_id != str(execution.get("id") or ""):
        raise ValueError(f"artifact_ref execution {execution_id!r} does not match loaded execution")
    artifacts = await execution_artifacts(execution, sc=sc)
    selected = _select_artifact_by_ref(
        artifacts,
        selector_kind=selector_kind,
        selector_value=selector_value,
    )
    if selected is None:
        raise FileNotFoundError("The requested execution artifact was not found.")
    storage_root = pathlib.Path(sc["storage_root"])
    data = await _read_artifact_bytes(selected, storage_root=storage_root)
    target_name = _filename_from_artifact(selected)
    rel = pathlib.PurePosixPath("recovered-job-artifacts") / _safe_filename(execution_id, "execution") / target_name
    physical_path = f"{turn_id}/outputs/{rel.as_posix()}"
    target = pathlib.Path(outdir_raw) / physical_path
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, data)
    logical = f"fi:{turn_id}.outputs/{rel.as_posix()}"
    mime_type = str(selected.get("mime_type") or selected.get("mime") or mimetypes.guess_type(target_name)[0] or "").strip()
    return {
        "execution_id": execution.get("id"),
        "task_id": execution.get("task_id"),
        "artifact_ref": artifact_ref,
        "artifact": selected,
        "current_turn": {
            "logical_path": logical,
            "physical_path": physical_path,
            "filename": target_name,
            "mime_type": mime_type,
            "size_bytes": len(data),
        },
        "usage": {
            "read": f"react.read(paths=['{logical}'])",
            "code": f"Use physical path {physical_path!r} from code/rendering tools.",
            "note": "This is a current-turn output artifact copy. It is not a workspace checkout ref.",
        },
    }


async def read_execution_artifact_for_download(
    *,
    artifact_ref: str,
    execution: Dict[str, Any],
    sc: Dict[str, Any],
) -> Dict[str, Any]:
    execution_id, selector_kind, selector_value = _parse_artifact_ref(artifact_ref)
    if execution_id != str(execution.get("id") or ""):
        raise ValueError(f"artifact_ref execution {execution_id!r} does not match loaded execution")
    artifacts = await downloadable_execution_artifacts(execution, sc=sc)
    selected = _select_artifact_by_ref(
        artifacts,
        selector_kind=selector_kind,
        selector_value=selector_value,
    )
    if selected is None:
        raise FileNotFoundError("The requested execution artifact was not found.")
    data = await _read_artifact_bytes(selected, storage_root=pathlib.Path(sc["storage_root"]))
    filename = _filename_from_artifact(selected)
    mime_type = str(
        selected.get("mime_type")
        or selected.get("mime")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    ).strip()
    return {
        "execution_id": execution.get("id"),
        "task_id": execution.get("task_id"),
        "artifact_ref": artifact_ref,
        "artifact": selected,
        "filename": filename,
        "mime_type": mime_type,
        "content": data,
        "size_bytes": len(data),
    }
