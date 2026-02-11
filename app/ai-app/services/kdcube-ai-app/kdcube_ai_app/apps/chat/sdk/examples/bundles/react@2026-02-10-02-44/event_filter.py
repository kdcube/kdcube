from typing import Set, Optional, Dict, Any

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import IEventFilter, EventFilterInput

class BundleEventFilter(IEventFilter):
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
        "chat.step",
    }

    LIST_2: Set[str] = {
        # explicit exceptions / always-allow types
        # even if they appear in LIST_1
        # "accounting.usage",
        "chat.conversation.accepted",
        "chat.conversation.title",
        "chat.conversation.topics",
        "chat.followups",
        "chat.clarification_questions",
        "chat.files",
        "chat.citations",
        # "chat.turn.summary",
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
