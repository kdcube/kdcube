# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Advanced RAG pipeline orchestration.

Composes existing KDCube primitives — query rewrite, entity extraction,
hybrid retrieval (BM25 + pgvector), compound rerank, and ±N neighbor
expansion — into a single multi-step retrieval call. Reads per-turn knobs
from `RuntimeCtx.search_settings`; falls back to defaults that match the
plain `react.search_knowledge` behaviour when knobs are missing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from kdcube_ai_app.apps.knowledge_base.db.data_models import HybridSearchParams
from kdcube_ai_app.infra.embedding.embedding import get_embedding
from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank

from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.entity_extract import extract_entities
from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.query_rewrite import rewrite_for_retrieval

logger = logging.getLogger(__name__)


# ----- knob extraction ---------------------------------------------------

def _adv_settings(runtime_ctx: Any) -> Dict[str, Any]:
    """
    Read knobs from RuntimeCtx.search_settings.

    Strategy: reuse the existing `hybrid.*` panel fields wherever they map
    naturally to a step of the advanced pipeline (top_k, min_score_threshold,
    use_reranking, context_window, distance_type). Read advanced-RAG-specific
    knobs from `advancedRag.*`. Defaults match the existing search_knowledge
    behaviour when no settings are supplied.

    Mapping from UI field → pipeline knob:
      hybrid.enabled              -> overall enable
      hybrid.top_k_vector         -> top_k for hybrid pass (and final return)
      hybrid.use_reranking        -> compound_rerank
      hybrid.min_score_threshold  -> drop rows below this semantic_score
      hybrid.context_window       -> neighbor_window
      hybrid.distance_type        -> HybridSearchParams.distance_type
      hybrid.w_sem / hybrid.w_bm25-> blend weights
      hybrid.providers            -> provider filter
      advancedRag.enable_query_rewrite, enable_entity_pass, entity_top_k,
      advancedRag.rerank_weights, advancedRag.min_priority_slots,
      advancedRag.priority_keys, advancedRag.include_expired
    """
    s = getattr(runtime_ctx, "search_settings", None) or {}
    if not isinstance(s, dict):
        s = {}
    hybrid = s.get("hybrid") or {}
    if not isinstance(hybrid, dict):
        hybrid = {}
    adv = s.get("advancedRag") or s.get("advanced_rag") or {}
    if not isinstance(adv, dict):
        adv = {}

    return {
        # Master enable: respect hybrid.enabled when present (otherwise on).
        "enabled":             bool(hybrid.get("enabled", adv.get("enabled", True))),
        # Step toggles (advancedRag-only knobs)
        "rewrite":             bool(adv.get("enable_query_rewrite", True)),
        "entity_pass":         bool(adv.get("enable_entity_pass", True)),
        "entity_top_k":        int(adv.get("entity_top_k", 6) or 6),
        # Reuse hybrid.use_reranking as the master rerank toggle.
        "compound_rerank":     bool(hybrid.get("use_reranking", adv.get("compound_rerank", True))),
        "rerank_weights":      adv.get("rerank_weights") or None,
        "min_priority_slots":  int(adv.get("min_priority_slots", 0) or 0),
        "priority_keys":       list(adv.get("priority_keys") or []),
        # Reuse hybrid.context_window for ±N neighbors.
        "neighbor_window":     int(hybrid.get("context_window", adv.get("neighbor_window", 0)) or 0),
        # Result controls (reuse existing hybrid fields)
        "ui_top_k":            int(hybrid.get("top_k_vector") or 0),
        "min_score_threshold": float(hybrid.get("min_score_threshold", 0.0) or 0.0),
        # Hybrid weights / provider filter
        "w_sem":               float(hybrid.get("w_sem", 0.6)),
        "w_bm25":              float(hybrid.get("w_bm25", 0.4)),
        "distance_type":       str(hybrid.get("distance_type") or "cosine"),
        "providers":           list(adv.get("providers") or hybrid.get("providers") or []) or None,
        "include_expired":     bool(adv.get("include_expired", False)),
    }


# ----- conversation history --------------------------------------------------

