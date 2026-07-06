# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""User settings — durable per-user choices over ``user_bundle_props``.

``store`` is the generic core (table access + conventions); concrete stores
build on it — ``agent_selection`` carries the per-agent selection record
(deny-list toggles, model pick, cache policy, pending deltas).
"""

from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.agent_selection import (
    AGENT_SELECTION_KEY_PREFIX,
    AGENT_SELECTION_SUBSYSTEM,
    UserAgentSelectionStore,
    agent_selection_key,
    merge_selection_patch,
)
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings.store import (
    PLATFORM_WIDE_BUNDLE_ID,
    USER_SETTINGS_TABLE,
    UserSettingsStore,
    json_value,
    utc_now_iso,
)

__all__ = [
    "AGENT_SELECTION_KEY_PREFIX",
    "AGENT_SELECTION_SUBSYSTEM",
    "PLATFORM_WIDE_BUNDLE_ID",
    "USER_SETTINGS_TABLE",
    "UserAgentSelectionStore",
    "UserSettingsStore",
    "agent_selection_key",
    "json_value",
    "merge_selection_patch",
    "utc_now_iso",
]
