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
    normalize_sources_any,
    dedupe_sources_by_url,
    normalize_url, _rewrite_md_citation_tokens,
)
from kdcube_ai_app.apps.chat.sdk.util import ts_key

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
        "sources_pool": [ { sid, url, title, text } ... ],
        "sid_maps": { run_id -> { old_sid -> sid } }
      }
    Side-effect (optional): rewrites [[S:n]] in project_log AND all deliverables with sources_used,
    and updates per-turn sources_pool-derived citations.
    """
    def _meta_sources_pool(meta: dict) -> list[dict]:
        pool = meta.get("sources_pool")
        if not pool and isinstance(meta.get("turn_log"), dict):
            pool = meta["turn_log"].get("sources_pool")
        return normalize_sources_any(pool)

    def _collect_used_sids(meta: dict) -> set[int]:
        from kdcube_ai_app.apps.chat.sdk.tools.citations import sids_in_text
        used: set[int] = set()

        # project log
        proj = meta.get("project_log") or {}
        if isinstance(proj, dict):
            txt = proj.get("text") or proj.get("value") or ""
            if txt:
                used.update(sids_in_text(txt))

        # deliverables
        deliverables = meta.get("deliverables") or []
        for d in deliverables:
            if not isinstance(d, dict):
                continue
            artifact = d.get("value")
            if not isinstance(artifact, dict):
                continue
            for rec in (artifact.get("sources_used") or []):
                if isinstance(rec, (int, float)):
                    used.add(int(rec))
                elif isinstance(rec, dict):
                    sid = rec.get("sid")
                    if isinstance(sid, (int, float)):
                        used.add(int(sid))
            for sid in (artifact.get("sources_used_sids") or []):
                if isinstance(sid, (int, float)):
                    used.add(int(sid))
            text = artifact.get("text") or ""
            if isinstance(text, str) and text.strip():
                used.update(sids_in_text(text))
        return used

    collected: list[dict] = []
    per_turn_used: dict[str, set[int]] = {}

    for rec in (history or []):
        run_id, meta = next(iter(rec.items()))
        pool_rows = _meta_sources_pool(meta)
        used_sids = _collect_used_sids(meta)
        per_turn_used[run_id] = used_sids
        if not pool_rows or not used_sids:
            continue
        for row in pool_rows:
            sid = row.get("sid")
            if isinstance(sid, int) and sid in used_sids:
                collected.append(row)

    # Deduplicate by URL while preserving existing SIDs
    merged = dedupe_sources_by_url([], collected)
    sources_pool = [
        {k: v for k, v in row.items() if k in ("sid", "url", "title", "text") or k in CITATION_OPTIONAL_ATTRS}
        for row in merged if isinstance(row.get("sid"), int) and row.get("url")
    ]
    sources_pool = sources_pool[:max_sources]
    canonical_by_sid = {s["sid"]: s for s in sources_pool}

    if rewrite_tokens_in_place:
        for rec in (history or []):
            run_id, meta = next(iter(rec.items()))
            used = per_turn_used.get(run_id) or set()
            if used:
                meta["sources_pool_used"] = [canonical_by_sid[sid] for sid in sorted(used) if sid in canonical_by_sid]
            else:
                meta["sources_pool_used"] = []

    return {
        "sources_pool": sources_pool,
        "sid_maps": {},
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
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import unwrap_payload

        turn_log_env = mat.get("turn_log") or {}
        turn_log = unwrap_payload((turn_log_env or {}).get("payload") or {})

        dels_env = mat.get("deliverables") or {}
        assistant_env = mat.get("assistant") or {}
        user_env = mat.get("user") or {}
        files_env = mat.get("files") or {}

        dels = unwrap_payload((dels_env or {}).get("payload") or {})
        files = (unwrap_payload((files_env or {}).get("payload") or {}).get("files_by_slot", {})) or {}

        user = (turn_log.get("user") if isinstance(turn_log, dict) else None) or {}
        assistant = (turn_log.get("assistant") if isinstance(turn_log, dict) else None) or {}
        if not user:
            user = unwrap_payload((user_env or {}).get("payload") or {})
        if not assistant:
            assistant = unwrap_payload((assistant_env or {}).get("payload") or {})

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

        round_reason = (dels or {}).get("round_reasoning") or ""

        # Prefer user ts, else assistant ts
        ts_val = user_env.get("ts") or assistant_env.get("ts") or (turn_log_env.get("ts") if isinstance(turn_log_env, dict) else "") or ""

        # codegen_run_id priority: deliverables.payload -> tags -> presentation markdown
        # REMOVE AMBIGUOUS SIGNAL "run id vs turn id"
        codegen_run_id = (dels or {}).get("execution_id") or f"cg-{_uuid.uuid4().hex[:8]}"


        # Extract canvas/log from deliverables items
        # canvas = _pick_canvas_slot(d_items) or {}
        project_log = _pick_project_log_slot(d_items) or {}
        materialized_canvas = {}
        try:
            glue = project_log.get("value","") if project_log else ""
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import _materialize_glue_canvas
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
        sources_pool = turn_log.get("sources_pool") or []
        execution = solver_result.get("execution") or {}
        program_presentation = solver_result.get("program_presentation")
        solver_interpretation_instruction = solver_result.get("interpretation_instruction") or ""

        solver_failure_md = (execution.get("failure_presentation") or {}).get("markdown")

        ret = {
            **({"program_presentation": program_presentation} if program_presentation else {}),
            **({"solver_interpretation_instruction": solver_interpretation_instruction} if solver_interpretation_instruction else {}),
            **({"project_log": {"format": project_log.get("format","markdown"), "text": project_log.get("value","")}} if project_log else {}),
            **({"turn_log": turn_log} if turn_log else {}),
            **({"sources_pool": sources_pool} if sources_pool else {}),
            **({"project_log_materialized": materialized_canvas} if materialized_canvas else {}),
            **({"solver_failure": solver_failure_md} if solver_failure_md else {}),
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
    out.sort(
        key=lambda e: ts_key((next(iter(e.values()), {}) or {}).get("ts")),
        reverse=True
    )
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
