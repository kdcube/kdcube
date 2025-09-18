# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/tools/ctx_tool.py
import json, re, pathlib
from typing import Annotated, Optional, Dict, Any, List, Tuple
import semantic_kernel as sk
try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

_SID_RE = re.compile(r"\[\[S:(\d+(?:,\d+)*)\]\]")

def _norm_url(u: str) -> str:
    # conservative normalization: lowercase scheme+host; strip trailing slash
    # (don’t over-normalize; avoid changing meaning)
    u = (u or "").strip()
    if not u: return ""
    try:
        from urllib.parse import urlsplit, urlunsplit
        sp = urlsplit(u)
        host = (sp.netloc or "").lower()
        path = sp.path.rstrip("/") or sp.path
        return urlunsplit((sp.scheme.lower(), host, path, sp.query, sp.fragment))
    except Exception:
        return u

def _as_rows(val) -> List[Dict[str, Any]]:
    if not val: return []
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return []
    # Accept dict-of-dicts {"1":{...}} or list-of-dicts
    if isinstance(val, dict):
        rows = []
        for k, v in val.items():
            if not isinstance(v, dict): continue
            sid = int(v.get("sid") or k) if str(k).isdigit() else v.get("sid")
            rows.append({"sid": sid, "title": v.get("title",""), "url": v.get("url",""), "text": v.get("text") or v.get("body") or ""})
        return rows
    if isinstance(val, list):
        rows = []
        for v in val:
            if not isinstance(v, dict): continue
            rows.append({
                "sid": v.get("sid"),
                "title": v.get("title",""),
                "url": v.get("url") or v.get("href") or "",
                "text": v.get("text") or v.get("body") or v.get("content") or "",
            })
        return rows
    return []

def _max_sid(rows: List[Dict[str,Any]]) -> int:
    m = 0
    for r in rows:
        try:
            s = int(r.get("sid") or 0)
            if s > m: m = s
        except Exception:
            pass
    return m

# ---- Working set from context.json ----
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

def _outdir() -> pathlib.Path:
    return resolve_output_dir()

def _read_context() -> Dict[str, Any]:
    p = _outdir() / "context.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _latest_with_canvas(history: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
    for item in history or []:
        try:
            exec_id, inner = next(iter(item.items()))
            text = ((inner.get("project_canvas") or {}).get("text") or "").strip()
            if text:
                return exec_id, inner
        except Exception:
            continue
    return None, {}

