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

        Once a day, one instance per tenant and project, only when the
        assembly property routines.conversation_store.archive_enabled is true
        (default off). The window is routines.conversation_store.hot_days
        (default 90).
        Every step is recorded in conv_archive_batches, so a run that stops
        resumes on the next one.
        """
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import hot_cutoff

        settings = get_settings()
        if not settings.CONVERSATION_ARCHIVE_ENABLED:
            # Retention is the operator's decision: nothing moves until the
            # assembly property routines.conversation_store.archive_enabled is true.
            logger.info("[conversation-archive] off (routines.conversation_store.archive_enabled is not true)")
            return
        index = ConvIndex(pool=self.pg_pool)
        if index._pool is None:
            await index.init()
        try:
            retention = index.retention()
            if retention is None:
                logger.warning("[conversation-archive] cold tier unavailable; nothing archived")
                return
            cutoff = hot_cutoff(settings.CONVERSATION_HOT_DAYS)
            summary = await retention.archive_before(cutoff)
            logger.info(
                "[conversation-archive] cutoff=%s hot_days=%s resumed=%s batches=%s rows=%s",
                cutoff.isoformat(), settings.CONVERSATION_HOT_DAYS,
                summary["resumed"], summary["batches"], summary["rows"],
            )
        finally:
            await index.close()
