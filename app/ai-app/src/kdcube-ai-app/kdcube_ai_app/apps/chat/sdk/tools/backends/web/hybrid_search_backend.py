# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/web/hybrid_search_backends.py
"""
Hybrid search backend with result substitution and two execution modes.

Key design:
- Decorator on backend.search_many() methods (not search())
- Each backend tracks only successful queries (non-429, non-error)
- Two modes: parallel (both run) or sequential (spare runs only for failures)
- Transparent result substitution: client sees best results regardless of source
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class HybridMode(str, Enum):
    """Execution mode for hybrid backend."""
    PARALLEL = "parallel"      # Run both backends simultaneously
    SEQUENTIAL = "sequential"  # Run spare only for primary failures


@dataclass
class QueryResult:
    """Result for a single query with status tracking."""
    query: str
    hits: List[Dict[str, Any]]
    success: bool              # True if backend returned 200/success
    error: Optional[Exception] = None
    provider: str = "unknown"


class HybridSearchBackend:
    """
    Hybrid search backend with transparent result substitution.

    Execution modes:
    1. PARALLEL: Both primary and spare run for all queries simultaneously.
       - Primary failures (429, errors) are substituted with spare results.
       - Both backends emit accounting events with their successful query counts.

    2. SEQUENTIAL: Primary runs first, spare runs only for failures.
       - More efficient (spare only runs when needed).
       - Each backend tracks only what it successfully executed.

    Design philosophy:
    - Decorators on backend.search_many() methods
    - Each backend's event shows only successful queries
    - Result substitution is transparent to caller
    """

    name = "hybrid"

    def __init__(
            self,
            primary,
            spare,
            mode: HybridMode = HybridMode.SEQUENTIAL
    ):
        """
        Args:
            primary: Primary backend (e.g. BraveSearchBackend)
            spare: Fallback backend (e.g. DDGSearchBackend)
            mode: Execution mode (parallel or sequential)
        """
        self.primary = primary
        self.spare = spare
        self.mode = mode

        # Inherit capability flags from primary
        self.default_use_external_reconciler = getattr(
            primary, "default_use_external_reconciler", True
        )
        self.default_use_external_refinement = getattr(
            primary, "default_use_external_refinement", True
        )
        self.max_results_hard_cap = min(
            getattr(primary, "max_results_hard_cap", 100),
            getattr(spare, "max_results_hard_cap", 100)
        )

        logger.info(
            f"HybridSearchBackend: primary={primary.name}, spare={spare.name}, "
            f"mode={mode.value}"
        )

    async def search(
            self,
            query: str,
            max_results: int = 10,
            freshness: Optional[str] = None,
            country: Optional[str] = None,
            safesearch: str = "moderate",
    ) -> List[Dict[str, Any]]:
        """
        Single query search - delegates to search_many with one query.
        """
        results = await self.search_many(
            queries=[query],
            per_query_max=max_results,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
            concurrency=1,
        )
        return results[0] if results else []

    async def search_many(
            self,
            queries: Sequence[str],
            *,
            per_query_max: int,
            freshness: Optional[str] = None,
            country: Optional[str] = None,
            safesearch: str = "moderate",
            concurrency: int = 8,
    ) -> List[List[Dict[str, Any]]]:
        """
        Multi-query search with transparent result substitution.

        Returns:
            List[List[Dict]] - results per query (same order as input)

        Accounting:
        - Each backend.search_many() call is decorated
        - Backend events show only successful query counts
        - Failed queries don't count toward primary's usage
        """
        qs = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not qs:
            return []

        if self.mode == HybridMode.PARALLEL:
            return await self._search_parallel(
                qs, per_query_max, freshness, country, safesearch, concurrency
            )
        else:
            return await self._search_sequential(
                qs, per_query_max, freshness, country, safesearch, concurrency
            )

    async def _search_parallel(
            self,
            queries: List[str],
            per_query_max: int,
            freshness: Optional[str],
            country: Optional[str],
            safesearch: str,
            concurrency: int,
    ) -> List[List[Dict[str, Any]]]:
        """
        Parallel mode: Run both backends simultaneously for all queries.

        Strategy:
        1. Run primary.search_many(all_queries) - decorated, emits event
        2. Run spare.search_many(all_queries) - decorated, emits event
        3. For each query: use primary result if success, else use spare result

        Accounting:
        - Primary event: counts only successful queries
        - Spare event: counts only successful queries
        - Total may be < len(queries) if both fail for same query
        """
        logger.info(
            f"Hybrid parallel mode: running both backends for {len(queries)} queries"
        )

        # Run both backends simultaneously
        # Each backend.search_many() is decorated and will emit its own event
        primary_task = self.primary.search_many(
            queries,
            per_query_max=per_query_max,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
            concurrency=concurrency,
        )

        spare_task = self.spare.search_many(
            queries,
            per_query_max=per_query_max,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
            concurrency=concurrency,
        )

        # Wait for both
        primary_results, spare_results = await asyncio.gather(
            primary_task, spare_task, return_exceptions=True
        )

        # Handle exceptions
        if isinstance(primary_results, Exception):
            logger.error(f"Primary backend failed completely: {primary_results}")
            primary_results = [[] for _ in queries]

        if isinstance(spare_results, Exception):
            logger.error(f"Spare backend failed completely: {spare_results}")
            spare_results = [[] for _ in queries]

        # Merge results: use primary where available, spare as fallback
        merged = []
        for i, query in enumerate(queries):
            primary_hits = primary_results[i] if i < len(primary_results) else []
            spare_hits = spare_results[i] if i < len(spare_results) else []

            # Use primary if non-empty, else spare
            if primary_hits:
                merged.append(primary_hits)
            else:
                merged.append(spare_hits)
                if not spare_hits:
                    logger.warning(f"Both backends failed for query: {query[:50]}...")

        logger.info(
            f"Hybrid parallel completed: {len(merged)} query results merged"
        )

        return merged

    async def _search_sequential(
            self,
            queries: List[str],
            per_query_max: int,
            freshness: Optional[str],
            country: Optional[str],
            safesearch: str,
            concurrency: int,
    ) -> List[List[Dict[str, Any]]]:
        """
        Sequential mode: Run spare only for primary failures.

        Strategy:
        1. Run primary.search_many(all_queries) - decorated, emits event
        2. Identify failed queries (empty results or errors)
        3. If failures exist, run spare.search_many(failed_queries_only) - decorated, emits event
        4. Substitute spare results for failed primary results

        Accounting:
        - Primary event: counts all attempted queries (decorator tracks successes)
        - Spare event: counts only failed queries it was asked to handle
        """
        logger.info(
            f"Hybrid sequential mode: running primary for {len(queries)} queries"
        )

        # Run primary for all queries
        # Backend.search_many() is decorated and will emit event
        try:
            primary_results = await self.primary.search_many(
                queries,
                per_query_max=per_query_max,
                freshness=freshness,
                country=country,
                safesearch=safesearch,
                concurrency=concurrency,
            )
        except Exception as e:
            logger.error(f"Primary backend failed completely: {e}")
            primary_results = [[] for _ in queries]

        # Identify failed queries (empty results)
        failed_indices: List[int] = []
        failed_queries: List[str] = []

        for i, (query, hits) in enumerate(zip(queries, primary_results)):
            if not hits:
                failed_indices.append(i)
                failed_queries.append(query)

        if not failed_queries:
            logger.info("Hybrid sequential: all queries succeeded with primary")
            return primary_results

        logger.info(
            f"Hybrid sequential: {len(failed_queries)} queries failed, "
            f"running spare for them"
        )

        # Run spare only for failed queries
        # Backend.search_many() is decorated and will emit event
        try:
            spare_results = await self.spare.search_many(
                failed_queries,
                per_query_max=per_query_max,
                freshness=freshness,
                country=country,
                safesearch=safesearch,
                concurrency=concurrency,
            )
        except Exception as e:
            logger.error(f"Spare backend failed: {e}")
            spare_results = [[] for _ in failed_queries]

        # Substitute spare results for failed primary results
        merged = list(primary_results)  # Copy
        for i, spare_idx in enumerate(failed_indices):
            spare_hits = spare_results[i] if i < len(spare_results) else []
            merged[spare_idx] = spare_hits
            if not spare_hits:
                logger.warning(
                    f"Both backends failed for query: {queries[spare_idx][:50]}..."
                )

        logger.info(
            f"Hybrid sequential completed: {len(failed_queries)} results "
            f"substituted with spare"
        )

        return merged


async def get_hybrid_search_backend(
        primary_name: Optional[str] = None,
        spare_name: str = "duckduckgo",
        mode: HybridMode = HybridMode.SEQUENTIAL,
) -> HybridSearchBackend:
    """
    Factory for hybrid backend.

    Args:
        primary_name: Name of primary backend (e.g. "brave" / "exa" / "serper" ..),
                     or None to read from WEB_SEARCH_BACKEND env var
        spare_name: Name of spare/fallback backend (default: "duckduckgo")
        mode: Execution mode (parallel or sequential)

    Returns:
        HybridSearchBackend instance
    """
    # Import here to avoid circular dependency
    from .search_backends import get_search_backend

    primary = await get_search_backend(primary_name)
    spare = await get_search_backend(spare_name)

    return HybridSearchBackend(primary=primary, spare=spare, mode=mode)
