# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/retrieval.py

from __future__ import annotations

import uuid as _uuid
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_sources_any,
    dedupe_sources_by_url,
    rewrite_citation_tokens,
)
from kdcube_ai_app.apps.chat.sdk.util import ts_key

PROJECT_LOG_SLOTS = { "project_log" }
SOURCES_POOL_ARTIFACT_TAG = "artifact:conv:sources_pool"

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

        # Prefer explicit "used" flags in sources_pool if present
        pool_rows = _meta_sources_pool(meta)
        if pool_rows and any(isinstance(r, dict) and r.get("used") is True for r in pool_rows):
            for r in pool_rows:
                if not isinstance(r, dict):
                    continue
                if r.get("used") is not True:
                    continue
                sid = r.get("sid")
                if isinstance(sid, (int, float)):
                    used.add(int(sid))
            return used

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
            text = artifact.get("text") or ""
            if isinstance(text, str) and text.strip():
                used.update(sids_in_text(text))
        return used

    def _map_sids(seq: Any, sid_map: Dict[int, int]) -> list[int]:
        out: list[int] = []
        if not seq:
            return out
        if not isinstance(seq, list):
            return out
        for s in seq:
            if isinstance(s, dict):
                sid = s.get("sid")
            else:
                sid = s
            if isinstance(sid, (int, float)):
                new = sid_map.get(int(sid), int(sid))
                if new not in out:
                    out.append(new)
        return out

    def _rewrite_artifact_sources(artifact: dict, sid_map: Dict[int, int]) -> None:
        if not isinstance(artifact, dict) or not sid_map:
            return

        # Rewrite inline citation tokens in text-like fields
        for key in ("text", "content"):
            if isinstance(artifact.get(key), str):
                artifact[key] = rewrite_citation_tokens(artifact.get(key) or "", sid_map)
        val = artifact.get("value")
        if isinstance(val, dict):
            for key in ("text", "content"):
                if isinstance(val.get(key), str):
                    val[key] = rewrite_citation_tokens(val.get(key) or "", sid_map)

        # Normalize sources_used lists to reconciled SIDs
        su = _map_sids(artifact.get("sources_used"), sid_map)
        if su:
            artifact["sources_used"] = su
        if isinstance(val, dict):
            v_su = _map_sids(val.get("sources_used"), sid_map)
            if v_su:
                val["sources_used"] = v_su

    collected: list[dict] = []
    per_turn_used: dict[str, set[int]] = {}
    per_turn_pool: dict[str, list[dict]] = {}

    history_list = list(history or [])
    for rec in history_list:
        run_id, meta = next(iter(rec.items()))
        pool_rows = _meta_sources_pool(meta)
        used_sids = _collect_used_sids(meta)
        per_turn_used[run_id] = used_sids
        per_turn_pool[run_id] = pool_rows

    # Preserve first-seen turn_id by iterating oldest -> newest.
    for rec in reversed(history_list):
        run_id, meta = next(iter(rec.items()))
        pool_rows = per_turn_pool.get(run_id) or []
        used_sids = per_turn_used.get(run_id) or set()
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
    canonical_by_url = {s.get("url"): s for s in sources_pool if s.get("url")}
    sid_maps: dict[str, dict[int, int]] = {}

    if rewrite_tokens_in_place:
        for rec in (history or []):
            run_id, meta = next(iter(rec.items()))
            pool_rows = per_turn_pool.get(run_id) or []
            sid_map: Dict[int, int] = {}
            for row in pool_rows:
                if not isinstance(row, dict):
                    continue
                old_sid = row.get("sid")
                url = row.get("url")
                if not isinstance(old_sid, int) or not url:
                    continue
                canon = canonical_by_url.get(url)
                if canon and isinstance(canon.get("sid"), int):
                    sid_map[int(old_sid)] = int(canon["sid"])
            if sid_map:
                sid_maps[run_id] = sid_map

            # Rewrite citations in project log
            proj = meta.get("project_log") or {}
            if isinstance(proj, dict):
                txt = proj.get("text") or proj.get("value") or ""
                if isinstance(txt, str) and txt:
                    new_txt = rewrite_citation_tokens(txt, sid_map)
                    if proj.get("text"):
                        proj["text"] = new_txt
                    else:
                        proj["value"] = new_txt

            # Rewrite citations + sources_used in deliverables
            for d in (meta.get("deliverables") or []):
                if not isinstance(d, dict):
                    continue
                art = d.get("value")
                if not isinstance(art, dict):
                    continue
                _rewrite_artifact_sources(art, sid_map)

            # Rewrite citations + sources_used in assistant completion
            assistant = meta.get("assistant") if isinstance(meta.get("assistant"), dict) else {}
            completion = assistant.get("completion") if isinstance(assistant.get("completion"), dict) else {}
            if completion:
                _rewrite_artifact_sources(completion, sid_map)
                assistant["completion"] = completion
                meta["assistant"] = assistant

            # Rewrite citations in turn_log assistant completion if present
            tlog = meta.get("turn_log") if isinstance(meta.get("turn_log"), dict) else {}
            if tlog:
                t_asst = tlog.get("assistant") if isinstance(tlog.get("assistant"), dict) else {}
                t_comp = t_asst.get("completion") if isinstance(t_asst.get("completion"), dict) else {}
                if t_comp:
                    _rewrite_artifact_sources(t_comp, sid_map)
                    t_asst["completion"] = t_comp
                    tlog["assistant"] = t_asst
                    meta["turn_log"] = tlog

            used = per_turn_used.get(run_id) or set()
            used_mapped = {sid_map.get(sid, sid) for sid in used if isinstance(sid, int)}
            if used_mapped:
                used_sources = [
                    canonical_by_sid[sid] for sid in sorted(used_mapped) if sid in canonical_by_sid
                ]
            else:
                used_sources = []

            # Update per-turn sources_pool to reconciled, used-only sources
            meta["sources_pool"] = used_sources

    return {
        "sources_pool": [
            {**row, "used": False} if isinstance(row, dict) else row
            for row in sources_pool
        ],
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

async def _build_program_history_from_turn_ids(ctx_client, *,
                                               user_id: str,
                                               turn_ids: List[str],
                                               conversation_id: Optional[str] = None,
                                               scope: str = "track",
                                               days: int = 365) -> List[Dict[str, Any]]:
    """
    For each turn_id, materialize: program presentation (if present), project_canvas / project_log
    from deliverables, and citations tied to the run. Returns the same shape as _build_program_history().
    """
    if not ctx_client:
        return []
    out = []
    seen_runs = set()

    for tid in turn_ids:
        mat = await ctx_client.materialize_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=tid,
            scope=scope,
            days=days,
            with_payload=True,
            extra_artifact_tags=[SOURCES_POOL_ARTIFACT_TAG],
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
        # materialized_canvas = {}
        # try:
        #     glue = project_log.get("value","") if project_log else ""
        #     from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import _materialize_glue_canvas
        #     mat = _materialize_glue_canvas(glue, d_items)
        #     if mat and mat != glue:
        #         materialized_canvas = {"format": "markdown", "text": mat}
        # except Exception as ex:
        #     materialized_canvas = {}

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
            # **({"project_log_materialized": materialized_canvas} if materialized_canvas else {}),
            **({"solver_failure": solver_failure_md} if solver_failure_md else {}),
            **{"media": []},
            "ts": ts_val,
            # **({"codegen_run_id": codegen_run_id} if codegen_run_id else {}),
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


async def build_program_history_from_turn_ids(self, *,
                                              user_id: str,
                                              turn_ids: List[str],
                                              conversation_id: Optional[str] = None,
                                              scope: str = "track",
                                              days: int = 365) -> List[Dict[str, Any]]:
    if not self.context_rag_client:
        return []
    return await _build_program_history_from_turn_ids(
        self.context_rag_client,
        user_id=user_id,
        turn_ids=turn_ids,
        conversation_id=conversation_id,
        scope=scope,
        days=days,
    )


async def build_program_history_from_turn_ids_by_client(*,
                                                        ctx_client,
                                                        user_id: str,
                                                        turn_ids: List[str],
                                                        conversation_id: Optional[str] = None,
                                                        scope: str = "track",
                                                        days: int = 365) -> List[Dict[str, Any]]:
    return await _build_program_history_from_turn_ids(
        ctx_client,
        user_id=user_id,
        turn_ids=turn_ids,
        conversation_id=conversation_id,
        scope=scope,
        days=days,
    )

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
