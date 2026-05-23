from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_package(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py",
        submodule_search_locations=[str(path)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_react_tools_module():
    root = _bundle_root()
    package_name = "copilot_bundle_testpkg"
    _ensure_package(package_name, root)
    _ensure_package(f"{package_name}.knowledge", root / "knowledge")
    _ensure_package(f"{package_name}.tools", root / "tools")
    module_name = f"{package_name}.tools.react_tools"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "tools" / "react_tools.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_doc_reader_helpers_delegate_to_knowledge_resolver(tmp_path, monkeypatch):
    mod = _load_react_tools_module()
    monkeypatch.setattr(mod.knowledge_resolver, "KNOWLEDGE_ROOT", None)

    captured: dict[str, object] = {}

    def _search(**kwargs):
        captured["search"] = kwargs
        return [{"path": "ks:docs/example.md", "title": "Example", "score": 1.0}]

    def _read(*, path: str, **kwargs):
        captured["read"] = {"path": path, **kwargs}
        return {"text": "# Example\n", "mime": "text/markdown"}

    monkeypatch.setattr(mod.knowledge_resolver, "search_knowledge", _search)
    monkeypatch.setattr(mod.knowledge_resolver, "read_knowledge", _read)

    hits = asyncio.run(
        mod.search_knowledge_docs(
            query="example",
            root="ks:docs",
            top_k=5,
            storage_root=tmp_path,
        )
    )
    doc = asyncio.run(
        mod.read_knowledge_doc(
            path="ks:docs/example.md",
            storage_root=tmp_path,
        )
    )

    assert hits == [{"path": "ks:docs/example.md", "title": "Example", "score": 1.0}]
    assert doc == {"text": "# Example\n", "mime": "text/markdown"}
    assert captured["search"] == {
        "query": "example",
        "root": "ks:docs",
        "max_hits": 5,
        "keywords": None,
    }
    assert captured["read"] == {"path": "ks:docs/example.md"}
    assert mod.knowledge_resolver.KNOWLEDGE_ROOT == tmp_path.resolve()


def test_doc_reader_can_read_path_returned_by_search(tmp_path):
    mod = _load_react_tools_module()
    mod.knowledge_resolver.KNOWLEDGE_ROOT = None

    doc_path = tmp_path / "docs" / "sdk" / "bundle" / "example.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text("# Example Bundle Doc\n\nReadable content.\n", encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "knowledge_root": "ks:",
                "items": [
                    {
                        "path": "ks:docs/sdk/bundle/example.md",
                        "title": "Example Bundle Doc",
                        "summary": "Readable bundle overview",
                        "tags": ["bundle"],
                        "keywords": ["bundle overview"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    hits = asyncio.run(
        mod.search_knowledge_docs(
            query="bundle overview",
            storage_root=tmp_path,
        )
    )
    assert hits
    assert hits[0]["path"] == "ks:docs/sdk/bundle/example.md"

    doc = asyncio.run(
        mod.read_knowledge_doc(
            path=hits[0]["path"],
            storage_root=tmp_path,
        )
    )

    assert doc.get("missing") is not True
    assert doc["mime"] == "text/markdown"
    assert "Readable content." in doc["text"]


def test_build_doc_reader_mcp_app_returns_streamable_http_app(tmp_path):
    mod = _load_react_tools_module()
    app = mod.build_doc_reader_mcp_app(
        name="kdcube.copilot.doc_reader",
        storage_root_provider=lambda: tmp_path,
        refresh_knowledge_space=lambda: None,
    )

    assert app.settings.stateless_http is True
    assert hasattr(app, "streamable_http_app")
    assert callable(app.streamable_http_app)
    assert app.streamable_http_app() is not None


def test_doc_reader_mcp_prepare_awaits_async_refresh(tmp_path, monkeypatch):
    mod = _load_react_tools_module()
    monkeypatch.setattr(mod.knowledge_resolver, "search_knowledge", lambda **kwargs: [])
    refreshed: list[str] = []

    async def _refresh():
        refreshed.append("done")

    app = mod.build_doc_reader_mcp_app(
        name="kdcube.copilot.doc_reader",
        storage_root_provider=lambda: tmp_path,
        refresh_knowledge_space=_refresh,
    )

    result = asyncio.run(app.call_tool("search_knowledge", {"query": "bundle docs"}))

    assert result == ([], {"result": []})
    assert refreshed == ["done"]
