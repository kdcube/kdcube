# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import pathlib
import base64
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List

from kdcube_ai_app.apps.chat.sdk.util import _truncate, token_count
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import extract_source_sids
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import tc_result_path


def normalize_physical_path(path_value: str, *, turn_id: str) -> tuple[str, str, bool]:
    """
    Normalize a user-supplied path to a physical OUT_DIR-relative path.
    Always returns a path starting with "<turn_id>/files/…".
    Returns (physical_path, relpath, rewritten_flag).
    """
    raw = (path_value or "").strip()
    if not raw:
        return "", "", False
    # Accept logical paths and convert to physical
    if raw.startswith("fi:"):
        logical = raw[len("fi:"):]
        # fi:<turn_id>.files/<relpath>
        if ".files/" in logical:
            tid, rel = logical.split(".files/", 1)
            rel = rel.lstrip("/")
            use_turn = turn_id or tid
            physical = f"{use_turn}/files/{rel}"
            return physical, rel, True
        # fi:<turn_id>.user.attachments/<name>
        if ".user.attachments/" in logical:
            tid, rel = logical.split(".user.attachments/", 1)
            rel = rel.lstrip("/")
            use_turn = turn_id or tid
            physical = f"{use_turn}/attachments/{rel}"
            return physical, rel, True
        # unknown logical -> return as-is
        return raw, raw, False
    rel = raw
    rewritten = False
    if "/files/" in raw and raw.startswith("turn_"):
        rel = raw.split("/files/", 1)[1]
        rewritten = True
    prefix = f"{turn_id}/files/"
    if turn_id and raw.startswith(prefix):
        rel = raw[len(prefix):]
        rewritten = True
    physical = f"{turn_id}/files/{rel}" if turn_id else rel
    if physical != raw:
        rewritten = True
    return physical, rel, rewritten


def normalize_relpath(path_value: str, *, turn_id: str) -> str:
    """
    Return OUT_DIR-relative relpath for a user-supplied path.
    """
    try:
        _, rel, _ = normalize_physical_path(path_value, turn_id=turn_id)
        return rel
    except Exception:
        return (path_value or "").strip()


def detect_edit(*, timeline: Any, artifact_path: str, tool_call_id: str) -> bool:
    if not timeline or not artifact_path:
        return False
    try:
        existing = timeline.resolve_artifact(artifact_path)
        if not isinstance(existing, dict):
            return False
        prev_call_id = existing.get("tool_call_id")
        if prev_call_id and prev_call_id != tool_call_id:
            return True
        return True
    except Exception:
        return False


def build_artifact_meta_block(
    *,
    turn_id: str,
    tool_call_id: str,
    artifact: Dict[str, Any],
    artifact_path: str,
    physical_path: str,
    edited: bool = False,
    tokens: Optional[int] = None,
) -> Dict[str, Any]:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta = {
        "artifact_path": artifact_path,
        "physical_path": physical_path,
        "mime": (artifact.get("value") or {}).get("mime") or artifact.get("mime"),
        "kind": artifact.get("artifact_kind") or artifact.get("kind"),
        "visibility": artifact.get("visibility"),
        "channel": artifact.get("channel"),
        "tool_id": artifact.get("tool_id"),
        "tool_call_id": tool_call_id,
        "edited": bool(edited),
        "ts": ts,
    }
    size_bytes = (artifact.get("value") or {}).get("size_bytes") or artifact.get("size_bytes")
    if size_bytes is not None:
        meta["size_bytes"] = size_bytes
    description = (artifact.get("value") or {}).get("description") or artifact.get("description")
    if description:
        meta["description"] = description
    write_warning = (artifact.get("value") or {}).get("write_warning")
    if write_warning:
        meta["write_warning"] = write_warning
    sources_used = artifact.get("sources_used") or (artifact.get("value") or {}).get("sources_used")
    if sources_used:
        meta["sources_used"] = sources_used
    if artifact.get("error"):
        meta["error"] = artifact.get("error")
    if tokens is not None:
        try:
            meta["tokens"] = int(tokens)
        except Exception:
            meta["tokens"] = tokens
    for key in ("hosted_uri", "rn", "key", "local_path"):
        val = (artifact.get("value") or {}).get(key)
        if val:
            meta[key] = val
    # Drop empty or None attributes to avoid confusing metadata.
    meta = {
        k: v
        for k, v in meta.items()
        if v is not None and (not isinstance(v, str) or v.strip() != "")
    }
    return {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": artifact_path if (artifact_path or "").startswith("so:") else tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(meta, ensure_ascii=False, indent=2),
        "ts": ts,
    }


