# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import json
import os
import types
import pathlib
import shutil
import sys
import importlib

from kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry import (
    _bootstrap_supervisor_runtime,
    _build_executor_runtime_globals,
    _ensure_dynamic_package_chain,
    _hydrate_runtime_payload_from_secret,
    _materialize_runtime_descriptor_payloads,
    _prepare_runtime_environment,
)
from kdcube_ai_app.apps.chat.sdk.runtime.isolated.supervisor_entry import PrivilegedSupervisor
from kdcube_ai_app.apps.chat.sdk.runtime.isolated.secure_client import ToolStub
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_from_file


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def log(self, message: str, level: str = "INFO") -> None:
        self.messages.append((level, message))


def test_supervisor_allowed_peer_uids_default_to_executor_uid(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_ALLOWED_UIDS", raising=False)

    assert PrivilegedSupervisor._allowed_peer_uids() == {1001}


def test_supervisor_allowed_peer_uids_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_ALLOWED_UIDS", "42, 43")

    assert PrivilegedSupervisor._allowed_peer_uids() == {42, 43}


def test_supervisor_rejects_missing_or_wrong_socket_token(tmp_path):
    sup = PrivilegedSupervisor.__new__(PrivilegedSupervisor)
    sup.auth_token = "secret-token"

    assert sup._auth_error({}) == "Unauthorized supervisor request"
    assert sup._auth_error({"auth_token": "wrong"}) == "Unauthorized supervisor request"
    assert sup._auth_error({"auth_token": "secret-token"}) is None


def test_tool_stub_includes_socket_auth_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_AUTH_TOKEN", "secret-token")

    payload = ToolStub(socket_path="/tmp/supervisor.sock")._build_payload(
        tool_id="web_tools.web_search",
        params={"q": "x"},
        reason="test",
    )

    assert payload["auth_token"] == "secret-token"
    assert payload["tool_id"] == "web_tools.web_search"
    assert payload["params"] == {"q": "x"}


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


def test_materialize_runtime_descriptor_payloads_restores_root_only_descriptor_files(monkeypatch):
    exec_id = "exec-descriptor-test"
    runtime_dir = pathlib.Path("/tmp/kdcube-runtime-descriptors") / exec_id
    shutil.rmtree(runtime_dir, ignore_errors=True)

    monkeypatch.setenv("EXECUTION_ID", exec_id)
    monkeypatch.setenv(
        "KDCUBE_RUNTIME_ASSEMBLY_YAML_B64",
        base64.b64encode(b"context:\n  tenant: demo\n").decode("ascii"),
    )
    monkeypatch.setenv(
        "KDCUBE_RUNTIME_BUNDLES_YAML_B64",
        base64.b64encode(b"bundles:\n  demo: {}\n").decode("ascii"),
    )
    monkeypatch.setenv(
        "KDCUBE_RUNTIME_SECRETS_YAML_B64",
        base64.b64encode(b"secrets:\n  services:\n    openai:\n      api_key: x\n").decode("ascii"),
    )
    logger = _CaptureLogger()

    try:
        result = _materialize_runtime_descriptor_payloads(logger)

        assert result == runtime_dir.resolve()
        assert os.environ["PLATFORM_DESCRIPTORS_DIR"] == str(runtime_dir.resolve())
        assert pathlib.Path(os.environ["ASSEMBLY_YAML_DESCRIPTOR_PATH"]).read_text(encoding="utf-8") == "context:\n  tenant: demo\n"
        assert pathlib.Path(os.environ["BUNDLES_YAML_DESCRIPTOR_PATH"]).read_text(encoding="utf-8") == "bundles:\n  demo: {}\n"
        assert pathlib.Path(os.environ["GLOBAL_SECRETS_YAML"]).read_text(encoding="utf-8") == (
            "secrets:\n  services:\n    openai:\n      api_key: x\n"
        )
        assert "KDCUBE_RUNTIME_ASSEMBLY_YAML_B64" not in os.environ
        assert any("Materialized descriptor payloads into" in msg for _, msg in logger.messages)
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_prepare_runtime_environment_materializes_descriptors_before_settings_cache(monkeypatch):
    exec_id = "exec-prepare-env-test"
    runtime_dir = pathlib.Path("/tmp/kdcube-runtime-descriptors") / exec_id
    shutil.rmtree(runtime_dir, ignore_errors=True)
    monkeypatch.setenv("EXECUTION_ID", exec_id)
    monkeypatch.delenv("KDCUBE_RUNTIME_ENV_PREPARED", raising=False)
    monkeypatch.setenv(
        "KDCUBE_RUNTIME_SECRETS_YAML_B64",
        base64.b64encode(b"secrets:\n  services:\n    brave:\n      api_key: brave-secret\n").decode("ascii"),
    )
    cache_clear_calls: list[bool] = []

    class _FakeSettingsFn:
        def cache_clear(self):
            cache_clear_calls.append(True)

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_settings",
        _FakeSettingsFn(),
    )
    logger = _CaptureLogger()

    try:
        _prepare_runtime_environment(logger)

        assert os.environ["KDCUBE_RUNTIME_ENV_PREPARED"] == "1"
        assert pathlib.Path(os.environ["GLOBAL_SECRETS_YAML"]).read_text(encoding="utf-8") == (
            "secrets:\n  services:\n    brave:\n      api_key: brave-secret\n"
        )
        assert cache_clear_calls == [True]
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


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