def _read_history(runtime: Any, runtime_ctx: Any, max_messages: int) -> List[Dict[str, Any]]:
    """Best-effort fetch of recent messages for query rewrite. Returns [] on any failure."""
    if max_messages <= 0:
        return []
    store = getattr(runtime, "conv_store", None)
    if store is None:
        return []
    tenant = getattr(runtime_ctx, "tenant", None)
    project = getattr(runtime_ctx, "project", None)
    user_id = getattr(runtime_ctx, "user_id", None)
    user_type = getattr(runtime_ctx, "user_type", None)
    conv_id = getattr(runtime_ctx, "conversation_id", None)
    if not (tenant and project and user_id and conv_id):
        return []
    try:
        msgs = store.list_conversation(
            tenant=tenant,
            project=project,
            user_type=user_type or "",
            user_or_fp=user_id,
            conversation_id=conv_id,
        ) or []
    except Exception:
        logger.debug("conversation history fetch failed", exc_info=True)
        return []
    out: List[Dict[str, Any]] = []
    for m in msgs[-max_messages:]:
        role = (m.get("role") or "").lower()
        if role not in ("user", "assistant", "human", "ai"):
            continue
        out.append({"role": role, "content": m.get("content") or ""})
    return out


# ----- result shaping --------------------------------------------------------

def _shape_source(row: Dict[str, Any], sid: int) -> Dict[str, Any]:
    """Project a KB row into the standard source-pool schema."""
    ds = (row.get("extensions") or {}).get("datasource") or {}
    title = row.get("title") or ds.get("title") or row.get("rn") or "Untitled"
    url = ds.get("uri") or row.get("url") or ""
    text = row.get("content") or ""
    return {
        "sid": sid,
        "title": str(title)[:300],
        "url": str(url)[:1000],
        "text": text,
        "summary": row.get("summary") or "",
        "provider": row.get("provider") or ds.get("provider") or "kb",
        "resource_id": row.get("resource_id"),
        "version": row.get("version"),
        "segment_id": row.get("id"),
        "scores": {
            "rerank":   row.get("rerank_score"),
            "semantic": row.get("semantic_score"),
            "final":    row.get("final_score"),
            "components": row.get("rerank_components"),
        },
        "neighbor_offset": row.get("neighbor_offset", 0),
        "is_seed":         row.get("is_seed", True),
    }


# ----- merge & dedup ---------------------------------------------------------

def _segment_key(row: Dict[str, Any]) -> tuple:
    return (str(row.get("resource_id") or ""), int(row.get("version") or 0), str(row.get("id") or ""))


