# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Simple search system
Removes all unnecessary complexity and focuses on what's needed for backtracking.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from kdcube_ai_app.apps.knowledge_base.db.data_models import HybridSearchParams, NavigationSearchResult

@dataclass
class SearchResult:
    """Simple search result with actual navigation data."""
    query: str
    relevance_score: float
    heading: str
    subheading: str
    backtrack: Dict[str, Any]  # Actual navigation data for frontend

# Simplified API for knowledge base
class SimpleKnowledgeBaseSearch:
    """Simple search API for knowledge base."""

    def __init__(self, kb):
        self.kb = kb

    def search(self, query: str, resource_id: str, top_k: int = 5) -> List[NavigationSearchResult]:
        """Simple search that returns useful results."""
        from kdcube_ai_app.infra.accounting import with_accounting
        with with_accounting("kb.search",
                             metadata={
                                "query": query,
                                "phase": "user_query_embedding"
                            }):
            query_embedding = self.kb.db_connector.get_embedding(query)

        resource_ids = [resource_id] if resource_id else None
        params = HybridSearchParams(
            query=query,
            top_n=20,
            min_similarity=0.2,
            text_weight=0.5,
            semantic_weight=0.5,
            embedding=query_embedding,
            match_all=False,
            rerank_threshold=0.6,
            rerank_top_k=top_k,
            resource_ids=resource_ids
        )
        return self.kb.db_connector.pipeline_search(params)