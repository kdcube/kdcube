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
# ---- Working set from context.json ----
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_url,
    normalize_sources_any,
    sids_in_text,
)

log = logging.getLogger(__name__)

# Slot leaves we allow agents to read
_ALLOWED_SLOT_LEAVES = {
    "name", "type", "description", "content_guidance", "summary", "gaps", "draft", "sources_used",
    "text", "format", "path", "mime", "filename"
}

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
    str_path = os.environ.get("CTX_PATH")
    if str_path:
        p = pathlib.Path(str_path)
    else:
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

def _flatten_slot(slot_name: str, raw: Any) -> Dict[str, Any]:
    """
    Return a flattened slot object that matches the fetch_ctx doc:
      {name,type,description,text,format?,path?,mime?,filename?,sources_used?,summary?,gaps?,draft?,content_guidance?}

    Supports both:
      - current_turn slots (already mostly flat)
      - prior_turn deliverables wrapper: {slot, description, value:{...}}
    """
    if not isinstance(raw, dict):
        return {}

    # Case A: historical deliverables wrapper: {slot, description, value:{...}}
    v = raw.get("value")
    if isinstance(v, dict) and ("type" in v or "text" in v or "path" in v or "mime" in v):
        typ = v.get("type") or "inline"
        desc = (raw.get("description") or v.get("description") or "").strip()

        out: Dict[str, Any] = {
            "name": slot_name,
            "type": typ,
            "description": desc,
            "text": _coerce_text(v.get("text") if v.get("text") is not None else v.get("value")),
        }

        # Optional fields (prefer outer if present)
        for k in ("summary", "gaps", "draft", "content_guidance"):
            if raw.get(k) is not None:
                out[k] = raw.get(k)
            elif v.get(k) is not None:
                out[k] = v.get(k)

        su = v.get("sources_used") if v.get("sources_used") is not None else raw.get("sources_used")
        if su:
            out["sources_used"] = su

        if out["type"] == "file":
            out["mime"] = v.get("mime")
            out["path"] = (v.get("path") or "").strip()
            out["filename"] = (v.get("filename") or (os.path.basename(out["path"]) if out["path"] else ""))
        else:
            out["format"] = (v.get("format") or "text")

        # keep only doc-advertised leaves
        return {k: out[k] for k in out.keys() if k in _ALLOWED_SLOT_LEAVES}

    # Case B: current_turn slot already flat-ish
    typ2 = raw.get("type") or "inline"
    desc2 = (raw.get("description") or raw.get("desc") or "").strip()

    out2: Dict[str, Any] = {
        "name": slot_name,
        "type": typ2,
        "description": desc2,
        "text": _coerce_text(raw.get("text") if raw.get("text") is not None else raw.get("value")),
    }

    for k in ("summary", "gaps", "draft", "content_guidance"):
        if raw.get(k) is not None:
            out2[k] = raw.get(k)

    if raw.get("sources_used"):
        out2["sources_used"] = raw.get("sources_used")

    if out2["type"] == "file":
        out2["mime"] = raw.get("mime")
        out2["path"] = (raw.get("path") or "").strip()
        out2["filename"] = (raw.get("filename") or (os.path.basename(out2["path"]) if out2["path"] else ""))
    else:
        out2["format"] = (raw.get("format") or "text")

    return {k: out2[k] for k in out2.keys() if k in _ALLOWED_SLOT_LEAVES}

