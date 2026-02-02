# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError


class FollowupsOut(BaseModel):
    followups: List[str] = Field(default_factory=list)


def _build_user_message(*, user_text: str, attachments_summary: str, retrospective_context: str):
    user_blocks = [
        {"text": "[USER]\n" + user_text.strip(), "cache": False},
        {"text": "[ATTACHMENTS SUMMARY]\n" + (attachments_summary.strip() if attachments_summary else "(none)"), "cache": False},
        {"text": "[RETROSPECTIVE CONTEXT]\n" + retrospective_context, "cache": False},
    ]
    return create_cached_human_message(user_blocks)


async def stream_final_answer(
    svc: ModelServiceBase,
    *,
    user_text: str,
    attachments_summary: str,
    retrospective_context: str,
    timezone: str,
    emit_delta,
    prepared_messages: Optional[List[Any]] = None,
    max_tokens: int = 1800,
) -> Tuple[str, List[str], str, Dict[str, str]]:
    sys_prompt = (
        "You are a simple, helpful assistant.\n"
        "Use provided context only when it is relevant to the user's question.\n"
        "If context is insufficient, ask a brief clarifying question.\n"
        "Respond directly and succinctly.\n\n"
        "IMPORTANT: The THINKING channel is shown to the user.\n"
        "Keep it very short (1–2 sentences, no lists).\n\n"
        "Output protocol (strict):\n"
        "<channel:thinking> ... </channel:thinking>\n"
        "<channel:answer> ... </channel:answer>\n"
        "<channel:followup> {\"followups\": [ ...list of string... ] } </channel:followup>\n"
        "FOLLOWUP may be empty: {\"followups\": []}.\n\n"
        f"User timezone: {timezone}\n"
    )

    messages = prepared_messages or [
        create_cached_system_message([{"text": sys_prompt, "cache": True}]),
        _build_user_message(
            user_text=user_text,
            attachments_summary=attachments_summary,
            retrospective_context=retrospective_context,
        ),
    ]

    followup_chunks: List[str] = []

    async def _emit(**kwargs):
        channel = kwargs.get("channel")
        text = kwargs.get("text") or ""
        if channel == "followup" and text:
            followup_chunks.append(text)
        await emit_delta(**kwargs)

    channels = [
        ChannelSpec(name="thinking", format="text", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="answer", format="markdown", replace_citations=False, emit_marker="answer"),
        ChannelSpec(name="followup", format="json", model=FollowupsOut, replace_citations=False, emit_marker="subsystem"),
    ]

    results, meta = await stream_with_channels(
        svc,
        messages=messages,
        role="answer.generator.simple",
        channels=channels,
        emit=_emit,
        agent="answer.generator.simple",
        max_tokens=max_tokens,
        temperature=0.3,
        return_full_raw=True,
    )
    service_error = (meta or {}).get("service_error")
    if service_error:
        raise ServiceException(ServiceError.model_validate(service_error))

    answer = (results.get("answer").raw if results.get("answer") else "") or ""
    followups = []
    f_res = results.get("followup")
    if f_res and f_res.obj and isinstance(f_res.obj, FollowupsOut):
        followups = [s.strip() for s in f_res.obj.followups if isinstance(s, str) and s.strip()]
    elif followup_chunks:
        try:
            data = json.loads("".join(followup_chunks))
            vals = (data or {}).get("followups") or []
            followups = [s.strip() for s in vals if isinstance(s, str) and s.strip()]
        except Exception:
            followups = []

    thinking = (results.get("thinking").raw if results.get("thinking") else "") or ""
    channel_dump = {
        "thinking": (results.get("thinking").raw if results.get("thinking") else "") or "",
        "answer": (results.get("answer").raw if results.get("answer") else "") or "",
        "followup": (results.get("followup").raw if results.get("followup") else "") or "",
    }
    return answer.strip(), followups, thinking, channel_dump
