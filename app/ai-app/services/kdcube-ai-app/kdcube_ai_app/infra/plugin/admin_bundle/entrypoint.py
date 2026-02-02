# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.infra.service_hub.inventory import Config


BUNDLE_ID = "kdcube.admin"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class AdminBundleEntrypoint(BaseEntrypoint):
    """Built-in admin-only bundle used as a safe default for UI access."""

    BUNDLE_ID = BUNDLE_ID

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
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
