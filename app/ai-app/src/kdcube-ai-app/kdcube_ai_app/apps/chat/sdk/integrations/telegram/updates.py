"""Seeing each Telegram update once, with the claim kept where the app chooses.

Telegram resends an update until the webhook answers 200, and one app may run
on several replicas. ``claim_telegram_update_once`` is the one entry point: it
answers True the first time an ``update_id`` is seen and False afterwards.
``release_telegram_update`` gives a claim back when handling failed before
the webhook answered, so Telegram's resend is handled instead of dropped.

The claim lives in a store that implements ``TelegramUpdateClaims``. The
default is the file-backed ``TelegramUserAdminStorage`` (one node). An app on
several replicas passes a store of its own, for example a table with the
update id as its key, through the same interface.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from kdcube_ai_app.apps.chat.sdk.integrations.telegram.user_storage import (
    TelegramUserAdminStorage,
)


@runtime_checkable
class TelegramUpdateClaims(Protocol):
    """A store that records each Telegram ``update_id`` once."""

    async def claim_telegram_update(self, update_id: int) -> bool:
        """True the first time ``update_id`` is claimed, False afterwards."""
        ...

    async def release_telegram_update(self, update_id: int) -> None:
        """Forget a claim, so the next delivery of ``update_id`` is claimed again."""
        ...


class FileTelegramUpdateClaims:
    """The default store: the file-backed Telegram user admin storage.

    Its own claim is a processing lock that a handler later completes or
    fails. Here the claim is once-only, so a claimed update is completed at
    once: a resend is refused as completed and never reclaimed as stale.
    """

    def __init__(self, storage: TelegramUserAdminStorage) -> None:
        self._storage = storage

    async def claim_telegram_update(self, update_id: int) -> bool:
        update = str(update_id)
        claim = await asyncio.to_thread(self._storage.claim_telegram_update, update_id=update)
        if not claim.get("claimed"):
            return False
        await asyncio.to_thread(
            self._storage.complete_telegram_update,
            update_id=update,
            result={"stage": "claimed_once"},
        )
        return True

    async def release_telegram_update(self, update_id: int) -> None:
        await asyncio.to_thread(self._storage.release_telegram_update, update_id=str(update_id))


def _update_id(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


async def claim_telegram_update_once(
    update_id: Any,
    *,
    store: TelegramUpdateClaims | None = None,
    entrypoint: Any = None,
) -> bool:
    """True the first time this update is seen, False for a repeat.

    ``store`` is the app's claim store; without one, the Telegram user admin
    file storage configured for ``entrypoint`` is used. An update without a
    readable ``update_id`` cannot be told apart from another and is not
    claimed (False).
    """
    number = _update_id(update_id)
    if number is None:
        return False
    if store is None:
        from kdcube_ai_app.apps.chat.sdk.integrations.telegram.user_admin import storage

        store = FileTelegramUpdateClaims(storage(entrypoint))
    return bool(await store.claim_telegram_update(number))


async def release_telegram_update(
    update_id: Any,
    *,
    store: TelegramUpdateClaims | None = None,
    entrypoint: Any = None,
) -> None:
    """Give back the claim on an update whose handling failed.

    Call it when the webhook will not answer 200 (an unexpected error), so
    Telegram's resend is handled rather than dropped as a duplicate.
    """
    number = _update_id(update_id)
    if number is None:
        return
    if store is None:
        from kdcube_ai_app.apps.chat.sdk.integrations.telegram.user_admin import storage

        store = FileTelegramUpdateClaims(storage(entrypoint))
    await store.release_telegram_update(number)


__all__ = [
    "FileTelegramUpdateClaims",
    "TelegramUpdateClaims",
    "claim_telegram_update_once",
    "release_telegram_update",
]
