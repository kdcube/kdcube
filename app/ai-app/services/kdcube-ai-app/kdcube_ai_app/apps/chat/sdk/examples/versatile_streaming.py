# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Minimal local test for versatile_streamer.
Run similar to multimodal_streaming_with_accounting.py (see that file for env prerequisites).
"""

from __future__ import annotations

import asyncio
from typing import Dict, Any, List

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError
from kdcube_ai_app.infra.service_hub.inventory import (
    ConfigRequest,
    create_workflow_config,
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
ROLE_GATE = "gate.simple"


def configure_env() -> ModelServiceBase:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
    settings = get_settings()
    req = ConfigRequest(
        openai_api_key=settings.OPENAI_API_KEY,
        claude_api_key=settings.ANTHROPIC_API_KEY,
        google_api_key=settings.GOOGLE_API_KEY,
        selected_model=DEFAULT_MODEL,
        role_models={
            ROLE_GATE: {"provider": "anthropic", "model": DEFAULT_MODEL},
        },
    )
    return ModelServiceBase(create_workflow_config(req))


async def main():
    # Build model service (mirrors multimodal_streaming_with_accounting.py setup)
    model_service = configure_env()

    # Static input
    user_text = "Is there a MIME type that identifies Markdown files?"

    system_msg = create_cached_system_message([
        {
            "type": "text",
            "text": (
                "You are a gate agent. Output ONLY channel-tagged content.\n\n"
                "Required output protocol:\n"
                "<channel:thinking>...private reasoning...</channel:thinking>\n"
                "<channel:output>{\"conversation_title\": \"...\"}</channel:output>\n\n"
                "The conversation_title must be <= 6 words."
            ),
            "cache": True,
        }
    ])

    sources_list = [
        {
            "sid": 1,
            "title": "RFC 7763 — The text/markdown Media Type",
            "url": "https://www.rfc-editor.org/rfc/rfc7763",
            "text": "This document registers the text/markdown media type and the Markdown format.",
        },
        {
            "sid": 2,
            "title": "CommonMark spec",
            "url": "https://spec.commonmark.org/",
            "text": "A strongly defined, highly compatible specification of Markdown.",
        },
    ]

    user_msg = create_cached_human_message([
        {"type": "text", "text": user_text, "cache": True},
        {"type": "text", "text": "[SOURCES]\n" + "\n\n".join(
            f"[S:{s['sid']}] {s['title']} {s['url']}\n{s.get('text','')}" for s in sources_list
        ), "cache": False},
    ])

    channels = [
        ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="output", format="json", replace_citations=False, emit_marker="answer"),
    ]

    async def _emit_delta(**kwargs):
        channel = kwargs.get("channel")
        agent = kwargs.get("agent")
        print(f"[delta] idx={kwargs.get('index')} marker={kwargs.get('marker')} agent={agent} channel={channel} :: {kwargs.get('text')}")

    print("[step] versatile_streaming started")

    results, meta = await stream_with_channels(
        model_service,
        messages=[system_msg, user_msg],
        role=ROLE_GATE,
        channels=channels,
        emit=_emit_delta,
        agent=ROLE_GATE,
        artifact_name="gate.output",
        sources_list=sources_list,
        max_tokens=600,
        temperature=0.2,
        return_full_raw=True,
    )
    service_error = (meta or {}).get("service_error")
    if service_error:
        raise ServiceException(ServiceError.model_validate(service_error))

    print("[step] versatile_streaming completed")

    print("\n--- RESULTS ---")
    for k, v in results.items():
        print(k, "::", v.raw.strip())
    print("\n--- FULL RAW ---")
    print(meta.get("raw", ""))
    if meta.get("service_error"):
        print("\n--- SERVICE ERROR ---")
        print(meta.get("service_error"))


if __name__ == "__main__":
    asyncio.run(main())
