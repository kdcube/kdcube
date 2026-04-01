from __future__ import annotations

from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite import build_test_targets


def test_build_test_targets_includes_shared_suite_and_bundle_tests(tmp_path):
    bundle_dir = tmp_path / "bundle@2026-04-01"
    bundle_dir.mkdir()
    (bundle_dir / "entrypoint.py").write_text("", encoding="utf-8")
    (bundle_dir / "tests").mkdir()

    targets = build_test_targets(bundle_dir)

    assert any(target.name == "bundle" and target.parent.name == "tests" for target in targets)
    assert bundle_dir / "tests" in targets


def test_build_test_targets_omits_bundle_tests_when_absent(tmp_path):
    bundle_dir = tmp_path / "bundle@2026-04-01"
    bundle_dir.mkdir()
    (bundle_dir / "entrypoint.py").write_text("", encoding="utf-8")

    targets = build_test_targets(bundle_dir)

    assert len(targets) == 1
    assert targets[0].name == "bundle"
    assert targets[0].parent.name == "tests"


def test_build_test_targets_supports_bundle_only_mode(tmp_path):
    bundle_dir = tmp_path / "bundle@2026-04-01"
    bundle_dir.mkdir()
    (bundle_dir / "entrypoint.py").write_text("", encoding="utf-8")
    (bundle_dir / "tests").mkdir()

    targets = build_test_targets(bundle_dir, include_shared=False, include_bundle_local=True)

    assert targets == [bundle_dir / "tests"]


def test_build_test_targets_supports_shared_only_mode(tmp_path):
    bundle_dir = tmp_path / "bundle@2026-04-01"
    bundle_dir.mkdir()
    (bundle_dir / "entrypoint.py").write_text("", encoding="utf-8")
    (bundle_dir / "tests").mkdir()

    targets = build_test_targets(bundle_dir, include_shared=True, include_bundle_local=False)

    assert len(targets) == 1
    assert targets[0].name == "bundle"
    assert targets[0].parent.name == "tests"