def _norm_sources(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in items or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("sid")
        title = s.get("title") or ""
        url = s.get("url") or s.get("href") or ""
        text = s.get("text") or s.get("body") or ""
        if sid is None and not url and not text:
            continue
        row = {"sid": sid, "title": title, "url": url, "text": text}
        out.append(row)
    return out

def _dedupe_sources(prior: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_url = {}
    last_sid = 0
    for s in prior or []:
        url = (s.get("url") or "").strip().lower()
        by_url[url] = dict(s)
        if isinstance(s.get("sid"), int):
            last_sid = max(last_sid, int(s["sid"]))
    next_sid = last_sid + 1
    for s in new or []:
        url = (s.get("url") or "").strip().lower()
        if url in by_url:
            continue
        row = dict(s)
        if row.get("sid") in (None, "", 0):
            row["sid"] = next_sid
            next_sid += 1
        by_url[url] = row
    return list(by_url.values())

# --- reconcile
def _sids_in_text(md: str) -> List[int]:
    found = set()
    for m in _SID_RE.finditer(md or ""):
        for part in (m.group(1) or "").split(","):
            try:
                found.add(int(part))
            except Exception:
                pass
    return sorted(found)


# --- reconciliation helpers (cross-turn) ---

def _rewrite_tokens_to_global(md: str, sid_map: Dict[int, int]) -> str:
    if not md or not sid_map:
        return md or ""

    def repl(m):
        body = m.group(1)
        new_ids = []
        for part in body.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            old = int(part)
            new = sid_map.get(old)
            if new:
                new_ids.append(str(new))
        # drop the token entirely if nothing maps
        return f"[[S:{','.join(new_ids)}]]" if new_ids else ""
    return _SID_RE.sub(repl, md)

def _flatten_history_citations(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Returns newest-first flat list of normalized {url,title,text,sid?, run_id}.
    Uses 'web_links_citations.items' from each turn.
    """
    flat: List[Dict[str, Any]] = []
    for item in (history or []):
        try:
            run_id, inner = next(iter(item.items()))
        except Exception:
            continue
        cites = ((inner.get("web_links_citations") or {}).get("items")) or []
        for c in cites:
            if not isinstance(c, dict):
                continue
            url = _norm_url(c.get("url") or c.get("href") or "")
            if not url:
                continue
            flat.append({
                "run_id": run_id,
                "url": url,
                "title": c.get("title") or c.get("description") or url,
                "text": c.get("text") or c.get("body") or "",
                "sid": c.get("sid"),
            })
    return flat

def _reconcile_history_sources(
        history: List[Dict[str, Any]],
        max_sources: int = 80
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[int, int]]]:
    """
    Build a canonical, deduped source list across all turns and
    a per-run mapping old_sid -> global_sid.

    Returns:
      (canonical_sources, sid_maps)
        canonical_sources: [{sid,int, url,title,text}]
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
        canonical.append({"url": u, "title": row["title"], "text": row["text"]})
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

class ContextTools:

    """
    Context working-set helpers for codegen.
    Exposes: fetch_working_set(), merge_sources()
    """

    @kernel_function(
        name="fetch_working_set",
        description=(
            "Retrieve the latest project state for editing workflows. "
            "Gets the most recent project canvas, citations, and media from prior runs. "
            "Use this at the start of edit/update requests to build upon existing work."
        )
    )
    async def fetch_working_set(
            self,
            select: Annotated[str, "Selection method: 'latest' (most recent with content)", {"enum": ["latest"]}] = "latest",
    ) -> Annotated[dict, "dict with {existing_project_canvas: str, existing_project_log: str, existing_sources: [{sid,title,url,text}]}. Use existing_project_canvas as base content to edit."]:
    # ) -> Annotated[dict, "dict with {project_canvas: str, project_log_md: str, sources: [{sid,title,url,text}], media: []}. Use canvas_md as base content to edit."]:


        goal_kind: Annotated[str, "Optional filter by project type (unused currently)"] = "",
        query: Annotated[str, "Optional search query for specific content (unused currently)"] = ""

        ctx = _read_context()
        hist: List[Dict[str, Any]] = ctx.get("program_history") or []

        # Build canonical sources + per-run SID maps
        canonical_sources, sid_maps = _reconcile_history_sources(hist, max_sources=80)

        exec_id, inner = _latest_with_canvas(hist)
        reused = bool(exec_id and inner)

        project_canvas = ""
        project_log = ""
        media = []

        if reused:
            pc = inner.get("project_canvas") or {}
            pl = inner.get("project_log") or {}

            project_canvas = (pc.get("text") or "").strip()
            project_log = (pl.get("text") or "").strip()

            # Rewrite [[S:n]] tokens in the latest texts to the canonical SIDs (if we have a map)
            sid_map = sid_maps.get(exec_id, {})
            if sid_map:
                project_canvas = _rewrite_tokens_to_global(project_canvas, sid_map)
                project_log = _rewrite_tokens_to_global(project_log, sid_map)

            # Keep whatever media you store (unchanged)
            media = (inner.get("media") or {}).get("items") or []

        # IMPORTANT: always return the canonical (cross-turn) sources here
        sources = canonical_sources

        return {
            # "reused": reused,
            "existing_selection": {"exec_id": exec_id or "", "select": select},
            "existing_project_canvas": project_canvas,
            "existing_project_log": project_log,
            "existing_sources": sources,   # ← global, deduped, contiguous SIDs
            "existing_media": media,
        }

    @kernel_function(
        name="merge_sources",
        description=(
                "Combine multiple citation source collections into a unified, deduplicated list. "
                "Pass all source collections in a single JSON array. REQUIRED when using multiple source tools."
        )
    )
    async def merge_sources(
            self,
            source_collections: Annotated[str, "JSON array containing multiple source collections: [[sources1], [sources2], [sources3], ...]"],
    ) -> Annotated[str, "JSON array of unified sources: [{sid:int, title:str, url:str, text:str}]"]:
        """Merge multiple source collections, deduplicating by URL and preserving/assigning SIDs."""

        try:
            collections = json.loads(source_collections)
            if not isinstance(collections, list):
                collections = [collections]  # Handle single collection case
        except:
            return "[]"

        all_sources = []
        for collection in collections:
            all_sources.extend(_as_rows(collection))

        if not all_sources:
            return "[]"

        # Deduplicate and assign SIDs
        by_url = {}
        max_sid = 0

        for source in all_sources:
            url = _norm_url(source.get("url", ""))
            if not url:
                continue

            if url in by_url:
                # Keep first occurrence, update if new has more content
                existing = by_url[url]
                if len(source.get("title", "")) > len(existing.get("title", "")):
                    existing["title"] = source.get("title", "")
                if len(source.get("text", "")) > len(existing.get("text", "")):
                    existing["text"] = source.get("text", "")
                continue

            # Assign SID: use existing if valid, otherwise assign new
            sid = source.get("sid")
            if not isinstance(sid, int) or sid <= 0:
                max_sid += 1
                sid = max_sid
            else:
                max_sid = max(max_sid, sid)

            by_url[url] = {
                "sid": sid,
                "title": source.get("title", ""),
                "url": url,
                "text": source.get("text", "")
            }

        merged = sorted(by_url.values(), key=lambda x: x["sid"])
        return json.dumps(merged, ensure_ascii=False)

    # @kernel_function(
    #     name="reconcile_citations",
    #     description=(
    #         "Validate that all [[S:n]] citation tokens in content have corresponding sources. "
    #         "Optionally removes unused sources to keep the source list clean. "
    #         "Use before final output to ensure citation integrity."
    #     )
    # )
    async def reconcile_citations(
            self,
            project_canvas: Annotated[str, "Markdown content containing [[S:n]] citation tokens"],
            sources_json: Annotated[str, "JSON array of available sources"],
            drop_unreferenced: Annotated[bool, "Remove sources not cited in the content"] = True,
    ) -> Annotated[str, "JSON object: {project_canvas:str, sources:[...], warnings:[str]}. Use sources for final file generation."]:
        rows = _as_rows(sources_json)
        # index by sid
        by_sid = {int(r["sid"]): r for r in rows if r.get("sid") is not None}

        used = set(_sids_in_text(project_canvas))
        warnings = []

        # check for missing SIDs in sources
        missing = [s for s in used if s not in by_sid]
        if missing:
            warnings.append(f"Missing sources for SIDs: {missing}")

        # drop unreferenced if requested
        keep_sids = used if drop_unreferenced else set(by_sid.keys())
        pruned = [by_sid[s] for s in sorted(keep_sids) if s in by_sid]

        ret = {"project_canvas": project_canvas, "sources": pruned, "warnings": warnings}
        return json.dumps(ret, ensure_ascii=False)

kernel = sk.Kernel()
tools = ContextTools()
kernel.add_plugin(tools, "context_tools")
