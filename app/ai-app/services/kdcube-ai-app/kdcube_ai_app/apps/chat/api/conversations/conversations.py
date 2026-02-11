# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/conversations/conversations.py
from __future__ import annotations

import uuid
from typing import List, Optional
import logging
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.api.resolvers import (
    get_user_session_dependency,
    get_conversation_system,
    get_pg_pool,
)
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
from kdcube_ai_app.apps.chat.sdk.tools.citations import strip_base64_from_citables_artifact

"""
Conversations API

File: api/conversations/conversations.py
"""


logger = logging.getLogger("Conversations.API")


router = APIRouter()

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

class TurnFeedbackRequest(BaseModel):
    """
    User feedback on a specific turn.

    This single endpoint handles all feedback operations:
    - Add feedback: Set reaction to "ok", "not_ok", or "neutral"
    - Clear feedback: Set reaction to null

    Examples:
        {"reaction": "ok", "text": "Great!"}          # Add positive feedback
        {"reaction": "not_ok", "text": "Incorrect"}   # Add negative feedback
        {"reaction": "neutral", "text": "Note"}       # Add neutral comment
        {"reaction": null}                            # Clear feedback
    """
    reaction: Optional[str] = Field(
        None,
        description="Reaction: 'ok', 'not_ok', 'neutral', or null to clear. Required unless clearing."
    )
    text: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional feedback text (ignored when clearing)"
    )
    ts: Optional[str] = Field(
        default=None,
        description="Optional ISO8601 timestamp (defaults to now)"
    )

class TurnFeedbackResponse(BaseModel):
    """Response after applying or clearing feedback"""
    success: bool
    turn_id: str
    reaction: Optional[str] = None  # null when cleared
    message: str
    cleared: bool = Field(default=False, description="True if feedback was cleared")


class ConversationFeedbackTurnsRequest(BaseModel):
    turn_ids: Optional[List[str]] = Field(
        default=None,
        description="If provided, restrict to these exact turn IDs. If omitted, server discovers turns with feedbacks."
    )
    days: int = Field(default=365, ge=1, le=3650)


class FeedbackPeriodWindow(BaseModel):
    start: str
    end: str

class FeedbackCounts(BaseModel):
    total: int
    user: int
    machine: int
    ok: int
    not_ok: int
    neutral: int

class FeedbackTurnSummary(BaseModel):
    turn_id: str
    ts: Optional[str] = None
    feedbacks: Optional[List[dict]] = None   # structured from turn_log.feedbacks[]

class FeedbackConversationItem(BaseModel):
    conversation_id: str
    last_activity_at: Optional[str] = None
    started_at: Optional[str] = None
    feedback_counts: FeedbackCounts
    turns: Optional[List[FeedbackTurnSummary]] = None

class ConversationsInPeriodRequest(BaseModel):
    start: str = Field(..., description="ISO8601 timestamp, inclusive")
    end: str = Field(..., description="ISO8601 timestamp, inclusive")
    include_turns: bool = Field(default=False, description="When true, returns turn details with feedbacks")
    limit: int = Field(default=100, ge=1, le=500, description="Max conversations to return")
    cursor: Optional[str] = Field(default=None, description="Opaque pagination cursor")

class ConversationsInPeriodResponse(BaseModel):
    tenant: str
    project: str
    window: FeedbackPeriodWindow
    items: List[FeedbackConversationItem]
    next_cursor: Optional[str] = None

class ConversationStatus(BaseModel):
    conversation_id: str
    state: str            # idle | in_progress | error
    updated_at: Optional[str] = None
    meta: Optional[dict] = None

class ConversationDeleteResponse(BaseModel):
    conversation_id: str
    deleted_messages: int = Field(..., description="Rows deleted from conv_messages")
    deleted_storage_messages: int = Field(
        ..., description="Message blobs removed from storage (best-effort)"
    )
    deleted_storage_attachments: int = Field(
        ..., description="Attachment files removed from storage (best-effort)"
    )
    deleted_storage_executions: int = Field(
        ..., description="Execution snapshot files removed from storage (best-effort)"
    )

# -------------------- Endpoints --------------------

