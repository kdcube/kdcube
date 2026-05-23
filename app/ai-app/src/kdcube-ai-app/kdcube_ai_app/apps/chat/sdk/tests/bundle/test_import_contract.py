from __future__ import annotations

from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.tests.bundle.import_contract import (
    collect_bundle_local_import_violations,
)


def test_import_contract_rejects_top_level_bundle_local_package_imports(tmp_path: Path):
    bundle_dir = tmp_path / "demo.bundle"
    (bundle_dir / "services").mkdir(parents=True)
    (bundle_dir / "services" / "__init__.py").write_text("", encoding="utf-8")
    (bundle_dir / "services" / "news.py").write_text("VALUE = 1\n", encoding="utf-8")
    (bundle_dir / "tools.py").write_text("VALUE = 2\n", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "from services.news import VALUE\n"
        "import tools\n",
        encoding="utf-8",
    )

    violations = collect_bundle_local_import_violations(bundle_dir)

    assert [violation.import_name for violation in violations] == ["services.news", "tools"]
    assert "use package-relative imports" in violations[0].render(bundle_dir)


def test_import_contract_rejects_top_level_namespace_style_bundle_dirs(tmp_path: Path):
    bundle_dir = tmp_path / "demo.bundle"
    (bundle_dir / "services").mkdir(parents=True)
    (bundle_dir / "services" / "news.py").write_text("VALUE = 1\n", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "from services.news import VALUE\n",
        encoding="utf-8",
    )

    violations = collect_bundle_local_import_violations(bundle_dir)

    assert [violation.import_name for violation in violations] == ["services.news"]


def test_import_contract_allows_package_relative_bundle_local_imports(tmp_path: Path):
    bundle_dir = tmp_path / "demo.bundle"
    (bundle_dir / "services").mkdir(parents=True)
    (bundle_dir / "services" / "__init__.py").write_text("", encoding="utf-8")
    (bundle_dir / "services" / "news.py").write_text("VALUE = 1\n", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "from .services.news import VALUE\n",
        encoding="utf-8",
    )

    assert collect_bundle_local_import_violations(bundle_dir) == []


def test_bundle_python_uses_package_relative_bundle_local_imports(bundle_dir: Path):
    violations = collect_bundle_local_import_violations(bundle_dir)

    assert not violations, "\n".join(violation.render(bundle_dir) for violation in violations)
