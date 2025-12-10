
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/sdk/comm/event_filter.py

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, Set


@dataclass(frozen=True)
class EventFilterInput:
    type: str
    route: Optional[str]
    socket_event: str
    agent: Optional[str]
    step: str
    status: str
    broadcast: bool

    @property
    def route_key(self) -> str:
        # Prefer explicit route from payload if present,
        # otherwise fall back to the actual emitted socket event name.
        return self.route or self.socket_event


class IEventFilter(ABC):
    @abstractmethod
    def allow_event(
        self,
        *,
        user_type: str,
        user_id: str,
        event: EventFilterInput,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        pass


class DefaultEventFilter(IEventFilter):
    """
    Policy:
      - privileged: allow everything
      - others:
          if route is chat.step (or chat_step):
              allow if type NOT in LIST_1 OR type IN LIST_2
          else:
              allow
    """

    LIST_1: Set[str] = {
        # block these types inside chat.step route
        # fill with your real restricted types
        "chat.step"
    }

    LIST_2: Set[str] = {
        # explicit exceptions / always-allow types
        # even if they appear in LIST_1
        "chat.conversation.accepted",
        "chat.conversation.title",
        "chat.conversation.topics",
        "chat.followups",
        "chat.clarification_questions",
        "chat.files",
        "chat.citations",
        "chat.turn.summary",
        "chat.conversation.turn.completed",
        "ticket"
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
