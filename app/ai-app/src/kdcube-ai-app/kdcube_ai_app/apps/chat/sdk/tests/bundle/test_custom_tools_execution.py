# SPDX-License-Identifier: MIT

"""Custom tools execution tests.

Test that custom tools execute correctly, handle errors, and return
the expected output format.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_custom_tools_execution.py -v
  pytest test_custom_tools_execution.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestToolSubsystemBasics:
    """Test ToolSubsystem public API independent of any specific bundle."""

    def test_tool_subsystem_class_is_importable(self):
        """ToolSubsystem can be imported from the SDK."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
        assert ToolSubsystem is not None

    def test_parse_tool_id_returns_three_tuple(self):
        """parse_tool_id() always returns a 3-tuple."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id
        result = parse_tool_id("alias.tool")
        assert len(result) == 3

    def test_parse_tool_id_handles_empty_string(self):
        """parse_tool_id('') returns ('mod', '', '')."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id
        origin, alias, name = parse_tool_id("")
        assert origin == "mod"

    def test_parse_tool_id_handles_single_segment(self):
        """parse_tool_id('tool') does not crash."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id
        origin, alias, name = parse_tool_id("tool")
        assert isinstance(origin, str)


class TestToolModuleLoading:
    """Test that SDK tool modules load without errors."""

    def test_io_tools_module_loads(self):
        """SDK io_tools module is importable."""
        try:
            import importlib
            mod = importlib.import_module("kdcube_ai_app.apps.chat.sdk.tools.io_tools")
            assert mod is not None
        except ImportError:
            pytest.skip("io_tools not available")

    def test_web_tools_module_loads(self):
        """SDK web_tools module is importable."""
        try:
            import importlib
            mod = importlib.import_module("kdcube_ai_app.apps.chat.sdk.tools.web_tools")
            assert mod is not None
        except ImportError:
            pytest.skip("web_tools not available")

    def test_ctx_tools_module_loads(self):
        """SDK ctx_tools module is importable."""
        try:
            import importlib
            mod = importlib.import_module("kdcube_ai_app.apps.chat.sdk.tools.ctx_tools")
            assert mod is not None
        except ImportError:
            pytest.skip("ctx_tools not available")

    def test_exec_tools_module_loads(self):
        """SDK exec_tools module is importable."""
        try:
            import importlib
            mod = importlib.import_module("kdcube_ai_app.apps.chat.sdk.tools.exec_tools")
            assert mod is not None
        except ImportError:
            pytest.skip("exec_tools not available")

    def test_ref_module_loading_supports_relative_imports(self, tmp_path):
        """File-based ref modules can use normal relative imports within a bundle package."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem

        bundle_root = tmp_path / "bundle@2026-04-01"
        tools_dir = bundle_root / "tools"
        bundle_root.mkdir()
        tools_dir.mkdir()
        (bundle_root / "__init__.py").write_text("", encoding="utf-8")
        (tools_dir / "__init__.py").write_text("", encoding="utf-8")
        (bundle_root / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
        mod_path = tools_dir / "sample_tool.py"
        mod_path.write_text(
            "\n".join(
                [
                    "from ..helper import VALUE",
                    "EXPORTED_VALUE = VALUE",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        ts = object.__new__(ToolSubsystem)
        mod_name, mod = ts._load_tools_module(str(mod_path))

        assert mod_name.endswith(".tools.sample_tool")
        assert mod.EXPORTED_VALUE == 7


class TestToolSubsystemGetToolRuntime:
    """Test ToolSubsystem.get_tool_runtime() return values."""

    def _make_subsystem(self, tool_runtime: dict):
        """Build a minimal ToolSubsystem-like object with the given runtime config."""
        from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
        ts = object.__new__(ToolSubsystem)
        ts._tool_runtime = tool_runtime
        return ts

    def test_get_tool_runtime_returns_none_for_unknown_tool(self):
        """get_tool_runtime() returns None for an unregistered tool."""
        ts = self._make_subsystem({})
        assert ts.get_tool_runtime("unknown.tool") is None

    def test_get_tool_runtime_returns_local_for_configured_tool(self):
        """get_tool_runtime() returns 'local' when configured."""
        ts = self._make_subsystem({"web_tools.web_search": "local"})
        assert ts.get_tool_runtime("web_tools.web_search") == "local"

    def test_get_tool_runtime_returns_none_for_invalid_value(self):
        """get_tool_runtime() returns None for invalid runtime values."""
        ts = self._make_subsystem({"tool.x": "invalid_runtime"})
        assert ts.get_tool_runtime("tool.x") is None

    def test_get_tool_runtime_handles_empty_tool_id(self):
        """get_tool_runtime() returns None for empty tool_id."""
        ts = self._make_subsystem({"": "local"})
        assert ts.get_tool_runtime("") is None
