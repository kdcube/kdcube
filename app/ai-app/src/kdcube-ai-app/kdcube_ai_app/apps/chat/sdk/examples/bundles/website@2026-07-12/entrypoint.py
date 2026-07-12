# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.bundle_loader import api, bundle_entrypoint, bundle_id


SITE_BUILD_COMMAND = "cp index.html site.js styles.css <VI_BUILD_DEST_ABSOLUTE_PATH>/"


@bundle_entrypoint(name="website", version="2026.07.12", priority=10)
@bundle_id(id="website@2026-07-12")
class WebsiteEntrypoint(BaseEntrypoint):
    """Own the reference website shell and its deployment-scoped composition."""

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "ui": {
                "main_view": {
                    "src_folder": "ui/site",
                    "build_command": SITE_BUILD_COMMAND,
                    "site": {
                        "enabled": False,
                        "alias": "workspace",
                        "default": False,
                        "hosts": [],
                        "title": "KDCube Workspace",
                        "scene_application_id": "workspace@2026-03-31-13-36",
                    },
                },
            },
        }

    @api(method="GET", alias="site_config", route="public")
    async def site_config(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        identity = self.runtime_identity()
        spec = getattr(self.config, "ai_bundle_spec", None)
        application_id = str(getattr(spec, "id", None) or "").strip()
        site = self.bundle_prop("ui.main_view.site", {}) or {}
        if not isinstance(site, dict):
            site = {}
        return {
            "application_id": application_id,
            "site_alias": str(site.get("alias") or "").strip(),
            "title": str(site.get("title") or "KDCube Workspace").strip(),
            "scene_application_id": str(site.get("scene_application_id") or "").strip(),
            "tenant": str(identity.get("tenant") or "").strip(),
            "project": str(identity.get("project") or "").strip(),
            "platform_config_url": "/api/cp-frontend-config",
            "profile_url": "/profile",
        }