def build_artifact_binary_block(
    *,
    turn_id: str,
    tool_call_id: str,
    artifact_path: str,
    abs_path: pathlib.Path,
    mime: str,
) -> Optional[Dict[str, Any]]:
    try:
        if not abs_path.exists() or not abs_path.is_file():
            return None
        data = abs_path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
    except Exception:
        return None
    return {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": mime,
        "path": artifact_path,
        "base64": b64,
        "meta": {
            "artifact_path": artifact_path,
            "tool_call_id": tool_call_id,
        },
    }


def build_tool_result_error_block(
    *,
    turn_id: str,
    tool_call_id: str,
    tool_id: str,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "error": {
            "code": code,
            "message": message,
            **({"details": details} if details else {}),
        },
        "ts": ts,
    }
    return {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "ts": ts,
    }


def materialize_inline_artifact_to_file(
    *,
    artifact: "ArtifactView",
    outdir: pathlib.Path,
    turn_id: Optional[str],
    filename_hint: Optional[str] = None,
    mime_hint: Optional[str] = None,
    visibility: Optional[str] = None,
    scratchpad=None,
) -> None:
    workdir = outdir
    if turn_id:
        workdir = workdir / turn_id / "files"
    updated, produced = artifact.save_inline(
        workdir=workdir,
        filename_hint=filename_hint,
        mime_hint=mime_hint,
        visibility=visibility,
    )
    if produced and scratchpad is not None:
        try:
            scratchpad.add_produced_file(produced)
        except Exception:
            pass


