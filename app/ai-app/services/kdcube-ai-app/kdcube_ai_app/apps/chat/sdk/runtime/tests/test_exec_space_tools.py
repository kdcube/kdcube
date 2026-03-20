import asyncio
import importlib.util
import pathlib
import sys

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.external.base import is_isolated_exec_process


def _load_exec_space_tools_module():
    module_name = "_test_react_doc_exec_space_tools"
    tool_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "examples"
        / "bundles"
        / "react.doc@2026-03-02-22-10"
        / "tools"
        / "exec_space_tools.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


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
async def test_react_doc_exec_space_tool_rejects_non_exec_runtime(monkeypatch):
    monkeypatch.delenv("RUNTIME_GLOBALS_JSON", raising=False)
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("WORKDIR", raising=False)
    monkeypatch.delenv("BUNDLE_STORAGE_DIR", raising=False)
    sys.modules.pop("_kdcube_react_doc_knowledge_resolver", None)

    module = _load_exec_space_tools_module()
    result = await module.tools.resolve_namespace("ks:docs")

    assert result["ok"] is False
    assert result["error"]["code"] == "exec_only_tool"
    assert result["error"]["details"]["logical_ref"] == "ks:docs"
    assert result["ret"]["browseable"] is False
    assert result["ret"]["physical_path"] is None
    assert result["ret"]["access"] == "r"


@pytest.mark.asyncio
async def test_react_doc_exec_space_tool_resolves_docs_dir_in_exec(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    docs_root = knowledge_root / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "intro.md").write_text("# Intro\n", encoding="utf-8")
    (knowledge_root / "index.md").write_text("# Index\n", encoding="utf-8")

    monkeypatch.setenv("RUNTIME_GLOBALS_JSON", "{}")
    monkeypatch.setenv("OUTPUT_DIR", "/workspace/out")
    monkeypatch.setenv("WORKDIR", "/workspace/work")
    monkeypatch.setenv("BUNDLE_STORAGE_DIR", str(knowledge_root))
    sys.modules.pop("_kdcube_react_doc_knowledge_resolver", None)

    module = _load_exec_space_tools_module()
    result = await module.tools.resolve_namespace("ks:docs")

    assert result["ok"] is True
    assert result["error"] is None
    assert result["ret"]["browseable"] is True
    assert result["ret"]["access"] == "r"
    assert result["ret"]["physical_path"] == str(docs_root.resolve())


@pytest.mark.asyncio
async def test_react_doc_exec_space_tool_returns_managed_error_for_missing_namespace(tmp_path, monkeypatch):
    knowledge_root = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    knowledge_root.mkdir(parents=True)

    monkeypatch.setenv("RUNTIME_GLOBALS_JSON", "{}")
    monkeypatch.setenv("OUTPUT_DIR", "/workspace/out")
    monkeypatch.setenv("WORKDIR", "/workspace/work")
    monkeypatch.setenv("BUNDLE_STORAGE_DIR", str(knowledge_root))
    sys.modules.pop("_kdcube_react_doc_knowledge_resolver", None)

    module = _load_exec_space_tools_module()
    result = await module.tools.resolve_namespace("ks:docs")

    assert result["ok"] is False
    assert result["error"]["code"] == "namespace_not_found"
    assert result["error"]["managed"] is True
    assert "ks:docs" in result["error"]["message"]
    assert result["ret"]["physical_path"] is None
    assert result["ret"]["access"] == "r"
    assert result["ret"]["browseable"] is False


@pytest.mark.asyncio
async def test_react_doc_exec_space_tool_explains_missing_bundle_storage(monkeypatch):
    monkeypatch.setenv("RUNTIME_GLOBALS_JSON", "{}")
    monkeypatch.setenv("OUTPUT_DIR", "/workspace/out")
    monkeypatch.setenv("WORKDIR", "/workspace/work")
    monkeypatch.delenv("BUNDLE_STORAGE_DIR", raising=False)
    sys.modules.pop("_kdcube_react_doc_knowledge_resolver", None)

    module = _load_exec_space_tools_module()
    result = await module.tools.resolve_namespace("ks:tests")

    assert result["ok"] is False
    assert result["error"]["code"] == "bundle_storage_unavailable"
    assert result["error"]["managed"] is True
    assert "BUNDLE_STORAGE_DIR is missing" in result["error"]["message"]
    assert "substitute tests" in result["error"]["message"]
    assert result["ret"]["physical_path"] is None
