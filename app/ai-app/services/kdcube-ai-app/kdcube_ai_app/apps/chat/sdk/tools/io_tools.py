# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/io_tools.py

import os, json, pathlib, re, mimetypes, inspect
from typing import Annotated, Optional, Any, Dict, List, Tuple
import base64

import semantic_kernel as sk

from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_from_text, dedupe_sources_by_url, \
    CITATION_OPTIONAL_ATTRS, normalize_sources_any
from kdcube_ai_app.apps.chat.sdk.tools.tools_insights import CITABLE_TOOL_IDS
from kdcube_ai_app.apps.chat.sdk.util import strip_lone_surrogates

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

# We’ll use this only in limited executor mode
try:
    from kdcube_ai_app.apps.chat.sdk.runtime.isolated.secure_client import ToolStub
except Exception:  # non-supervised environments, tests, etc.
    ToolStub = None

from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id

# ---------- basics ----------

_INDEX_FILE = "tool_calls_index.json"

_TOOL_SUBSYSTEM = None


def bind_integrations(integrations: dict) -> None:
    global _TOOL_SUBSYSTEM
    if isinstance(integrations, dict) and integrations.get("tool_subsystem"):
        _TOOL_SUBSYSTEM = integrations.get("tool_subsystem")

def _is_limited_executor() -> bool:
    """
    Return True when running in the 'limited' child that must NOT call tools directly,
    but route all tool executions through the privileged supervisor.

    Convention:
      - AGENT_IO_CONTEXT=limited → limited child
      - anything else / unset → normal (local / supervisor / old behavior)

    This keeps non-docker and legacy paths working unchanged.
    """
    ctx = (os.environ.get("AGENT_IO_CONTEXT") or "").strip().lower()
    return ctx == "limited"

def _outdir() -> pathlib.Path:
    return resolve_output_dir()

def _sanitize_tool_id(tid: str) -> str:
    # "generic_tools.web_search" -> "generic_tools_web_search"
    return re.sub(r"[^a-zA-Z0-9]+", "_", tid).strip("_")

def _guess_mime(path: str, default: str = "application/octet-stream") -> str:
    mt, _ = mimetypes.guess_type(path)
    return mt or default

def _serialize_params_for_json(params: dict) -> str:
    """
    Serialize params to JSON, base64-encoding any bytes values.
    This is for persisting tool calls, not for execution.
    """
    def encode_value(v):
        if isinstance(v, bytes):
            return {
                "__type__": "bytes",
                "__data__": base64.b64encode(v).decode("ascii"),
                "__length__": len(v)
            }
        elif isinstance(v, dict):
            return {k: encode_value(val) for k, val in v.items()}
        elif isinstance(v, list):
            return [encode_value(item) for item in v]
        else:
            return v

    encoded = {k: encode_value(v) for k, v in params.items()}
    return json.dumps(encoded, ensure_ascii=False, default=str)

def _normalize_workdir_paths(data: Any,
                             workspace_outdir: str = "/workspace/out") -> Any:
    """
    Recursively scan data for container paths and convert to relative paths.

    Converts:
      - "/workspace/out/file.xlsx" → "file.xlsx"
      - "/workspace/out/subdir/file.txt" → "subdir/file.txt"

    Works on strings, dicts, lists, and nested structures.
    """
    if isinstance(data, str):
        # Check if it's a container path
        if data.startswith(workspace_outdir + "/"):
            # Strip the container prefix, keep relative path
            return data[len(workspace_outdir) + 1:]
        elif data == workspace_outdir:
            # Edge case: path is exactly the outdir
            return "."
        return data

    elif isinstance(data, dict):
        return {k: _normalize_workdir_paths(v, workspace_outdir) for k, v in data.items()}

    elif isinstance(data, list):
        return [_normalize_workdir_paths(item, workspace_outdir) for item in data]

    elif isinstance(data, tuple):
        return tuple(_normalize_workdir_paths(item, workspace_outdir) for item in data)

    else:
        # Primitives (int, float, bool, None, etc.) pass through unchanged
        return data

