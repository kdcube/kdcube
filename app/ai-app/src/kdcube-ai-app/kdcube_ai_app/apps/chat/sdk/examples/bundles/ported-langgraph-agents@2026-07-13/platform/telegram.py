# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── telegram.py ── the Telegram ingress seam ──
#
# A SECOND surface for the SAME research turn. Where the reactive chat turn is
# driven by a browser message, this seam is driven by a Telegram Bot API webhook.
# Both land on the SAME `execute_core` — the Telegram side only routes + renders;
# it duplicates no product (research) logic.
#
# All Telegram protocol mechanics — webhook secret verification, update parsing,
# attachment hydration, per-conversation turn locking, progress streaming, Bot API
# rendering, the Telegram user registry, and final delivery — are owned by the
# reusable SDK integration:
#
#   kdcube_ai_app.apps.chat.sdk.integrations.telegram
#
# This module is the thin bundle-side wiring that the SDK checklist prescribes:
#   1. bind the bundle-owned registry storage (under the bundle storage root),
#   2. delegate the webhook to `user_admin.handle_webhook(...)`,
#   3. wrap the turn run with `user_admin.run_with_queued_telegram_delivery(...)`
#      so the processor side (which sees the real turn result) delivers the answer.
#
# Nothing here knows how research works; the entrypoint keeps that in execute_core.

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

# Bundle importing the SDK uses ABSOLUTE imports (SDK contract); only bundle-local
# imports are package-relative.
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin

# Telegram echoes this header back on every webhook call; the SDK compares it,
# constant-time, against the configured per-integration webhook secret.
TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def storage_root_or_error(entrypoint: Any) -> Path:
    """The bundle storage root the Telegram user registry is written under.

    The research turn itself owns no bundle-local storage; the Telegram registry
    (chat/user metadata, conversation binding, webhook update-id claims) is the
    one bundle-owned store this second surface needs, and it is SDK-shaped."""
    root = entrypoint.bundle_storage_root()
    if not root:
        raise RuntimeError("Bundle storage backend is not configured for this bundle.")
    return root


def _telegram_user_admin_storage(entrypoint: Any) -> TelegramUserAdminStorage:
    return TelegramUserAdminStorage(storage_root_or_error(entrypoint))


def configure(*, bundle_id: str) -> None:
    """Bind the SDK Telegram user-admin subsystem to this bundle's storage.

    Called once at module load. No `migrate_telegram_user_to_kdcube_scope` hook:
    this app keeps no bundle-local per-user store to migrate — the preserved
    solution's per-user memory is keyed by the platform identity that the turn
    already carries (see identity.py), so a linked Telegram user simply resolves
    to a different platform identity and therefore a different memory key."""
    telegram_user_admin.configure_telegram_user_admin(
        storage_factory=_telegram_user_admin_storage,
        storage_root_or_error=storage_root_or_error,
        bundle_id=bundle_id,
    )


async def handle_webhook(entrypoint: Any, *, request: Any = None, **update: Any) -> dict:
    """Route one Telegram update to the shared turn path.

    `handle_webhook` verifies the webhook secret, claims the update id (dedupe),
    resolves the Telegram user + conversation binding, and then EITHER submits
    the message as `external_events[]` through the shared chat ingress (async
    submit — the processor runs the turn) OR, when no submitter is present, runs
    the turn inline. Either way the message drives the SAME `execute_core`."""
    return await telegram_user_admin.handle_webhook(entrypoint, request=request, **update)


async def run_turn_with_delivery(entrypoint: Any, *, runner: Callable[[], Any]) -> Any:
    """Wrap the turn run so a queued Telegram turn is delivered from the processor.

    For a browser turn there is no Telegram metadata on the request, so the SDK
    wrapper simply runs `runner()` and returns its result unchanged (the reactive
    chat surface is untouched). For a Telegram-originated turn the wrapper opens a
    progress card, runs the turn, and renders the final answer back over the Bot
    API — from the side that actually sees the turn result."""
    return await telegram_user_admin.run_with_queued_telegram_delivery(entrypoint, runner=runner)