def _flatten_slots_from_turn(turn_blob: Any) -> Dict[str, Any]:
    if not isinstance(turn_blob, dict):
        return {}

    raw_slots = (turn_blob.get("slots") or turn_blob.get("deliverables") or {}) or {}
    out: Dict[str, Any] = {}

    # normal case: dict of slots
    if isinstance(raw_slots, dict):
        for slot_name, raw in raw_slots.items():
            flat = _flatten_slot(str(slot_name), raw)
            if flat:
                out[str(slot_name)] = flat
        return out

    # defensive: some contexts store deliverables as list
    if isinstance(raw_slots, list):
        for raw in raw_slots:
            if not isinstance(raw, dict):
                continue
            slot_name = raw.get("slot") or raw.get("name")
            if not slot_name:
                continue
            flat = _flatten_slot(str(slot_name), raw)
            if flat:
                out[str(slot_name)] = flat

    return out

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
                "• Use this BEFORE inserting new citations into any slot text; keep SIDs stable."
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
                "    'user':str,           # user message on the turn. Always in markdown. \n"
                "    'assistant':str,      # assistant message on the turn. Always in markdown. \n"
                "    'solver_failure':str | null,  # non-empty if the solver experienced failure on this turn. \n"
                "    'deliverables': {\n"
                "      '<slot_name>': {\n"
                "        'type': 'file' | 'inline',\n"
                "        'text': str,              # ALWAYS present; authoritative text representation\n"
                "        'summary'?: str,          # Optional inventorization (structural and semantic) summary of the slot contents\n"
                "        'gaps'?: str,             # Optional gaps (structural and semantic) in the artifact contents\n"
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
                "5. For editing bases when solver failed for the relevant turn (or didn’t run): result[tid]['assistant'] is authoritative (what the user saw).\n"                
                "6. If the user's message needed, get it via result[turn_id]['user'].\n"
                "7. If assistant message needed, get it via result[turn_id]['assistant'].\n"
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
                str | list,
                "Array of turn_ids to fetch, i.e.: [\"turn_1760743886365_abcdef\", \"turn_1760743886365_abcdeg\"]",
            ],
    ) -> Annotated[
        dict,
        "Object mapping turn_id → {ts, program_log, deliverables}",
    ]:
        try:
            # Handle both list and JSON string
            if isinstance(turn_ids, list):
                ids = turn_ids
            else:
                ids = json.loads(turn_ids)

            # Ensure it's a list
            if not isinstance(ids, list):
                ids = [ids]
        except Exception as e:
            log.error(f"Failed to parse turn_ids: {turn_ids}, error: {e}")
            return {"error": "Invalid turn_ids format; expected array or list"}

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
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import reconcile_citations_for_context

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
                summary = artifact.get("summary")
                gaps = artifact.get("gaps")
                if summary:
                    slot_data["summary"] = summary
                if gaps:
                    slot_data["gaps"] = gaps

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
                "turn_log": turn_log,
                "solver_failure": solver_failure,
                "program_log": {"text": pl_text, "format": pl_fmt} if pl_text else None,
                "deliverables": deliverables_dict,
                "user": user_msg or "",
                "assistant": assistant or ""
                # "sources": turn_sources
            }

        return result

    @kernel_function(
        name="fetch_ctx",
        description=(
                "Fetch a single artifact from context.json by dot-path.\n"
                "Return shape is ALWAYS a dict:\n"
                "  {\"ret\": <value|null>, \"err\": <null|{code,message,details?}>}\n"
                "\n"
                "IMPORTANT NAMESPACES\n"
                "• Slots are contract deliverables for a turn (final outputs).\n"
                "  Paths: <turn_id>.slots...\n"
                "• artifacts are intermediate artifacts produced by tool calls in the CURRENT TURN ONLY.\n"
                "  Paths: current_turn.artifacts...\n"
                "\n"
                "PATH FORMAT\n"
                "• Root: <turn_id> or current_turn -> returns turn view {turn_id,user,assistant,slots,artifacts}\n"
                "• Messages: <turn_id>.user | <turn_id>.assistant | current_turn.user | current_turn.assistant\n"
                "Forgiving behavior: if the agent accidentally appends segments (e.g. turn_12.assistant.whatever),\n"
                "the tool will ignore extra segments and treat it as turn_12.assistant.\n"
                "• Slots (deliverables): <turn_id>.slots | <turn_id>.slots.<slot> | <turn_id>.slots.<slot>.<leaf>\n"
                "Allowed slot leaves: name, type, description, content_guidance, summary, gaps, draft, sources_used, text, format, path, mime, filename, .\n"
                "Hard rule: Slots NEVER allow '.value' ('.value' or '.value.*' is an error).\n"
                "Forgiving behavior: if extra segments appear after a valid slot leaf, they are ignored.\n"                
                "• Tool results (CURRENT TURN ONLY): current_turn.artifacts | current_turn.artifacts.<artifact_id> |\n"
                "  current_turn.artifacts.<artifact_id>.<leaf> | current_turn.artifacts.<artifact_id>.value.<subkeys>\n"
                "\n"
                "Common tool_result leaves:\n"
                "  tool_id, value, summary, sources_used, timestamp, inputs, call_record, artifact_type, content_lineage, error\n"
                "\n"
                "Structured traversal rule:\n"
                "  The high-level shape of the value reflects the shape of the tool return value which produced it"
                "  You may traverse inside tool_result.value using dotted keys.\n"
                "  If value is a JSON string, it is auto-parsed to allow traversal. If you fetch the enclosing object, you will have to manage the traversal on your own\n"                
                "HARD RULES\n"
                "• 'literal:' anywhere in path => error\n"
                "• turn_id normalization:\n"
                "  - accepts shorthand without 'turn_' if it exists (e.g. '12' -> 'turn_12' if present)\n"
                "  - accepts the canonical current turn id (e.g. 'turn_999') and normalizes it to 'current_turn'\n"
                "• historical '<turn_id>.artifacts.*' is not stored.\n"
                "  Forgiving behavior: historical artifacts paths are rewritten to 'current_turn.artifacts.*'.\n"
                "• invalid slot leaf => error\n"
                "\n"
                "WHAT YOU RECEIVE (TYPICAL TURN VIEW)\n"
                "When you call with a ROOT path (just <turn_id> or 'current_turn'), you receive:\n"
                "{\n"
                "  \"turn_id\": \"turn_123\" | \"current_turn\",\n"
                "  \"user\": <string|dict|null>,\n"
                "  \"assistant\": <string|dict|null>,\n"
                "  \"slots\": {\n"
                "    \"<slot_name>\": <slot_object>,\n"
                "    ...\n"
                "  },\n"
                "  \"artifacts\": {\n"
                "    \"<artifact_id>\": <tool_result_object>,\n"
                "    ...\n"
                "  }\n"
                "}\n"
                "\n"
                "Notes:\n"
                "• For historical turns: artifacts is always {} (not stored historically).\n"
                "• For current_turn: artifacts can be non-empty.\n"
                "\n"
                "TYPICAL SLOT OBJECT SHAPE (in turn_view.slots[slot_name])\n"
                "Slots are produced by solver mapping and look like one of these:\n"
                "\n"
                "INLINE SLOT:\n"
                "{\n"
                "  \"type\": \"inline\",\n"
                "  \"format\": \"text\"|\"markdown\"|\"json\"|..., \n"
                "  \"text\": \"...\",                 # authoritative text representation\n"
                "  \"description\": \"...\",\n"
                "  \"sources_used\": [ {\"sid\":..., \"url\":..., \"title\":..., \"body\"?:...}, ... ],\n"
                "  \"summary\"?: \"...\",\n"
                "  \"gaps\"?: \"...\",\n"
                "  \"draft\"?: true\n"
                "}\n"
                "\n"
                "FILE SLOT:\n"
                "{\n"
                "  \"type\": \"file\",\n"
                "  \"mime\": \"application/pdf\"|\"text/plain\"|..., \n"
                "  \"path\": \"...\",                 # output path (often OUTPUT_DIR-relative)\n"
                "  \"filename\"?: \"...\",\n"
                "  \"text\": \"...\",                 # authoritative surrogate text\n"
                "  \"description\": \"...\",\n"
                "  \"sources_used\": [ {\"sid\":..., \"url\":..., \"title\":..., \"body\"?:...}, ... ],\n"
                "  \"summary\"?: \"...\",\n"
                "  \"gaps\"?: \"...\",\n"
                "  \"draft\"?: true\n"
                "}\n"
                "\n"
                "TYPICAL TOOL RESULT OBJECT SHAPE (in current_turn.artifacts[artifact_id])\n"
                "{\n"
                "  \"tool_id\": \"...\",\n"
                "  \"value\": <any - the shape defined by tool produced it, any nested shape will be clear from playbook>,\n"
                "  \"summary\": \"...\",\n"
                "  \"sources_used\": [ ... ],\n"
                "  \"timestamp\": <float>,\n"
                "  \"inputs\": { ... },\n"
                "  \"call_record\": {\"rel\":..., \"abs\":...},\n"
                "  \"artifact_type\": \"...\"|null,\n"
                "  \"content_lineage\"?: [\"current_turn.artifacts.X\", ...],\n"
                "  \"error\"?: { ... }\n"
                "}\n"
                "\n"
                "HOW TO OPERATE\n"
                "1) If you want to borwse the turn view in code, you can fetch entire turn:\n"
                "   • fetch_ctx(\"current_turn\")\n"
                "   • fetch_ctx(\"turn_123\")\n"
                "\n"
                "2) To retrieve the individual slots (deliverables) or their attributes:\n"
                "   • fetch_ctx(\"turn_123.slots\")\n"
                "   • fetch_ctx(\"turn_123.slots.report_md\")\n"
                "   • fetch_ctx(\"turn_123.slots.report_md.text\")\n"
                "\n"
                "3) For current-turn intermediate artifacts and their results. Usually you are interested in \n"
                " tool result <artifact_id>.value.content for non-file results and <artifact_id>.value.text for files (might contain surrogate text) for deep content connection or"
                " <artifact_id>.summary for structural / semantic summary of the result content:\n"
                "   • fetch_ctx(\"current_turn.artifacts\")\n"
                "   • fetch_ctx(\"current_turn.artifacts.web_1\")\n"
                "   • fetch_ctx(\"current_turn.artifacts.web_1.value.content\")\n"
                "\n"
                "4) Use messages if you need what the user/assistant said:\n"
                "   • fetch_ctx(\"turn_123.user\")\n"
                "   • fetch_ctx(\"turn_123.assistant\")\n"
                "\n"
                "ERRORS\n"
                "If something cannot be resolved, err is non-null, e.g.:\n"
                "{\n"
                "  \"ret\": null,\n"
                "  \"err\": {\"code\": \"not_found\", \"message\": \"Path not found\", \"details\": {\"normalized_path\": \"...\"}}\n"
                "}\n"                
                "WHEN TO USE\n"
                "• After reading the program playbook, when you need to re-use artifacts.\n"
                "• When you know (or suspect) a turn_id / slot name / tool_result id and want to inspect it or connect this data to your flow.\n"
                "\n"

        ),
    )
    async def fetch_ctx(
            self,
            path: Annotated[str, "Dot-path to an existing artifact, or a <turn_id> root."],
    ) -> Annotated[dict, "dict: {ret, err}. Dict. Not a JSON string!"]:

        def _err(code: str, msg: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            e = {"code": code, "message": msg}
            if details:
                e["details"] = details
            return e

        def _extract_user(turn_blob: Any) -> Any:
            if isinstance(turn_blob, dict):
                u = turn_blob.get("user")
                if isinstance(u, dict):
                    return u.get("prompt") if u.get("prompt") is not None else u.get("text")
                return u
            return None

        def _extract_assistant(turn_blob: Any) -> Any:
            if isinstance(turn_blob, dict):
                return turn_blob.get("assistant")
            return None

        def _extract_slots(turn_blob: Any) -> Dict[str, Any]:
            # Always return doc-shape flattened slots (no nested 'value')
            return _flatten_slots_from_turn(turn_blob)

        def _extract_artifacts(turn_blob: Any) -> Dict[str, Any]:
            if not isinstance(turn_blob, dict):
                return {}
            return (turn_blob.get("artifacts") or {}) or {}

        def _build_turn_view(turn_id: str, turn_blob: Dict[str, Any], *, include_artifacts: bool) -> Dict[str, Any]:
            return {
                "turn_id": turn_id,
                "user": _extract_user(turn_blob),
                "assistant": _extract_assistant(turn_blob),
                "slots": _extract_slots(turn_blob),
                "artifacts": _extract_artifacts(turn_blob) if include_artifacts else {}
            }

        try:
            if not isinstance(path, str) or not path.strip():
                return {"ret": None, "err": _err("invalid_path_empty", "path must be a non-empty string")}

            p = path.strip()
            if "literal:" in p:
                return {"ret": None, "err": _err("invalid_path_literal", "Paths must reference existing artifacts; 'literal:' is forbidden.")}

            ctx = _read_context() or {}
            prior = (ctx.get("prior_turns") or {})
            cur = (ctx.get("current_turn") or {})
            cur_id = (cur.get("turn_id") or "").strip()  # canonical id of current turn (may be "")

            def normalize_tid(seg0: str) -> Optional[str]:
                s = (seg0 or "").strip()
                if s == "current_turn":
                    return "current_turn"
                if s in prior:
                    return s
                if not s.startswith("turn_"):
                    cand = f"turn_{s}"
                    if cand in prior:
                        return cand
                # canonical current id can appear; normalize it to 'current_turn'
                if cur_id and s == cur_id:
                    return "current_turn"
                return None

            parts = [x for x in p.split(".") if x != ""]
            if not parts:
                return {"ret": None, "err": _err("invalid_path_empty", "path must be a non-empty dot-path")}

            tid = normalize_tid(parts[0])
            if tid is None:
                return {"ret": None, "err": _err("unknown_turn_id", f"Unknown turn_id '{parts[0]}'")}

            is_current = (tid == "current_turn")
            turn_blob = cur if is_current else (prior.get(tid) or {})
            turn_view = _build_turn_view(tid, turn_blob, include_artifacts=is_current)

            # ---- Root turn fetch ----
            if len(parts) == 1:
                return {"ret": turn_view, "err": None}

            # Normalize first segment in the working path
            parts[0] = tid

            # ---- Forgive message mistakes: turn.assistant.whatever -> turn.assistant ----
            if parts[1] in {"user", "assistant"} and len(parts) > 2:
                parts = parts[:2]

            # ---- Messages: current_turn.user/current_turn.assistant are NOT handled by resolve_path ----
            if parts[1] in {"user", "assistant"}:
                leaf = parts[1]
                if is_current:
                    if leaf == "user":
                        return {"ret": (turn_view.get("user")), "err": None}
                    if leaf == "assistant":
                        return {"ret": (turn_view.get("assistant")), "err": None}
                # prior messages: let resolve_path do it (it already knows turn.user / turn.assistant)
                normalized_path = ".".join(parts)
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
                rctx = ReactContext.load_from_dict(ctx)
                val, _owner = rctx.resolve_path(normalized_path, mode="full")
                if val is None:
                    return {"ret": None, "err": _err("not_found", "Path not found", {"normalized_path": normalized_path})}
                return {"ret": val, "err": None}

            # ---- Tool results: must be current turn; forgive historical prefix by rewriting to current_turn ----
            if parts[1] == "artifacts" and not is_current:
                parts[0] = "current_turn"
                is_current = True  # effective for this resolution

            # ---- Slots / artifacts object reads (resolve_path is leaf-only) ----
            if parts[1] == "slots":
                # <turn>.slots
                if len(parts) == 2:
                    return {"ret": turn_view.get("slots") or {}, "err": None}
                # <turn>.slots.<slot>
                if len(parts) == 3:
                    slot_obj = (turn_view.get("slots") or {}).get(parts[2])
                    if slot_obj is None:
                        return {"ret": None, "err": _err("not_found", "Slot not found", {"slot": parts[2], "turn": parts[0]})}
                    return {"ret": slot_obj, "err": None}

                # hard rule: forbid '.value' anywhere under slots
                i = 1  # index of "slots"
                if "value" in parts[i + 1:]:
                    return {"ret": None, "err": _err("invalid_slot_value_access", "Slots cannot be accessed via '.value' or '.value.*'.")}

                # <turn>.slots.<slot>.<leaf>[.*]
                if len(parts) >= 4:
                    slot_name = parts[2]
                    leaf = parts[3]
                    if leaf not in _ALLOWED_SLOT_LEAVES:
                        return {"ret": None, "err": _err("invalid_slot_leaf", f"Slot leaf '{leaf}' is not allowed.", {"allowed": sorted(_ALLOWED_SLOT_LEAVES)})}
                    # forgive deeper-than-leaf for slots
                    if len(parts) > 4:
                        parts = parts[:4]


                    slot_obj = (turn_view.get("slots") or {}).get(slot_name)
                    if slot_obj is None:
                        return {"ret": None, "err": _err("not_found", "Slot not found", {"slot": slot_name, "turn": parts[0]})}

                    if leaf not in slot_obj:
                        return {"ret": None, "err": _err("not_found", "Slot leaf not found", {"slot": slot_name, "leaf": leaf, "turn": parts[0]})}

                    return {"ret": slot_obj.get(leaf), "err": None}

            if parts[1] == "artifacts":
                # current_turn.artifacts
                if len(parts) == 2:
                    from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
                    rctx = ReactContext().load_from_dict(ctx)
                    return {"ret": rctx.artifacts or {}, "err": None}
                # current_turn.artifacts.<id>
                if len(parts) == 3:
                    from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
                    rctx = ReactContext().load_from_dict(ctx)
                    obj = (rctx.artifacts or {}).get(parts[2])
                    if obj is None:
                        return {"ret": None, "err": _err("not_found", "Tool result not found", {"artifact_id": parts[2]})}
                    return {"ret": obj, "err": None}
                # forgive ".summary.*" -> ".summary"
                if len(parts) > 4 and parts[3] == "summary":
                    parts = parts[:4]

            normalized_path = ".".join(parts)

            # ---- Resolve leaf via ReactContext.resolve_path (full, no truncation) ----
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
            rctx = ReactContext.load_from_dict(ctx)
            val, _owner = rctx.resolve_path(normalized_path, mode="full")

            if val is None:
                return {"ret": None, "err": _err("not_found", "Path not found", {"normalized_path": normalized_path})}

            return {"ret": val, "err": None}

        except Exception as e:
            log.exception("fetch_ctx failed")
            return {"ret": None, "err": _err("tool_failure", f"fetch_ctx failed: {e}")}

kernel = sk.Kernel()
tools = ContextTools()
kernel.add_plugin(tools, "context_tools")