def test_load_dynamic_bundle_tool_supports_same_bundle_relative_imports():
    bundle_root = (
        pathlib.Path(__file__).resolve().parents[2]
        / "examples"
        / "bundles"
        / "kdcube.copilot@2026-04-03-19-05"
    )
    tool_file = bundle_root / "tools" / "react_tools.py"
    module_name = "dynpkg_test_copilot.tools.react_tools"

    for name in [
        "dynpkg_test_copilot",
        "dynpkg_test_copilot.tools",
        "dynpkg_test_copilot.knowledge",
        "dynpkg_test_copilot.knowledge.resolver",
        module_name,
    ]:
        sys.modules.pop(name, None)

    mod = load_dynamic_module_from_file(module_name, tool_file)
    resolver = importlib.import_module("dynpkg_test_copilot.knowledge.resolver")

    assert mod.knowledge_resolver is resolver


def test_bootstrap_supervisor_runtime_resolves_library_module_specs_without_explicit_paths(
    tmp_path,
    monkeypatch,
):
    logger = _CaptureLogger()
    loaded: list[tuple[str, str]] = []
    bootstrapped: dict[str, object] = {}

    def _fake_loader(module_name: str, file_path: str):
        mod = types.ModuleType(module_name)
        mod.__file__ = file_path
        sys.modules[module_name] = mod
        loaded.append((module_name, file_path))
        return mod

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry.load_dynamic_module_from_file",
        _fake_loader,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry.bootstrap_bind_all",
        lambda ps_str, module_names, bootstrap_env=False: bootstrapped.update(
            {
                "ps_str": ps_str,
                "module_names": list(module_names),
                "bootstrap_env": bootstrap_env,
            }
        ),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.runtime.isolated.py_code_exec_entry.get_comm",
        lambda: None,
    )

    runtime_globals = {
        "PORTABLE_SPEC_JSON": "{\"ok\": true}",
        "TOOL_ALIAS_MAP": {"io_tools": "dyn_io_tools_test"},
        "TOOL_MODULE_FILES": {},
        "RAW_TOOL_SPECS": [
            {
                "alias": "io_tools",
                "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",
            }
        ],
    }

    _bootstrap_supervisor_runtime(runtime_globals, [], logger, tmp_path)

    assert loaded
    assert loaded[0][0] == "dyn_io_tools_test"
    assert loaded[0][1].endswith("/kdcube_ai_app/apps/chat/sdk/tools/io_tools.py")
    assert bootstrapped["ps_str"] == "{\"ok\": true}"
    assert "dyn_io_tools_test" in bootstrapped["module_names"]
    assert any("resolved library module: io_tools" in msg for _, msg in logger.messages)


def test_build_executor_runtime_globals_strips_privileged_paths_and_descriptors():
    raw = {
        "CONTRACT": {"ok": True},
        "COMM_SPEC": {"channel": "chat.events"},
        "TOOL_ALIAS_MAP": {"io_tools": "dyn_io_tools_test"},
        "TOOL_MODULE_FILES": {"io_tools": "/workspace/bundles/demo/tools/io_tools.py"},
        "BUNDLE_SPEC": {"id": "demo.bundle"},
        "BUNDLE_STORAGE_DIR": "/bundle-storage/demo",
        "RAW_TOOL_SPECS": [{"alias": "io_tools", "ref": "tools/io_tools.py"}],
        "SKILLS_DESCRIPTOR": {"custom_skills_root": "/workspace/bundles/demo/skills"},
        "PORTABLE_SPEC_JSON": "{\"sensitive\":true}",
    }

    sanitized = _build_executor_runtime_globals(raw)

    assert sanitized["CONTRACT"] == {"ok": True}
    assert sanitized["TOOL_ALIAS_MAP"] == {"io_tools": "dyn_io_tools_test"}
    assert "TOOL_MODULE_FILES" not in sanitized
    assert "BUNDLE_SPEC" not in sanitized
    assert "BUNDLE_STORAGE_DIR" not in sanitized
    assert "COMM_SPEC" not in sanitized
    assert "RAW_TOOL_SPECS" not in sanitized
    assert "SKILLS_DESCRIPTOR" not in sanitized
    assert "PORTABLE_SPEC_JSON" not in sanitized