def _extract_candidate_sources_from_tool_output(tool_id: str, tool_output: Any) -> List[Dict[str, Any]]:
    """
    Extract raw source rows from a tool's return, for citable tools only.
    Tries common shapes without guessing content text; URLs are required.
    Returns a list of loose rows to be normalized later.
    """
    rows: List[Dict[str, Any]] = []
    if not _is_citable_tool(tool_id):
        return rows

    return normalize_sources_any(tool_output)

def _sources_pool_from_citable_tools_generators(promoted: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """
    Build the sources_pool list (with stable sids) **only** from citable tools' outputs
    present in `promoted`. Returns (sources_pool, sources_by_sid_map).
    """
    collected: List[Dict[str, Any]] = []
    for it in promoted or []:
        tool_id = (it.get("tool_id") or "").strip()
        if not _is_citable_tool(tool_id):
            continue
        tool_output = it.get("output")
        # promoted stores raw decoded output in 'output'
        collected.extend(_extract_candidate_sources_from_tool_output(tool_id, tool_output))

    # Deduplicate by URL and assign/keep SIDs
    unified = dedupe_sources_by_url([], collected)
    sources_pool: List[Dict[str, Any]] = [
        {k: v for k, v in row.items() if k in ("sid", "url", "title", "text") or k in CITATION_OPTIONAL_ATTRS}
        for row in unified if isinstance(row.get("sid"), int) and row.get("url")
    ]
    sources_by_sid = { int(r["sid"]): r for r in sources_pool }
    return sources_pool, sources_by_sid

def _enrich_sources_pool_with_deliverables(
        initial_pool: List[Dict[str, Any]],
        initial_by_sid: Dict[int, Dict[str, Any]],
        out_dyn: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """
    Enrich sources_pool with sources from out_dyn deliverables.
    This handles cases where artifacts fetched from context bring their own sources.

    Returns (enriched_sources_pool, enriched_sources_by_sid).
    """
    if not isinstance(out_dyn, dict):
        return initial_pool, initial_by_sid

    # Collect sources from deliverables
    deliverable_sources: List[Dict[str, Any]] = []
    for slot, val in out_dyn.items():
        if isinstance(val, dict):
            sources = val.get("sources_used")
            if sources:
                if isinstance(sources, list) and all(isinstance(x, (int, float)) for x in sources):
                    for sid in sources:
                        try:
                            sid_int = int(sid)
                        except Exception:
                            continue
                        if sid_int in initial_by_sid:
                            deliverable_sources.append(initial_by_sid[sid_int])
                else:
                    deliverable_sources.extend(normalize_sources_any(sources))

    if not deliverable_sources:
        return initial_pool, initial_by_sid

    # Merge with existing canonical sources using dedupe logic
    enriched = dedupe_sources_by_url(initial_pool, deliverable_sources)

    # Rebuild canonical list and map
    enriched_pool: List[Dict[str, Any]] = [
        {k: v for k, v in row.items() if k in ("sid", "url", "title", "text") or k in CITATION_OPTIONAL_ATTRS}
        for row in enriched if isinstance(row.get("sid"), int) and row.get("url")
    ]
    enriched_by_sid = {int(r["sid"]): r for r in enriched_pool}

    return enriched_pool, enriched_by_sid

# ---------- formats & normalization ----------

def _detect_format_from_value(val: Any, fallback: str = "plain_text") -> str:
    if isinstance(val, (dict, list)):
        return "json"
    if isinstance(val, str):
        # very light heuristic: treat fenced/headers as markdown
        if "\n#" in val or val.strip().startswith("#") or "```" in val:
            return "markdown"
        return "plain_text"
    return fallback

def _coerce_value_and_format(val: Any, fmt: Optional[str]) -> Tuple[Any, Optional[str]]:
    """
    If format is provided (e.g. 'json') and val is a stringified JSON, parse to typed object.
    Else, keep as-is. Return (value, format or None).
    """
    if not fmt:
        return val, None
    f = fmt.strip().lower()
    if f == "json" and isinstance(val, str):
        s = val.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                return json.loads(s), "json"
            except Exception:
                return val, "json"
        return val, "json"
    return val, f

def _stringify_for_format(v: Any, fmt: Optional[str]) -> str:
    """
    Return a string representation of `v` suitable for the given `fmt`.
    - json  → pretty JSON (UTF-8, unicode kept)
    - yaml  → YAML (falls back to JSON if pyyaml missing or dump fails)
    - markdown/html/text/plain → str(v)
    - default: dict/list → pretty JSON, else str(v)
    """
    # bytes → best-effort decode
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return v.decode("latin-1", errors="replace")

    # strings stay as-is
    if isinstance(v, str):
        return v

    f = (fmt or "").strip().lower()

    if f == "json":
        try:
            return json.dumps(v, ensure_ascii=False, indent=2)
        except Exception:
            # last-ditch
            return str(v)

    if f == "yaml":
        try:
            import yaml as _yaml  # local import to avoid hard dep at module import
            return _yaml.safe_dump(v, allow_unicode=True, sort_keys=False)
        except Exception:
            # fall back to JSON pretty
            try:
                return json.dumps(v, ensure_ascii=False, indent=2)
            except Exception:
                return str(v)

    if f in ("markdown", "html", "text", "plain_text", "plaintext"):
        return str(v)

    # default fallback: objects/arrays → pretty JSON, otherwise str()
    try:
        if isinstance(v, (dict, list, tuple)):
            return json.dumps(v, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return str(v)


def normalize_contract_deliverables(out_dyn: Dict[str, Any],
                                    canonical_by_sid: Optional[Dict[int, Dict[str, Any]]] = None,
                                    artifact_lvl: Optional[str] = "slot") -> List[Dict[str, Any]]:
    """
    Canonicalize dynamic contract dict {slot: VALUE} → list of artifacts for result['out'].

    TARGET FIELDS PER ARTIFACT (for slots):
      - resource_id: "slot:<slot>"
      - type: "inline" | "file"
      - tool_id: "program"
      - output: <inline string/object> | <relative file path>
      - format: optional (markdown|json|plain_text|url|yaml|xml|object)
      - mime: for files only
      - text: for files, the textual surrogate
      - citable: bool (inline URLs default to True)
      - description: str
      - draft: bool (optional; True = incomplete/partial deliverable)
      - gaps: str (optional; missing parts / TODOs)
      - summary: str (optional; short slot summary)
      - input: {}   # reserved, empty for program slots
      - sources_used: list of int SIDs (optional)
    """
    artifacts: List[Dict[str, Any]] = []

    def _norm_opt_str(x: Any, *, limit: int = 2000) -> Optional[str]:
        """
        Best-effort stringify for optional human text fields (gaps/summary).
        Backward compatible: returns None if empty/None/unusable.
        """
        if x is None:
            return None

        if isinstance(x, str):
            s = x.strip()
            return s or None
            # return (s[:limit] + "...") if len(s) > limit else s

        if isinstance(x, (dict, list, tuple)):
            try:
                s = json.dumps(x, ensure_ascii=False)
                s = s.strip()
                # return (s[:limit] + "...") if len(s) > limit else s
                return s or None
            except Exception:
                s = str(x).strip()
                return s or None
                # return (s[:limit] + "...") if len(s) > limit else s

        s = str(x).strip()
        return s or None
        # if not s:
        #     return None
        # return (s[:limit] + "...") if len(s) > limit else s

    def _unify_sources(parsed_sids: List[int], explicit_sources: Any) -> List[int]:
        """
        Merge SIDs parsed from text with SIDs present in explicit sources_used.
        Only SIDs are stored on artifacts; sources are materialized later.
        """
        explicit_sids: List[int] = []
        if isinstance(explicit_sources, list):
            for item in explicit_sources:
                if isinstance(item, (int, float)):
                    explicit_sids.append(int(item))
                elif isinstance(item, dict):
                    sid = item.get("sid")
                    if isinstance(sid, (int, float)):
                        explicit_sids.append(int(sid))

        unified = set(parsed_sids or [])
        unified.update(explicit_sids)

        return [s for s in sorted([k for k in unified if isinstance(k, int)])]

    def push_inline(
        slot: str,
        value: Any,
        *,
        fmt: Optional[str],
        desc: str,
        citable: bool,
        sources_used: Any = None,
        draft: bool = False,
        gaps: Any = None,
        summary: Any = None,
        artifact_lvl: Optional[str] = "slot",
    ):
        v, use_fmt = _coerce_value_and_format(value, fmt)
        if not use_fmt:
            use_fmt = _detect_format_from_value(v)

        text_str = _stringify_for_format(v, use_fmt)
        parsed_sids = extract_citation_sids_from_text(text_str)
        final_sids = _unify_sources(parsed_sids, sources_used)
        if canonical_by_sid and final_sids:
            for sid in final_sids:
                if sid in canonical_by_sid:
                    canonical_by_sid[sid]["used"] = True

        row = {
            "resource_id": f"{artifact_lvl}:{slot}",
            "type": "inline",
            "tool_id": "program",
            "output": {"text": text_str},
            "citable": bool(citable),
            "description": desc or "",
            "input": {},
        }

        if draft:
            row["draft"] = True

        gaps_s = _norm_opt_str(gaps, limit=1200)
        if gaps_s:
            row["gaps"] = gaps_s

        summary_s = _norm_opt_str(summary, limit=2000)
        if summary_s:
            row["summary"] = summary_s

        if use_fmt:
            row["format"] = use_fmt
        if final_sids:
            row["sources_used"] = final_sids

        artifacts.append(row)

    def push_file(
        slot: str,
        relpath: str,
        *,
        mime: Optional[str],
        desc: str,
        text: str,
        hosted_uri: Optional[str] = None,
        key: Optional[str] = None,
        rn: Optional[str] = None,
        filename: Optional[str] = None,
        citable: bool = False,
        sources_used: Any = None,
        draft: bool = False,
        gaps: Any = None,
        summary: Any = None,
        artifact_lvl: Optional[str] = "slot",
    ):
        invalid_path = False
        invalid_path_type = None
        if not isinstance(relpath, str):
            invalid_path = True
            invalid_path_type = type(relpath).__name__
            relpath = ""
        relpath = (relpath or "").strip()

        if text is None:
            text_str = ""
        elif isinstance(text, str):
            text_str = text
        else:
            try:
                if isinstance(text, (dict, list, tuple)):
                    text_str = json.dumps(text, ensure_ascii=False)
                else:
                    text_str = str(text)
            except Exception:
                text_str = str(text)

        parsed_sids = extract_citation_sids_from_text(text_str)
        final_sids = _unify_sources(parsed_sids, sources_used)
        if canonical_by_sid and final_sids:
            for sid in final_sids:
                if sid in canonical_by_sid:
                    canonical_by_sid[sid]["used"] = True

        row = {
            "resource_id": f"{artifact_lvl}:{slot}",
            "type": "file",
            "tool_id": "program",
            "output": {"path": relpath, "text": text_str},
            "mime": (mime or _guess_mime(relpath)),
            "citable": False,
            "description": desc or "",
            "input": {},
        }
        if hosted_uri:
            row["hosted_uri"] = hosted_uri
        if key:
            row["key"] = key
        if rn:
            row["rn"] = rn
        if filename:
            row["filename"] = filename

        if draft or invalid_path:
            row["draft"] = True

        if invalid_path:
            gaps_msg = f"Invalid file path type: {invalid_path_type or 'unknown'}"
            gaps = (str(gaps).strip() + "; " + gaps_msg).strip("; ") if gaps else gaps_msg

        gaps_s = _norm_opt_str(gaps, limit=1200)
        if gaps_s:
            row["gaps"] = gaps_s

        summary_s = _norm_opt_str(summary, limit=2000)
        if summary_s:
            row["summary"] = summary_s

        if final_sids:
            row["sources_used"] = final_sids

        artifacts.append(row)

    for slot, val in (out_dyn or {}).items():
        if not isinstance(val, dict):
            continue

        slot_type = val.get("type")
        desc = val.get("description") or val.get("desc") or ""
        citable = bool(val.get("citable", False))
        fmt = val.get("format")
        draft = bool(val.get("draft", False))
        sources_used = val.get("sources_used")

        gaps = val.get("gaps")
        summary = val.get("summary")

        if slot_type == "file":
            mime = val.get("mime") or None
            text_surrogate = val.get("text") or ""
            filepath = val.get("path") or ""
            hosted_uri = val.get("hosted_uri")
            hosted_key = val.get("key")
            hosted_rn = val.get("rn")
            filename = val.get("filename")
            push_file(
                slot,
                filepath,
                mime=mime,
                desc=desc,
                text=text_surrogate,
                hosted_uri=hosted_uri,
                key=hosted_key,
                rn=hosted_rn,
                filename=filename,
                sources_used=sources_used,
                draft=draft,
                gaps=gaps,
                summary=summary,
                artifact_lvl=artifact_lvl
            )
            continue

        if slot_type == "inline":
            if ("value" in val) or ("text" in val):
                vv = val.get("value", val.get("text"))
                push_inline(
                    slot,
                    vv,
                    fmt=fmt,
                    desc=desc,
                    citable=citable,
                    sources_used=sources_used,
                    draft=draft,
                    gaps=gaps,
                    summary=summary,
                    artifact_lvl=artifact_lvl
                )
            continue

    return artifacts


# ---------- promotion of saved tool-calls ----------

def _infer_format_for_tool_output(tool_id: str, out: Any) -> Optional[str]:
    if isinstance(out, (dict, list)):
        return "json"
    if isinstance(out, str) and tool_id.endswith("summarize_llm"):
        return "markdown"
    return _detect_format_from_value(out, fallback=None)

def _is_citable_tool(tool_id: str) -> bool:
    tid = (tool_id or "").lower()
    # allow kb_search variants too
    return tid in CITABLE_TOOL_IDS or tid.endswith(".kb_search")

def _promote_tool_calls(raw_files: Dict[str, List[str]], outdir: pathlib.Path) -> List[Dict[str, Any]]:
    """
    Promote each saved tool-call JSON as ONE artifact:
      - resource_id: "tool:<tool_id>:<index>"
      - path: <relative filename of the saved call JSON>
      - input: params (decoded)
      - output: ret (decoded, object or string)
      - format: inferred from output
      - type: 'inline' for citable web/browse (keeps path), else 'file'
      - citable: True for web/browsing only
      - mime: only for 'file' type (application/json)
      - description: passthrough if present
    """
    promos: List[Dict[str, Any]] = []
    for tool_id, rels in (raw_files or {}).items():
        for idx, rel in enumerate(rels or []):
            p = (outdir / rel)
            desc = ""
            tool_input: Dict[str, Any] = {}
            tool_output: Any = None
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
                desc = payload.get("description") or ""
                tool_input = (payload.get("in") or {}).get("params", {}) or {}
                tool_output = payload.get("ret")
            except Exception:
                tool_output = None

            citable = _is_citable_tool(tool_id)
            fmt = _infer_format_for_tool_output(tool_id, tool_output)

            base = {
                "resource_id": f"tool:{tool_id}:{idx}",
                "tool_id": tool_id,
                "path": rel,
                "citable": citable,
                "description": desc,
                "input": tool_input,
                "output": tool_output
            }
            if fmt:
                base["format"] = fmt
            if citable:
                base["type"] = "inline"
            else:
                base["type"] = "file"
                base["mime"] = "application/json"

            # carry sources_used from llm tool outputs (SIDs only)
            try:
                if isinstance(tool_output, dict):
                    if tool_id.endswith("generate_content_llm"):
                        us = tool_output.get("sources_used")
                        sids: List[int] = []
                        if isinstance(us, list):
                            for item in us:
                                if isinstance(item, (int, float)):
                                    sids.append(int(item))
                                elif isinstance(item, dict):
                                    sid = item.get("sid")
                                    if isinstance(sid, (int, float)):
                                        sids.append(int(sid))
                        # Also add SIDs inferred from content (if any)
                        content = tool_output.get("content")
                        if isinstance(content, str):
                            sids.extend(extract_citation_sids_from_text(content))
                        if sids:
                            base["sources_used"] = sorted({int(s) for s in sids})
            except Exception:
                pass

            promos.append(base)
    return promos


# ---------- index helpers (for wrapper) ----------

def _read_index(od: pathlib.Path) -> Dict[str, List[str]]:
    p = od / _INDEX_FILE
    if not p.exists():
        return {}
    try:
        m = json.loads(p.read_text(encoding="utf-8")) or {}
        # normalize shape
        return {k: list(v or []) for k, v in m.items() if isinstance(v, list)}
    except Exception:
        return {}

def _write_index(od: pathlib.Path, m: Dict[str, List[str]]) -> None:
    (od / _INDEX_FILE).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

def _next_index(m: Dict[str, List[str]], tool_id: str) -> int:
    return len(m.get(tool_id, []))


# ---------- AgentIO tools ----------

class AgentIO:
    """
    Writer helpers and infra tools:
      - tool_call(fn, params_json, call_reason, tool_id, ...) → execute + persist one tool call
      - save_ret(data) → writes result.json with normalized out & auto-promoted tool-call artifacts
      - save_tool_call(...) → internal helper (not exposed to LLM) that writes a single call JSON
    """

    # the wrapper the codegen will use
    @kernel_function(
        name="tool_call",
        description=(
                "Execute a tool function and persist the call payload to OUTPUT_DIR as JSON "
                "(indexing the filename per tool). Returns the tool's raw result. "
                "Mandatory to use from generated code (and ONLY from generated code) to invoke any catalog tool. Never should be called as a tool from tool-calling agents."
        )
    )
    async def tool_call(
            self,
            fn: Annotated[Any, "Callable tool to invoke (e.g., generic_tools.web_search)."],
            params: Annotated[str | dict, "Tool parameters. Pass as JSON string or dict. Dict form supports bytes for binary content."] = "{}",
            call_reason: Annotated[Optional[str], "Short human reason for the call (5–12 words)."] = None,
            tool_id: Annotated[Optional[str], "Qualified id like 'generic_tools.web_search'. If omitted, derived from fn.__module__+'.'+fn.__name__."] = None,
            filename: Annotated[Optional[str], "Override filename for the saved JSON (relative in OUTPUT_DIR)."] = None,
    ) -> Annotated[Any, "Raw return from the tool"]:
        # ---- Parse params: accept str (JSON) or dict ----
        if isinstance(params, dict):
            final_params = params  # Use directly (may contain bytes)
        elif isinstance(params, str):
            try:
                final_params = json.loads(params)
            except Exception:
                final_params = {"_raw": params}
        else:
            final_params = {}

        # ---- Compute tool_id (tid) exactly once ----------------------------
        if tool_id:
            tid = tool_id.strip()
        else:
            if fn is None:
                raise ValueError("tool_call: tool_id is required when fn is None")
            mod = (getattr(fn, "__module__", "tool") or "tool").split(".")[-1]
            name = getattr(fn, "__name__", "call") or "call"
            tid = f"{mod}.{name}".strip()

        # ---- Index + filename (same logic as before) -----------------------
        od   = _outdir()
        idx  = _read_index(od)
        i    = _next_index(idx, tid)
        rel  = filename or f"{_sanitize_tool_id(tid)}-{i}.json"

        # ---- MODE 1: limited executor → delegate to supervisor via socket --
        if _is_limited_executor() and ToolStub is not None:
            socket_path = os.environ.get("SUPERVISOR_SOCKET_PATH", "/tmp/supervisor.sock")
            stub = ToolStub(socket_path=socket_path)

            try:
                stub_res = await stub.call_tool(tool_id=tid, params=final_params, reason=str(call_reason or ""))

                if not isinstance(stub_res, dict):
                    raise RuntimeError(f"Bad supervisor response type: {type(stub_res)!r}")

                if stub_res.get("ok"):
                    ret = stub_res.get("result")

                    # Persist SUCCESS via save_tool_call
                    await self.save_tool_call(
                        tool_id=tid,
                        description=str(call_reason or ""),
                        data=ret,
                        params=final_params,
                        index=i,
                        filename=rel,
                    )
                    idx.setdefault(tid, []).append(rel)
                    _write_index(od, idx)
                    return ret

                # Supervisor reported a logical error (but the call *did* happen)
                msg = stub_res.get("error") or "Supervisor returned an error"
                err_env = {
                    "ok": False,
                    "error": {
                        "code": "supervisor_error",
                        "message": str(msg),
                        "where": tid,
                        "managed": True,
                    }
                }
                await self.save_tool_call(
                    tool_id=tid,
                    description=str(call_reason or ""),
                    data=err_env,
                    params=final_params,
                    index=i,
                    filename=rel,
                )
                idx.setdefault(tid, []).append(rel)
                _write_index(od, idx)
                # Preserve old semantics: surface failure as exception
                raise RuntimeError(str(msg))

            except Exception as e:
                # Transport / protocol / other failure in the limited child
                err_env = {
                    "ok": False,
                    "error": {
                        "code": type(e).__name__,
                        "message": str(e),
                        "where": tid,
                        "managed": False,
                    }
                }
                try:
                    await self.save_tool_call(
                        tool_id=tid,
                        description=str(call_reason or ""),
                        data=err_env,
                        params=final_params,
                        index=i,
                        filename=rel,
                    )
                    idx.setdefault(tid, []).append(rel)
                    _write_index(od, idx)
                finally:
                    # Preserve external behavior: re-raise
                    raise

        # ---- MODE 2: normal / supervisor → execute tool locally (old path) -
        try:
            # Execute tool *locally* (supervisor / legacy behavior)
            if fn is None:
                origin, provider, name = parse_tool_id(tid)
                if origin == "mcp" and provider and name:
                    if not _TOOL_SUBSYSTEM:
                        raise ValueError("tool_call: tool_subsystem not bound")
                    mcp = _TOOL_SUBSYSTEM.get_mcp_subsystem()
                    if not mcp:
                        raise ValueError("tool_call: MCP subsystem not configured")
                    ret = await mcp.execute_tool(
                        alias=provider,
                        tool_name=name,
                        params=final_params,
                        trace_id=str(call_reason or ""),
                    )
                else:
                    raise ValueError("tool_call: fn cannot be None in local/supervisor mode")
            else:
                ret = fn(**final_params) if final_params else fn()
                if inspect.isawaitable(ret):
                    ret = await ret

            # Persist SUCCESS via save_tool_call (strict shape; no top-level extras)
            await self.save_tool_call(
                tool_id=tid,
                description=str(call_reason or ""),
                data=ret,  # raw
                params=final_params,
                index=i,
                filename=rel,
            )

            # Update index and return raw result unchanged
            idx.setdefault(tid, []).append(rel)
            _write_index(od, idx)
            return ret

        except Exception as e:
            # Managed error envelope INSIDE ret; then persist via save_tool_call
            err_env = {
                "ok": False,
                "error": {
                    "code": type(e).__name__,
                    "message": str(e),
                    "where": tid,
                    "managed": False,
                }
            }
            try:
                await self.save_tool_call(
                    tool_id=tid,
                    description=str(call_reason or ""),
                    data=err_env,  # stored in ret
                    params=final_params,
                    index=i,
                    filename=rel,
                )
                idx.setdefault(tid, []).append(rel)
                _write_index(od, idx)
            finally:
                # Re-raise to keep external behavior identical
                raise

    # INTERNAL helper (kept public on the instance, but not advertised to LLM)
    async def save_tool_call(
            self,
            tool_id: str,
            description: Optional[str],
            data: Any,
            params: str | dict = "{}",
            index: int = 0,
            filename: Optional[str] = None,
    ) -> str:
        od = _outdir()
        rel = filename or f"{_sanitize_tool_id(tool_id)}-{index}.json"
        path = od / rel

        # Decode params if string, or use directly if dict
        if isinstance(params, dict):
            p = params  # For payload structure
            params_json_str = _serialize_params_for_json(params)  # Safe JSON with base64 for bytes
        elif isinstance(params, str):
            try:
                p = json.loads(params)
                params_json_str = params
            except Exception:
                p = {"_raw": params}
                params_json_str = json.dumps(p)
        else:
            p = {}
            params_json_str = "{}"

        # Parse params_json_str to get the dict for normalization
        try:
            p = json.loads(params_json_str)
        except Exception:
            p = {"_raw": params_json_str}

        # keep RAW unless it's JSON-looking string
        ret: Any = data
        if isinstance(data, str):
            s = data.strip()
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                try:
                    ret = json.loads(s)
                except Exception:
                    ret = s
        # Normalize container paths to relative paths
        # This makes persisted JSON portable for the host
        ret = _normalize_workdir_paths(ret, workspace_outdir=str(resolve_output_dir()))

        payload = {"description": description or "", "in": {"tool_id": tool_id, "params": p}, "ret": ret}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except UnicodeEncodeError:
            safe_json = strip_lone_surrogates(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
            path.write_text(safe_json, encoding="utf-8")
        return rel

    # @kernel_function(
    #     name="save_ret",
    #     description=(
    #             "Write the program's result to OUTPUT_DIR (default 'result.json').\n"
    #             "RESULT SHAPE (authoritative):\n"
    #             "  - ok: bool (required)\n"
    #             "  - objective: str (recommended)\n"
    #             "  - contract: dict(slot-> {description, type})\n"
    #             "  - out_dyn:  dict(slot->VALUE)\n"
    #             "  - error: dict with keys error, where, details, managed: optional keys for failures\n"
    #     )
    # )
    async def save_ret(
            self,
            data: Annotated[str, "JSON-encoded object to write."],
            filename: Annotated[str, "Relative filename (defaults to 'result.json')."] = "result.json",
            artifact_lvl: Annotated[Optional[str], "Level prefix for resource_id in artifacts (e.g., 'slot' or 'artifact')."] = "slot",
    ) -> Annotated[str, "Saved relative filename"]:
        od = _outdir()
        rel = filename or "result.json"
        path = od / rel

        obj = json.loads(data) if isinstance(data, str) else data

        # 1) Merge tool-call index FIRST so we see all persisted calls
        raw_files = obj.get("raw_files") or {}
        idx_map = _read_index(od)
        if idx_map:
            merged: Dict[str, List[str]] = {k: list(v) for k, v in (raw_files or {}).items()}
            for k, arr in idx_map.items():
                merged.setdefault(k, [])
                for relpath in arr:
                    if relpath not in merged[k]:
                        merged[k].append(relpath)
            raw_files = merged
            obj["raw_files"] = merged  # keep for traceability

        # 2) Promote saved tool calls
        promoted = _promote_tool_calls(raw_files, od)

        # 3) Build canonical source space from citable tools' outputs
        sources_pool, canonical_by_sid = _sources_pool_from_citable_tools_generators(promoted)

        # 3b) Enrich with sources from deliverables (e.g., fetched from context)
        out_dyn = obj.get("out_dyn") or {}
        sources_pool, canonical_by_sid = _enrich_sources_pool_with_deliverables(
            sources_pool, canonical_by_sid, out_dyn
        )
        if sources_pool:
            obj["sources_pool"] = sources_pool

        # 4) Normalize dynamic contract deliverables with access to canonical_by_sid
        out_dyn = obj.get("out_dyn") or {}
        normalized_out = normalize_contract_deliverables(
            out_dyn,
            canonical_by_sid=canonical_by_sid,
            artifact_lvl=artifact_lvl
        ) if isinstance(out_dyn, dict) else []

        # 5) Merge normalized slots with promoted artifacts (de-dup)
        def _key(a: Dict[str, Any]):
            rid = a.get("resource_id")
            if rid:
                return ("rid", rid)
            return ("fallback", a.get("type"), (a.get("output") or {}).get("text") or a.get("path"))

        seen = set()
        merged_out: List[Dict[str, Any]] = []
        for row in normalized_out + promoted:
            k = _key(row)
            if k in seen:
                continue
            seen.add(k)
            merged_out.append(row)

        obj["out"] = merged_out
        if out_dyn:
            obj["_out_dyn_raw"] = out_dyn

        try:
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except UnicodeEncodeError:
            safe_json = strip_lone_surrogates(
                json.dumps(obj, ensure_ascii=False, indent=2)
            )
            path.write_text(safe_json, encoding="utf-8")
        return rel


# module-level exports
kernel = sk.Kernel()
tools = AgentIO()
kernel.add_plugin(tools, "agent_io_tools")
