# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# chat/proc/rest/integrations/code_core.py
#
# HTTP endpoints that let the frontend query the code-graph (Neo4j) directly,
# without going through the chat agent. Used by the Configuration Assistant
# inspect drawer so clicking a node fetches its details synchronously
# instead of asking the LLM to call the tool again.

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from kdcube_ai_app.apps.chat.api.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.retrieval.code_graph_client import (
    CodeGraphClient,
    NullCodeGraphClient,
    create_code_graph_client,
)
from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession

logger = logging.getLogger("ChatProc.Integrations.CodeCore")

router = APIRouter()

_client_lock = asyncio.Lock()
_cached_client: Optional[CodeGraphClient] = None


async def _get_client() -> CodeGraphClient | NullCodeGraphClient:
    """
    Lazy-initialise a single CodeGraphClient for HTTP-route use, separate
    from the per-bundle client managed by the orchestrate node. Reads the
    same NEO4J_URI / APP_GRAPH_ENABLED env config the bundle sees.
    """
    global _cached_client
    if _cached_client is not None:
        return _cached_client
    async with _client_lock:
        if _cached_client is not None:
            return _cached_client
        client = create_code_graph_client()
        if client.enabled:
            try:
                await client.init()
            except Exception:
                logger.exception("CodeGraphClient init failed; falling back to NullCodeGraphClient")
                _cached_client = NullCodeGraphClient()
                return _cached_client
        _cached_client = client
        return _cached_client


@router.get("/code-core/define")
async def code_core_define(
    request: Request,
    term: str = Query(..., min_length=1),
    scope: Optional[str] = Query(default=None),
    session: UserSession = Depends(require_auth(RequireUser())),
) -> Dict[str, Any]:
    """
    Resolve a framework concept / style policy / glossary term by name,
    id, or alias (case-insensitive).
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")
    client = await _get_client()
    if not getattr(client, "enabled", False):
        raise HTTPException(status_code=503, detail="Code graph disabled (APP_GRAPH_ENABLED=false)")
    scope_arg = (scope or "").strip() or None
    try:
        result = await client.define(term=term.strip(), scope=scope_arg)
    except Exception as exc:
        logger.exception("code_core.define failed for term=%r scope=%r", term, scope)
        raise HTTPException(status_code=500, detail=f"define failed: {exc}") from exc
    return result


@router.get("/code-core/class_footprint")
async def code_core_class_footprint(
    request: Request,
    qualified_name: str = Query(..., alias="qualified_name", min_length=1),
    session: UserSession = Depends(require_auth(RequireUser())),
) -> Dict[str, Any]:
    """
    Return the augmented class footprint (structural + concepts + style policies).
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")
    client = await _get_client()
    if not getattr(client, "enabled", False):
        raise HTTPException(status_code=503, detail="Code graph disabled (APP_GRAPH_ENABLED=false)")
    try:
        result = await client.class_footprint(qualified_name=qualified_name.strip())
    except Exception as exc:
        logger.exception("code_core.class_footprint failed for qn=%r", qualified_name)
        raise HTTPException(status_code=500, detail=f"class_footprint failed: {exc}") from exc
    return result


__all__ = ["router"]
