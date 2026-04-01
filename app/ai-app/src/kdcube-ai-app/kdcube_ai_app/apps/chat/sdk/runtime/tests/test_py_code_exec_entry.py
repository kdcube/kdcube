# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import pathlib
import sys

from kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry import (
    _ensure_dynamic_package_chain,
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


def test_ensure_dynamic_package_chain_creates_parent_packages(tmp_path):
    bundle_root = tmp_path / "bundle"
    tools_dir = bundle_root / "tools"
    tools_dir.mkdir(parents=True)
    tool_file = tools_dir / "preference_tools.py"
    tool_file.write_text("", encoding="utf-8")

    root_name = "dynpkg_60bebbdd6f"
    tools_name = f"{root_name}.tools"
    full_name = f"{tools_name}.preference_tools"

    for name in (root_name, tools_name):
        sys.modules.pop(name, None)

    _ensure_dynamic_package_chain(full_name, tool_file)

    assert root_name in sys.modules
    assert tools_name in sys.modules
    assert pathlib.Path(sys.modules[root_name].__path__[0]).resolve() == bundle_root.resolve()  # type: ignore[attr-defined]
    assert pathlib.Path(sys.modules[tools_name].__path__[0]).resolve() == tools_dir.resolve()  # type: ignore[attr-defined]
