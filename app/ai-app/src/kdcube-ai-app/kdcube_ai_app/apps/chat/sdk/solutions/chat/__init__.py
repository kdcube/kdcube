"""Reusable chat solution surfaces."""

from kdcube_ai_app.apps.chat.sdk.solutions.chat.backend import (
    CHAT_WIDGET_SDK_SOURCE,
    DEFAULT_CHAT_WIDGET_ALIAS,
    DEFAULT_CHAT_WIDGET_BUILD_COMMAND,
    apply_chat_widget_engine,
    chat_widget_ui_config,
    default_chat_widget_config,
)

__all__ = [
    "CHAT_WIDGET_SDK_SOURCE",
    "DEFAULT_CHAT_WIDGET_ALIAS",
    "DEFAULT_CHAT_WIDGET_BUILD_COMMAND",
    "chat_widget_ui_config",
    "apply_chat_widget_engine",
    "default_chat_widget_config",
]
