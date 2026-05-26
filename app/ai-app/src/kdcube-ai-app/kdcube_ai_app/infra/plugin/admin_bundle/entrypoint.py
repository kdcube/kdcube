# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.bundle_loader import api, bundle_entrypoint, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import Config


BUNDLE_ID = "kdcube.admin"


@bundle_entrypoint(name=BUNDLE_ID, version="1.0.0", priority=100)
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

    def configuration_defaults(self) -> Dict[str, Any]:
        admin_defaults = {
            "ui": {
                "widgets": {
                    "bundle_storage": {
                        "src_folder": "ui/storage",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), admin_defaults)

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:archive-box",
            "lucide": "Archive",
        },
        alias="bundle_storage",
        user_types=("privileged",),
    )
    def bundle_storage(self, user_id: str | None = None, **kwargs):
        del user_id, kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Bundle storage is served from the built widget source folder."
            "</div>"
        ]

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return {
            "final_answer": (
                "**Admin bundle active.**\n\n"
                "No valid default AI bundle is configured.\n"
                "Use the **AI Bundles** admin panel to set or add a default bundle."
            )
        }
