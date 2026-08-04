# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── event_filter.py ──
# Event filter for the comparison bundle. Same pattern as the react bundle.

from typing import Set, Optional, Dict, Any

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import IEventFilter, EventFilterInput


class BundleEventFilter(IEventFilter):
    LIST_1: Set[str] = {
        "chat.step",
    }

    LIST_2: Set[str] = {
        "chat.conversation.accepted",
        "chat.conversation.title",
        "chat.conversation.topics",
        "chat.followups",
        "chat.clarification_questions",
        "chat.files",
        "chat.citations",
        "chat.conversation.turn.completed",
        "ticket",
    }

    def allow_event(
            self,
            *,
            user_type: str,
            user_id: str,
            event: EventFilterInput,
            data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        ut = (user_type or "anonymous").lower()
        if ut == "privileged":
            return True
        rk = event.route_key
        if rk in ("chat_step"):
            t = event.type or ""
            return (t not in self.LIST_1) or (t in self.LIST_2)
        return True
