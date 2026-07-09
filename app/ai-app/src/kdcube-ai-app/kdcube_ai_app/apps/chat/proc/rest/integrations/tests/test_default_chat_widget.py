# SPDX-License-Identifier: MIT

"""The reserved default-chat widget follows the descriptor declaration.

An app declares its chat surface with ``surfaces.as_provider.bundle.default_chat``;
the SDK serves the chat widget under the reserved ``chat`` alias for declaring
apps and the platform gates listing/serving on that same declaration.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.proc.rest.integrations.integrations import (
    _raw_static_widget_config,
    _static_widget_config,
    is_widget_enabled,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chat import (
    CHAT_WIDGET_SDK_SOURCE,
    DEFAULT_CHAT_WIDGET_ALIAS,
)
from kdcube_ai_app.infra.plugin.bundle_loader import UIWidgetSpec

DECLARED = {"surfaces": {"as_provider": {"bundle": {"default_chat": True}}}}

CHAT_SPEC = UIWidgetSpec(method_name="default_chat_widget", alias=DEFAULT_CHAT_WIDGET_ALIAS, icon={})
OTHER_SPEC = UIWidgetSpec(method_name="settings", alias="settings", icon={})


def test_chat_widget_enabled_only_for_declaring_bundles() -> None:
    assert is_widget_enabled(DECLARED, CHAT_SPEC) is True
    assert is_widget_enabled({}, CHAT_SPEC) is False
    assert is_widget_enabled(None, CHAT_SPEC) is False
    # Widgets under other aliases keep the default-enabled semantics.
    assert is_widget_enabled({}, OTHER_SPEC) is True


def test_explicit_enabled_entry_wins_over_declaration() -> None:
    declared_but_off = {
        **DECLARED,
        "enabled": {"widget": {DEFAULT_CHAT_WIDGET_ALIAS: False}},
    }
    assert is_widget_enabled(declared_but_off, CHAT_SPEC) is False
    undeclared_but_on = {"enabled": {"widget": {DEFAULT_CHAT_WIDGET_ALIAS: True}}}
    assert is_widget_enabled(undeclared_but_on, CHAT_SPEC) is True


def test_declared_bundle_gets_sdk_chat_widget_build_config() -> None:
    cfg = _static_widget_config(DECLARED, widget_alias=DEFAULT_CHAT_WIDGET_ALIAS)
    assert cfg is not None
    assert cfg["src_folder"] == CHAT_WIDGET_SDK_SOURCE
    assert "npm run build" in cfg["build_command"]
    assert set(cfg["shared_sources"]) == {"components_core", "components_react"}


def test_explicit_widget_config_wins_over_default() -> None:
    own = {
        **DECLARED,
        "ui": {"widgets": {DEFAULT_CHAT_WIDGET_ALIAS: {"src_folder": "ui/chat", "build_command": "npm run build"}}},
    }
    cfg = _raw_static_widget_config(own, widget_alias=DEFAULT_CHAT_WIDGET_ALIAS)
    assert cfg == {"src_folder": "ui/chat", "build_command": "npm run build"}


def test_undeclared_bundle_serves_no_chat_widget_config() -> None:
    assert _raw_static_widget_config({}, widget_alias=DEFAULT_CHAT_WIDGET_ALIAS) is None
    assert _static_widget_config({}, widget_alias=DEFAULT_CHAT_WIDGET_ALIAS) is None
