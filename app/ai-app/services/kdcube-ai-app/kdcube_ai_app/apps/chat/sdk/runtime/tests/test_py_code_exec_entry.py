# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json

from kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry import (
    _hydrate_runtime_payload_from_secret,
)


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def log(self, message: str, level: str = "INFO") -> None:
        self.messages.append((level, message))


def test_hydrate_runtime_payload_from_secret_restores_env(monkeypatch):
    monkeypatch.delenv("RUNTIME_GLOBALS_JSON", raising=False)
    monkeypatch.delenv("RUNTIME_TOOL_MODULES", raising=False)
    monkeypatch.setenv("KDCUBE_EXEC_PAYLOAD_SECRET_ID", "secret-id")
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry.get_exec_payload_secret",
        lambda **_kwargs: {
            "runtime_globals": {"PORTABLE_SPEC_JSON": "{\"ok\":true}"},
            "tool_module_names": ["dyn_exec_tools_1"],
            "env": {"OPENAI_API_KEY": "secret"},
        },
    )
    logger = _CaptureLogger()

    _hydrate_runtime_payload_from_secret(logger)

    assert json.loads(__import__("os").environ["RUNTIME_GLOBALS_JSON"])["PORTABLE_SPEC_JSON"] == "{\"ok\":true}"
    assert json.loads(__import__("os").environ["RUNTIME_TOOL_MODULES"]) == ["dyn_exec_tools_1"]
    assert __import__("os").environ["OPENAI_API_KEY"] == "secret"
    assert any("hydrate start" in msg for _, msg in logger.messages)
    assert any("Restored runtime payload from secret secret-id" in msg for _, msg in logger.messages)


def test_hydrate_runtime_payload_from_secret_skips_when_inline_payload_present(monkeypatch):
    monkeypatch.setenv("KDCUBE_EXEC_PAYLOAD_SECRET_ID", "secret-id")
    monkeypatch.setenv("RUNTIME_GLOBALS_JSON", "{}")
    monkeypatch.setenv("RUNTIME_TOOL_MODULES", "[]")
    logger = _CaptureLogger()

    _hydrate_runtime_payload_from_secret(logger)

    assert any("hydrate start" in msg for _, msg in logger.messages)
    assert any("skipping secret hydration" in msg for _, msg in logger.messages)
