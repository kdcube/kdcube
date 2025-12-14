# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/retrieval.py

from __future__ import annotations

import copy, logging
import uuid as _uuid
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient, FINGERPRINT_KIND
from kdcube_ai_app.apps.chat.sdk.tools.tools_insights import CITABLE_TOOL_IDS
from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_citation_item,
    normalize_url, _rewrite_md_citation_tokens,
)

PROJECT_LOG_SLOTS = { "project_log" }

def _latest_merge_sources_row(out_items: list[dict]) -> list[dict]:
    """Return the output list from the latest ctx_tools.merge_sources (if any)."""
    import re
    latest_idx, latest_row = -1, None
    for r in (out_items or []):
        if not (isinstance(r, dict) and r.get("type") == "inline" and r.get("citable") is True):
            continue
        if (r.get("tool_id") or "") != "ctx_tools.merge_sources":
            continue
        rid = str(r.get("resource_id") or "")
        m = re.search(r":(\d+)$", rid)
        idx = int(m.group(1)) if m else -1
        if idx > latest_idx:
            latest_idx, latest_row = idx, r
    if latest_row and isinstance(latest_row.get("output"), list):
        return latest_row["output"]
    return []

def reconcile_citations_for_context(history: list[dict], *, max_sources: int = 60, rewrite_tokens_in_place: bool = True):
    """
    Input: output of _build_program_history_from_turn_ids (a list of {exec_id: {...}} sorted newest first).
    Output:
      {
        "canonical_sources": [ { sid, url, title, text } ... ],
        "sid_maps": { run_id -> { old_sid -> sid } }
      }
    Side-effect (optional): rewrites [[S:n]] in project_log AND all deliverables with sources_used,
    and updates web_links_citations per turn to match canonical sources.
    """
    # 1-4: Build canonical sources and sid_maps (existing logic)
    flat: list[tuple[str, dict, dict]] = []
    per_run_rows: dict[str, list[dict]] = {}
    per_run_out_items: dict[str, list[dict]] = {}

    for rec in (history or []):
        run_id, meta = next(iter(rec.items()))
        per_run_out_items[run_id] = meta.get("out_items") or []

    # Synthesize out_items from web_links_citations for extraction
    for rec in (history or []):
        run_id, meta = next(iter(rec.items()))
        citations_list = ((meta.get("web_links_citations") or {}).get("items") or [])
        faux_out_item = {
            "type": "inline",
            "citable": True,
            "tool_id": "ctx_tools.merge_sources",
            "resource_id": f"tool:ctx_tools.merge_sources:0",
            "output": citations_list
        }
        out_items = [faux_out_item]
        per_run_out_items[run_id] = out_items

        ext = _extract_citable_items_from_out_items(out_items)
        per_run_rows[run_id] = ext
        for it in ext:
            flat.append((run_id, meta, it))

    # 2) Determine base ordering
    latest_run_id = next(iter(history[0].keys())) if history else None
    base_order_urls: list[str] = []
    if latest_run_id:
        base_merge = _latest_merge_sources_row(per_run_out_items.get(latest_run_id, []))
        if base_merge:
            base_order_urls = [normalize_url(c.get("url") or "") for c in base_merge if isinstance(c, dict)]
            base_order_urls = [u for u in base_order_urls if u]

    # 3) Build canonical map
    by_url: dict[str, dict] = {}
    ordered_urls: list[str] = []

    for run_id, meta, it in flat:
        u = it["url"]
        if u not in by_url:
            by_url[u] = {
                "url": u,
                "title": it["title"],
                "text": it.get("text") or it.get("body") or ""
            }
            for k in CITATION_OPTIONAL_ATTRS:
                if it.get(k):
                    by_url[u][k] = it[k]
            ordered_urls.append(u)

    ordered_urls = ordered_urls[:max_sources]
    global_sid_of_url: dict[str,int] = {u: i+1 for i,u in enumerate(ordered_urls)}

    canonical_sources = []
    for u in ordered_urls:
        src = by_url[u]
        row = {
            "sid": global_sid_of_url[u],
            "url": u,
            "title": src.get("title") or u,
            "text": src.get("text") or src.get("body") or "",
        }
        for k in CITATION_OPTIONAL_ATTRS:
            if src.get(k):
                row[k] = src[k]
        canonical_sources.append(row)

    # 4) Build per-run sid maps (old → global)
    sid_maps: dict[str, dict[int,int]] = {}
    for run_id, rows in per_run_rows.items():
        m: dict[int,int] = {}
        for it in rows:
            u = it["url"]
            old_sid = it.get("sid")
            if old_sid is None:
                continue
            try:
                old_sid = int(old_sid)
            except Exception:
                continue
            new_sid = global_sid_of_url.get(u)
            if new_sid:
                m[old_sid] = new_sid
        if m:
            sid_maps[run_id] = m

    # 5) Rewrite tokens and update web_links_citations
    if rewrite_tokens_in_place:
        from kdcube_ai_app.apps.chat.sdk.tools.citations import sids_in_text

        # Quick lookup by SID
        canonical_by_sid = {s["sid"]: s for s in canonical_sources}

        for rec in history:
            run_id, meta = next(iter(rec.items()))
            sid_map = sid_maps.get(run_id, {})
            if not sid_map:
                continue

            # Track SIDs used in this turn
            used_sids_in_turn = set()

            # Rewrite project_log
            if "project_log" in meta and isinstance(meta["project_log"], dict):
                val = meta["project_log"].get("text") or meta["project_log"].get("value") or ""
                if val:
                    new_val = _rewrite_md_citation_tokens(val, sid_map)
                    if "text" in meta["project_log"]:
                        meta["project_log"]["text"] = new_val
                    else:
                        meta["project_log"]["value"] = new_val
                    # Collect SIDs from project_log
                    used_sids_in_turn.update(sids_in_text(new_val))

            # Rewrite all deliverables
            deliverables = meta.get("deliverables") or []
            for d in deliverables:
                if not isinstance(d, dict):
                    continue

                artifact = d.get("value")
                if not isinstance(artifact, dict):
                    continue

                # Check if this deliverable uses sources
                sources_used = artifact.get("sources_used")
                if not sources_used:
                    continue

                # Find text to rewrite
                text = artifact.get("text") or ""
                if text:
                    new_text = _rewrite_md_citation_tokens(text, sid_map)
                    artifact["text"] = new_text
                    used_sids_in_turn.update(sids_in_text(new_text))

            # ===== UPDATE web_links_citations =====
            # Replace with canonical sources that are actually used in this turn
            if used_sids_in_turn:
                turn_sources = [
                    canonical_by_sid[sid]
                    for sid in sorted(used_sids_in_turn)
                    if sid in canonical_by_sid
                ]
                meta["web_links_citations"] = {"items": turn_sources}
            else:
                # No citations used in this turn
                meta["web_links_citations"] = {"items": []}

    return {
        "canonical_sources": canonical_sources,
        "sid_maps": sid_maps,
    }