def _merge_dedup(*lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge multiple result lists; for duplicate segments keep the highest semantic_score."""
    best: Dict[tuple, Dict[str, Any]] = {}
    for lst in lists:
        for r in lst or []:
            k = _segment_key(r)
            if not k[2]:
                continue
            cur = best.get(k)
            if cur is None:
                best[k] = r
                continue
            cur_score = float(cur.get("semantic_score") or 0.0)
            new_score = float(r.get("semantic_score") or 0.0)
            if new_score > cur_score:
                best[k] = r
    return list(best.values())


# ----- main pipeline ---------------------------------------------------------

async def run_advanced_rag(
        *,
        runtime: Any,
        query: str,
        top_k: int = 8,
        history_messages: int = 6,
) -> Dict[str, Any]:
    """
    Run the advanced RAG pipeline. Returns:
      {
        "rewritten_query": str,
        "entities": list[str],
        "sources": list[dict],  # ordered, top_k entries with sid 1..N
        "stats": {...},
      }
    """
    if not runtime or not getattr(runtime, "is_available", lambda: False)():
        return {"rewritten_query": query, "entities": [], "sources": [], "stats": {"available": False}}

    runtime_ctx = runtime.get_runtime_ctx() if callable(getattr(runtime, "get_runtime_ctx", None)) else None
    knobs = _adv_settings(runtime_ctx)
    if not knobs.get("enabled", True):
        return {"rewritten_query": query, "entities": [], "sources": [], "stats": {"enabled": False}}

    kb = runtime.kb
    model_service = runtime.model_service
    raw_query = (query or "").strip()
    if not raw_query:
        return {"rewritten_query": "", "entities": [], "sources": [], "stats": {"empty": True}}

    stats: Dict[str, Any] = {}

    # --- Step 1: history-aware rewrite -------------------------------------
    rewritten = raw_query
    if knobs["rewrite"]:
        history = _read_history(runtime, runtime_ctx, history_messages)
        if history:
            rewritten = await rewrite_for_retrieval(
                query=raw_query, history=history, model_service=model_service,
            )
        stats["history_messages"] = len(history) if knobs["rewrite"] else 0
    stats["rewritten_query"] = rewritten

    # --- Step 2: entity extraction -----------------------------------------
    entities: List[str] = []
    if knobs["entity_pass"]:
        entities = await extract_entities(query=rewritten, model_service=model_service)
    stats["entities"] = entities

    # --- Step 3: embed -----------------------------------------------------
    try:
        emb = await get_embedding(rewritten)
    except Exception:
        logger.warning("embedding failed; falling back to BM25-only retrieval", exc_info=True)
        emb = None

    # --- Step 4: hybrid pass ------------------------------------------------
    # The UI's `hybrid.top_k_vector` is the user's preferred final size, when set.
    effective_top_k = knobs["ui_top_k"] if knobs["ui_top_k"] > 0 else top_k
    over_fetch = max(effective_top_k * 2, effective_top_k + 4)
    hybrid_params = HybridSearchParams(
        query=rewritten,
        embedding=emb,
        top_n=over_fetch,
        should_rerank=False,               # we run our own (compound) rerank below
        providers=knobs["providers"],
        include_expired=knobs["include_expired"],
        text_weight=knobs["w_bm25"],
        semantic_weight=knobs["w_sem"],
        distance_type=knobs["distance_type"],
        min_similarity=(knobs["min_score_threshold"] or None),
    )
    try:
        hybrid_rows = await kb.hybrid_pipeline_search_nojoin_blend(hybrid_params)
    except Exception:
        logger.error("hybrid retrieval failed", exc_info=True)
        hybrid_rows = []
    stats["hybrid_rows"] = len(hybrid_rows)

    # --- Step 5: entity pass (re-issue retrieval with entity-string query) --
    entity_rows: List[Dict[str, Any]] = []
    if entities:
        ent_query = " ".join(entities)
        try:
            ent_emb = await get_embedding(ent_query)
        except Exception:
            ent_emb = None
        ent_params = HybridSearchParams(
            query=ent_query,
            embedding=ent_emb,
            top_n=knobs["entity_top_k"],
            should_rerank=False,
            providers=knobs["providers"],
            include_expired=knobs["include_expired"],
            distance_type=knobs["distance_type"],
            min_similarity=(knobs["min_score_threshold"] or None),
        )
        try:
            entity_rows = await kb.hybrid_pipeline_search_nojoin_blend(ent_params)
        except Exception:
            logger.warning("entity-pass retrieval failed", exc_info=True)
            entity_rows = []
    stats["entity_rows"] = len(entity_rows)

    # --- Step 6: merge + dedup ---------------------------------------------
    merged = _merge_dedup(hybrid_rows, entity_rows)
    stats["merged_rows"] = len(merged)
    if not merged:
        return {"rewritten_query": rewritten, "entities": entities, "sources": [], "stats": stats}

    # --- Step 7: compound rerank -------------------------------------------
    if knobs["compound_rerank"]:
        priority_keys = list(knobs["priority_keys"]) + list(entities)  # entities count as priority hints
        try:
            merged = cross_encoder_rerank(
                rewritten,
                merged,
                column_name="content",
                top_k=None,
                mode="compound",
                weights=knobs["rerank_weights"] or None,
                priority_keys=priority_keys,
                min_priority_slots=knobs["min_priority_slots"],
            )
        except Exception:
            logger.warning("compound rerank failed; falling back to original ordering", exc_info=True)

    top = merged[: effective_top_k]

    # --- Step 8: neighbor expansion ----------------------------------------
    if knobs["neighbor_window"] > 0:
        try:
            top = await kb.expand_neighbors(top, window=knobs["neighbor_window"])
        except Exception:
            logger.warning("neighbor expansion failed", exc_info=True)

    # --- Step 9: shape sources pool ----------------------------------------
    sources = [_shape_source(r, sid=i + 1) for i, r in enumerate(top)]
    stats["returned"] = len(sources)

    return {
        "rewritten_query": rewritten,
        "entities": entities,
        "sources": sources,
        "stats": stats,
    }
