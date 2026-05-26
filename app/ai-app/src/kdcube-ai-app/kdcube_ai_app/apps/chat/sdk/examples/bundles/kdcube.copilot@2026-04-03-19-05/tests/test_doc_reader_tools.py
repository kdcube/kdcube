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


def _load_knowledge_module(name: str):
    root = _bundle_root()
    package_name = "copilot_bundle_testpkg"
    _ensure_package(package_name, root)
    _ensure_package(f"{package_name}.knowledge", root / "knowledge")
    module_name = f"{package_name}.knowledge.{name}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "knowledge" / f"{name}.py",
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


def test_knowledge_index_builds_sqlite_and_compact_navigation(tmp_path):
    index_builder = _load_knowledge_module("index_builder")
    resolver = _load_knowledge_module("resolver")
    resolver.KNOWLEDGE_ROOT = None

    docs = tmp_path / "docs"
    docs.mkdir()
    for idx in range(12):
        body = "General documentation body."
        if idx == 7:
            body = "This page explains iframe embedding and control plane integration."
        (docs / f"doc-{idx}.md").write_text(
            "\n".join(
                [
                    "---",
                    f'title: "Doc {idx}"',
                    f'summary: "Summary for document {idx}"',
                    'tags: ["docs"]',
                    'keywords: ["navigation"]',
                    "---",
                    f"# Doc {idx}",
                    "",
                    body,
                ]
            ),
            encoding="utf-8",
        )

    index_builder.build_knowledge_index(
        knowledge_root=tmp_path,
        docs_root=docs,
    )

    assert (tmp_path / "index.json").exists()
    assert (tmp_path / ".cache" / "knowledge_search.sqlite").exists()
    index_md = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "Retrieval index" in index_md
    assert "doc-11.md" not in index_md
    assert len(index_md.splitlines()) < 60

    resolver.KNOWLEDGE_ROOT = tmp_path
    hits = resolver.search_knowledge(
        query="iframe embedding",
        root="ks:docs",
        max_hits=5,
    )

    assert hits
    assert hits[0]["path"] == "ks:docs/doc-7.md"
    assert "iframe embedding" in hits[0]["excerpt"]


def test_knowledge_index_builder_map_prioritizes_builder_docs(tmp_path):
    index_builder = _load_knowledge_module("index_builder")

    docs = tmp_path / "docs"
    (docs / "sdk" / "bundle" / "build").mkdir(parents=True)
    (docs / "sdk" / "agents" / "claude").mkdir(parents=True)
    (docs / "configuration").mkdir(parents=True)
    (docs / "exec").mkdir(parents=True)
    fixtures = {
        docs / "sdk" / "bundle" / "build" / "how-to-navigate-kdcube-docs-README.md": (
            "How To Navigate KDCube Bundle Docs",
            "Tier 1 navigation guide.",
        ),
        docs / "sdk" / "bundle" / "build" / "how-to-write-bundle-README.md": (
            "How To Write A Bundle",
            "Bundle authoring guide.",
        ),
        docs / "sdk" / "bundle" / "build" / "how-to-assemble-bundle-with-sdk-building-blocks-README.md": (
            "How To Assemble A Bundle With SDK Building Blocks",
            "Reusable SDK building blocks.",
        ),
        docs / "sdk" / "bundle" / "build" / "sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md": (
            "Tier 1 Bundle Pack For Build-With-KDCube Plugins",
            "Plugin handoff note.",
        ),
        docs / "sdk" / "bundle" / "bundle-index-README.md": ("Bundle Index", "Bundle authoring docs."),
        docs / "sdk" / "bundle" / "bundle-client-ui-README.md": ("Bundle Client UI", "Client UI docs."),
        docs / "sdk" / "bundle" / "bundle-storage-and-cache-README.md": ("Bundle Storage", "Storage docs."),
        docs / "sdk" / "agents" / "claude" / "claude-code-workspace-bootstrap-README.md": (
            "Claude Code Workspace Management",
            "Workspace implementation detail.",
        ),
        docs / "exec" / "README-iso-runtime.md": ("ISO Runtime", "Isolation docs."),
        docs / "configuration" / "assembly-descriptor-README.md": ("Assembly Descriptor", "Configuration docs."),
    }
    for path, (title, summary) in fixtures.items():
        path.write_text(
            "\n".join(
                [
                    "---",
                    f'title: "{title}"',
                    f'summary: "{summary}"',
                    "---",
                    f"# {title}",
                ]
            ),
            encoding="utf-8",
        )

    index_builder.build_knowledge_index(
        knowledge_root=tmp_path,
        docs_root=docs,
    )

    index_md = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "## Builder map" in index_md
    assert "## Primary landing docs" not in index_md
    assert index_md.index("### Start here for bundle builders") < index_md.index(
        "### Client UI, widgets, and streaming"
    )
    assert index_md.index("ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md") < index_md.index(
        "ks:docs/configuration/assembly-descriptor-README.md"
    )
    assert "ks:docs/sdk/bundle/build/sync-tier1-bundle-docs-to-build-with-kdcube-plugins-README.md" not in index_md
    assert "ks:docs/sdk/agents/claude/claude-code-workspace-bootstrap-README.md" not in index_md


def test_oversized_index_read_returns_compact_search_guidance(tmp_path):
    resolver = _load_knowledge_module("resolver")
    resolver.KNOWLEDGE_ROOT = tmp_path

    (tmp_path / "docs").mkdir()
    (tmp_path / "index.md").write_text("# Giant Index\n\n" + ("- item\n" * 5000), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "knowledge_root": "ks:",
                "items": [
                    {
                        "path": "ks:docs/example.md",
                        "title": "Example",
                        "summary": "Example summary",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "knowledge_search.sqlite").write_bytes(b"placeholder")

    result = resolver.read_knowledge(path="ks:index.md")

    assert result.get("missing") is not True
    assert result["source_truncated"] is True
    assert result["size_bytes"] > 20_000
    assert "react.search_knowledge" in result["text"]
    assert len(result["text"]) < 1000


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