def _pick_project_log_slot(d_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    matches = [ d for d in d_items if d.get("slot") in PROJECT_LOG_SLOTS ]
    for m in matches:
        v = m.get("value") or {}
        txt = v.get("value") or v.get("value_preview") or ""
        if not txt:
            continue
        fmt  = (v.get("format") or "").lower()
        return { "slot": m, "format": fmt or "markdown", "value": txt }

def _extract_citable_items_from_out_items(out_items: list[dict]) -> list[dict]:
    rows = []
    for r in (out_items or []):
        if not isinstance(r, dict):
            continue
        if r.get("type") != "inline" or not bool(r.get("citable")):
            continue
        tid = (r.get("tool_id") or "").lower()
        if not (tid in CITABLE_TOOL_IDS or tid.endswith(".kb_search") or tid.endswith(".kb_search_advanced")):
            continue
        out = r.get("output")
        pack = out if isinstance(out, list) else ([out] if isinstance(out, dict) else [])
        for c in pack:
            if isinstance(c, dict):
                url = normalize_url(str(c.get("url") or c.get("href") or ""))
                if not url:
                    continue
                item = {
                    "url": url,
                    "title": c.get("title") or c.get("description") or url,
                    "text": c.get("text") or c.get("body") or c.get("content") or "",
                    "sid": c.get("sid"),
                    "_tool_id": r.get("tool_id") or "",
                    "_resource_id": r.get("resource_id") or "",
                }
                for k in CITATION_OPTIONAL_ATTRS:
                    if c.get(k):
                        item[k] = c[k]
                rows.append(item)
    return rows


def transform_codegen_to_turnid(data):
    transformed = []

    for item in data:
        # Check if this is the current_turn item (keep as is)
        if "current_turn" in item:
            transformed.append(item)
            continue

        # For other items, replace codegen key with turn_id
        new_item = {}
        for key, value in item.items():
            # key is something like "cg-e0f6a62a"
            if isinstance(value, dict) and "turn_id" in value:
                # Use turn_id as the new key
                turn_id = value["turn_id"]
                new_item[turn_id] = value
            else:
                # If no turn_id found, keep original (shouldn't happen)
                new_item[key] = value

        transformed.append(new_item)

    return transformed

async def build_program_history_from_turn_ids(self, *,
                                              user_id: str,
                                              turn_ids: List[str],
                                              conversation_id: Optional[str] = None,
                                              scope: str = "track",
                                              days: int = 365) -> List[Dict[str, Any]]:
    """
    For each turn_id, materialize: program presentation (if present), project_canvas / project_log
    from deliverables, and citations tied to the run. Returns the same shape as _build_program_history().
    """
    if not self.context_rag_client:
        return []
    out = []
    seen_runs = set()

    for tid in turn_ids:
        mat = await self.context_rag_client.materialize_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=tid,
            scope=scope,
            days=days,
            with_payload=True
        )

        # Unpack rich envelopes (payload + ts + tags)
        turn_log_env = mat.get("turn_log") or {}
        turn_log = ((turn_log_env or {}).get("payload") or {}).get("payload") or {}

        dels_env = mat.get("deliverables") or {}
        assistant_env = mat.get("assistant") or {}
        user_env = mat.get("user") or {}
        citables_env = mat.get("citables") or {}
        files_env = mat.get("files") or {}

        dels = ((dels_env or {}).get("payload") or {}).get("payload") or {}
        citables = ((citables_env or {}).get("payload") or {}).get("payload") or {}
        assistant = ((((assistant_env or {}).get("payload") or {}).get("payload") or {})).get("completion") or ""
        user = ((user_env or {}).get("payload") or {}).get("payload") or {}
        files = (((files_env or {}).get("payload") or {}).get("payload") or {}).get("files_by_slot", {})

        d_items = list((dels or {}).get("items") or [])
        for de in d_items:
            de = de or {}
            artifact = de.get("value") or {}
            if artifact.get("type") == "file":
                slot_name = de.get("slot") or "default"
                file = files.get(slot_name) or {}
                artifact["path"] = file.get("path") or ""
                artifact["filename"] = file.get("filename")
                print()

        cite_items =  list((citables or {}).get("items") or [])
        round_reason = (dels or {}).get("round_reasoning") or ""

        # Prefer assistant ts, else user ts
        ts_val = assistant_env.get("ts") or user_env.get("ts") or ""

        # codegen_run_id priority: deliverables.payload -> tags -> presentation markdown
        # REMOVE AMBIGUOUS SIGNAL "run id vs turn id"
        codegen_run_id = (dels or {}).get("execution_id") or f"cg-{_uuid.uuid4().hex[:8]}"

        # Citations bundle (if we have run id)
        cites = {"items": cite_items}

        # Extract canvas/log from deliverables items
        # canvas = _pick_canvas_slot(d_items) or {}
        project_log = _pick_project_log_slot(d_items) or {}
        materialized_canvas = {}
        try:
            glue = project_log.get("value","") if project_log else ""
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.presentation import _materialize_glue_canvas
            mat = _materialize_glue_canvas(glue, d_items)
            if mat and mat != glue:
                materialized_canvas = {"format": "markdown", "text": mat}
        except Exception as ex:
            materialized_canvas = {}

        exec_id = codegen_run_id
        if exec_id in seen_runs:
            continue
        seen_runs.add(exec_id)

        solver_result = turn_log.get("solver_result") or {}
        execution = solver_result.get("execution") or {}
        program_presentation = solver_result.get("program_presentation")
        solver_interpretation_instruction = solver_result.get("interpretation_instruction") or ""

        solver_failure_md = (execution.get("failure_presentation") or {}).get("markdown")

        ret = {
            **({"program_presentation": program_presentation} if program_presentation else {}),
            **({"solver_interpretation_instruction": solver_interpretation_instruction} if solver_interpretation_instruction else {}),
            **({"project_log": {"format": project_log.get("format","markdown"), "text": project_log.get("value","")}} if project_log else {}),
            **({"turn_log": turn_log} if turn_log else {}),
            **({"project_log_materialized": materialized_canvas} if materialized_canvas else {}),
            **({"solver_failure": solver_failure_md} if solver_failure_md else {}),
            **({"web_links_citations": {"items": [normalize_citation_item(c) for c in cites["items"] if normalize_citation_item(c)]}}),
            **{"media": []},
            "ts": ts_val,
            **({"codegen_run_id": codegen_run_id} if codegen_run_id else {}),
            "turn_id": tid,
            **({"round_reasoning": round_reason} if round_reason else {}),
            "assistant": assistant,
            "user": user,
            "deliverables": d_items if d_items else []
        }
        out.append({exec_id: ret})

    # newest first
    out.sort(key=lambda e: next(iter(e.values())).get("ts","") or "", reverse=True)
    return out

