# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── agents/gate.py ──
# Lightweight "gate" agent — the first LLM call in the pipeline.
#
# In this kdcube.copilot bundle the gate has a single job: propose a short
# conversation title on the first turn of a new conversation.
#
# How it works:
#   1. If not a new conversation → skip (return empty defaults)
#   2. Build a system prompt instructing the LLM to emit two channels:
#      - <channel:thinking>  → streamed to the user as "thinking" indicator
#      - <channel:output>    → structured JSON parsed into GateOut
#   3. Call the LLM via stream_with_channels()
#   4. Parse the output channel into GateOut (Pydantic model)
#   5. If ctx_browser is provided, use retry_with_compaction to auto-retry
#      on token-limit errors (compacts context and retries)
#
# To extend:
#   Add more fields to GateOut (e.g. route, intent, clarification_questions)
#   and update the system prompt. The orchestrator reads them from scratchpad.gate.

from __future__ import annotations

from typing import Any, Dict, Tuple, Optional, Callable, List

from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)
from pydantic import BaseModel, Field
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry import retry_with_compaction
from kdcube_ai_app.apps.chat.sdk.util import token_count


class GateOut(BaseModel):
    """Structured output from the gate agent. Add fields here to extend."""
    conversation_title: str | None = Field(default=None, description="Conversation title (first turn only)")


async def gate_stream(
    svc: ModelServiceBase,
    *,
    is_new_conversation: bool = False,
    on_thinking_delta=None,
    ctx_browser: Any = None,
    emit_status: Optional[Callable[[List[str]], Any]] = None,
    render_params: Optional[Dict[str, Any]] = None,
    sanitize_on_fail: bool = True,
    system_message_token_count_fn: Optional[Callable[[], int]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Run the gate agent. Returns (payload_dict, channel_dump_dict).
    Skipped entirely on subsequent turns (is_new_conversation=False).
    """

    # On subsequent turns there is no work for the gate
    if not is_new_conversation:
        return {"conversation_title": ""}, {"thinking": "", "output": ""}

    # System prompt — instructs LLM to emit two channels
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
    # Cached message — reuses KV cache on repeated calls
    system_msg = create_cached_system_message([{"text": sys_prompt, "cache": True}])

    # Streaming callback — routes channel chunks to the UI
    async def _emit(**kwargs):
        channel = kwargs.pop("channel", None)
        text = kwargs.get("text") or ""
        # Only "thinking" channel is forwarded to the user in real time
        if channel == "thinking" and on_thinking_delta:
            await on_thinking_delta(text=text, completed=kwargs.get("completed", False))

    # Channel definitions:
    #   "thinking" = free-form text shown as "thinking" in UI
    #   "output"   = structured JSON validated against GateOut
    channels = [
        ChannelSpec(name="thinking", format="text", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="output", format="json", model=GateOut, replace_citations=False, emit_marker="subsystem"),
    ]

    async def _call_gate(*, blocks):
        """Inner function that calls the LLM and parses output."""
        messages = [system_msg, create_cached_human_message(blocks)]
        results, meta = await stream_with_channels(
            svc,
            messages=messages,
            role="gate.simple",           # resolved to concrete model via configuration
            channels=channels,
            emit=_emit,
            agent="gate.simple",
            max_tokens=800,               # gate output is tiny — keep cap low
            temperature=0.2,              # low temperature for deterministic titles
            return_full_raw=True,
        )
        # Propagate service-level errors (rate limit, provider down, etc.)
        service_error = (meta or {}).get("service_error")
        if service_error:
            raise ServiceException(ServiceError.model_validate(service_error))

        # Try auto-parsed Pydantic object first; fall back to raw JSON
        res = results.get("output")
        if res and res.obj and isinstance(res.obj, GateOut):
            payload = res.obj.model_dump()
        else:
            raw = (res.raw if res else "") or ""
            payload = {"conversation_title": ""}
            if raw:
                try:
                    parsed = GateOut.model_validate_json(raw)
                    payload = parsed.model_dump()
                except Exception:
                    payload = {"conversation_title": ""}

        # Capture raw channel text for debug logging
        channel_dump = {
            "thinking": (results.get("thinking").raw if results.get("thinking") else "") or "",
            "output": (results.get("output").raw if results.get("output") else "") or "",
        }

        return payload, channel_dump

    # When ctx_browser is available, retry_with_compaction auto-shrinks
    # context and retries on token-limit errors. Otherwise call directly.
    if ctx_browser:
        if system_message_token_count_fn is None:
            system_message_token_count_fn = lambda: token_count(sys_prompt)
        return await retry_with_compaction(
            ctx_browser=ctx_browser,
            system_text_fn=lambda: sys_prompt,
            system_message_token_count_fn=system_message_token_count_fn,
            render_params=render_params,
            agent_fn=_call_gate,
            emit_status=emit_status,
            sanitize_on_fail=sanitize_on_fail,
        )

    return await _call_gate(blocks=None)
