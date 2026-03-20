import importlib.util
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

    stale_tests = stale_repo / "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"
    fresh_tests = fresh_repo / "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"
    fresh_docs = fresh_repo / "app/ai-app/docs"
    fresh_src = fresh_repo / "app/ai-app/services/kdcube-ai-app/kdcube_ai_app"
    fresh_deploy = fresh_repo / "app/ai-app/deployment"

    fresh_tests.mkdir(parents=True, exist_ok=True)
    fresh_docs.mkdir(parents=True, exist_ok=True)
    fresh_src.mkdir(parents=True, exist_ok=True)
    fresh_deploy.mkdir(parents=True, exist_ok=True)
    (fresh_tests / "README.md").write_text("bundle tests\n", encoding="utf-8")
    (fresh_docs / "intro.md").write_text("# Intro\n", encoding="utf-8")

    stale_link = knowledge_root / "tests"
    stale_link.symlink_to(stale_tests, target_is_directory=True)
    assert stale_link.is_symlink()
    assert stale_link.exists() is False

    index_builder.prepare_knowledge_space(
        bundle_root=bundle_root,
        knowledge_root=knowledge_root,
        docs_root=fresh_docs,
        src_root=fresh_src,
        deploy_root=fresh_deploy,
        tests_root=fresh_tests,
        validate_refs=False,
    )

    replaced_tests = knowledge_root / "tests"
    assert replaced_tests.is_symlink()
    assert replaced_tests.exists() is True
    assert replaced_tests.resolve() == fresh_tests.resolve()
    assert (knowledge_root / "docs").resolve() == fresh_docs.resolve()
    assert (knowledge_root / "src").resolve() == fresh_src.resolve()
    assert (knowledge_root / "deploy").resolve() == fresh_deploy.resolve()
