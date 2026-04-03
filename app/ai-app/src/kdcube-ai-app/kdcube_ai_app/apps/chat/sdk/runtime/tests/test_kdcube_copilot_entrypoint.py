import importlib.util
import pathlib
import sys


def _bundle_root() -> pathlib.Path:
    return (
        pathlib.Path(__file__).resolve().parents[2]
        / "examples"
        / "bundles"
        / "kdcube.copilot@2026-04-03-19-05"
    )


def _load_package_root(bundle_root: pathlib.Path):
    package_name = "_test_kdcube_copilot_bundle_pkg"
    init_path = bundle_root / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(bundle_root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return package_name


def _load_entrypoint_module():
    bundle_root = _bundle_root()
    package_name = _load_package_root(bundle_root)
    full_name = f"{package_name}.entrypoint"
    spec = importlib.util.spec_from_file_location(full_name, bundle_root / "entrypoint.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


class _Logger:
    def __init__(self):
        self.records = []

    def log(self, message, level="INFO"):
        self.records.append((level, str(message)))


def test_kdcube_copilot_shared_signature_skips_rebuild(tmp_path, monkeypatch):
    entrypoint_mod = _load_entrypoint_module()

    ws_root = tmp_path / "bundle-storage"
    bundle_root = tmp_path / "bundle"
    source_root = tmp_path / "repo" / "app" / "ai-app"
    bundle_root.mkdir(parents=True)
    (source_root / "docs").mkdir(parents=True)
    signature = f"repo|main|{source_root}|True"

    setup = (
        ws_root,
        bundle_root,
        source_root,
        True,
        "repo",
        "main",
        signature,
    )

    build_calls = []

    def fake_prepare_knowledge_space(**kwargs):
        build_calls.append(kwargs)
        kwargs["knowledge_root"].mkdir(parents=True, exist_ok=True)
        (kwargs["knowledge_root"] / "docs").mkdir(parents=True, exist_ok=True)
        (kwargs["knowledge_root"] / "src").mkdir(parents=True, exist_ok=True)
        (kwargs["knowledge_root"] / "deployment").mkdir(parents=True, exist_ok=True)
        (kwargs["knowledge_root"] / "index.json").write_text("{}", encoding="utf-8")
        (kwargs["knowledge_root"] / "index.md").write_text("# Index\n", encoding="utf-8")

    monkeypatch.setattr(entrypoint_mod.knowledge_resolver, "prepare_knowledge_space", fake_prepare_knowledge_space)

    wf1 = entrypoint_mod.ReactWorkflow.__new__(entrypoint_mod.ReactWorkflow)
    wf1.logger = _Logger()
    wf1._knowledge_signature = None
    wf1._resolve_knowledge_setup = lambda: setup

    wf1._ensure_knowledge_space(reason="test")

    assert len(build_calls) == 1
    assert (ws_root / ".knowledge.signature").read_text(encoding="utf-8").strip() == signature

    wf2 = entrypoint_mod.ReactWorkflow.__new__(entrypoint_mod.ReactWorkflow)
    wf2.logger = _Logger()
    wf2._knowledge_signature = None
    wf2._resolve_knowledge_setup = lambda: setup

    wf2._ensure_knowledge_space(reason="test")

    assert len(build_calls) == 1
    assert any("shared signature cache hit" in message for _, message in wf2.logger.records)
