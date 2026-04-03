from pathlib import Path

from kdcube_ai_app.infra.plugin import bundle_store


def _write_example_bundle(root: Path, *, marker: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "entrypoint.py").write_text(f'BUNDLE_ID = "{marker}"\n', encoding="utf-8")
    (root / "skills_descriptor.py").write_text("SKILLS = []\n", encoding="utf-8")


def test_ensure_example_bundle_shared_uses_versioned_path(monkeypatch, tmp_path):
    src = tmp_path / "kdcube.copilot@2026-04-03-19-05"
    shared = tmp_path / "shared"
    _write_example_bundle(src, marker="v1")

    monkeypatch.setattr(bundle_store, "_SHARED_BUNDLES_ROOT", shared)
    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)
    monkeypatch.setenv("PLATFORM_REF", "2026.3.21.410")

    first = bundle_store._ensure_example_bundle_shared(src)
    second = bundle_store._ensure_example_bundle_shared(src)

    assert first == second
    assert first != src
    assert first.parent == shared
    assert first.name.startswith(f"{src.name}__2026.3.21.410__")
    assert (first / "entrypoint.py").read_text(encoding="utf-8") == 'BUNDLE_ID = "v1"\n'


def test_ensure_example_bundle_shared_keeps_old_version_when_source_changes(monkeypatch, tmp_path):
    src = tmp_path / "kdcube.copilot@2026-04-03-19-05"
    shared = tmp_path / "shared"
    _write_example_bundle(src, marker="v1")

    monkeypatch.setattr(bundle_store, "_SHARED_BUNDLES_ROOT", shared)
    monkeypatch.setattr(bundle_store, "_is_running_in_docker", lambda: True)
    monkeypatch.setenv("PLATFORM_REF", "2026.3.21.410")

    first = bundle_store._ensure_example_bundle_shared(src)
    _write_example_bundle(src, marker="v2")
    second = bundle_store._ensure_example_bundle_shared(src)

    assert first != second
    assert (first / "entrypoint.py").read_text(encoding="utf-8") == 'BUNDLE_ID = "v1"\n'
    assert (second / "entrypoint.py").read_text(encoding="utf-8") == 'BUNDLE_ID = "v2"\n'


def test_cleanup_old_shared_example_bundles_keeps_active_version(monkeypatch, tmp_path):
    root = tmp_path / "shared"
    old_dir = root / "kdcube.copilot@2026-04-03-19-05__2026.3.20.111__aaaaaaaaaaaa"
    new_dir = root / "kdcube.copilot@2026-04-03-19-05__2026.3.21.410__bbbbbbbbbbbb"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "entrypoint.py").write_text("old\n", encoding="utf-8")
    (new_dir / "entrypoint.py").write_text("new\n", encoding="utf-8")

    removed = bundle_store.cleanup_old_shared_example_bundles(
        bundle_id="kdcube.copilot@2026-04-03-19-05",
        bundles_root=root,
        keep=1,
        active_paths=[str(new_dir)],
    )

    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()
