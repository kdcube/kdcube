# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/tools/ctx_tool.py
import json, re, pathlib
import os
from typing import Annotated, Optional, Dict, Any, List, Tuple
import semantic_kernel as sk
try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

import logging
# ---- Working set from timeline.json ----
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_url,
    normalize_sources_any,
    sids_in_text,
)
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_local_paths_any

log = logging.getLogger(__name__)

def _max_sid(rows: List[Dict[str,Any]]) -> int:
    m = 0
    for r in rows:
        try:
            s = int(r.get("sid") or 0)
            if s > m: m = s
        except Exception:
            pass
    return m
def _outdir() -> pathlib.Path:
    return resolve_output_dir()

class SourcesUsedStore:
    def __init__(self) -> None:
        self.path = _outdir() / "sources_used.json"
        self.entries: List[Dict[str, Any]] = []

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(raw, list):
            self.entries = [r for r in raw if isinstance(r, dict)]

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.entries, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            pass

    def upsert(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            return
        changed = False
        touched: List[Dict[str, Any]] = []
        deduped: Dict[Tuple[Optional[str], Optional[str]], Dict[str, Any]] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            name = (rec.get("artifact_name") or "").strip() or None
            filename = (rec.get("filename") or "").strip() or None
            if not (name or filename):
                continue
            sids_val = rec.get("sids")
            sids = None
            if isinstance(sids_val, list):
                sids = sorted({int(s) for s in sids_val if isinstance(s, int)})
            key = (name, filename)
            if key not in deduped:
                deduped[key] = {"artifact_name": name, "filename": filename, "sids": sids}
            else:
                if deduped[key].get("sids") is None and sids is not None:
                    deduped[key]["sids"] = sids

        for (name, filename), rec in deduped.items():
            sids = rec.get("sids")
            idx = None
            for i, existing in enumerate(self.entries):
                if not isinstance(existing, dict):
                    continue
                existing_name = existing.get("artifact_name") or None
                existing_filename = existing.get("filename") or None
                if (existing_name, existing_filename) == (name, filename):
                    idx = i
                    break
                if name and existing_name == name:
                    idx = i
                    break
                if filename and existing_filename == filename:
                    idx = i
                    break
            if idx is None:
                row = {
                    "artifact_name": name,
                    "filename": filename,
                    "sids": sids if sids is not None else [],
                }
                self.entries.append(row)
                touched.append(row)
                changed = True
                continue
            row = self.entries[idx]
            row_changed = False
            if name and not row.get("artifact_name"):
                row["artifact_name"] = name
                row_changed = True
            if filename and not row.get("filename"):
                row["filename"] = filename
                row_changed = True
            if sids is not None:
                if row.get("sids") != sids:
                    row["sids"] = sids
                    row_changed = True
            if row_changed:
                touched.append(row)
                changed = True
        if changed:
            self.save()
            total = len(self.entries)
            files = 0
            files_with_sources = 0
            nonfiles_with_sources = 0
            without_sources = 0
            for entry in self.entries:
                if not isinstance(entry, dict):
                    continue
                has_file = bool(entry.get("filename"))
                has_sources = bool(entry.get("sids"))
                if has_file:
                    files += 1
                    if has_sources:
                        files_with_sources += 1
                else:
                    if has_sources:
                        nonfiles_with_sources += 1
                if not has_sources:
                    without_sources += 1
            log.info(
                "sources_used upsert: touched=%s stats={total:%s files:%s files_with_sources:%s nonfiles_with_sources:%s without_sources:%s}",
                touched,
                total,
                files,
                files_with_sources,
                nonfiles_with_sources,
                without_sources,
            )

    def get_sids(self, *, artifact_name: Optional[str] = None, filename: Optional[str] = None) -> List[int]:
        name = (artifact_name or "").strip()
        fname = (filename or "").strip()
        if not (name or fname):
            return []
        for entry in self.entries:
            if not isinstance(entry, dict):
                continue
            e_name = (entry.get("artifact_name") or "").strip()
            e_fname = (entry.get("filename") or "").strip()
            if name and fname:
                if e_name == name and e_fname == fname:
                    return entry.get("sids") or []
            elif name and e_name == name:
                return entry.get("sids") or []
            elif fname and e_fname == fname:
                return entry.get("sids") or []
        return []

def _read_timeline() -> Dict[str, Any]:
    p = _outdir() / "timeline.json"
    if not p.exists():
        log.error("Timeline file not found: %s", str(p))
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        log.info("Timeline file read: %s", str(p))
        return data
    except Exception:
        return {}

def _flatten_history_citations(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Returns newest-first flat list of normalized {url,title,text,sid?, run_id}.
    Uses per-turn sources_pool from turn logs.
    """
    flat: List[Dict[str, Any]] = []
    for item in (history or []):
        try:
            run_id, inner = next(iter(item.items()))
        except Exception:
            continue
        pool = inner.get("sources_pool") or (inner.get("turn_log") or {}).get("sources_pool") or []
        for c in normalize_sources_any(pool):
            if not isinstance(c, dict):
                continue
            # url = _norm_url(c.get("url") or c.get("href") or "")
            url = normalize_url(c.get("url") or c.get("href") or "")
            if not url:
                continue
            row = {
                "run_id": run_id,
                "url": url,
                "title": c.get("title") or c.get("description") or url,
                "text": c.get("text") or c.get("body") or "",
                "sid": c.get("sid"),
            }
            for k in CITATION_OPTIONAL_ATTRS:
                if c.get(k):
                    row[k] = c[k]
            flat.append(row)
    return flat


def _reconcile_history_sources(
        history: List[Dict[str, Any]],
        max_sources: int = 80
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[int, int]]]:
    """
    Build a canonical, deduped source list across all turns and
    a per-run mapping old_sid -> global_sid.

    Returns:
      (sources_pool, sid_maps)
        sources_pool: [{sid,int, url,title,text}]
        sid_maps: { run_id: { old_sid:int -> new_sid:int }, ... }
    """
    flat = _flatten_history_citations(history)  # newest-first
    if not flat:
        return [], {}

    # 1) canonical order: newest-first first-seen by URL
    seen: set[str] = set()
    canonical: List[Dict[str, Any]] = []
    for row in flat:
        u = row["url"]
        if u in seen:
            continue
        seen.add(u)
        dst = {"url": u, "title": row["title"], "text": row["text"]}
        for k in CITATION_OPTIONAL_ATTRS:
            if row.get(k):
                dst[k] = row[k]
        canonical.append(dst)
        if len(canonical) >= max_sources:
            break

    # 2) assign global SIDs 1..N (deterministic by canonical order)
    for i, r in enumerate(canonical, 1):
        r["sid"] = i

    # quick lookup
    sid_by_url = {r["url"]: r["sid"] for r in canonical}

    # 3) per-run sid maps (old -> global) via URL
    sid_maps: Dict[str, Dict[int, int]] = {}
    for row in flat:
        run_id = row["run_id"]
        old = row.get("sid")
        if old is None:
            continue
        try:
            old = int(old)
        except Exception:
            continue
        new = sid_by_url.get(row["url"])
        if not new:
            continue
        sid_maps.setdefault(run_id, {})[old] = new

    return canonical, sid_maps

def _compute_next_sid(rows) -> int:
    sids = []
    for r in rows or []:
        try:
            sid = int((r or {}).get("sid"))
            if sid > 0:
                sids.append(sid)
        except (TypeError, ValueError):
            pass
    return (max(sids) + 1) if sids else 1  # empty → start at 1

def _coerce_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    # deterministic surrogate text for non-strings
    try:
        return json.dumps(v, ensure_ascii=False, indent=2)
    except Exception:
        return str(v)

class ContextTools:

    """
    Context working-set helpers for codegen.
    Exposes: fetch_working_set(), merge_sources()
    """

    @kernel_function(
        name="merge_sources",
        description=(
                "• Input is an array of collections: [[sources1], [sources2], ...].\n"
                "• Dedupes by URL; preserves richer title/text; assigns or preserves SIDs.\n"
                "• Use this BEFORE inserting new citations into any artifact text; keep SIDs stable."
                "Pass all source collections in a single array. REQUIRED when using multiple source tools."
        )
    )
    async def merge_sources(
            self,
            source_collections: Annotated[str | list, "Array containing multiple source collections: [[sources1], [sources2], [sources3], ...]."],
    ) -> Annotated[list[dict], "Array of unified sources: [{sid:int, title:str, url:str, text:str}]"]:
        """
        Merge multiple source collections with LEFT→RIGHT precedence:
          - First occurrence of a URL wins its SID (if valid) or gets a new SID.
          - Later items with the SAME URL merge richer fields; their SID is ignored.
          - Later items with a DIFFERENT URL but a SID that’s ALREADY USED get a NEW SID = max_sid + 1.
          - Result has unique (url, sid) pairs; SIDs are dense and stable left→right.
        """
        if isinstance(source_collections, str):
            try:
                collections = json.loads(source_collections)
            except Exception:
                collections = []
        else:
            collections = source_collections

        if not isinstance(collections, list):
            collections = [collections]

        # Normalize and flatten in left→right order
        all_sources: list[dict] = []
        for collection in collections:
            all_sources.extend(normalize_sources_any(collection))

        if not all_sources:
            return []

        # Add embedded local media references (e.g., <img src="...">) as sources.
        media_seen: set[str] = set()
        for src in list(all_sources):
            if not isinstance(src, dict):
                continue
            for field in ("content", "text"):
                content = src.get(field)
                if not isinstance(content, str) or not content:
                    continue
                for path in extract_local_paths_any(content):
                    if path in media_seen:
                        continue
                    media_seen.add(path)
                    title = os.path.basename(path) or path
                    source_type = "attachment" if "/attachments/" in path else "file"
                    all_sources.append({
                        "url": path,
                        "title": title,
                        "text": "Embedded media",
                        "source_type": source_type,
                        "local_path": path,
                    })

        by_url: dict[str, dict] = {}
        used_sids: set[int] = set()
        max_sid: int = 0

        def _merge_richer(dst: dict, src: dict) -> None:
            # prefer longer title/text; carry optional attrs if missing on dst
            if len(src.get("title", "")) > len(dst.get("title", "")):
                dst["title"] = src.get("title", "")
            if len(src.get("text", "")) > len(dst.get("text", "")):
                dst["text"] = src.get("text", "")

            # prefer longer full content
            if len(src.get("content", "")) > len(dst.get("content", "")):
                dst["content"] = src.get("content", "")

            # timestamps, provider, etc. – first non-empty wins
            for k in CITATION_OPTIONAL_ATTRS:
                if not dst.get(k) and src.get(k):
                    dst[k] = src[k]

            # Scoring – keep the best
            try:
                if src.get("objective_relevance") is not None:
                    dst["objective_relevance"] = max(
                        float(dst.get("objective_relevance") or 0.0),
                        float(src["objective_relevance"]),
                    )
            except Exception:
                pass

            try:
                if src.get("query_relevance") is not None:
                    dst["query_relevance"] = max(
                        float(dst.get("query_relevance") or 0.0),
                        float(src["query_relevance"]),
                    )
            except Exception:
                pass

        for src in all_sources:
            local_path = (src.get("local_path") or "").strip()
            url = normalize_url(src.get("url", "")) if not local_path else ""
            key = f"local:{local_path}" if local_path else url
            if not key:
                continue

            # Already have this URL → merge & keep original SID
            if key in by_url:
                _merge_richer(by_url[key], src)
                by_url[key].pop("content_blocks", None)
                # do NOT touch SID / used_sids / max_sid
                continue

            # New URL → determine SID
            proposed_sid = src.get("sid")
            try:
                proposed_sid = int(proposed_sid) if proposed_sid is not None else None
            except Exception:
                proposed_sid = None

            # If proposed is invalid OR collides with an already-used SID, assign a fresh one
            if not proposed_sid or proposed_sid <= 0 or proposed_sid in used_sids:
                max_sid += 1
                sid = max_sid
            else:
                sid = proposed_sid
                if sid > max_sid:
                    max_sid = sid

            used_sids.add(sid)

            row = {
                "sid": sid,
                "url": local_path or url,
                "title": src.get("title", ""),
                "text": src.get("text") or src.get("body") or src.get("content") or "",
            }
            if src.get("content"):
                row["content"] = src["content"]
            if local_path:
                row["local_path"] = local_path
            for k in CITATION_OPTIONAL_ATTRS:
                if src.get(k):
                    row[k] = src[k]

            by_url[key] = row

        # Stable output: sort by SID so left-most items (assigned earlier) appear first
        for row in by_url.values():
            row.pop("content_blocks", None)
        merged = sorted(by_url.values(), key=lambda x: x["sid"])
        next_sid = _compute_next_sid(merged)
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
            SOURCE_ID_CV.set({"next": next_sid})
        except Exception:
            # non-fatal: context var not available, etc.
            pass
        return merged

    async def reconcile_citations(
            self,
            content: Annotated[str, "Markdown content containing [[S:n]] citation tokens"],
            sources_list: Annotated[list, "List of available sources"],
            drop_unreferenced: Annotated[bool, "Remove sources not cited in the content"] = True,
    ) -> Annotated[dict, "Object: {content:str, sources:[...], warnings:[str]}. Use sources for final file generation."]:
        rows = normalize_sources_any(sources_list)
        # index by sid
        by_sid = {int(r["sid"]): r for r in rows if r.get("sid") is not None}

        used = set(sids_in_text(content))
        warnings = []

        # check for missing SIDs in sources
        missing = [s for s in used if s not in by_sid]
        if missing:
            warnings.append(f"Missing sources for SIDs: {missing}")

        # drop unreferenced if requested
        keep_sids = used if drop_unreferenced else set(by_sid.keys())
        pruned = [by_sid[s] for s in sorted(keep_sids) if s in by_sid]

        ret = {"content": content, "sources": pruned, "warnings": warnings}
        return ret

    @kernel_function(
        name="fetch_ctx",
        description=(
            "Fetch a single artifact from timeline.json by path.\n"
            "Return shape is ALWAYS a dict:\n"
            "  {\"ret\": <value|null>, \"err\": <null|{code,message,details?}>}\n"
            "\n"
            "USAGE RESTRICTION (HARD)\n"
            "- This tool may ONLY be used by a code generation agent (the generator writing the code).\n"
            "- Do NOT call this tool directly from planning/decision roles unless you are authoring code.\n"
            "\n"
            "SUPPORTED PATHS (same as react.read)\n"
            "• so:sources_pool[<sid>,<sid>] or sources_pool[<sid>,<sid>]\n"
            "• ar:<turn_id>.user.prompt\n"
            "• ar:<turn_id>.assistant.completion\n"
            "• tc:<turn_id>.tool_calls.<id>.out.json\n"
            "\n"
            "NOT SUPPORTED in fetch_ctx (use physical paths instead):\n"
            "• fi:<turn_id>.* (attachments/files) — use OUT_DIR/<turn_id>/attachments/... or OUT_DIR/<turn_id>/files/...\n"
            "• sk:<skill id> — read skills via react.read (not from exec)\n"
            "\n"
            "CANONICAL ARTIFACT SHAPE\n"
            "{\n"
            "  \"path\": \"...\",\n"
            "  \"kind\": \"display\"|\"file\",\n"
            "  \"mime\": \"text/plain\"|\"application/pdf\"|...,\n"
            "  \"sources_used\": [sid, sid, ...],\n"
            "  \"filepath\": \"...\"   # only for kind=file, relative to outdir\n"
            "  \"text\" or \"base64\" payload depending on mime/kind\n"
            "}\n"
        ),
    )
    async def fetch_ctx(
            self,
            path: Annotated[str, "Artifact path or sources_pool selector."],
    ) -> Annotated[dict, "dict: {ret, err}. Dict. Not a JSON string!"]:

        def _err(code: str, msg: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            e = {"code": code, "message": msg}
            if details:
                e["details"] = details
            return e

        try:
            if not isinstance(path, str) or not path.strip():
                return {"ret": None, "err": _err("invalid_path_empty", "path must be a non-empty string")}

            p = path.strip()
            if "literal:" in p:
                return {"ret": None, "err": _err("invalid_path_literal", "Paths must reference existing artifacts; 'literal:' is forbidden.")}
            if p.startswith("fi:"):
                return {"ret": None, "err": _err("invalid_path_file", "fetch_ctx does not support fi: paths. Use physical OUT_DIR paths in code for files/attachments.")}
            if p.startswith("sk:"):
                return {"ret": None, "err": _err("invalid_path_skill", "fetch_ctx does not support sk: paths. Use react.read for skills before exec.")}

            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import resolve_artifact_from_timeline

            timeline = _read_timeline() or {}
            art = resolve_artifact_from_timeline(timeline, p)
            if art is None:
                return {"ret": None, "err": _err("not_found", "Path not found", {"path": p})}
            # sources_pool selector returns {kind: sources_pool, items: [...]}
            if art.get("kind") == "sources_pool":
                return {"ret": art.get("items") or [], "err": None}
            return {"ret": art, "err": None}

        except Exception as e:
            log.exception("fetch_ctx failed")
            return {"ret": None, "err": _err("tool_failure", f"fetch_ctx failed: {e}")}


kernel = sk.Kernel()
tools = ContextTools()
kernel.add_plugin(tools, "context_tools")
