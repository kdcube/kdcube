# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/context/vector/conv_ticket_index.py
from __future__ import annotations

from typing import Optional, List, Dict, Any, Callable, Awaitable
import datetime, asyncio

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore, Ticket


class ConvTicketIndex:
    """
    Thin helper over the ConvTicketStore APIs tailored for conversation tracks.
    It provides:
      - fetching the latest open ticket for a given (user, conversation, track)
      - opening a clarification ticket from a list of questions
      - resolving (closing) a ticket with a result tag and optional answer text
      - parsing questions from a ticket description
    """

    def __init__(self, store: ConvTicketStore):
        # has methods: list_tickets(conversation_id, status?), create_ticket(...), update_ticket(...), get_ticket(ticket_id)
        self.store = store

    @staticmethod
    def _now_iso() -> str:
        return datetime.datetime.utcnow().isoformat() + "Z"

    @staticmethod
    def get_route_decision(t: Ticket) -> List[str]:
        d = dict(getattr(t, "data", {}) or {})
        return d.get("route_decision")

    @staticmethod
    def get_context_pins(t: Ticket) -> List[str]:
        d = dict(getattr(t, "data", {}) or {})
        pins = d.get("context_pins") or []
        return [p for p in pins if isinstance(p, str) and p.strip()]

    @staticmethod
    def get_memory_pins(t: Ticket) -> List[str]:
        d = dict(getattr(t, "data", {}) or {})
        return [p for p in (d.get("memory_pins") or []) if isinstance(p, str) and p.strip()]

    @staticmethod
    def get_selected_bucket_ids(t: Ticket) -> List[str]:
        d = dict(getattr(t, "data", {}) or {})
        return [bid for bid in (d.get("selected_bucket_ids") or []) if isinstance(bid, str) and bid.strip()]

    async def set_context_pins(self, *, ticket_id: str, pins: List[str]) -> None:
        await self.store.update_ticket(
            ticket_id=ticket_id,
            data_patch={"context_pins": list(dict.fromkeys([p for p in pins if p]))},
            embed_texts_fn=None  # not re-embedding for data patches
        )

    @staticmethod
    def parse_questions(description: str) -> List[str]:
        """
        Extract question lines from a ticket description.
        Recognizes lines starting with '-', '*', or numeric bullets like '1)', '1.'.
        """
        if not isinstance(description, str):
            return []
        lines = [ln.strip() for ln in description.splitlines() if ln.strip()]
        out: List[str] = []
        for ln in lines:
            low = ln.lower()
            if low.startswith("questions:"):
                continue
            # bullets
            if ln.startswith(("-", "*")):
                q = ln[1:].strip()
                if q:
                    out.append(q)
                continue
            # numeric bullets
            import re
            if re.match(r"^\s*\d+[\.\)]\s+", ln):
                q = re.sub(r"^\s*\d+[\.\)]\s+", "", ln).strip()
                if q:
                    out.append(q)
                continue
            # Q: prefix
            if low.startswith("q:") or low.startswith("q)"):
                q = ln[2:].strip(" :)")
                if q:
                    out.append(q.strip())
        return out

    async def fetch_latest_open_ticket(self, *, user_id: str, conversation_id: str,
                                       turn_id: Optional[str] = None, ) -> Optional[Ticket]:
        """
        Return the most recently updated OPEN ticket for the conversation, or None.
        """
        tickets = await self.store.list_tickets(user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, status="open")
        # list_tickets already returns ordered by updated_at DESC per store implementation
        return next(iter(tickets), None)

    async def open_clarification_ticket(
            self,
            *,
            user_id: str,
            conversation_id: str,
            title: str,
            questions: List[str],
            embed_texts_fn: Callable[[List[str]], Awaitable[List[Any]]],
            tags: Optional[List[str]] = None,
            assignee: Optional[str] = None,
            priority: int = 3,
            extra_context: Optional[str] = None,
            turn_id: Optional[str] = None,
            data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Ticket]:
        """
        Create a new clarification ticket containing the provided questions.
        """
        tags = list(tags or [])
        if "clarification" not in tags:
            tags.append("clarification")
        if "qa" not in tags:
            tags.append("qa")
        desc_lines: List[str] = []
        if extra_context:
            desc_lines.append(f"Context: {extra_context.strip()}")
        if questions:
            desc_lines.append("Questions:")
            for i, q in enumerate(questions, 1):
                desc_lines.append(f"{i}. {q}")
        desc_lines.append(f"(opened_at: {self._now_iso()})")
        description = "\n".join(desc_lines)
        ticket = await self.store.create_ticket(
            embed_texts_fn=embed_texts_fn,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            title=title,
            description=description,
            priority=priority,
            tags=tags,
            assignee=assignee,
            data=(data or {}),
        )
        return ticket

    async def resolve_ticket(
            self,
            *,
            ticket_id: str,
            answered: bool,
            embed_texts_fn: Callable[[List[str]], Awaitable[List[Any]]],
            answer_text: Optional[str] = None,
            append_to_description: bool = True,
            result_tag_prefix: str = "result"
    ) -> None:
        """
        Close the ticket, tagging with an outcome. Optionally append the user's answer into the description.
        """
        try:
            t = await self.store.get_ticket(ticket_id)
        except Exception:
            t = None

        tags = list(getattr(t, "tags", []) or [])
        # remove prior result:* tags
        tags = [tg for tg in tags if not tg.startswith(f"{result_tag_prefix}:")]
        tags.append(f"{result_tag_prefix}:{'answered' if answered else 'none'}")

        new_desc = None
        if append_to_description:
            prev = getattr(t, "description", "") or ""
            lines = [prev.strip(), "", f"(resolved_at: {self._now_iso()}, outcome: {'answered' if answered else 'none'})"]
            if answered and answer_text:
                lines.append("User answer (verbatim):")
                lines.append(answer_text.strip())
            new_desc = "\n".join([ln for ln in lines if ln is not None])

        await self.store.update_ticket(
            ticket_id=ticket_id,
            status="closed",
            tags=tags,
            embed_texts_fn=embed_texts_fn,
            description=new_desc if new_desc is not None else getattr(t, "description", None)
        )
