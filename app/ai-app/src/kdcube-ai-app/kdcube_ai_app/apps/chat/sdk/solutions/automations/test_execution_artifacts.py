# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import importlib

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations.execution_artifacts import (
    artifact_ref_for_execution_artifact,
    materialize_execution_artifact_for_current_turn,
)


def test_materialized_artifact_logical_path_carries_conversation_scope(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    artifact = {
        "id": "a1",
        "kind": "file",
        "filename": "src.bin",
        "source_physical_path": str(src),
        "visibility": "external",
    }
    execution = {"id": "exec-1", "automation_id": "auto-1", "artifacts": [artifact]}
    sc = {
        "tenant": "t",
        "project": "p",
        "user_id": "u",
        "turn_id": "turn_9",
        "outdir": str(tmp_path / "out"),
        "storage_root": str(tmp_path),
        "conversation_id": "cur1",
    }
    ref = artifact_ref_for_execution_artifact(execution, artifact, index=0)

    result = asyncio.run(
        materialize_execution_artifact_for_current_turn(
            artifact_ref=ref, execution=execution, sc=sc
        )
    )

    current = result["current_turn"]
    assert current["logical_path"] == (
        "conv:fi:conv_cur1.turn_9.files/recovered-job-artifacts/exec-1/src.bin"
    )
    assert current["physical_path"] == "turn_9/files/recovered-job-artifacts/exec-1/src.bin"
    # The materialized copy exists where the physical path says it does.
    assert (tmp_path / "out" / current["physical_path"]).read_bytes() == b"data"


# --- containment of local artifact reads -------------------------------------

# The package re-exports a function named ``execution_artifacts``, which shadows
# the submodule attribute; load the module itself.
ea = importlib.import_module("kdcube_ai_app.apps.chat.sdk.solutions.automations.execution_artifacts")


def _read(artifact, root):
    return asyncio.run(ea._read_artifact_bytes(artifact, storage_root=root))


@pytest.fixture
def storage_tree(tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    (root / "report.csv").write_text("mine", encoding="utf-8")
    outside = tmp_path / "host-secret.txt"
    outside.write_text("host secret", encoding="utf-8")
    return root, root / "report.csv", outside


@pytest.mark.parametrize("key", ["source_physical_path", "physical_path", "local_path"])
def test_an_absolute_path_outside_the_storage_root_is_refused(storage_tree, key):
    root, _inside, outside = storage_tree
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({key: str(outside)}, root)


def test_a_traversing_relative_path_is_refused(storage_tree):
    root, _inside, outside = storage_tree
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({"stored_path": f"../{outside.name}"}, root)
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({"physical_path": f"../{outside.name}"}, root)


def test_a_file_uri_outside_the_storage_root_is_refused(storage_tree):
    root, _inside, outside = storage_tree
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({"hosted_uri": outside.as_uri()}, root)


def test_a_sibling_directory_sharing_the_root_prefix_is_outside(tmp_path):
    root = tmp_path / "storage"
    sibling = tmp_path / "storage-evil"
    root.mkdir()
    sibling.mkdir()
    victim = sibling / "secret.txt"
    victim.write_text("x", encoding="utf-8")
    assert str(victim).startswith(str(root))
    assert ea._contained_path(victim, root) is None
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({"physical_path": str(victim)}, root)


def test_a_symlink_inside_the_root_pointing_out_is_refused(storage_tree):
    root, _inside, outside = storage_tree
    (root / "innocent.txt").symlink_to(outside)
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        _read({"stored_path": "innocent.txt"}, root)


def test_contained_paths_still_read(storage_tree):
    root, inside, _outside = storage_tree
    assert _read({"physical_path": str(inside)}, root) == b"mine"
    assert _read({"stored_path": "report.csv"}, root) == b"mine"
    assert _read({"stored_path": "sub/../report.csv"}, root) == b"mine"
    assert _read({"hosted_uri": inside.as_uri()}, root) == b"mine"


def test_a_refused_candidate_does_not_block_a_contained_fallback(storage_tree):
    root, _inside, outside = storage_tree
    artifact = {"physical_path": str(outside), "stored_path": "report.csv"}
    assert _read(artifact, root) == b"mine"


def test_a_refusal_is_logged_with_the_offending_key(storage_tree, caplog):
    root, _inside, outside = storage_tree
    with caplog.at_level("WARNING"):
        with pytest.raises(ea.ArtifactOutsideStorageRoot):
            _read({"local_path": str(outside)}, root)
    assert "REFUSED local_path" in caplog.text


def test_a_missing_contained_file_is_still_not_found(storage_tree):
    root, _inside, _outside = storage_tree
    with pytest.raises(FileNotFoundError) as excinfo:
        _read({"stored_path": "no-such-file.csv"}, root)
    assert not isinstance(excinfo.value, PermissionError)


def test_materialize_does_not_copy_an_outside_file_into_the_turn(tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    outside = tmp_path / "host-secret.txt"
    outside.write_text("host secret", encoding="utf-8")
    artifact = {"id": "a1", "kind": "file", "filename": "x.txt", "source_physical_path": str(outside), "visibility": "external"}
    execution = {"id": "exec-1", "automation_id": "auto-1", "artifacts": [artifact]}
    sc = {"turn_id": "turn_1", "outdir": str(tmp_path / "out"), "storage_root": str(root), "conversation_id": "c1"}
    ref = artifact_ref_for_execution_artifact(execution, artifact, index=0)
    with pytest.raises(ea.ArtifactOutsideStorageRoot):
        asyncio.run(materialize_execution_artifact_for_current_turn(artifact_ref=ref, execution=execution, sc=sc))
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").rglob("*.txt"))
