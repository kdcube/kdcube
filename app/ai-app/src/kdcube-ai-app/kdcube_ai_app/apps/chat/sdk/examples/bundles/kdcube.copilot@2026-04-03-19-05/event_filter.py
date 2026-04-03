# ── event_filter.py ──
# Controls which real-time events (SSE/WebSocket) reach the end user.
#
# The platform emits events as a turn progresses: thinking steps, tool calls,
# citations, status updates, etc. Not every event should be visible to every
# user tier. This module implements IEventFilter which the base entrypoint
# calls on every outgoing event — return True to send, False to suppress.
#
# How it works (two-list model):
#   LIST_1 — blocklist: event types suppressed for regular users
#   LIST_2 — allowlist: overrides the blocklist (always delivered)
#
# Decision logic (for non-privileged users on the chat_step route):
#   1. If event type NOT in LIST_1 → allow
#   2. If event type IN LIST_1 but ALSO in LIST_2 → allow (override)
#   3. Otherwise → block
#
# Privileged users bypass all filtering (see everything).
#
# To customise:
#   - Add internal/debug event types to LIST_1
#   - Add user-facing event types to LIST_2

from typing import Set, Optional, Dict, Any

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import IEventFilter, EventFilterInput

class BundleEventFilter(IEventFilter):
    """Two-list event filter: blocklist (LIST_1) + allowlist (LIST_2)."""

    # Blocklist — event types blocked for regular users on the chat_step route
    LIST_1: Set[str] = {
        "chat.step",
    }

    # Allowlist — always delivered even if they match LIST_1
    LIST_2: Set[str] = {
        # "accounting.usage",          # uncomment to show token usage to users
        "chat.conversation.accepted",  # conversation accepted by the system
        "chat.conversation.title",     # conversation title (from gate agent)
        "chat.conversation.topics",    # detected conversation topics
        "chat.followups",              # suggested follow-up questions
        "chat.clarification_questions",# clarification prompts from the agent
        "chat.files",                  # file attachments in the response
        "chat.citations",              # source citations
        # "chat.turn.summary",         # uncomment to show turn summaries
        "chat.conversation.turn.completed",  # signals the turn is done
        "ticket"                       # support / context ticket events
    }

    def allow_event(
            self,
            *,
            user_type: str,
            user_id: str,
            event: EventFilterInput,
            data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Decide whether event should be sent to the connected client."""
        ut = (user_type or "anonymous").lower()

        # Privileged users (admins / debuggers) see everything
        if ut == "privileged":
            return True

        # Only apply blocklist/allowlist on the chat_step route;
        # all other routes pass through unfiltered
        rk = event.route_key
        if rk in ("chat_step"):
            t = event.type or ""
            return (t not in self.LIST_1) or (t in self.LIST_2)

        return True