def surrogate_from_writer_inputs(tool_id: str, inputs: Dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(inputs, dict):
        return None, None
    mime_hint = (inputs.get("mime") or None)
    if tool_id == "infra.write":
        content = inputs.get("content")
        if isinstance(content, (bytes, bytearray)):
            cd = inputs.get("content_description")
            return (cd if isinstance(cd, str) and cd.strip() else None), (mime_hint or "application/octet-stream")
        if isinstance(content, str):
            return content, (mime_hint or "text/plain")
    for key in ("content", "markdown", "html", "text"):
        if isinstance(inputs.get(key), str) and inputs.get(key).strip():
            return inputs.get(key), (mime_hint or None)
    return None, mime_hint


def build_artifact_view(
    *,
    turn_id: str,
    is_current: bool,
    artifact_id: str,
    tool_id: str,
    value: Any,
    summary: str,
    sources_used: List[Any] | None = None,
    inputs: Dict[str, Any] | None = None,
    call_record_rel: str | None = None,
    call_record_abs: str | None = None,
    artifact_kind: Optional[str] = None,
    visibility: Optional[str] = None,
    description: Optional[str] = None,
    channel: Optional[str] = None,
    error: Optional[Dict[str, Any]] = None,
    content_lineage: List[str] | None = None,
    tool_call_id: str | None = None,
    artifact_stats: Optional[Dict[str, Any]] = None,
) -> "ArtifactView":
    value_norm = value
    if tools_insights.is_write_tool(tool_id):
        if isinstance(value, dict) and isinstance(value.get("path"), str) and value["path"].strip():
            file_path = value["path"].strip()
        elif isinstance(value, str) and value.strip():
            file_path = value.strip()
        else:
            file_path = ""
        surrogate_text, mime_hint = surrogate_from_writer_inputs(tool_id, inputs or {})
        value_norm = {
            "type": "file",
            "path": file_path,
            "text": (surrogate_text or ""),
            "mime": (mime_hint or tools_insights.default_mime_for_write_tool(tool_id)),
        }
        if isinstance(artifact_stats, dict) and artifact_stats:
            for k, v in artifact_stats.items():
                if k not in value_norm:
                    value_norm[k] = v
        if sources_used:
            value_norm["sources_used"] = extract_source_sids(sources_used)
        try:
            if file_path:
                from pathlib import Path
                value_norm["filename"] = Path(file_path).name
        except Exception:
            pass

    artifact = {
        "artifact_id": artifact_id,
        "tool_id": tool_id,
        "value": value_norm,
        "summary": str(summary or ""),
        "sources_used": extract_source_sids(sources_used) if sources_used else [],
        "timestamp": time.time(),
        "inputs": dict(inputs or {}),
        "call_record": {"rel": call_record_rel, "abs": call_record_abs},
        "artifact_kind": artifact_kind,
        "visibility": visibility,
        "description": description or "",
        "channel": channel or "",
        "tool_call_id": tool_call_id,
        "error": error,
        "content_lineage": content_lineage or [],
    }
    if isinstance(value_norm, dict):
        path_val = (value_norm.get("path") or "").strip()
        if path_val:
            artifact["path"] = path_val
    return ArtifactView.from_artifact_dict(
        artifact,
        turn_id=turn_id,
        is_current=is_current,
    )

def _artifact_fields_from_dict(
    artifact: Dict[str, Any],
    *,
    turn_id: Optional[str],
    is_current: bool,
) -> Dict[str, Any]:
    value = artifact.get("value") if isinstance(artifact.get("value"), dict) else {}
    path_val = (artifact.get("path") or value.get("path") or "").strip()
    filename = (value.get("filename") or artifact.get("filename") or "").strip()
    if not filename and path_val:
        filename = pathlib.Path(path_val).name
    mime = (value.get("mime") or artifact.get("mime") or "").strip()
    kind = (artifact.get("artifact_kind") or artifact.get("kind") or "").strip()
    visibility = (artifact.get("visibility") or "").strip()
    channel = (artifact.get("channel") or "").strip()
    summary = (artifact.get("summary") or "").strip()
    text = (
        (artifact.get("text") or "")
        if isinstance(artifact.get("text"), str)
        else (value.get("text") or value.get("content") or "")
    )
    sources_used = artifact.get("sources_used") or artifact.get("used_sids") or []
    tool_id = (artifact.get("tool_id") or "").strip()
    tool_call_id = (artifact.get("tool_call_id") or "").strip()
    size_bytes = value.get("size_bytes") if isinstance(value, dict) else None
    return {
        "path": path_val,
        "filename": filename,
        "mime": mime,
        "kind": kind,
        "visibility": visibility,
        "channel": channel,
        "summary": summary,
        "text": text if isinstance(text, str) else "",
        "sources_used": sources_used if isinstance(sources_used, list) else [],
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "size_bytes": size_bytes,
        "turn_id": turn_id or "",
        "is_current": is_current,
        "raw": artifact,
    }


@dataclass
class ArtifactView:
    path: str = ""
    filename: str = ""
    mime: str = ""
    kind: str = ""
    visibility: str = ""
    channel: str = ""
    summary: str = ""
    text: str = ""
    sources_used: List[Any] = field(default_factory=list)
    tool_id: str = ""
    tool_call_id: str = ""
    size_bytes: Optional[int] = None
    turn_id: str = ""
    is_current: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_output(cls, output: Dict[str, Any]) -> "ArtifactView":
        if not isinstance(output, dict):
            return cls()
        path_val = (output.get("path") or "").strip()
        filename = (output.get("filename") or "").strip()
        if not filename and path_val:
            filename = pathlib.Path(path_val).name
        mime = (output.get("mime") or "").strip()
        return cls(path=path_val, filename=filename, mime=mime)

    @classmethod
    def from_artifact_dict(
        cls,
        artifact: Dict[str, Any],
        *,
        turn_id: Optional[str] = None,
        is_current: bool = False,
    ) -> "ArtifactView":
        if not isinstance(artifact, dict):
            return cls(turn_id=turn_id or "", is_current=is_current)
        fields = _artifact_fields_from_dict(artifact, turn_id=turn_id, is_current=is_current)
        try:
            return cls(**fields)
        except TypeError:
            # Fallback if __init__ signature is not accepting kwargs in some runtime contexts.
            inst = cls()
            for key, val in fields.items():
                setattr(inst, key, val)
            return inst

    @staticmethod
    def extract_files_from_contrib_log(contrib_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Build assistant file records from contrib_log blocks.
        We only include external files (visibility=external, kind=file).
        """
        files: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for blk in contrib_log or []:
            if not isinstance(blk, dict):
                continue
            if (blk.get("type") or "") != "react.tool.result":
                continue
            if (blk.get("mime") or "").strip() != "application/json":
                continue
            text = blk.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                meta = json.loads(text)
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            if meta.get("error"):
                continue
            if (meta.get("visibility") or "").strip() != "external":
                continue
            if (meta.get("kind") or "").strip() != "file":
                continue
            if not (meta.get("hosted_uri") or meta.get("rn") or meta.get("key") or meta.get("local_path")):
                continue
            artifact_path = (meta.get("artifact_path") or "").strip()
            if not artifact_path or artifact_path in seen:
                continue
            seen.add(artifact_path)
            physical_path = (meta.get("physical_path") or "").strip()
            rec = {
                "artifact_path": artifact_path,
                "filename": physical_path.split("/")[-1] if physical_path else "",
                "mime": meta.get("mime") or "",
                "visibility": meta.get("visibility") or "external",
                "kind": meta.get("kind") or "file",
                "hosted_uri": meta.get("hosted_uri"),
                "rn": meta.get("rn"),
                "key": meta.get("key"),
                "path": physical_path,
                "tool_id": meta.get("tool_id") or "",
                "tool_call_id": meta.get("tool_call_id") or "",
            }
            files.append(rec)
        return files


def normalize_file_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure filename is basename only (no directories).
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    filename = out.get("filename")
    if isinstance(filename, str) and filename.strip():
        out["filename"] = filename.strip().split("/")[-1]
    return out

    def save_inline(
        self,
        *,
        workdir: pathlib.Path,
        filename_hint: Optional[str] = None,
        mime_hint: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        artifact = dict(self.raw or {})
        value = artifact.get("value")
        artifact_id = (artifact.get("artifact_id") or "").strip()

        # If already marked as file, ensure the file exists on disk.
        if isinstance(value, dict) and value.get("type") == "file":
            path = value.get("path")
            if isinstance(path, str) and path.strip():
                file_path = pathlib.Path(path)
                if not file_path.is_absolute():
                    file_path = workdir / file_path
                if not file_path.exists():
                    text = value.get("text")
                    if text is None:
                        text = value.get("content")
                    if text is None:
                        try:
                            text = json.dumps(value, ensure_ascii=False, indent=2)
                        except Exception:
                            text = ""
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(str(text), encoding="utf-8")
            return artifact, None

        if (artifact.get("artifact_kind") or "").strip() == "file":
            return artifact, None

        text = None
        fmt = None
        if isinstance(value, dict):
            fmt = value.get("format") if isinstance(value.get("format"), str) else None
            if isinstance(value.get("content"), str):
                text = value.get("content")
            elif isinstance(value.get("text"), str):
                text = value.get("text")
        if text is None and isinstance(value, str):
            text = value

        if text is None:
            try:
                text = json.dumps(value, ensure_ascii=False, indent=2)
                fmt = fmt or "json"
            except Exception:
                return artifact, None

        ext = "txt"
        if isinstance(fmt, str):
            f = fmt.strip().lower()
            if f in {"md", "markdown"}:
                ext = "md"
            elif f in {"json"}:
                ext = "json"
            elif f in {"html", "htm"}:
                ext = "html"
            elif f in {"yaml", "yml"}:
                ext = "yaml"

        files_dir = workdir
        files_dir.mkdir(parents=True, exist_ok=True)
        filename = str(filename_hint).strip() if isinstance(filename_hint, str) and filename_hint.strip() else ""
        if not filename:
            filename = (self.filename or "").strip()
        if not filename:
            filename = f"{artifact_id or 'artifact'}.{ext}"
        elif "." not in filename:
            filename = f"{filename}.{ext}"
        file_path = files_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")

        mime = str(mime_hint).strip() if isinstance(mime_hint, str) and mime_hint.strip() else ""
        if not mime:
            mime = (self.mime or "").strip()
        if not mime:
            try:
                from kdcube_ai_app.tools.content_type import get_mime_type_enhanced
                mime = get_mime_type_enhanced(filename)
            except Exception:
                mime = ""
        if not mime:
            if ext == "md":
                mime = "text/markdown"
            elif ext == "json":
                mime = "application/json"
            elif ext == "html":
                mime = "text/html"
            elif ext == "yaml":
                mime = "application/x-yaml"
            else:
                mime = "text/plain"

        if not (artifact.get("artifact_kind") or "").strip():
            artifact["artifact_kind"] = "file"
        final_visibility = visibility or (self.visibility or "").strip() or None
        if final_visibility:
            artifact["visibility"] = final_visibility
        artifact["value"] = {
            "type": "file",
            "path": filename,
            "text": text,
            "mime": mime,
            "filename": filename,
        }
        produced_file = {
            "filename": filename,
            "path": filename,
            "artifact_name": artifact_id,
            "mime": mime,
            "size": len(text.encode("utf-8")),
            "summary": artifact.get("summary") or "",
            "visibility": final_visibility or "internal",
            "kind": (artifact.get("artifact_kind") or "file"),
        }
        return artifact, produced_file

    def physical_path(self, *, run_outdir: pathlib.Path) -> pathlib.Path:
        """
        Engineering helper: resolve absolute path for execution / IO.
        Do NOT use for journal/presentation (those use OUT_DIR-relative paths).
        """
        if self.path:
            return pathlib.Path(run_outdir) / self.path
        return pathlib.Path("")

    def artifact_path(self) -> str:
        if self.turn_id and self.path:
            return f"fi:{self.turn_id}.files/{self.path}"
        return ""

    def to_historical_format(self, *, max_tokens: int = 200) -> List[str]:
        lines: List[str] = []
        name = self.path or self.filename or "artifact"
        lines.append(f"- {name}")
        art_path = self.artifact_path() or (f"fi:{self.turn_id}.files" if self.turn_id else "")
        if art_path:
            lines.append(f"    artifact_path: {art_path}")
        phys = f"{self.turn_id}/files/{self.path}" if self.turn_id and self.path else ""
        if phys:
            lines.append(f"    physical_path: {phys}")
        meta = []
        if self.tool_id:
            meta.append(f"tool={self.tool_id}")
        if self.tool_call_id:
            meta.append(f"tool_call_id={self.tool_call_id}")
        if self.kind:
            meta.append(f"kind={self.kind}")
        if self.visibility:
            meta.append(f"visibility={self.visibility}")
        if self.channel:
            meta.append(f"channel={self.channel}")
        if self.mime:
            meta.append(f"mime={self.mime}")
        if self.size_bytes is not None:
            meta.append(f"size={self.size_bytes}B")
        if meta:
            lines.append("    meta: " + "; ".join(meta))
        used_sids: List[str] = []
        for s in self.sources_used or []:
            if isinstance(s, (int, float)):
                used_sids.append(f"S{int(s)}")
            elif isinstance(s, dict) and isinstance(s.get("sid"), (int, float)):
                used_sids.append(f"S{int(s.get('sid'))}")
        if used_sids:
            lines.append("    sources_used: " + ", ".join(used_sids))

        content = (self.text or "").strip()
        if content:
            if token_count(content) <= max_tokens:
                lines.append("    content:")
                lines.append("    ```text")
                lines.append(content)
                lines.append("    ```")
            elif self.summary:
                lines.append(f"    summary: {self.summary}")
            else:
                lines.append("    content:")
                lines.append("    ```text")
                lines.append(_truncate(content, 1000))
                lines.append("    ```")
        elif self.summary:
            lines.append(f"    summary: {self.summary}")
        return lines

    def to_current_format(self, *, max_tokens: int = 200, fallback_name: Optional[str] = None) -> List[str]:
        lines: List[str] = []
        name = self.path or self.filename or (fallback_name or "artifact")
        lines.append(f"- {name}")
        art_path = self.artifact_path() or (f"fi:{self.turn_id}.files" if self.turn_id else "")
        if art_path:
            lines.append(f"    artifact_path: {art_path}")
        if self.turn_id and self.path:
            lines.append(f"    physical_path: {self.turn_id}/files/{self.path}")
        meta = []
        if self.tool_id:
            meta.append(f"tool={self.tool_id}")
        if self.tool_call_id:
            meta.append(f"tool_call_id={self.tool_call_id}")
        if self.kind:
            meta.append(f"kind={self.kind}")
        if self.visibility:
            meta.append(f"visibility={self.visibility}")
        if self.channel:
            meta.append(f"channel={self.channel}")
        if self.mime:
            meta.append(f"mime={self.mime}")
        if self.size_bytes is not None:
            meta.append(f"size={self.size_bytes}B")
        if meta:
            lines.append("    meta: " + "; ".join(meta))
        used_sids: List[str] = []
        for s in self.sources_used or []:
            if isinstance(s, (int, float)):
                used_sids.append(f"S{int(s)}")
            elif isinstance(s, dict) and isinstance(s.get("sid"), (int, float)):
                used_sids.append(f"S{int(s.get('sid'))}")
        if used_sids:
            lines.append("    sources_used: " + ", ".join(used_sids))

        content = (self.text or "").strip()
        if content:
            if token_count(content) <= max_tokens:
                lines.append("    content:")
                lines.append("    ```text")
                lines.append(content)
                lines.append("    ```")
            elif self.summary:
                lines.append(f"    summary: {self.summary}")
            else:
                lines.append("    content:")
                lines.append("    ```text")
                lines.append(_truncate(content, 1000))
                lines.append("    ```")
        elif self.summary:
            lines.append(f"    summary: {self.summary}")
        return lines
