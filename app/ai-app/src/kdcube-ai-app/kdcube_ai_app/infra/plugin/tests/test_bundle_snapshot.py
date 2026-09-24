"""An activation names what it loads (W209).

The case a dirty-tree guard cannot catch: a clean tree at commit A moves to
a clean tree at commit B between the caller's look and the import. Here the
activation carries the commit, the loaded path is a snapshot of that commit
exported from the object store, and a pin that differs from the caller's
expectation is refused before anything is evicted.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from kdcube_ai_app.infra.plugin import bundle_snapshot as snap
from kdcube_ai_app.infra.plugin import bundle_store

BUNDLE_ID = "demo.bundle"
SUBDIR = "apps/demo@1-0"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str, str]:
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q")
    bundle = repo / SUBDIR
    (bundle / "pkg").mkdir(parents=True)
    (bundle / "entrypoint.py").write_text("VERSION = 'A'\n", encoding="utf-8")
    (bundle / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "README.md").write_text("outside the bundle\n", encoding="utf-8")
    commit_a = _commit_all(repo, "A")
    (bundle / "entrypoint.py").write_text("VERSION = 'B'\n", encoding="utf-8")
    (bundle / "pkg" / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    commit_b = _commit_all(repo, "B")
    return repo, bundle, commit_a, commit_b


def _entry(bundle: pathlib.Path, **activation) -> dict:
    entry = {"id": BUNDLE_ID, "path": str(bundle), "module": "entrypoint", "singleton": True}
    if activation:
        entry["activation"] = activation
    return entry


async def test_activation_at_a_commit_loads_a_snapshot_of_that_commit_not_the_tree(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"
    # The tree is clean at B. The activation asks for A.
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD") == commit_b

    commit, source = await snap.prepare_activation(
        _entry(bundle), commit=commit_a, expected_commit=commit_a, managed_root=managed
    )

    assert commit == commit_a
    assert source["mode"] == "snapshot"
    assert source["commit"] == commit_a
    assert source["origin"] == "request"
    assert source["durable"] is False
    loaded = pathlib.Path(source["path"])
    assert loaded == managed / BUNDLE_ID / snap.SNAPSHOTS_DIR / commit_a
    assert (loaded / "entrypoint.py").read_text(encoding="utf-8") == "VERSION = 'A'\n"
    assert not (loaded / "pkg" / "extra.py").exists()
    assert not (loaded / "README.md").exists()
    marker = json.loads((loaded / snap.SNAPSHOT_MARKER).read_text(encoding="utf-8"))
    assert marker["commit"] == commit_a and marker["subdir"] == SUBDIR and marker["bundle_id"] == BUNDLE_ID
    # The mounted tree stays what it was: nothing of the activation touches it.
    assert (bundle / "entrypoint.py").read_text(encoding="utf-8") == "VERSION = 'B'\n"


async def test_clean_a_to_clean_b_is_fenced_by_the_expected_commit(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"
    # The caller read `main` when it pointed at A. By the time the activation
    # runs, main is at B and the tree is clean at B: a dirty guard sees nothing.
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(_entry(bundle), commit=branch, expected_commit=commit_a, managed_root=managed)

    assert refusal.value.code == "bundle_activation_commit_mismatch"
    assert refusal.value.details["resolved"] == commit_b
    assert refusal.value.details["expected"] == commit_a
    assert refusal.value.details["ref"] == branch
    # Refused before any export: nothing under the managed root.
    assert not (managed / BUNDLE_ID).exists()


async def test_the_descriptor_commit_applies_when_the_request_names_none(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"

    commit, source = await snap.prepare_activation(
        _entry(bundle, commit=commit_a), commit=None, expected_commit=None, managed_root=managed
    )

    assert commit == commit_a
    assert source["origin"] == "descriptor"
    assert source["durable"] is True
    assert pathlib.Path(source["path"]).name == commit_a


async def test_a_request_commit_wins_over_the_descriptor_commit_for_this_activation(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"

    commit, source = await snap.prepare_activation(
        _entry(bundle, commit=commit_a), commit=commit_b, expected_commit=None, managed_root=managed
    )

    assert commit == commit_b and source["origin"] == "request"
    assert (pathlib.Path(source["path"]) / "pkg" / "extra.py").exists()


async def test_without_a_commit_the_receipt_carries_evidence_about_the_mounted_tree(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    (bundle / "entrypoint.py").write_text("VERSION = 'B-dirty'\n", encoding="utf-8")

    commit, source = await snap.prepare_activation(_entry(bundle), commit=None, expected_commit=None)

    assert commit is None
    assert source["mode"] == "local-path"
    assert source["path"] == str(bundle)
    assert source["head"] == commit_b
    assert source["dirty"] is True
    assert source["changed_paths"] == [f"{SUBDIR}/entrypoint.py"]
    assert source["origin"] == ""


async def test_require_commit_refuses_a_commitless_activation_naming_the_flag(repository):
    repo, bundle, commit_a, commit_b = repository

    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(_entry(bundle, require_commit=True), commit=None, expected_commit=None)

    assert refusal.value.code == "bundle_activation_commit_required"
    assert "activation.require_commit" in str(refusal.value)
    assert refusal.value.details == {"bundle_id": BUNDLE_ID}


async def test_missing_mounted_path_is_refused_before_activation(tmp_path):
    missing = tmp_path / "missing"

    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(_entry(missing), commit=None, expected_commit=None)

    assert refusal.value.code == "bundle_path_unreachable"
    assert refusal.value.details == {"bundle_id": BUNDLE_ID, "path": str(missing)}
    assert "Nothing was evicted" in str(refusal.value)


async def test_mounted_path_must_be_a_directory(tmp_path):
    source_file = tmp_path / "entrypoint.py"
    source_file.write_text("VERSION = 1\n", encoding="utf-8")

    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(_entry(source_file), commit=None, expected_commit=None)

    assert refusal.value.code == "bundle_path_unreachable"
    assert refusal.value.details["path"] == str(source_file)


async def test_expected_commit_without_a_commit_is_refused(repository):
    repo, bundle, commit_a, commit_b = repository
    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(_entry(bundle), commit=None, expected_commit=commit_a)
    assert refusal.value.code == "bundle_activation_expect_without_commit"


async def test_an_unknown_ref_and_a_path_outside_a_repository_are_named(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    with pytest.raises(snap.BundleSnapshotError) as unknown:
        await snap.prepare_activation(_entry(bundle), commit="no-such-ref", expected_commit=None)
    assert unknown.value.code == "bundle_commit_unknown"

    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(snap.BundleSnapshotError) as plain:
        await snap.prepare_activation(_entry(outside), commit="HEAD", expected_commit=None)
    assert plain.value.code == "bundle_path_not_a_repository"


async def test_a_snapshot_is_reused_when_intact_and_rebuilt_when_a_file_was_altered(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"
    first = await snap.materialize_snapshot(bundle_id=BUNDLE_ID, mounted_path=bundle, commit=commit_a, managed_root=managed)
    marker_before = (first.path / snap.SNAPSHOT_MARKER).read_text(encoding="utf-8")

    again = await snap.materialize_snapshot(bundle_id=BUNDLE_ID, mounted_path=bundle, commit=commit_a, managed_root=managed)
    assert again.created_at == first.created_at
    assert (again.path / snap.SNAPSHOT_MARKER).read_text(encoding="utf-8") == marker_before

    (first.path / "entrypoint.py").write_text("VERSION = 'tampered'\n", encoding="utf-8")
    rebuilt = await snap.materialize_snapshot(bundle_id=BUNDLE_ID, mounted_path=bundle, commit=commit_a, managed_root=managed)
    assert (rebuilt.path / "entrypoint.py").read_text(encoding="utf-8") == "VERSION = 'A'\n"
    assert snap.verify_snapshot_files(rebuilt.path, SUBDIR, await snap._expected_blobs(repo, commit_a, SUBDIR)) == []


async def test_prune_keeps_the_named_snapshots(repository, tmp_path):
    repo, bundle, commit_a, commit_b = repository
    managed = tmp_path / "managed"
    await snap.materialize_snapshot(bundle_id=BUNDLE_ID, mounted_path=bundle, commit=commit_a, managed_root=managed)
    await snap.materialize_snapshot(bundle_id=BUNDLE_ID, mounted_path=bundle, commit=commit_b, managed_root=managed)

    removed = snap.prune_snapshots(BUNDLE_ID, keep=(commit_b,), managed_root=managed)

    assert removed == [commit_a]
    assert sorted(p.name for p in (managed / BUNDLE_ID / snap.SNAPSHOTS_DIR).iterdir()) == [commit_b]


async def test_a_git_backed_entry_refuses_an_activation_commit(repository):
    repo, bundle, commit_a, commit_b = repository
    entry = {"id": BUNDLE_ID, "path": str(bundle), "repo": "https://example.com/r.git", "ref": "v1"}
    with pytest.raises(snap.BundleSnapshotError) as refusal:
        await snap.prepare_activation(entry, commit=commit_a, expected_commit=None)
    assert refusal.value.code == "bundle_activation_not_local_path"
    commit, source = await snap.prepare_activation(entry, commit=None, expected_commit=None)
    assert commit is None and source["mode"] == "git" and source["ref"] == "v1"


def test_the_descriptor_entry_carries_the_activation_block_and_refuses_a_malformed_one():
    entry = bundle_store._to_entry(
        BUNDLE_ID,
        {"id": BUNDLE_ID, "path": "/bundles/x", "module": "entrypoint", "activation": {"commit": "abc123", "require_commit": True}},
    )
    assert entry.activation is not None
    assert entry.activation.commit == "abc123" and entry.activation.require_commit is True
    assert entry.model_dump()["activation"] == {"commit": "abc123", "require_commit": True}

    plain = bundle_store._to_entry(BUNDLE_ID, {"id": BUNDLE_ID, "path": "/bundles/x", "module": "entrypoint"})
    assert plain.activation is None
    assert not bundle_store._entries_equivalent(entry, plain)

    with pytest.raises(ValueError) as refusal:
        bundle_store._to_entry(BUNDLE_ID, {"id": BUNDLE_ID, "path": "/bundles/x", "activation": "abc123"})
    assert "'activation' must be a mapping" in str(refusal.value)

    # The same carrier serves the service block, which was declared on the
    # model and dropped by the normalizer before this change.
    with_service = bundle_store._to_entry(
        BUNDLE_ID, {"id": BUNDLE_ID, "path": "/bundles/x", "service": {"readiness": "required"}}
    )
    assert with_service.service is not None and with_service.service.readiness == "required"
