from pathlib import Path

from kdcube_ai_app.infra.plugin import bundle_registry
from kdcube_ai_app.infra.plugin import bundle_store
from kdcube_ai_app.infra.plugin.git_bundle import GitBundlePaths


def test_bundle_store_repo_entry_ignores_supplied_path(monkeypatch):
    def _fake_compute_git_bundle_paths(*, bundle_id, git_url, git_ref, git_subdir):
        assert bundle_id == "bundle.demo"
        assert git_url == "https://example.com/org/repo.git"
        assert git_ref == "release-2"
        assert git_subdir == "subdir"
        return GitBundlePaths(
            repo_root=Path("/managed-bundles/repo__bundle.demo__release-2"),
            bundle_root=Path("/managed-bundles/repo__bundle.demo__release-2/subdir"),
        )

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.git_bundle.compute_git_bundle_paths",
        _fake_compute_git_bundle_paths,
    )

    entry = bundle_store._to_entry(
        "bundle.demo",
        {
            "id": "bundle.demo",
            "repo": "https://example.com/org/repo.git",
            "ref": "release-2",
            "subdir": "subdir",
            "path": "/stale/old/path",
            "module": "entrypoint",
        },
    )

    assert entry.path == "/managed-bundles/repo__bundle.demo__release-2/subdir"


def test_bundle_registry_normalize_repo_entry_ignores_supplied_path(monkeypatch):
    def _fake_compute_git_bundle_paths(*, bundle_id, git_url, git_ref, git_subdir):
        assert bundle_id == "bundle.demo"
        assert git_url == "https://example.com/org/repo.git"
        assert git_ref == "release-2"
        assert git_subdir == "subdir"
        return GitBundlePaths(
            repo_root=Path("/managed-bundles/repo__bundle.demo__release-2"),
            bundle_root=Path("/managed-bundles/repo__bundle.demo__release-2/subdir"),
        )

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.git_bundle.compute_git_bundle_paths",
        _fake_compute_git_bundle_paths,
    )

    normalized = bundle_registry._normalize(
        {
            "id": "bundle.demo",
            "repo": "https://example.com/org/repo.git",
            "ref": "release-2",
            "subdir": "subdir",
            "path": "/stale/old/path",
            "module": "entrypoint",
        }
    )

    assert normalized["path"] == "/managed-bundles/repo__bundle.demo__release-2/subdir"
