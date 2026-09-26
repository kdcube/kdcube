from __future__ import annotations

import pytest
from kdcube_ai_app.apps.chat.sdk.integrations import telegram
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.updates import (
    FileTelegramUpdateClaims,
    TelegramUpdateClaims,
    claim_telegram_update_once,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.user_storage import (
    TelegramUserAdminStorage,
)


class MemoryClaims:
    """An app's own claim store, e.g. a table keyed by update id on several replicas."""

    def __init__(self) -> None:
        self.seen: set[int] = set()

    async def claim_telegram_update(self, update_id: int) -> bool:
        if update_id in self.seen:
            return False
        self.seen.add(update_id)
        return True


@pytest.mark.asyncio
async def test_an_app_store_is_used_through_the_interface() -> None:
    store = MemoryClaims()
    assert isinstance(store, TelegramUpdateClaims)

    assert await claim_telegram_update_once(41, store=store) is True
    assert await claim_telegram_update_once("41", store=store) is False
    assert await claim_telegram_update_once(42, store=store) is True
    assert store.seen == {41, 42}


@pytest.mark.asyncio
async def test_an_update_without_a_readable_id_is_not_claimed() -> None:
    store = MemoryClaims()
    for value in (None, "", "abc", -1):
        assert await claim_telegram_update_once(value, store=store) is False
    assert store.seen == set()


@pytest.mark.asyncio
async def test_the_file_store_claims_once_and_never_reclaims_as_stale(tmp_path) -> None:
    storage = TelegramUserAdminStorage(tmp_path)
    claims = FileTelegramUpdateClaims(storage)

    assert await claim_telegram_update_once(7, store=claims) is True
    assert await claim_telegram_update_once(7, store=claims) is False
    # The claim is completed at once: no processing lock is left to go stale.
    again = storage.claim_telegram_update(update_id="7", stale_after_seconds=0)
    assert again["claimed"] is False and again["status"] == "completed"


@pytest.mark.asyncio
async def test_the_file_store_is_the_default_for_an_entrypoint(tmp_path, monkeypatch) -> None:
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    storage = TelegramUserAdminStorage(tmp_path)
    entrypoint = object()
    asked: list[object] = []

    def fake_storage(value):
        asked.append(value)
        return storage

    monkeypatch.setattr(user_admin, "storage", fake_storage)

    assert await claim_telegram_update_once(9, entrypoint=entrypoint) is True
    assert await claim_telegram_update_once(9, entrypoint=entrypoint) is False
    assert asked == [entrypoint, entrypoint]


def test_the_claim_api_is_exported_from_the_package() -> None:
    assert telegram.claim_telegram_update_once is claim_telegram_update_once
    assert telegram.TelegramUpdateClaims is TelegramUpdateClaims
    assert telegram.FileTelegramUpdateClaims is FileTelegramUpdateClaims
