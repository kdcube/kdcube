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

# ---- Working set from context.json ----
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_url,
    normalize_sources_any,
    dedupe_sources_by_url,
    sids_in_text,
    rewrite_citation_tokens,
)

# _SID_RE = re.compile(r"\[\[S:(\d+(?:,\d+)*)\]\]")
#
# # ⬇️ Centralize of ptional citation attributes we preserve
# _CITATION_OPTIONAL_ATTRS = (
#     "provider", "published_time_iso", "modified_time_iso", "expiration",
#     # harmless extras we may get from KB
#     "mime", "source_type", "rn",
# )

# def _norm_url(u: str) -> str:
#     # conservative normalization: lowercase scheme+host; strip trailing slash
#     # (don’t over-normalize; avoid changing meaning)
#     u = (u or "").strip()
#     if not u: return ""
#     try:
#         from urllib.parse import urlsplit, urlunsplit
#         sp = urlsplit(u)
#         host = (sp.netloc or "").lower()
#         path = sp.path.rstrip("/") or sp.path
#         return urlunsplit((sp.scheme.lower(), host, path, sp.query, sp.fragment))
#     except Exception:
#         return u

# def _as_rows(val) -> List[Dict[str, Any]]:
#     if not val: return []
#     if isinstance(val, str):
#         try:
#             val = json.loads(val)
#         except Exception:
#             return []
#
#     def process_rich_attrs(citation: Dict[str, Any], source: Dict[str, Any]) -> None:
#         for k in _CITATION_OPTIONAL_ATTRS:
#             if k in source and source[k] not in (None, ""):
#                 citation[k] = source[k]
#
#     if isinstance(val, dict):
#         rows = []
#         for k, v in val.items():
#             if not isinstance(v, dict): continue
#             sid = int(v.get("sid") or k) if str(k).isdigit() else v.get("sid")
#             citation = {
#                 "sid": sid,
#                 "title": v.get("title",""),
#                 "url": v.get("url",""),
#                 "text": v.get("text") or v.get("body") or v.get("content") or "",
#             }
#             process_rich_attrs(citation, v)
#             rows.append(citation)
#         return rows
#
#     if isinstance(val, list):
#         rows = []
#         for v in val:
#             if not isinstance(v, dict): continue
#             citation = {
#                 "sid": v.get("sid"),
#                 "title": v.get("title",""),
#                 "url": v.get("url") or v.get("href") or "",
#                 "text": v.get("text") or v.get("body") or v.get("content") or "",
#             }
#             process_rich_attrs(citation, v)
#             rows.append(citation)
#         return rows
#
#     return []

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

def _read_context() -> Dict[str, Any]:
    p = _outdir() / "context.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _latest_with_project_log(history: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
    for item in history or []:
        try:
            exec_id, inner = next(iter(item.items()))
            text = ((inner.get("project_log") or {}).get("text") or "").strip()
            if text:
                return exec_id, inner
        except Exception:
            continue
    return None, {}
