# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Runtime propagation for descriptor-owned platform settings."""

from kdcube_ai_app.infra.platform_settings.updates import (
    PLATFORM_SETTINGS_UPDATE_VERSION,
    PlatformSettingsUpdate,
    PlatformSettingsUpdateListener,
    platform_settings_update_channel,
    publish_platform_settings_update,
)

__all__ = [
    "PLATFORM_SETTINGS_UPDATE_VERSION",
    "PlatformSettingsUpdate",
    "PlatformSettingsUpdateListener",
    "platform_settings_update_channel",
    "publish_platform_settings_update",
]
