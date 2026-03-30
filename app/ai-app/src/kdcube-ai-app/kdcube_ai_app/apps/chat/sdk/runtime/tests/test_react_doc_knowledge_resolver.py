import importlib.util
import json
import pathlib
import sys


def _load_resolver_module():
    module_name = "_test_react_doc_knowledge_resolver"
    resolver_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "examples"
        / "bundles"
        / "react.doc@2026-03-02-22-10"
        / "knowledge"
        / "resolver.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, resolver_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _load_index_builder_module():
    module_name = "_test_react_doc_index_builder"
    index_builder_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "examples"
        / "bundles"
        / "react.doc@2026-03-02-22-10"
        / "knowledge"
        / "index_builder.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, index_builder_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_react_doc_resolver_uses_bundle_storage_dir_when_knowledge_root_is_unset(tmp_path, monkeypatch):
    resolver = _load_resolver_module()
    knowledge_root = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    docs_root = knowledge_root / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "intro.md").write_text("# Intro\n", encoding="utf-8")
    (knowledge_root / "index.json").write_text(
        """
        {
          "items": [
            {
              "path": "ks:docs/intro.md",
              "title": "Intro Guide",
              "summary": "Knowledge root smoke test",
              "tags": ["guide"],
              "keywords": ["intro"]
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("BUNDLE_STORAGE_DIR", str(knowledge_root))
    resolver.KNOWLEDGE_ROOT = None

    hits = resolver.search_knowledge(query="intro")
    assert hits and hits[0]["path"] == "ks:docs/intro.md"

    doc = resolver.read_knowledge(path="ks:docs/intro.md")
    assert doc.get("missing") is not True
    assert doc["text"].startswith("# Intro")
    assert doc["physical_path"].endswith("docs/intro.md")


def test_prepare_knowledge_space_replaces_stale_symlinks(tmp_path):
    index_builder = _load_index_builder_module()
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()

    stale_repo = tmp_path / "repos" / "kdcube-ai-app__react.doc.knowledge"
    fresh_repo = tmp_path / "repos" / "kdcube-ai-app__react.doc.knowledge__main"

    stale_services = stale_repo / "app/ai-app/src"
    fresh_ai_app_root = fresh_repo / "app/ai-app"
    fresh_docs = fresh_ai_app_root / "docs"
    fresh_src = fresh_ai_app_root / "src"
    fresh_deployment = fresh_ai_app_root / "deployment"

    fresh_src.mkdir(parents=True, exist_ok=True)
    fresh_docs.mkdir(parents=True, exist_ok=True)
    fresh_deployment.mkdir(parents=True, exist_ok=True)
    (fresh_src / "kdcube-ai-app" / "kdcube_ai_app" / "apps" / "chat" / "sdk" / "examples" / "tests" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (
        fresh_src
        / "kdcube-ai-app"
        / "kdcube_ai_app"
        / "apps"
        / "chat"
        / "sdk"
        / "examples"
        / "tests"
        / "README.md"
    ).write_text("bundle tests\n", encoding="utf-8")
    (fresh_docs / "intro.md").write_text("# Intro\n", encoding="utf-8")

    stale_link = knowledge_root / "src"
    stale_link.symlink_to(stale_services, target_is_directory=True)
    assert stale_link.is_symlink()
    assert stale_link.exists() is False

    index_builder.prepare_knowledge_space(
        bundle_root=bundle_root,
        knowledge_root=knowledge_root,
        source_root=fresh_ai_app_root,
        validate_refs=False,
    )

    replaced_src = knowledge_root / "src"
    assert replaced_src.is_symlink()
    assert replaced_src.exists() is True
    assert replaced_src.readlink().is_absolute() is False
    assert replaced_src.resolve() == fresh_src.resolve()
    assert (knowledge_root / "docs").readlink().is_absolute() is False
    assert (knowledge_root / "docs").resolve() == fresh_docs.resolve()
    assert (knowledge_root / "deployment").resolve() == fresh_deployment.resolve()


def test_resolve_exec_namespace_allows_symlinked_source_subtree(tmp_path, monkeypatch):
    resolver = _load_resolver_module()
    knowledge_root = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    repo_root = tmp_path / "bundle-storage" / "tenant" / "project" / "repos" / "react.doc.knowledge__main" / "app" / "ai-app"
    target_dir = repo_root / "src" / "kdcube-ai-app" / "kdcube_ai_app" / "apps" / "chat" / "sdk" / "examples" / "tests"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "README.md").write_text("bundle tests\n", encoding="utf-8")
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "src").symlink_to(pathlib.Path("../repos/react.doc.knowledge__main/app/ai-app/src"), target_is_directory=True)

    monkeypatch.setenv("BUNDLE_STORAGE_DIR", str(knowledge_root))
    resolver.KNOWLEDGE_ROOT = None

    resolved = resolver.resolve_exec_namespace(
        logical_ref="ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"
    )

    assert resolved["browseable"] is True
    assert pathlib.Path(resolved["physical_path"]).resolve() == target_dir.resolve()


def test_build_knowledge_index_includes_headings_and_rich_metadata(tmp_path):
    index_builder = _load_index_builder_module()

    knowledge_root = tmp_path / "knowledge"
    docs_root = knowledge_root / "docs" / "sdk" / "bundle"
    docs_root.mkdir(parents=True, exist_ok=True)

    doc_path = docs_root / "bundle-platform-properties-README.md"
    doc_path.write_text(
        "\n".join(
            [
                "---",
                'id: ks:docs/sdk/bundle/bundle-platform-properties-README.md',
                'title: "Bundle Platform Properties"',
                'summary: "Reserved bundle property paths interpreted by the platform entrypoints and runtimes."',
                'tags: ["sdk", "bundle", "configuration", "runtime", "economics", "exec"]',
                'keywords: ["role_models", "embedding", "economics.reservation_amount_dollars", "execution.runtime"]',
                "see_also:",
                "  - ks:docs/sdk/bundle/bundle-dev-README.md",
                "---",
                "# Bundle Platform Properties",
                "",
                "## Reserved property paths",
                "",
                "## role_models",
                "",
                "## embedding",
                "",
                "## economics.reservation_amount_dollars",
                "",
                "## execution.runtime",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = index_builder.build_knowledge_index(
        knowledge_root=knowledge_root,
        docs_root=knowledge_root / "docs",
        deployment_root=None,
        logger=None,
    )

    assert payload["items"]
    item = payload["items"][0]
    assert item["path"] == "ks:docs/sdk/bundle/bundle-platform-properties-README.md"
    assert item["summary"] == "Reserved bundle property paths interpreted by the platform entrypoints and runtimes."
    assert "sdk" in item["tags"]
    assert "role_models" in item["keywords"]
    assert [h["text"] for h in item["headings"]] == [
        "Reserved property paths",
        "role_models",
        "embedding",
        "economics.reservation_amount_dollars",
        "execution.runtime",
    ]

    written = json.loads((knowledge_root / "index.json").read_text(encoding="utf-8"))
    written_item = written["items"][0]
    assert [h["text"] for h in written_item["headings"]] == [
        "Reserved property paths",
        "role_models",
        "embedding",
        "economics.reservation_amount_dollars",
        "execution.runtime",
    ]

    index_md = (knowledge_root / "index.md").read_text(encoding="utf-8")
    assert "summary: Reserved bundle property paths interpreted by the platform entrypoints and runtimes." in index_md
    assert "tags: sdk, bundle, configuration, runtime, economics, exec" in index_md
    assert "keywords: role_models, embedding, economics.reservation_amount_dollars, execution.runtime" in index_md
    assert "see also: ks:docs/sdk/bundle/bundle-dev-README.md" in index_md
    assert "sections: Reserved property paths; role_models; embedding; economics.reservation_amount_dollars; execution.runtime" in index_md
