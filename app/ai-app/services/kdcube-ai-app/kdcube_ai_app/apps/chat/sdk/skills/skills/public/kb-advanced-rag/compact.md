# Advanced KB RAG (compact)

`rag.advanced_rag_search(query, top_k=8)` runs multi-step KB retrieval: history-aware rewrite, entity extraction, dual-pass hybrid (BM25 + pgvector), compound rerank, optional ±N neighbors. Returns `{rewritten_query, entities, sources:[{sid,title,url,text,scores,...}], stats}`. Prefer over `react.search_knowledge` for follow-ups, named-entity questions, or multi-hop synthesis.
