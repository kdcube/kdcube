# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import logging
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, cron
from kdcube_ai_app.infra.service_hub.inventory import Config


BUNDLE_ID = "kdcube.admin"

logger = logging.getLogger(__name__)


@bundle_entrypoint(name=BUNDLE_ID, version="1.0.0", priority=100)
class AdminBundleEntrypoint(BaseEntrypoint):
    """Built-in admin-only bundle used as a safe default for UI access."""

    BUNDLE_ID = BUNDLE_ID

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ExternalEventPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )

    @property
    def configuration(self) -> Dict[str, Any]:
        return dict(super().configuration)

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return {
            "final_answer": (
                "**Admin bundle active.**\n\n"
                "No valid default AI bundle is configured.\n"
                "Use the **AI Bundles** admin panel to set or add a default bundle."
            )
        }

    @cron(alias="conversation-archive", cron_expression="20 2 * * *", span="system")
    async def archive_conversations(self) -> None:
        """Move conversation index rows older than the hot window to the cold tier.

        Once a day, one instance per tenant and project. On by default; the
        assembly property routines.conversation_store.archive_enabled: false
        turns it off. The window is routines.conversation_store.hot_days
        (default 90).
        Every step is recorded in conv_archive_batches, so a run that stops
        resumes on the next one.
        """
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import hot_cutoff, valid_hot_days

        settings = get_settings()
        if not settings.CONVERSATION_ARCHIVE_ENABLED:
            # Turned off by the assembly property
            # routines.conversation_store.archive_enabled: false.
            logger.info("[conversation-archive] off (routines.conversation_store.archive_enabled is false)")
            return
        hot_days = valid_hot_days(settings.CONVERSATION_HOT_DAYS)
        if hot_days is None:
            # Never fall back to another window: a typo must not archive the wrong rows.
            logger.error(
                "[conversation-archive] invalid hot_days %r (routines.conversation_store.hot_days or "
                "CONVERSATION_HOT_DAYS must be a whole number >= 1); nothing archived",
                settings.CONVERSATION_HOT_DAYS,
            )
            return
        index = ConvIndex(pool=self.pg_pool)
        if index._pool is None:
            await index.init()
        try:
            retention = index.retention()
            if retention is None:
                logger.warning("[conversation-archive] cold tier unavailable; nothing archived")
                return
            cutoff = hot_cutoff(hot_days)
            summary = await retention.archive_before(cutoff)
            stuck = summary.get("stuck", 0)
            logger.log(
                logging.WARNING if stuck else logging.INFO,
                "[conversation-archive] cutoff=%s hot_days=%s resumed=%s batches=%s rows=%s stuck=%s%s",
                cutoff.isoformat(), hot_days,
                summary["resumed"], summary["batches"], summary["rows"], stuck,
                f" stuck_batches={','.join(summary.get('stuck_batches') or [])}" if stuck else "",
            )
        finally:
            await index.close()