# ---------------------------------------------------------------------------
# Materialization for prior pairs (with artifacts and view policy)
# ---------------------------------------------------------------------------

def _dedup_citations(citations: list[dict]) -> list[dict]:
    seen_sids: set[int] = set()
    seen_urls: set[str] = set()
    result: list[dict] = []

    for c in citations:
        if not isinstance(c, dict):
            continue

        sid = c.get("sid")
        url = (c.get("url") or "").strip()

        key_is_sid = isinstance(sid, int)
        if key_is_sid:
            if sid in seen_sids:
                continue
            seen_sids.add(sid)
        elif url:
            # optionally normalize url here if you want:
            # url_norm = normalize_url(url)
            # if url_norm in seen_urls: continue
            # seen_urls.add(url_norm)
            if url in seen_urls:
                continue
            seen_urls.add(url)

        result.append(c)

    return result

def merge_pairs_chronological(materialized: list[dict], synthetic: list[dict]) -> list[dict]:
    """
    Merge two pair lists and order by time (oldest->newest).
    If the same turn_id exists in both, keep the materialized version and drop synthetic.
    Stable order among items with identical timestamps is preserved (materialized first).
    """
    mat_by_tid = {p.get("turn_id"): p for p in materialized if p.get("turn_id")}
    merged: list[dict] = []

    # 1) put all materialized first (we’ll sort later; this preserves stability)
    merged.extend(materialized or [])

    # 2) append synthetic that do not collide
    for sp in synthetic or []:
        tid = sp.get("turn_id")
        if tid and tid in mat_by_tid:
            continue
        merged.append(sp)

    def _ts_of_pair(p: dict) -> str:
        # prefer assistant ts then user ts; both are set to the same ts for synthetic
        return (p.get("assistant") or {}).get("ts") or (p.get("user") or {}).get("ts") or ""

    merged.sort(key=_ts_of_pair)
    return merged