def _latest_with_materialzied_project_log(history: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
    for item in history or []:
        try:
            exec_id, inner = next(iter(item.items()))
            text = ((inner.get("project_log_materialized") or {}).get("text") or "").strip()
            if text:
                return exec_id, inner
        except Exception:
            continue
    return None, {}

def _latest_with_deliverables(history: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
    for item in history or []:
        try:
            exec_id, inner = next(iter(item.items()))
            files = (inner.get("deliverables") or [])
            if files:
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
        text = s.get("text") or s.get("body") or s.get("content") or ""
        if sid is None and not url and not text:
            continue
        row = {"sid": sid, "title": title, "url": url, "text": text}
        for k in _CITATION_OPTIONAL_ATTRS:
            if k in s and s[k] not in (None, ""):
                row[k] = s[k]
        out.append(row)
    return out

def _dedupe_sources(prior: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_url = {}
    last_sid = 0
    for s in prior or []:
        url = (s.get("url") or "").strip().lower()
        row = dict(s)
        by_url[url] = row
        if isinstance(s.get("sid"), int):
            last_sid = max(last_sid, int(s["sid"]))

    next_sid = last_sid + 1
    for s in new or []:
        url = (s.get("url") or "").strip().lower()
        if not url:
            continue
        if url in by_url:
            existing = by_url[url]
            # prefer richer title/text
            if len(s.get("title","")) > len(existing.get("title","")):
                existing["title"] = s.get("title","")
            if len(s.get("text","")) > len(existing.get("text","")):
                existing["text"] = s.get("text","")
            # fill in optional attrs if missing
            for k in _CITATION_OPTIONAL_ATTRS:
                if not existing.get(k) and s.get(k):
                    existing[k] = s[k]
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
            row = {
                "run_id": run_id,
                "url": url,
                "title": c.get("title") or c.get("description") or url,
                "text": c.get("text") or c.get("body") or "",
                "sid": c.get("sid"),
            }
            for k in _CITATION_OPTIONAL_ATTRS:
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
        dst = {"url": u, "title": row["title"], "text": row["text"]}
        for k in _CITATION_OPTIONAL_ATTRS:
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

class ContextTools:

    """
    Context working-set helpers for codegen.
    Exposes: fetch_working_set(), merge_sources()
    """

    @kernel_function(
        name="fetch_working_set",
        description=(
               "Return the latest project working set for EDIT/CONTINUE flows / prior work.\n"
                "\n"
                "WHEN TO CALL\n"
                "• Always at the start of an edit/update/extend turn.\n"
                "• Whenever you need the current user message + the most relevant prior artifacts.\n"
                "\n"
                "WHAT YOU RECEIVE\n"
                "• existing_project_log — Markdown log (GLOBAL [[S:n]] allowed). It tells the story of the project\n"
                "  and explicitly mentions which slots/artifacts exist (by slot name). It NEVER embeds slot values.\n"
                "• existing_sources — canonical, deduplicated citations with contiguous SIDs (1..N).\n"
                "• existing_deliverables — the actual artifacts (by slot) from the most relevant prior turn, each with:\n"
                "    { slot_name:str, type:\"file|inline\", mime:str?, name?:str (files), path?:str (files),\n"
                "      description?:str, text:str }\n"
                "  - text is ALWAYS present and is the authoritative text representation; for binaries it is a faithful surrogate; for textual files it mirrors content.\n"
                "• Chat slice — current_user, previous_user, previous_assistant (text only).\n"
                "\n"
                "HOW TO USE (critical rules)\n"
                "• Do not parse the log for values. Use the log only to learn which slots exist and what changed.\n"
                "• Load actual content from existing_deliverables by slot name → use the .text field.\n"
                "• For structured artifacts (code/HTML/JSON/YAML): select the slot by name/mime, read .text, strip code fences if needed, then parse/validate.\n"
                "• Citations: .text may contain GLOBAL [[S:n]]. When adding new sources, first merge using context_tools.merge_sources and then preserve SIDs.\n"
                "\n"
                "DOs / DON'Ts\n"
                "• DO: read .text from the slot you need; edit in memory; write updated slot(s) to output contract.\n"
                "• DO: keep SIDs stable; use merge_sources if you bring multiple source collections.\n"
                "• DON'T: embed slot values inside the log. The log remains a story with slot names, not values.\n"
                "\n"
                "LIMITS\n"
                "• Returns a best-known snapshot; you must validate/repair/continue artifacts as needed."
        ),
    )
    async def fetch_working_set(
            self,
            select: Annotated[
                str,
                "Selection policy. 'latest' → most recent execution that produced canvas/log/media.",
                {"enum": ["latest"]},
            ] = "latest",
    ) -> Annotated[
        dict,
                (
                "Shape:\n"
                "{\n"
                "  existing_selection: {exec_id, select},\n"
                "  existing_project_log: str,              # Markdown log; may contain GLOBAL [[S:n]]\n"
                "  existing_sources: [ {sid,title,url?,text,...} ],  # canonical, global SIDs (1..N)\n"
                "  existing_deliverables: [\n"
                "    { slot_name, type, mime?, name?, path?, description?, text }  # ALWAYS has .text\n"
                "  ],\n"
                "  current_user: {text}, previous_user: {text}, previous_assistant: {text}\n"
                "}\n"
                "Usage:\n"
                "Choose slot(s) by name from existing_deliverables.<slot>.text (or multiple slots text) (or previous_assistant.text if empty) as the edit base; use the project_log as a context for LLMs to explain what has been done before if needed; "
                "• Use existing_sources as prior_sources; call merge_sources before adding new sources.\n"
                "• Strip code fences before parsing structured .text when necessary.\n"
                "• Read minimal chat spans only."
        ),
    ]:
        goal_kind: Annotated[str, "Optional filter by project type (unused currently)"] = "",
        query: Annotated[str, "Optional search query for specific content (unused currently)"] = ""

        ctx = _read_context()
        hist: List[Dict[str, Any]] = ctx.get("program_history") or []

        # Build canonical sources + per-run SID maps
        canonical_sources, sid_maps = _reconcile_history_sources(hist, max_sources=80)

        # exec_id, last_turn_with_project_log = _latest_with_project_log(hist)
        # exec_id, last_turn_with_project_log = _latest_with_materialzied_project_log(hist)
        exec_id, last_turn_with_deliverables = _latest_with_deliverables(hist)

        # reused = bool(exec_id and last_turn_with_project_log)
        reused = bool(exec_id and last_turn_with_deliverables)
        # project_canvas = ""
        project_log = ""
        deliverables = []

        if reused:
            # pc = last_turn_with_canvas_mat.get("project_canvas_materialized") or {}
            # pc = last_turn_with_canvas.get("project_canvas") or {}
            pl = last_turn_with_deliverables.get("project_log") or {}

            # project_canvas = (pc.get("text") or "").strip()
            # project_canvas_glue = (pc.get("text") or "").strip()
            project_log = (pl.get("text") or "").strip()

            # Rewrite [[S:n]] tokens in the latest texts to the canonical SIDs (if we have a map)
            sid_map = sid_maps.get(exec_id, {})
            if sid_map:
                # project_canvas = _rewrite_tokens_to_global(project_canvas, sid_map)
                # TODO: do for each deliverable text
                project_log = _rewrite_tokens_to_global(project_log, sid_map)

            deliverables = last_turn_with_deliverables.get("deliverables") or []

        # IMPORTANT: always return the canonical (cross-turn) sources here
        sources = canonical_sources

        current_turn = ctx.get("current_turn")
        current_user_prompt = current_turn.get("user")
        previous_user_prompt = (ctx.get("previous_user") or {})
        previous_assistant_reply = (ctx.get("previous_assistant") or {})

        deliverables = [
            [{
                **(d.get("value") or {}),
                "description": d.get("description"),
                "slot_name": d.get("slot"),
            } for d in deliverables if d.get("slot") not in ("project_log", "project_canvas")]
        ]
        return {
            # "reused": reused,
            "existing_selection": {"exec_id": exec_id or "", "select": select},
            # "existing_project_canvas": project_canvas,
            "existing_project_log": project_log,
            "existing_sources": sources,   # ← global, deduped, contiguous SIDs
            "existing_deliverables": deliverables,
            "current_user": {"text": (current_user_prompt or {}).get("text","")},
            "previous_user": {"text": (previous_user_prompt or {}).get("text","")},
            "previous_assistant": {"text": (previous_assistant_reply or {}).get("text","")},
        }

    @kernel_function(
        name="merge_sources",
        description=(
                    "• Input is a JSON array of collections: [[sources1], [sources2], ...].\n"
                    "• Dedupes by URL; preserves richer title/text; assigns or preserves SIDs.\n"
                    "• Use this BEFORE inserting new citations into any slot text; keep SIDs stable."
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
                for k in _CITATION_OPTIONAL_ATTRS:
                    if not existing.get(k) and source.get(k):
                        existing[k] = source[k]
                continue

            # Assign SID: use existing if valid, otherwise assign new
            sid = source.get("sid")
            if not isinstance(sid, int) or sid <= 0:
                max_sid += 1
                sid = max_sid
            else:
                max_sid = max(max_sid, sid)

            row = {"sid": sid, "title": source.get("title", ""), "url": url, "text": source.get("text", "")}
            for k in _CITATION_OPTIONAL_ATTRS:
                if source.get(k):
                    row[k] = source[k]
            by_url[url] = row

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
            content: Annotated[str, "Markdown content containing [[S:n]] citation tokens"],
            sources_json: Annotated[str, "JSON array of available sources"],
            drop_unreferenced: Annotated[bool, "Remove sources not cited in the content"] = True,
    ) -> Annotated[str, "JSON object: {content:str, sources:[...], warnings:[str]}. Use sources for final file generation."]:
        rows = _as_rows(sources_json)
        # index by sid
        by_sid = {int(r["sid"]): r for r in rows if r.get("sid") is not None}

        used = set(_sids_in_text(content))
        warnings = []

        # check for missing SIDs in sources
        missing = [s for s in used if s not in by_sid]
        if missing:
            warnings.append(f"Missing sources for SIDs: {missing}")

        # drop unreferenced if requested
        keep_sids = used if drop_unreferenced else set(by_sid.keys())
        pruned = [by_sid[s] for s in sorted(keep_sids) if s in by_sid]

        ret = {"content": content, "sources": pruned, "warnings": warnings}
        return json.dumps(ret, ensure_ascii=False)

    @kernel_function(
        name="fetch_turn_artifacts",
        description=(
                "Retrieve artifacts from specific historical turns by turn_id.\n"
                "\n"
                "WHEN TO USE\n"
                "• After reading the program playbook in OUTPUT_DIR/context.json\n"
                "• When you need specific artifacts from identified prior turns\n"
                "• For targeted retrieval (not just 'latest')\n"
                "\n"
                "WHAT YOU RECEIVE\n"
                "Map of turn_id → turn data:\n"
                "{\n"
                "  '<turn_id>': {\n"
                "    'ts': '2025-10-02',\n"
                "    'program_log': {text: str, format: str},\n"
                "    'deliverables': {\n"
                "      '<slot_name>': {\n"
                "        'type': 'file' | 'inline',\n"
                "        'text': str,              # ALWAYS present; authoritative text representation\n"
                "        'description': str,\n"
                "        'format': str,            # for inline\n"
                "        'mime': str,              # for file\n"
                "        'path': str,              # for file (OUTPUT_DIR-relative if rehosted)\n"
                "        'sources_used': str,    # [{sid:str, url:str, title: str, body?:str}\n" 
                "      }\n"
                "    }\n"
                "  }\n"
                "}\n"
                "\n"
                "HOW TO USE\n"
                "1. Read program_playbook from context.json to identify turn_ids\n"
                "2. Call this function with specific turn_ids\n"
                "3. Access artifacts via result[turn_id]['deliverables'][slot_name]['text']\n"
                "4. For structured content (code/JSON/etc), parse the 'text' field\n"
                "\n"
                "LIMITS\n"
                "• Returns up to 10 turns\n"
                "• Text fields may be truncated for very large artifacts"
        ),
    )
    async def fetch_turn_artifacts(
            self,
            turn_ids: Annotated[
                str,
                "JSON array of turn_ids to fetch: [\"turn_123\", \"turn_456\"]",
            ],
    ) -> Annotated[
        str,
        "JSON object mapping turn_id → {ts, program_log, deliverables}",
    ]:
        try:
            ids = json.loads(turn_ids)
            if not isinstance(ids, list):
                ids = [ids]
        except:
            return json.dumps({"error": "Invalid turn_ids format; expected JSON array"})

        ctx = _read_context()
        hist: List[Dict[str, Any]] = ctx.get("program_history") or []

        # Build index
        by_id = {}
        for rec in hist:
            try:
                exec_id, meta = next(iter(rec.items()))
                by_id[exec_id] = meta
            except:
                continue

        # Build result
        result = {}
        for tid in ids[:10]:  # limit to 10
            meta = by_id.get(tid)
            if not meta:
                continue

            ts = (meta.get("ts") or "")[:10]

            # Program log
            pl = (meta.get("project_log") or {})
            pl_text = (pl.get("text") or pl.get("value") or "").strip()
            pl_fmt = pl.get("format") or "markdown"

            # Deliverables
            deliverables_list = meta.get("deliverables") or []
            deliverables_dict = {}

            # Get global sources for this turn
            turn_sources = ((meta.get("web_links_citations") or {}).get("items")) or []
            sid_to_source = {ts["sid"]: ts for ts in turn_sources}
            for d in deliverables_list:
                slot_name = d.get("slot")
                if not slot_name or slot_name in {"project_log", "project_canvas"}:
                    continue

                artifact = d.get("value") or {}
                output = artifact.get("output") or {}

                slot_data = {
                    "type": artifact.get("type") or "inline",
                    "description": d.get("description") or "",
                }

                # Text - ALWAYS present
                text = output.get("text") or artifact.get("value") or ""
                if isinstance(text, dict):
                    text = json.dumps(text, ensure_ascii=False)
                slot_data["text"] = str(text or "")

                # Type-specific fields
                if slot_data["type"] == "file":
                    slot_data["mime"] = artifact.get("mime") or "application/octet-stream"
                    slot_data["path"] = output.get("path") or ""
                    slot_data["filename"] = output.get("filename") or ""
                else:
                    slot_data["format"] = artifact.get("format") or "text"

                sids_used = artifact.get("sources_used") or _sids_in_text(slot_data["text"]) or []
                slot_data["sources_used"] =  [
                    sid_to_source[sid] for sid in sids_used if sid in sid_to_source
                ]

                deliverables_dict[slot_name] = slot_data

            result[tid] = {
                "ts": ts,
                "program_log": {...},
                "deliverables": deliverables_dict,
                "sources": turn_sources,  # ← Add global sources. let's not "announce" them yet. Let's "materialize"
                # the sources directly in the deliverables "sources_used" as documented in the function description.
            }

            result[tid] = {
                "ts": ts,
                "program_log": {"text": pl_text, "format": pl_fmt} if pl_text else None,
                "deliverables": deliverables_dict,
            }

        return json.dumps(result, ensure_ascii=False)

kernel = sk.Kernel()
tools = ContextTools()
kernel.add_plugin(tools, "context_tools")
