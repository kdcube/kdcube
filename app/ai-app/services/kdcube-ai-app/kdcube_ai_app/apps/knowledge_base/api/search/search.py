# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import traceback
try:
    import faiss
except Exception as e:
    traceback.print_exc()

from fastapi import Depends
from pydantic import BaseModel, Field
import re

# Add the KB import
from kdcube_ai_app.apps.knowledge_base.api.resolvers import (get_project, get_faiss_index, get_faiss_cache,
                                                             get_kb_read_dep, get_kb_admin_dep,
                                                             get_kb_read_with_acct_dep)
from kdcube_ai_app.apps.knowledge_base.db.data_models import NavigationSearchResult
from kdcube_ai_app.apps.knowledge_base.search import SearchResult

"""
Search API

File: api/search/search.py
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Callable, List, Dict, Any, Union
import logging

# This will be set by the mount function
kb_getter: Optional[Callable] = None

logger = logging.getLogger("KBSearch.API")


def get_kb_fn(router, project):
    """Get KB instance."""
    if not router.kb_getter:
        raise HTTPException(
            status_code=500,
            detail="KB getter not configured"
        )

    try:
        return router.kb_getter(project=project)
    except Exception as e:
        logger.error(f"Failed to get KB: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get knowledge base: {str(e)}"
        )


# Create router
router = APIRouter()

import functools
get_kb = functools.partial(get_kb_fn, router=router)


# ================================================================================
#                            KB REQUEST MODELS
# ================================================================================

class KBSearchRequest(BaseModel):
    query: str
    resource_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)

class KBSearchResponse(BaseModel):
    query: str
    results: List[NavigationSearchResult]
    total_results: int
    search_metadata: Dict[str, Any]

class EnhancedKBSearchRequest(BaseModel):
    query: str
    resource_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)
    include_backtrack: bool = Field(default=True)
    include_navigation: bool = Field(default=True)

class BacktrackNavigation(BaseModel):
    start_line: int
    end_line: int
    start_pos: int
    end_pos: int
    citations: List[str]
    text: Optional[str] = None
    heading: Optional[str] = None
    subheading: Optional[str] = None

class BacktrackInfo(BaseModel):
    raw: Dict[str, Any]
    extraction: Dict[str, Any]
    segmentation: Dict[str, Any]

class EnhancedSearchResult(BaseModel):
    query: str
    relevance_score: float
    heading: str
    subheading: str
    backtrack: BacktrackInfo
    text_blocks: Optional[List[str]] = None
    combined_text: Optional[str] = None
    resource_id: Optional[str] = None
    version: Optional[str] = None

class EnhancedKBSearchResponse(BaseModel):
    query: str
    results: List[Union[SearchResult, NavigationSearchResult]]
    total_results: int
    search_metadata: Dict[str, Any]

class HighlightRequest(BaseModel):
    rn: str
    citations: List[str]
    navigation: Optional[List[BacktrackNavigation]] = None
    highlight_format: str = Field(default="<mark class='bg-yellow-200 px-1 rounded'>{}</mark>")

class SegmentContentRequest(BaseModel):
    rn: str
    segment_index: int
    highlight_citations: Optional[List[str]] = None
    include_context: bool = Field(default=True)
    context_lines: int = Field(default=3)

# ================================================================================
#                            SEARCH ENDPOINTS
# ================================================================================

@router.post("/search", response_model=KBSearchResponse)
@router.post("/{project}/search", response_model=KBSearchResponse)
async def search_kb(request: KBSearchRequest,
                    project: str = Depends(get_project),
                    session = Depends(get_kb_read_with_acct_dep())):
    """Enhanced search with navigation support and RN information - requires KB read access."""
    try:
        kb = get_kb(project=project)

        # If specific resource_id provided, search in that resource
        if request.resource_id:
            search_results = kb.hybrid_search(
                query=request.query,
                resource_id=request.resource_id,
                top_k=request.top_k
            )
        else:
            # Search across all resources
            resources = kb.list_resources()
            if not resources:
                return KBSearchResponse(
                    query=request.query,
                    results=[],
                    total_results=0,
                    search_metadata={"message": "No resources in knowledge base"}
                )

            # Search in all resources and combine results
            all_enhanced_results = []
            for resource in resources:
                try:
                    resource_results = kb.hybrid_search(
                        query=request.query,
                        resource_id=resource.id,
                        top_k=request.top_k
                    )
                    all_enhanced_results.extend(resource_results)
                except Exception as e:
                    logger.warning(f"Search failed for resource {resource.id}: {e}; user: {session.username}")
                    continue

            # Sort by relevance and limit results
            all_enhanced_results.sort(key=lambda x: x.relevance_score, reverse=True)
            search_results = all_enhanced_results[:request.top_k]

        return KBSearchResponse(
            query=request.query,
            results=search_results,
            total_results=len(search_results),
            search_metadata={
                "searched_resources": 1 if request.resource_id else len(kb.list_resources()),
                "total_resources": len(kb.list_resources()),
                "enhanced_search": True,
                "user": session.username
            }
        )

    except Exception as e:
        logger.error(f"Error searching KB: {e}; user: {session.username}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search/enhanced", response_model=EnhancedKBSearchResponse)
@router.post("/{project}/search/enhanced", response_model=EnhancedKBSearchResponse)
async def search_kb_enhanced(request: EnhancedKBSearchRequest,
                             project: str = Depends(get_project),
                             session = Depends(get_kb_read_with_acct_dep())):
    """Enhanced search with comprehensive backtrack information - requires KB read access."""
    try:
        kb = get_kb(project=project)

        enhanced_results = kb.hybrid_search(
            query=request.query,
            resource_id=request.resource_id,
            top_k=request.top_k
        )

        # Convert to enhanced format
        formatted_results = []
        for result in enhanced_results:
            try:
                formatted_results.append(result)
            except Exception as e:
                logger.error(f"Error formatting result: {e}; user: {session.username}")
                continue

        return EnhancedKBSearchResponse(
            query=request.query,
            results=enhanced_results,
            total_results=len(formatted_results),
            search_metadata={
                "enhanced_search": True,
                "backtrack_enabled": request.include_backtrack,
                "navigation_enabled": request.include_navigation,
                "searched_resources": 1 if request.resource_id else len(kb.list_resources()),
                "user": session.username
            }
        )

    except Exception as e:
        logger.error(f"Enhanced search error: {e}; user: {session.username}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/content/highlighted")
@router.post("/{project}/content/highlighted")
async def get_content_with_highlighting(request: HighlightRequest,
                                        project: str = Depends(get_project),
                                        session = Depends(get_kb_read_with_acct_dep())):
    """Get content with highlighting applied based on citations and navigation - requires KB read access."""
    try:
        kb = get_kb(project=project)

        # Parse RN to get resource info
        rn_parts = request.rn.split(":")
        if len(rn_parts) < 6:
            raise HTTPException(status_code=400, detail="Invalid RN format")

        resource_id = rn_parts[4]
        version = rn_parts[5]
        stage = rn_parts[3]

        # Get content
        if stage == "raw":
            content = kb.get_resource_content(resource_id, version, as_text=True)
        elif stage == "extraction":
            filename = rn_parts[6] if len(rn_parts) > 6 else "extraction_0.md"
            extraction_module = kb.get_extraction_module()
            content_bytes = extraction_module.get_asset(resource_id, version, filename)
            content = content_bytes.decode('utf-8') if content_bytes else None
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported stage: {stage}")

        if not content:
            raise HTTPException(status_code=404, detail="Content not found")

        # Apply highlighting
        highlighted_content = content
        for citation in request.citations:
            if citation.strip():
                # Escape special regex characters
                escaped_citation = re.escape(citation)
                pattern = f"({escaped_citation})"
                highlighted_content = re.sub(
                    pattern,
                    request.highlight_format.format(r'\1'),
                    highlighted_content,
                    flags=re.IGNORECASE
                )

        # Apply navigation-based highlighting if provided
        navigation_applied = False
        if request.navigation and stage == "extraction":
            navigation_applied = True
            # Apply additional highlighting based on navigation boundaries
            # This could be enhanced to show segment boundaries

        return {
            "content": content,
            "highlighted_content": highlighted_content,
            "navigation_applied": navigation_applied,
            "rn": request.rn,
            "citations_applied": len(request.citations),
            "user": session.username
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying highlighting: {e}; user: {session.username}")
        raise HTTPException(status_code=500, detail=str(e))

class FaissSearchRequest(BaseModel):
    vectors: List[List[float]]
    top_k: int = 5

@router.post("/{project}/faiss/search")
async def search(
        body: FaissSearchRequest,
        index: faiss.Index = Depends(get_faiss_index),
        user=Depends(get_kb_read_dep())
):
    """FAISS vector search - requires KB read access."""
    try:
        D, I = index.search(body.vectors, body.top_k)
    except Exception as e:
        logger.error(f"FAISS search failed: {e}; user: {user.username}")
        raise HTTPException(500, "FAISS search failed")
    return {
        "distances": D.tolist(),
        "indices": I.tolist(),
        "user": user.username
    }

# ================================================================================
#                            ADMIN SEARCH ENDPOINTS
# ================================================================================

@router.get("/admin/search/stats")
@router.get("/{project}/admin/search/stats")
async def get_search_stats(project: str = Depends(get_project),
                          user=Depends(get_kb_admin_dep())):
    """Get search statistics - admin only."""
    try:
        kb = get_kb(project=project)

        # Get various stats
        stats = {
            "total_resources": len(kb.list_resources()),
            "kb_stats": kb.get_stats(),
            "project": project,
            "admin_user": user.username
        }

        # Add FAISS index stats if available
        try:
            faiss_cache = get_faiss_cache()
            stats["faiss_stats"] = {
                "loaded_projects": len(faiss_cache._loaded),
                "max_loaded": faiss_cache.max_loaded
            }
        except Exception as e:
            logger.warning(f"Could not get FAISS stats: {e}")
            stats["faiss_stats"] = {"error": str(e)}

        return stats

    except Exception as e:
        logger.error(f"Error getting search stats: {e}; user: {user.username}")
        raise HTTPException(status_code=500, detail=str(e))