@router.get("/{tenant}/{project}", response_model=ConversationListResponse)
async def list_conversations(
        tenant: str,
        project: str,
        last_n: Optional[int] = Query(default=None, ge=1, le=500),
        started_after: Optional[str] = Query(default=None, description="ISO8601 timestamp"),
        days: int = Query(default=365, ge=1, le=3650),
        include_titles: bool = Query(default=True),
        session: UserSession = Depends(get_user_session_dependency()),
):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    # parse started_after if provided
    sa: Optional[datetime.datetime] = None
    if started_after:
        try:
            sa = datetime.datetime.fromisoformat(started_after.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid started_after timestamp")

    data = await router.state.conversation_browser.list_conversations(
        user_id=session.user_id,
        last_n=last_n,
        started_after=sa,
        days=days,
        include_titles=include_titles,
    )
    return data

@router.get("/{tenant}/{project}/{conversation_id}/status", response_model=ConversationStatus)
async def conversation_status(tenant: str, project: str, conversation_id: str, session: UserSession = Depends(get_user_session_dependency())):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")
    st = await router.state.conversation_browser.get_conversation_state(
        user_id=session.user_id, conversation_id=conversation_id
    )
    return ConversationStatus(conversation_id=conversation_id, **st)

@router.get("/{tenant}/{project}/{conversation_id}/details")
async def conversation_details(
        tenant: str,
        project: str,
        conversation_id: str,
        session: UserSession = Depends(get_user_session_dependency()),
):
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    out = await router.state.conversation_browser.get_conversation_details(
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


@router.post("/{tenant}/{project}/{conversation_id}/fetch")
async def fetch_conversation(
        tenant: str,
        project: str,
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

    data = await router.state.conversation_browser.fetch_conversation_artifacts(
        user_id=session.user_id,
        conversation_id=conversation_id,
        turn_ids=(req.turn_ids or None),
        materialize=bool(req.materialize),
        days=int(req.days),
    )
    for turn in (data or {}).get("turns", []):
        for artifact in turn.get("artifacts", []):
            strip_base64_from_citables_artifact(artifact)

    # Shape is:
    # {
    #   "user_id": ...,
    #   "conversation_id": ...,
    #   "turns": [
    #       {"turn_id": "t1", "artifacts": [
    #           {"message_id": "...", "type": "...", "ts": "...", "hosted_uri": "...", ["data": {...}]}
    #       ]},
    #       ...
    #   ]
    # }
    return data

@router.delete(
    "/{tenant}/{project}/{conversation_id}",
    response_model=ConversationDeleteResponse,
)
async def delete_conversation(
        tenant: str,
        project: str,
        conversation_id: str,
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Hard-delete a conversation (and related artifacts) for the authenticated user.

    This will:
      - remove all index rows for this {user_id, conversation_id} (including state, logs, reactions, etc.)
      - best-effort delete message JSONs, attachments, and execution artifacts in storage.

    NOTE: This operation is irreversible.
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    # Resolve user_type as the string used when writing messages (e.g. "standard")
    raw_user_type = getattr(session, "user_type", "standard")
    user_type_str = getattr(raw_user_type, "value", str(raw_user_type))

    # Bundle scoping (same pattern as feedback endpoints)
    try:
        from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
        spec_resolved = resolve_bundle(None, override=None)
        bundle_id = spec_resolved.id
    except Exception:
        bundle_id = None

    try:
        result = await router.state.conversation_browser.delete_conversation(
            tenant=tenant,
            project=project,
            user_id=session.user_id,
            conversation_id=conversation_id,
            user_type=user_type_str,
            bundle_id=bundle_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to delete conversation={conversation_id} for user={session.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete conversation: {str(e)}",
        )
    if result.get("deleted_messages", 0) == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # You can decide whether "0 deleted_messages" should be treated as 404.
    # For now we just return the counts.
    await router.state.chat_comm.emit_conversation_status(
        request_id=str(uuid.uuid4()),
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        user_id=session.user_id or session.fingerprint,
        session_id=session.session_id,
        conversation_id=conversation_id,
        state="deleted",
        target_sid=None,  # session-broadcast
    )
    return ConversationDeleteResponse(
        conversation_id=conversation_id,
        deleted_messages=result.get("deleted_messages", 0),
        deleted_storage_messages=result.get("deleted_storage_messages", 0),
        deleted_storage_attachments=result.get("deleted_storage_attachments", 0),
        deleted_storage_executions=result.get("deleted_storage_executions", 0),
    )


@router.post("/{tenant}/{project}/{conversation_id}/turns/{turn_id}/feedback", response_model=TurnFeedbackResponse)
async def submit_turn_feedback(
        tenant: str,
        project: str,
        conversation_id: str,
        turn_id: str,
        req: TurnFeedbackRequest = Body(...),
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Submit, update, or clear user feedback for a specific turn.

    This single endpoint handles all feedback operations:

    **Add/Update Feedback:**
    - Set reaction to "ok", "not_ok", or "neutral"
    - Optionally include text
    - Replaces any previous user feedback on this turn

    **Clear Feedback:**
    - Set reaction to null
    - Removes all user feedback from this turn

    Args:
        conversation_id: The conversation ID
        turn_id: The turn ID to provide feedback on
        req: Feedback request with reaction and optional text

    Returns:
        TurnFeedbackResponse with success status and cleared flag

    Examples:
        # Add positive feedback
        POST /conversations/{conv_id}/turns/{turn_id}/feedback
        {
            "reaction": "ok",
            "text": "Great explanation!"
        }

        # Add neutral comment
        POST /conversations/{conv_id}/turns/{turn_id}/feedback
        {
            "reaction": "neutral",
            "text": "Note for later"
        }

        # Clear feedback
        POST /conversations/{conv_id}/turns/{turn_id}/feedback
        {
            "reaction": null
        }
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    try:
        # Get tenant, project info from session or config
        user_type = session.user_type if hasattr(session, 'user_type') else 'standard'

        spec_resolved = resolve_bundle(None, override=None)
        bundle_id = spec_resolved.id
        # CASE 1: Clear feedback (reaction is null)
        if req.reaction is None:
            removed = await router.state.conversation_browser.remove_user_reaction(
                turn_id=turn_id,
                user_id=session.user_id,
                conversation_id=conversation_id
            )
            # Also scrub mirrored entries from turn log
            spec_resolved = resolve_bundle(None, override=None)
            bundle_id = spec_resolved.id
            _ = await router.state.conversation_browser.clear_user_feedback_in_turn_log(
                tenant=tenant,
                project=project,
                user=session.user_id,
                user_type=user_type.value,
                conversation_id=conversation_id,
                turn_id=turn_id,
                bundle_id=bundle_id,
            )

            logger.info(
                f"User feedback cleared: user={session.user_id}, "
                f"conversation={conversation_id}, turn={turn_id}, "
                f"removed={removed}"
            )

            return TurnFeedbackResponse(
                success=True,
                turn_id=turn_id,
                reaction=None,
                message="Feedback cleared" if removed else "No feedback to clear",
                cleared=True
            )

        # CASE 2: Add/update feedback (reaction is not null)
        # Validate reaction value
        if req.reaction not in ("ok", "not_ok", "neutral"):
            raise HTTPException(
                status_code=400,
                detail="reaction must be 'ok', 'not_ok', 'neutral', or null"
            )

        # Build reaction payload
        feedback_ts = req.ts or (datetime.datetime.utcnow().isoformat() + "Z")

        reaction = {
            "turn_id": turn_id,
            "text": req.text or "",
            "confidence": 1.0,  # User feedback is always high confidence
            "ts": feedback_ts,
            "reaction": req.reaction,
            "origin": "user",
        }

        # 1) Append reaction to turn log (this handles removing old user reactions)
        await router.state.conversation_browser.append_reaction_to_turn_log(
            turn_id=turn_id,
            reaction=reaction,
            tenant=tenant,
            project=project,
            user=session.user_id,
            fingerprint=None,
            user_type=user_type.value,
            conversation_id=conversation_id,
            bundle_id=bundle_id,
            origin="user",
        )

        # 2) Apply feedback to the turn log itself (update the turn log artifact)
        result = await router.state.conversation_browser.apply_feedback_to_turn_log(
            tenant=tenant,
            project=project,
            user=session.user_id,
            user_type=user_type.value,
            conversation_id=conversation_id,
            turn_id=turn_id,
            bundle_id=bundle_id,
            feedback={
                "text": req.text or "",
                "confidence": 1.0,
                "ts": feedback_ts,
                "from_turn_id": turn_id,  # Self-referential for user feedback
                "origin": "user",
                "reaction": req.reaction,
            }
        )

        if result is None:
            # Turn log not found
            raise HTTPException(
                status_code=404,
                detail=f"Turn log not found for turn_id={turn_id}"
            )

        logger.info(
            f"User feedback applied: user={session.user_id}, "
            f"conversation={conversation_id}, turn={turn_id}, "
            f"reaction={req.reaction}, has_text={bool(req.text)}"
        )

        return TurnFeedbackResponse(
            success=True,
            turn_id=turn_id,
            reaction=req.reaction,
            message="Feedback applied successfully",
            cleared=False
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to apply user feedback: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply feedback: {str(e)}"
        )

@router.post("/{tenant}/{project}/{conversation_id}/turns-with-feedbacks")
async def fetch_turns_with_feedbacks(
        tenant: str,
        project: str,
        conversation_id: str,
        req: ConversationFeedbackTurnsRequest = Body(...),
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Return all turns in a conversation that have feedbacks (or reactions), each with:
      - turn_id
      - full turn_log object (materialized)
      - assistant message (materialized)
      - user message (materialized)
      - feedbacks array (from the turn log payload)

    Optional:
      - limit to specific turn_ids.
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    try:
        spec_resolved = resolve_bundle(None, override=None)
        bundle_id = spec_resolved.id

        data = await router.state.conversation_browser.fetch_turns_with_feedbacks(
            user_id=session.user_id,
            conversation_id=conversation_id,
            turn_ids=(req.turn_ids or None),
            days=int(req.days),
            bundle_id=bundle_id,
        )
        # Shape:
        # {
        #   "user_id": ...,
        #   "conversation_id": ...,
        #   "turns": [
        #       {
        #           "turn_id": "...",
        #           "turn_log": { ... },   # full object
        #           "assistant": { ... }, # item with payload
        #           "user": { ... },      # item with payload
        #           "feedbacks": [ ... ], # from the turn log
        #           "reactions": [ ... ]  # optional raw reaction artifacts
        #       },
        #       ...
        #   ]
        # }
        return data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch turns-with-feedbacks for conversation={conversation_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedback turns: {str(e)}")

@router.post("/{tenant}/{project}/feedback/conversations-in-period", response_model=ConversationsInPeriodResponse)
async def conversations_with_feedbacks_in_period(
        tenant: str,
        project: str,
        req: ConversationsInPeriodRequest = Body(...),
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Aggregate conversations (and optionally turns) that have feedback reactions within a time window.

    Input:
      - start/end (ISO8601, inclusive)
      - include_turns: when true, also materializes involved turns and returns `feedbacks` + `reactions`
      - limit/cursor: pagination over conversations

    Output:
      - items[] per conversation with feedback_counts {total,user,machine,ok,not_ok,neutral}
      - optionally turns[] with per-turn summaries (id, ts, feedbacks[], reactions[])
      - next_cursor for pagination

    Notes:
      - Scopes to the authenticated user and the given {tenant}/{project}
      - Counts are derived from artifacts tagged 'kind:turn.log.reaction'
      - Turn details (when include_turns=true) are materialized similarly to /turns-with-feedbacks
    """
    if not session.user_id:
        raise HTTPException(status_code=401, detail="No user in session")

    # Validate & parse timestamps
    try:
        start_dt = datetime.datetime.fromisoformat(req.start.replace("Z", "+00:00"))
        end_dt = datetime.datetime.fromisoformat(req.end.replace("Z", "+00:00"))
        if end_dt < start_dt:
            raise ValueError("end must be >= start")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid start/end timestamp format (use ISO8601)")

    try:
        # Keep the implementation detail in the conversation_browser to match your existing architecture
        # and to allow swapping storage/index strategies without touching the API surface.
        spec_resolved = resolve_bundle(None, override=None)
        bundle_id = spec_resolved.id

        result = await router.state.conversation_browser.fetch_feedback_conversations_in_period(
            user_id=session.user_id,
            tenant=tenant,
            project=project,
            start_iso=req.start,
            end_iso=req.end,
            include_turns=bool(req.include_turns),
            limit=int(req.limit),
            cursor=(req.cursor or None),
            bundle_id=bundle_id,
        )

        # Expected shape of `result`:
        # {
        #   "tenant": "...",
        #   "project": "...",
        #   "window": { "start": "...", "end": "..." },
        #   "items": [
        #       {
        #         "conversation_id": "...",
        #         "last_activity_at": "...",
        #         "started_at": "...",
        #         "feedback_counts": { "total": 7, "user": 3, "machine": 4, "ok": 4, "not_ok": 2, "neutral": 1 },
        #         "turns": [
        #           {
        #             "turn_id": "...",
        #             "ts": "...",
        #             "feedbacks": [ ... ],   # from turn_log.payload.turn_log.feedbacks[]
        #           }
        #         ]
        #       }
        #   ],
        #   "next_cursor": "opaque-string-or-null"
        # }

        # Validate minimal keys; raise if backend returns unexpected shape
        if not isinstance(result, dict) or "items" not in result or "window" not in result:
            raise RuntimeError("Backend returned unexpected shape")

        return ConversationsInPeriodResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to summarize conversations with feedbacks in period: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to summarize feedbacks: {str(e)}")
