"""Reusable chat solution mount helpers.

Bundles can mount the shared chat widget with ``chat_widget_ui_config()`` and
provide their own event-source ids through Vite environment variables. The
widget defaults are intentionally generic; bundle-specific names belong in the
consumer bundle config.
"""

from __future__ import annotations

import shlex
from typing import Any, Mapping


CHAT_WIDGET_SDK_SOURCE = "sdk://solutions/chat/ui/widget"
DEFAULT_CHAT_WIDGET_BUILD_COMMAND = (
    "npm install --no-package-lock && "
    "OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
)


def chat_widget_ui_config(
    *,
    enabled: bool = True,
    src_folder: str = CHAT_WIDGET_SDK_SOURCE,
    build_command: str = DEFAULT_CHAT_WIDGET_BUILD_COMMAND,
    vite_env: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Return a bundle ``config.ui.widgets.<alias>`` entry for the chat widget."""

    if vite_env:
        env_prefix = " ".join(
            f"{key}={shlex.quote(str(value))}"
            for key, value in vite_env.items()
            if str(key).startswith("VITE_") and value is not None
        )
        if env_prefix:
            build_command = f"{env_prefix} {build_command}"

    return {
        "enabled": enabled,
        "src_folder": src_folder,
        "build_command": build_command,
        **extra,
    }


__all__ = [
    "CHAT_WIDGET_SDK_SOURCE",
    "DEFAULT_CHAT_WIDGET_BUILD_COMMAND",
    "chat_widget_ui_config",
]
