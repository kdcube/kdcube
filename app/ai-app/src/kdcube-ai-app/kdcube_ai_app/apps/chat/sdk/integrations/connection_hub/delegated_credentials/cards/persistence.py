# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube composition adapter for Connection Hub's durable card persistence."""

from __future__ import annotations

from typing import Any

from connection_hub.delegated_credentials.cards.credential_handles import (
    CardCredentialHandleStore,
)
from connection_hub.delegated_credentials.cards.persistence import (
    CardPersistence,
    DurableCardPersistence as _PortableDurableCardPersistence,
    LoadedCard,
)
from connection_hub.delegated_credentials.cards.service import CardMutationLock
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.service import (
    _kdcube_card_mutation_lock,
)


class DurableCardPersistence(_PortableDurableCardPersistence):
    """Connection Hub card persistence bound to KDCube's shared-storage lock."""

    def __init__(
        self,
        *,
        redis: Any,
        tenant: str,
        project: str,
        card_store: Any,
        settings: Any = None,
        mutation_lock: CardMutationLock | None = None,
        credential_handles: CardCredentialHandleStore | None = None,
    ) -> None:
        super().__init__(
            redis=redis,
            tenant=tenant,
            project=project,
            card_store=card_store,
            settings=settings,
            mutation_lock=mutation_lock or _kdcube_card_mutation_lock,
            credential_handles=credential_handles,
        )


__all__ = ["CardPersistence", "DurableCardPersistence", "LoadedCard"]