# ---------------------------------------------------------------------------
# Fingerprints loading
# ---------------------------------------------------------------------------

async def load_latest_fingerprint_for_turn(
        ctx_client: ContextRAGClient,
        *, user_id: str, conversation_id: str, turn_id: str
) -> Optional[dict]:
    try:
        hit = await ctx_client.recent(
            kinds=(FINGERPRINT_KIND,),
            roles=("artifact",),
            all_tags=[f"turn:{turn_id}"],
            limit=1,
            days=365,
            user_id=user_id,
            conversation_id=conversation_id,
            track_id=None,
            with_payload=True,
        )
        item = next(iter((hit or {}).get("items") or []), None)
        if not item:
            return None
        doc = (item.get("doc") or {})
        return doc.get("json") or doc.get("payload") or doc.get("content_json") or {}
    except Exception:
        return None

async def load_fingerprints_for_turns(
        *,
        ctx_client: ContextRAGClient,
        user_id: str,
        conversation_id: str,
        track_id: Optional[str],
        turn_ids: List[str],
        days: int = 365,
) -> Dict[str, Dict[str, Any]]:
    """
    For each turn_id, fetch the latest fingerprint artifact (kind=artifact:turn.fingerprint.v1).
    Returns a dict turn_id -> fingerprint_json
    """
    out: Dict[str, Dict[str, Any]] = {}
    for tid in [t for t in turn_ids or [] if t]:
        try:
            hit = await ctx_client.recent(
                kinds=(FINGERPRINT_KIND,),
                roles=("artifact",),
                all_tags=[f"turn:{tid}"],
                limit=1,
                days=days,
                user_id=user_id,
                conversation_id=conversation_id,
                track_id=track_id,
                with_payload=True,
            )
            item = next(iter((hit or {}).get("items") or []), {})
            doc = (item.get("doc") or {})
            fp = doc.get("json") or doc.get("payload") or doc.get("content_json") or {}
            if isinstance(fp, dict):
                out[tid] = fp
        except Exception:
            # non-fatal
            continue
    return out