---
name: kb-advanced-rag
description: |
  Multi-step retrieval over the knowledge base with history-aware query rewrite,
  entity extraction, dual-pass hybrid search (BM25 + pgvector), compound
  cross-encoder reranking, and optional ±N neighbor expansion.
version: 1.0.0
category: research
tags:
  - kb
  - retrieval
  - hybrid
  - rerank
  - rag
when_to_use:
  - The question references earlier turns ("the one we discussed", "that error", pronouns)
  - The question names specific entities, products, identifiers, or version numbers
  - The question needs reasoning across multiple chunks (multi-hop)
  - Plain search_knowledge returned weak or off-topic hits
author: kdcube
created: 2026-04-25
namespace: public
---

# Advanced KB RAG

## Overview
`rag.advanced_rag_search` is a multi-step retrieval pipeline over the project knowledge base. Compared to `react.search_knowledge` (which is a flat keyword search over a bundle's local docs), this tool performs the full retrieval pipeline: it rewrites follow-up questions into self-contained queries using conversation history, extracts named entities/identifiers, runs two parallel hybrid searches (the rewritten question and the entity terms), merges the results with deduplication, applies a compound cross-encoder reranker, and optionally pulls neighbor segments for context. It returns a structured `sources` list ready for the answer generator with `sid`/`title`/`url`/`text`.

## When to Use This Skill
Prefer `rag.advanced_rag_search` over `react.search_knowledge` when any of the following holds:
- The user's message contains pronouns or ellipsis that only resolve against prior turns (e.g. "what about the same thing for vector mode?", "and how does that fail?").
- The message names specific entities, model IDs, error codes, version numbers, or proper nouns.
- A previous flat search underperformed (few or off-topic hits).
- The answer needs to combine evidence from several chunks across the KB.

Use the simpler `react.search_knowledge` for browsing the bundle's own docs / index by keyword, where the conversation context isn't load-bearing.

## Rules
1. Pass the user's *original* question — the tool handles rewrite internally.
2. Default `top_k=8` is a good baseline; raise to 12-15 only for synthesis-heavy questions.
3. Inspect `sources[*].scores` and `stats` when debugging retrieval quality. `stats` reports `hybrid_rows`, `entity_rows`, `merged_rows`, and the rewritten query.
4. The pipeline honours the user's UI search settings (hybrid weights, advanced-RAG knobs). Do not pass overrides unless explicitly told to.

## Returned shape
```json
{
  "rewritten_query": "string",
  "entities": ["entity1", "entity2"],
  "sources": [
    {
      "sid": 1,
      "title": "Document title",
      "url": "https://...",
      "text": "Full chunk content...",
      "summary": "Optional summary",
      "provider": "kb",
      "scores": { "rerank": 0.82, "semantic": 0.71, "components": {...} },
      "neighbor_offset": 0,
      "is_seed": true
    }
  ],
  "stats": { "hybrid_rows": 16, "entity_rows": 6, "merged_rows": 19, "returned": 8 }
}
```

Use `sid` values when grounding citations: `[[S:1]]`, `[[S:1,3]]`.
