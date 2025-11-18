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

import logging
# ---- Working set from context.json ----
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_url,
    normalize_sources_any,
    sids_in_text,
)

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

def _read_context() -> Dict[str, Any]:
    p = _outdir() / "context.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

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

class ContextTools:

    """
    Context working-set helpers for codegen.
    Exposes: fetch_working_set(), merge_sources()
    """

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
        """
        Merge multiple source collections with LEFT→RIGHT precedence:
          - First occurrence of a URL wins its SID (if valid) or gets a new SID.
          - Later items with the SAME URL merge richer fields; their SID is ignored.
          - Later items with a DIFFERENT URL but a SID that’s ALREADY USED get a NEW SID = max_sid + 1.
          - Result has unique (url, sid) pairs; SIDs are dense and stable left→right.
        """
        try:
            collections = json.loads(source_collections)
            if not isinstance(collections, list):
                collections = [collections]
        except Exception:
            return "[]"

        # Normalize and flatten in left→right order
        all_sources: list[dict] = []
        for collection in collections:
            all_sources.extend(normalize_sources_any(collection))

        if not all_sources:
            return "[]"

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
            url = normalize_url(src.get("url", ""))
            if not url:
                continue

            # Already have this URL → merge & keep original SID
            if url in by_url:
                _merge_richer(by_url[url], src)
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
                "url": url,
                "title": src.get("title", ""),
                "text": src.get("text") or src.get("body") or src.get("content") or "",
            }
            if src.get("content"):
                row["content"] = src["content"]
            for k in CITATION_OPTIONAL_ATTRS:
                if src.get(k):
                    row[k] = src[k]

            by_url[url] = row

        # Stable output: sort by SID so left-most items (assigned earlier) appear first
        merged = sorted(by_url.values(), key=lambda x: x["sid"])
        next_sid = _compute_next_sid(merged)
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
            SOURCE_ID_CV.set({"next": next_sid})
        except Exception:
            # non-fatal: context var not available, etc.
            pass
        return json.dumps(merged, ensure_ascii=False)

    async def reconcile_citations(
            self,
            content: Annotated[str, "Markdown content containing [[S:n]] citation tokens"],
            sources_json: Annotated[str, "JSON array of available sources"],
            drop_unreferenced: Annotated[bool, "Remove sources not cited in the content"] = True,
    ) -> Annotated[str, "JSON object: {content:str, sources:[...], warnings:[str]}. Use sources for final file generation."]:
        rows = normalize_sources_any(sources_json)
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
                "    'status': 'success' | 'failed: solver_error' | 'answered_by_assistant' | 'no_activity'\n"
                "    'program_log': {text: str, format: str},\n"
                "    'user_msg':str,           # user message on the turn. Always in markdown. \n"
                "    'assistant_msg':str,      # assistant message on the turn. Always in markdown. \n"
                "    'solver_failure':str | null,  # non-empty if the solver experienced failure on this turn. \n"
                "    'deliverables': {\n"
                "      '<slot_name>': {\n"
                "        'type': 'file' | 'inline',\n"
                "        'text': str,              # ALWAYS present; authoritative text representation\n"
                "        'description': str,\n"
                "        'format': str,            # for inline\n"
                "        'mime': str,              # for file\n"
                "        'path': str,              # for file (OUTPUT_DIR-relative if rehosted)\n"
                "        'sources_used': list[{sid:str, url:str, title: str, body?:str}],    # [{sid:str, url:str, title: str, body?:str}\n" 
                "      }\n"
                "    }\n"
                "  }\n"
                "}\n"
                "\n"
                "HOW TO USE\n"
                "1. Examine the Program History Playbook to understand which turn_ids you need and which artifacts you need.\n"
                "1. Call this function with specific turn_ids. 'current_turn' turn id is for current turn.\n"
                "3. Access solver artifacts (deliverables) via result[turn_id]['deliverables'][slot_name]['text']\n"
                "4. For structured content (code/JSON/etc), parse the 'text' field\n"
                "5. For editing bases when solver failed for the relevant turn (or didn’t run): result[tid]['assistant_msg'] is authoritative (what the user saw).\n"                
                "6. If the user's message needed, get it via result[turn_id]['user_msg'].\n"
                "7. If assistant message needed, get it via result[turn_id]['assistant_msg'].\n"
                "8. With file deliverables, treat all text fields as authoritative text representations; do not re-crawl files directly unless you need binary content (rare).\n"
                
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
                "JSON array of turn_ids to fetch, i.e.: [\"turn_1760743886365_abcdef\", \"turn_1760743886365_abcdeg\"]",
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
            log.error(f"Failed to parse turn_ids: {turn_ids}")
            return json.dumps({"error": "Invalid turn_ids format; expected JSON array"})

        ctx = _read_context()
        hist: List[Dict[str, Any]] = ctx.get("program_history") or []

        ensure_prefix_fn = lambda id: id if id.startswith("turn_") else f"turn_{id}"
        log.info(f"[signal control]: turn_ids before preproc: {turn_ids}")
        ids = [ensure_prefix_fn(id) for id in ids]
        log.info(f"[signal control]: turn_ids after preproc: {ids}")

        # Build index
        by_id = {}
        for rec in hist:
            try:
                exec_id, meta = next(iter(rec.items()))
                by_id[exec_id] = meta
            except:
                continue

        # Build result
        from kdcube_ai_app.apps.chat.sdk.codegen.project_retrieval import reconcile_citations_for_context

        mini_history = []
        for tid in ids[:10]:
            if tid in by_id:
                mini_history.append({tid: by_id[tid]})

        # Reconcile citations across these turns (in-place rewriting)
        reconciled = reconcile_citations_for_context(
            mini_history,
            max_sources=80,
            rewrite_tokens_in_place=True  # This patches [[S:n]] in text
        )

        canonical_sources = reconciled["canonical_sources"]  # [{sid, url, title, text, ...}]
        sid_maps = reconciled["sid_maps"]  # {run_id: {old_sid -> global_sid}}

        # Quick lookup by global SID
        sources_by_sid = {s["sid"]: s for s in canonical_sources}
        # ===== END KEY ADDITION =====

        # Build result (now with reconciled citations)
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
            assistant = (meta.get("assistant") or "").strip()
            user_msg = (meta.get("user") or {}).get("prompt") or ""
            turn_log = meta.get("turn_log") or {}
            deliverables_dict = {}

            # Get global sources for this turn
            # turn_sources = ((meta.get("web_links_citations") or {}).get("items")) or []
            # sid_to_source = {ts["sid"]: ts for ts in turn_sources}
            for d in deliverables_list:
                slot_name = d.get("slot")
                if not slot_name or slot_name in {"project_log", "project_canvas"}:
                    continue

                artifact = d.get("value") or {}
                text = artifact.get("text") or ""

                slot_data = {
                    "type": artifact.get("type") or "inline",
                    "description": d.get("description") or "",
                    "text": text
                }

                # Type-specific fields
                if slot_data["type"] == "file":
                    slot_data["mime"] = artifact.get("mime") or "application/octet-stream"
                    slot_data["path"] = artifact.get("path") or ""
                    slot_data["filename"] = artifact.get("filename") or ""
                else:
                    slot_data["format"] = artifact.get("format") or "text"

                # ===== Materialize sources_used =====
                # Extract SIDs used in this slot's text
                from kdcube_ai_app.apps.chat.sdk.tools.citations import sids_in_text

                sids_used = sids_in_text(slot_data["text"])

                slot_data["sources_used"] = [
                    sources_by_sid[sid] for sid in sids_used if sid in sources_by_sid
                ]

                deliverables_dict[slot_name] = slot_data

            solver_failure = (meta.get("solver_failure") or "").strip() or None
            if deliverables_list:
                status = "success"
            elif solver_failure:
                status = "failed: solver_error"
            elif assistant:
                status = "answered_by_assistant"
            else:
                status = "no_activity"

            result[tid] = {
                "ts": ts,
                "status": status,
                "turn_log": (meta.get("turn_log") or "")[:1000],
                "solver_failure": solver_failure,
                "program_log": {"text": pl_text, "format": pl_fmt} if pl_text else None,
                "deliverables": deliverables_dict,
                "user_msg": user_msg or "",
                "assistant_msg": assistant or ""
                # "sources": turn_sources
            }

        return json.dumps(result, ensure_ascii=False)

kernel = sk.Kernel()
tools = ContextTools()
kernel.add_plugin(tools, "context_tools")
