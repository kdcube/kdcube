#!/usr/bin/env python
"""
End-to-end smoke test for the advanced RAG tool against the live KB.

Runs INSIDE chat-proc container:
  docker exec all_in_one_kdcube-chat-proc-1 python /app/tools/rag_e2e_smoke.py

What it verifies:
  1) KBClient connects to Postgres and reads from retrieval_segment
  2) `hybrid_pipeline_search_nojoin_blend` returns at least one row
  3) `run_advanced_rag` (the full pipeline) shapes sources for citations

Auth / model: uses the env wired into chat-proc (PG creds, embedding key).
No HTTP — direct Python invocation.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

QUERY = "What is KDCube Advanced RAG?"


async def main() -> int:
    print("=== Smoke test: advanced RAG against live KB ===")

    # Import KDCube bits (chat-proc image already has all deps installed)
    from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
    from kdcube_ai_app.apps.knowledge_base.db.data_models import HybridSearchParams
    from kdcube_ai_app.infra.embedding.embedding import get_embedding
    from kdcube_ai_app.infra.service_hub.inventory import embedding_model
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.runtime import AdvancedRAGRuntime
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import run_advanced_rag

    # ---- Step 1: KBClient round-trip on retrieval_segment ----
    kb = KBClient(pool=None)
    await kb.init()
    print(f"\n[1] KBClient initialised. schema={kb.schema!r}")

    try:
        emb = get_embedding(embedding_model(), QUERY)  # sync; takes ModelRecord first
        print(f"[1] embedded query (dim={len(emb) if emb else 'None'})")
    except Exception as e:
        print(f"[1] embedding failed: {e}")
        emb = None

    params = HybridSearchParams(
        query=QUERY,
        embedding=emb,
        top_n=5,
        should_rerank=False,
        include_expired=True,
    )
    rows = await kb.hybrid_pipeline_search_nojoin_blend(params)
    print(f"[2] hybrid_pipeline_search_nojoin_blend returned {len(rows)} rows")
    if rows:
        for r in rows[:3]:
            print(f"    - {r.get('resource_id')} sem={r.get('semantic_score'):.3f} content[:80]={r.get('content', '')[:80]!r}")

    # ---- Step 2: full advanced RAG pipeline ----
    runtime_ctx = types.SimpleNamespace(
        tenant="kdcube",
        project="default",
        user_id="smoke",
        user_type="privileged",
        conversation_id="smoke-conv",
        turn_id="smoke-turn",
        search_settings={
            "hybrid": {"enabled": True, "top_k_vector": 5, "use_reranking": False},
            "advancedRag": {
                "enable_query_rewrite": False,    # no LLM rewrite (avoids needing model bind)
                "enable_entity_pass": False,      # ditto
                "compound_rerank": False,
            },
        },
    )

    runtime = AdvancedRAGRuntime(
        kb=kb,
        model_service=None,                       # rewrite/entity disabled, so unused
        conv_store=None,
        get_runtime_ctx=lambda: runtime_ctx,
    )

    print("\n[3] Calling run_advanced_rag(...)")
    out = await run_advanced_rag(runtime=runtime, query=QUERY, top_k=5, history_messages=0)
    print(f"\n    rewritten_query: {out.get('rewritten_query')!r}")
    print(f"    entities:        {out.get('entities')}")
    print(f"    stats:           {json.dumps(out.get('stats'), indent=2)}")
    sources = out.get("sources") or []
    print(f"    sources:         {len(sources)} returned")
    for s in sources[:3]:
        print(f"      - sid={s['sid']} title={s['title']!r}")
        print(f"        scores={s['scores']}")
        preview = (s.get('text') or '')[:120].replace('\n', ' ')
        print(f"        text[:120]={preview!r}")

    await kb.close()

    if not sources:
        print("\nFAIL: advanced RAG returned no sources. Index has no segments matching the query?")
        return 1
    print("\nOK — advanced RAG returned hits from the KB.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
