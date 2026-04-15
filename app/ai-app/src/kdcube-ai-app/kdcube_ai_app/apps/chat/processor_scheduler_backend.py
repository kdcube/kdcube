# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional


if TYPE_CHECKING:
    from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor


SCHEDULER_BACKEND_LEGACY_LISTS = "legacy_lists"
SCHEDULER_BACKEND_CONVERSATION_STREAMS = "conversation_streams"


def normalize_processor_scheduler_backend(value: Optional[str]) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        return SCHEDULER_BACKEND_LEGACY_LISTS
    aliases = {
        "legacy": SCHEDULER_BACKEND_LEGACY_LISTS,
        "lists": SCHEDULER_BACKEND_LEGACY_LISTS,
        "legacy_lists": SCHEDULER_BACKEND_LEGACY_LISTS,
        "queue": SCHEDULER_BACKEND_LEGACY_LISTS,
        "legacy_queue": SCHEDULER_BACKEND_LEGACY_LISTS,
        "streams": SCHEDULER_BACKEND_CONVERSATION_STREAMS,
        "redis_streams": SCHEDULER_BACKEND_CONVERSATION_STREAMS,
        "conversation_streams": SCHEDULER_BACKEND_CONVERSATION_STREAMS,
    }
    normalized = aliases.get(raw)
    if normalized is None:
        raise ValueError(
            "Unknown processor scheduler backend "
            f"{value!r}. Expected one of: legacy_lists, conversation_streams."
        )
    return normalized


@dataclass(frozen=True)
class ProcessorSchedulingBackend:
    name: str

    def validate_startup(self, processor: "EnhancedChatRequestProcessor") -> None:
        del processor

    async def claim_next_task(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def recover_stale_claims(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class LegacyListsProcessorSchedulingBackend(ProcessorSchedulingBackend):
    name: str = SCHEDULER_BACKEND_LEGACY_LISTS

    async def claim_next_task(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> Optional[Dict[str, Any]]:
        return await processor._legacy_pop_any_queue_fair()

    async def recover_stale_claims(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> int:
        return await processor._legacy_requeue_stale_inflight_tasks()


@dataclass(frozen=True)
class ConversationStreamsProcessorSchedulingBackend(ProcessorSchedulingBackend):
    name: str = SCHEDULER_BACKEND_CONVERSATION_STREAMS

    def validate_startup(self, processor: "EnhancedChatRequestProcessor") -> None:
        raise RuntimeError(
            "CHAT_SCHEDULER_BACKEND=conversation_streams is not implemented yet. "
            "The processor backend abstraction is in place, but the conversation "
            "mailbox/lease/owner-loop scheduler has not been wired into proc yet. "
            f"Current processor instance={processor.process_id} must keep using {SCHEDULER_BACKEND_LEGACY_LISTS}."
        )

    async def claim_next_task(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> Optional[Dict[str, Any]]:
        del processor
        raise RuntimeError("conversation_streams backend is not implemented")

    async def recover_stale_claims(
        self,
        processor: "EnhancedChatRequestProcessor",
    ) -> int:
        del processor
        raise RuntimeError("conversation_streams backend is not implemented")


def build_processor_scheduler_backend(
    value: Optional[str],
) -> ProcessorSchedulingBackend:
    normalized = normalize_processor_scheduler_backend(value)
    if normalized == SCHEDULER_BACKEND_LEGACY_LISTS:
        return LegacyListsProcessorSchedulingBackend()
    if normalized == SCHEDULER_BACKEND_CONVERSATION_STREAMS:
        return ConversationStreamsProcessorSchedulingBackend()
    raise AssertionError(f"Unhandled processor scheduler backend {normalized!r}")
