from __future__ import annotations

import sys

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    build_dynamic_module_name,
    load_dynamic_module_for_path,
)
from kdcube_ai_app.apps.chat.sdk.runtime.external.base import is_isolated_exec_process
from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _import_root_for_module_file


def _clear_dynamic_package(module_name: str) -> None:
    parts = module_name.split(".")
    roots = {".".join(parts[:idx]) for idx in range(1, len(parts) + 1)}
    for name in list(sys.modules):
        if name in roots or any(name.startswith(f"{root}.") for root in roots):
            sys.modules.pop(name, None)


def test_is_isolated_exec_process_requires_runtime_globals(monkeypatch):
    monkeypatch.delenv("RUNTIME_GLOBALS_JSON", raising=False)
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("WORKDIR", raising=False)
    monkeypatch.delenv("EXECUTION_ID", raising=False)
    monkeypatch.delenv("EXECUTION_SANDBOX", raising=False)
    assert is_isolated_exec_process() is False

    monkeypatch.setenv("RUNTIME_GLOBALS_JSON", "{}")
    monkeypatch.setenv("OUTPUT_DIR", "/workspace/out")
    monkeypatch.setenv("WORKDIR", "/workspace/work")
    assert is_isolated_exec_process() is True


@pytest.mark.asyncio
async def test_dynamic_bundle_tool_supports_same_bundle_relative_imports(tmp_path):
    bundle_root = tmp_path / "task-and-memo-app@1-0"
    tools_root = bundle_root / "tools"
    tools_root.mkdir(parents=True)
    (bundle_root / "__init__.py").write_text("", encoding="utf-8")
    (tools_root / "__init__.py").write_text("", encoding="utf-8")
    (tools_root / "common.py").write_text(
        "VALUE = 'from-common'\n",
        encoding="utf-8",
    )
    tool_path = tools_root / "delivery_tools.py"
    tool_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "try:",
                "    from .common import VALUE",
                "except ImportError:",
                "    from common import VALUE",
                "",
                "class Tools:",
                "    async def ping(self):",
                "        return {'ok': True, 'value': VALUE}",
                "",
                "tools = Tools()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    module_name = build_dynamic_module_name(tool_path)
    _clear_dynamic_package(module_name)
    loaded_name, module = load_dynamic_module_for_path(tool_path)

    assert loaded_name.endswith(".tools.delivery_tools")
    assert (await module.tools.ping()) == {"ok": True, "value": "from-common"}


def test_bundle_tool_import_root_is_bundle_root_not_tools_leaf(tmp_path):
    bundle_root = tmp_path / "task-and-memo-app@1-0"
    tools_root = bundle_root / "tools"
    tools_root.mkdir(parents=True)
    (bundle_root / "__init__.py").write_text("", encoding="utf-8")
    (tools_root / "__init__.py").write_text("", encoding="utf-8")
    tool_path = tools_root / "delivery_tools.py"
    tool_path.write_text("", encoding="utf-8")
    (tools_root / "common.py").write_text("", encoding="utf-8")
    (tools_root / "types.py").write_text("", encoding="utf-8")

    assert _import_root_for_module_file(tool_path) == str(bundle_root)
