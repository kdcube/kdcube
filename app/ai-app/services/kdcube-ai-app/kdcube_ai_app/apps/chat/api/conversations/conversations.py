# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/conversations/conversations.py
from __future__ import annotations
from typing import List, Optional
import logging

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.api.resolvers import (
    get_user_session_dependency,
    get_conversation_system,
    get_pg_pool,
)
from kdcube_ai_app.auth.sessions import UserSession

"""
Conversations API

File: api/conversations/conversations.py
"""


logger = logging.getLogger("Conversations.API")


router = APIRouter()

# Reuse system components you already provision
conversation_browser, conversation_index, conversation_store = get_conversation_system(get_pg_pool())

# -------------------- Models --------------------

class ConversationListItem(BaseModel):
    conversation_id: str
    last_activity_at: Optional[str] = None
    started_at: Optional[str] = None
    title: Optional[str] = None

class ConversationListResponse(BaseModel):
    user_id: str
    items: List[ConversationListItem]

class ConversationFetchRequest(BaseModel):
    turn_ids: Optional[List[str]] = Field(default=None, description="If present, fetch only these turns")
    materialize: bool = Field(default=False, description="Fetch payloads from store for UI-visible items")
    days: int = Field(default=365, ge=1, le=3650)

# -------------------- Endpoints --------------------

@router.get("/", response_model=ConversationListResponse)
async def list_conversations(
        last_n: Optional[int] = Query(default=None, ge=1, le=500),
        started_after: Optional[str] = Query(default=None, description="ISO8601 timestamp"),
        days: int = Query(default=365, ge=1, le=3650),
        include_titles: bool = Query(default=True),
        session: UserSession = Depends(get_user_session_dependency()),
):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    # parse started_after if provided
    sa: Optional[datetime] = None
    if started_after:
        try:
            sa = datetime.fromisoformat(started_after.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid started_after timestamp")

    data = await conversation_browser.list_conversations(
        user_id=session.user_id,
        last_n=last_n,
        started_after=sa,
        days=days,
        include_titles=include_titles,
    )
    # data: {"user_id": ..., "items": [ {conversation_id, started_at, last_activity_at, title?}, ... ]}
    return data


@router.get("/{conversation_id}/details")
async def conversation_details(
        conversation_id: str,
        session: UserSession = Depends(get_user_session_dependency()),
):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    out = await conversation_browser.get_conversation_details(
        user_id=session.user_id,
        conversation_id=conversation_id,
    )
    #  Shape is:
    # {
    #   'conversation_id': ...,
    #   'conversation_title': ...,
    #   'last_activity_at': ...,
    #   'started_at': ...,
    #   'turns': [ { 'artifacts': [...], 'ts_first': ..., 'ts_last': ..., 'turn_id': ... } ],
    #   'user_id': ...
    # }
    return out


@router.post("/{conversation_id}/fetch")
async def fetch_conversation(
        conversation_id: str,
        req: ConversationFetchRequest = Body(...),
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Fetch UI-visible artifacts for a conversation. Optional:
      - restrict to a set of turn_ids
      - materialize payloads from store
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    data = await conversation_browser.fetch_conversation_artifacts(
        user_id=session.user_id,
        conversation_id=conversation_id,
        turn_ids=(req.turn_ids or None),
        materialize=bool(req.materialize),
        days=int(req.days),
    )

    # Shape is:
    # {
    #   "user_id": ...,
    #   "conversation_id": ...,
    #   "turns": [
    #       {"turn_id": "t1", "artifacts": [
    #           {"message_id": "...", "type": "...", "ts": "...", "s3_uri": "...", ["data": {...}]}
    #       ]},
    #       ...
    #   ]
    # }
    return data