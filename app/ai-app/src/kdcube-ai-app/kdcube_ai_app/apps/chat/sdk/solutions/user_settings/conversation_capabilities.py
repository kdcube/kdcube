# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable capability snapshots for one user, agent, and conversation.

The Agent Card is the base for a newly started conversation. At the first
materialized read, this store snapshots that positive Card projection twice:
``base_projection`` records what the conversation inherited and ``projection``
records what it currently selects. ``base_card_revision`` records which Agent
Card revision supplied that base. Later picker writes replace only the current
projection and preserve both inherited facts.

Positive projections are intentional. The descriptor-derived Control Card is
the only ceiling; the Agent Card supplies the initial selection, not authority.
The finite conversation projection therefore keeps newly allowed capabilities
off until the user selects them, while current Control Card revocations take
effect immediately.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.store import (
    UserSettingsStore,
    utc_now_iso,
)


CONVERSATION_CAPABILITY_SUBSYSTEM = "agent_capabilities"
CONVERSATION_CAPABILITY_KEY_PREFIX = "agent_capability_selection:"
CONVERSATION_CAPABILITY_SCHEMA_VERSION = 2


def conversation_capability_selection_key(agent_id: str, *, conversation_id: str) -> str:
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise ValueError("conversation_id_required")
    agent = str(agent_id or "").strip() or "main"
    return f"conversation:{conversation}:{CONVERSATION_CAPABILITY_KEY_PREFIX}{agent}"


class ConversationCapabilitySelectionStore(UserSettingsStore):
    """PostgreSQL-backed, per-conversation capability selection."""

    @staticmethod
    def _projection(value: Any) -> dict[str, Any]:
        return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}

    @classmethod
    def _selection_from_record(
        cls,
        record: Optional[Mapping[str, Any]],
        *,
        conversation_id: str,
    ) -> Optional[dict[str, Any]]:
        if record is None:
            return None
        value = record.get("value")
        value = value if isinstance(value, Mapping) else {}
        return {
            "schema_version": CONVERSATION_CAPABILITY_SCHEMA_VERSION,
            "base_projection": cls._projection(value.get("base_projection")),
            "base_card_revision": max(
                0, int(value.get("base_card_revision") or 0)
            ),
            "projection": cls._projection(value.get("projection")),
            "scope": {
                "kind": "conversation",
                "conversation_id": str(conversation_id or "").strip(),
            },
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
        }

    async def read_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str,
    ) -> Optional[dict[str, Any]]:
        """Read an existing snapshot without creating one."""

        conversation = str(conversation_id or "").strip()
        record = await self.get_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=CONVERSATION_CAPABILITY_SUBSYSTEM,
            key=conversation_capability_selection_key(
                agent_id,
                conversation_id=conversation,
            ),
        )
        return self._selection_from_record(record, conversation_id=conversation)

    async def get_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str,
        base_projection: Mapping[str, Any],
        base_card_revision: int = 0,
        materialize: bool = True,
    ) -> dict[str, Any]:
        """Return the exact conversation snapshot, creating it once.

        ``put_record_if_absent`` makes simultaneous first reads converge on
        one base. Neither reader can replace a picker write that won the race.
        """

        conversation = str(conversation_id or "").strip()
        stored = await self.read_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation,
        )
        if stored is not None:
            return stored

        base = self._projection(base_projection)
        inherited_revision = max(0, int(base_card_revision or 0))
        if materialize:
            await self.put_record_if_absent(
                user_id=user_id,
                bundle_id=bundle_id,
                subsystem=CONVERSATION_CAPABILITY_SUBSYSTEM,
                key=conversation_capability_selection_key(
                    agent_id,
                    conversation_id=conversation,
                ),
                value={
                    "schema_version": CONVERSATION_CAPABILITY_SCHEMA_VERSION,
                    "base_projection": base,
                    "base_card_revision": inherited_revision,
                    "projection": copy.deepcopy(base),
                    "updated_at": utc_now_iso(),
                },
            )
            stored = await self.read_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
                conversation_id=conversation,
            )
            if stored is not None:
                return stored

        return {
            "schema_version": CONVERSATION_CAPABILITY_SCHEMA_VERSION,
            "base_projection": base,
            "base_card_revision": inherited_revision,
            "projection": copy.deepcopy(base),
            "scope": {
                "kind": "conversation",
                "conversation_id": conversation,
            },
            "created_at": "",
            "updated_at": "",
        }

    async def set_projection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        conversation_id: str,
        base_projection: Mapping[str, Any],
        base_card_revision: int = 0,
        projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace this conversation's positive selection, preserving its base."""

        conversation = str(conversation_id or "").strip()
        current = await self.get_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation,
            base_projection=base_projection,
            base_card_revision=base_card_revision,
            materialize=True,
        )
        await self.put_record(
            user_id=user_id,
            bundle_id=bundle_id,
            subsystem=CONVERSATION_CAPABILITY_SUBSYSTEM,
            key=conversation_capability_selection_key(
                agent_id,
                conversation_id=conversation,
            ),
            value={
                "schema_version": CONVERSATION_CAPABILITY_SCHEMA_VERSION,
                "base_projection": self._projection(current.get("base_projection")),
                "base_card_revision": max(
                    0, int(current.get("base_card_revision") or 0)
                ),
                "projection": self._projection(projection),
                "updated_at": utc_now_iso(),
            },
        )
        stored = await self.read_selection(
            user_id=user_id,
            bundle_id=bundle_id,
            agent_id=agent_id,
            conversation_id=conversation,
        )
        return stored or current


__all__ = [
    "CONVERSATION_CAPABILITY_KEY_PREFIX",
    "CONVERSATION_CAPABILITY_SCHEMA_VERSION",
    "CONVERSATION_CAPABILITY_SUBSYSTEM",
    "ConversationCapabilitySelectionStore",
    "conversation_capability_selection_key",
]
