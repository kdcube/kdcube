# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, Tuple

from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)
from pydantic import BaseModel, Field
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError


class ContextRefTarget(BaseModel):
    where: str = Field(..., description="user|assistant")
    query: str = Field(..., description="short semantic search query")


class GateOut(BaseModel):
    ctx_retrieval_queries: list[ContextRefTarget] = Field(default_factory=list)
    conversation_title: str | None = Field(default=None, description="Conversation title (first turn only)")


async def gate_stream(
    svc: ModelServiceBase,
    *,
    user_text: str,
    attachments_summary: str,
    retrospective_context: str,
    timezone: str,
    is_new_conversation: bool = False,
    on_thinking_delta=None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    if is_new_conversation:
        sys_prompt = (
            "You are a minimal gate agent.\n"
            "Your only job: propose a conversation title.\n\n"
            "IMPORTANT: The THINKING channel is shown to the user.\n"
            "Keep it very short (1–2 sentences, no lists).\n\n"
            "Output protocol (strict):\n"
            "<channel:thinking> ... </channel:thinking>\n"
            "<channel:output> {\"conversation_title\": \"...\"} </channel:output>\n\n"
            "Return JSON with key:\n"
            "- conversation_title: short title (≤6 words).\n\n"
            "Rules:\n"
            "- Only emit conversation_title.\n"
            "- Do not add any other keys.\n"
        )
    else:
        sys_prompt = (
            "You are a minimal gate agent.\n"
            "Your only job: propose context search queries.\n\n"
            "IMPORTANT: The THINKING channel is shown to the user.\n"
            "Keep it very short (1–2 sentences, no lists).\n\n"
            "Output protocol (strict):\n"
            "<channel:thinking> ... </channel:thinking>\n"
            "<channel:output> {\"ctx_retrieval_queries\": [...]} </channel:output>\n\n"
            "Return JSON with key:\n"
            "- ctx_retrieval_queries: list of {where, query}. where ∈ {user, assistant}.\n\n"
            "Rules:\n"
            "- Only propose context queries if they are clearly helpful.\n"
            "- Keep queries short (≤10 words).\n"
            "- Do not add any other keys.\n"
            "- If unsure, return an empty list.\n\n"
            "Context query guidance (from our gate agent):\n"
            "- If user references earlier content ('this/that/you said/my earlier') → emit queries.\n"
            "- 'my/I said/provided' → where=user\n"
            "- 'you said/explained/suggested' → where=assistant\n"
            "- Keep queries entity-rich; no quotes/Booleans.\n"
        )

    user_blocks = [
        {"text": "[USER]\n" + user_text.strip(), "cache": False},
        {"text": "[ATTACHMENTS SUMMARY]\n" + (attachments_summary.strip() if attachments_summary else "(none)"), "cache": False},
        {"text": "[RETROSPECTIVE CONTEXT]\n" + retrospective_context, "cache": False},
        {"text": f"[TIMEZONE]\n{timezone}", "cache": False},
    ]

    system_msg = create_cached_system_message([{"text": sys_prompt, "cache": True}])
    user_msg = create_cached_human_message(user_blocks)

    async def _emit(**kwargs):
        channel = kwargs.pop("channel", None)
        text = kwargs.get("text") or ""
        if channel == "thinking" and on_thinking_delta:
            await on_thinking_delta(text=text, completed=kwargs.get("completed", False))

    channels = [
        ChannelSpec(name="thinking", format="text", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="output", format="json", model=GateOut, replace_citations=False, emit_marker="subsystem"),
    ]

    results, meta = await stream_with_channels(
        svc,
        messages=[system_msg, user_msg],
        role="gate.simple",
        channels=channels,
        emit=_emit,
        agent="gate.simple",
        max_tokens=800,
        temperature=0.2,
        return_full_raw=True,
    )
    service_error = (meta or {}).get("service_error")
    if service_error:
        raise ServiceException(ServiceError.model_validate(service_error))

    payload: Dict[str, Any] = {}
    res = results.get("output")
    if res and res.obj and isinstance(res.obj, GateOut):
        payload = res.obj.model_dump()
    else:
        raw = (res.raw if res else "") or ""
        payload = {"ctx_retrieval_queries": []}
        if raw:
            try:
                parsed = GateOut.model_validate_json(raw)
                payload = parsed.model_dump()
            except Exception:
                payload = {"ctx_retrieval_queries": []}

    channel_dump = {
        "thinking": (results.get("thinking").raw if results.get("thinking") else "") or "",
        "output": (results.get("output").raw if results.get("output") else "") or "",
    }

    return payload, channel_dump
