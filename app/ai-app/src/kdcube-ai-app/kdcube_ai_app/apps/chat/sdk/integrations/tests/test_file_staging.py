"""Staged references bind bounded, contained files to their authenticated owner."""
import inspect
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations import file_staging as staging


OWNER = SimpleNamespace(user_id="user-a", tenant="tenant-a", project="project-a")


def call(function, *args, owner=OWNER, **kwargs):
    # Execute the pre-change helper as well, so red/green exposes the unsafe
    # behavior rather than failing merely on the newly required keyword.
    keywords = {"owner": owner} if "owner" in inspect.signature(function).parameters else {}
    return function(*args, **kwargs, **keywords)


@pytest.mark.parametrize("identity", ["", "..", "../outside", "/outside", "a\\b", "%2e%2e", "a" * 31, "a" * 33, "A" * 32])
def test_only_minted_id_format_is_accepted_before_filesystem_access(identity, monkeypatch):
    def no_access(*args, **kwargs):
        pytest.fail("invalid identity must be refused before filesystem access")
    monkeypatch.setattr(Path, "resolve", no_access)
    with pytest.raises(ValueError):
        staging.parse_staged_ref(f"staged:{identity}:image.png")


def test_owned_slot_round_trip_and_metadata(tmp_path):
    ref = staging.new_staged_ref("folder/image.png")
    target = call(staging.save_staged, tmp_path, ref, b"image")
    assert call(staging.load_staged, tmp_path, ref) == ("image.png", b"image")
    record = json.loads((target.parent / ".owner.json").read_text())
    assert {k: record[k] for k in ("user", "tenant", "project")} == {"user": "user-a", "tenant": "tenant-a", "project": "project-a"}
    assert record["created"] <= time.time()
    call(staging.delete_staged, tmp_path, ref)
    assert not target.parent.exists()


@pytest.mark.parametrize("field", ["user_id", "tenant", "project"])
def test_different_owner_gets_the_same_missing_answer_and_cannot_delete(tmp_path, field):
    ref = staging.new_staged_ref("image.png")
    call(staging.save_staged, tmp_path, ref, b"private bytes")
    other = SimpleNamespace(**{**vars(OWNER), field: "other"})
    with pytest.raises(FileNotFoundError) as mismatch:
        call(staging.load_staged, tmp_path, ref, owner=other)
    with pytest.raises(FileNotFoundError) as absent:
        call(staging.load_staged, tmp_path, staging.new_staged_ref("image.png"), owner=other)
    assert str(mismatch.value) == str(absent.value)
    call(staging.delete_staged, tmp_path, ref, owner=other)
    assert call(staging.load_staged, tmp_path, ref)[1] == b"private bytes"


def test_unowned_legacy_slot_is_refused(tmp_path):
    ref = staging.new_staged_ref("image.png")
    identity, name = staging.parse_staged_ref(ref)
    (tmp_path / identity).mkdir()
    (tmp_path / identity / name).write_bytes(b"old bytes")
    with pytest.raises(FileNotFoundError):
        call(staging.load_staged, tmp_path, ref)


def test_upload_requires_precreated_owned_slot(tmp_path):
    ref = staging.new_staged_ref("image.png")
    with pytest.raises(FileNotFoundError):
        call(staging.save_staged, tmp_path, ref, b"bytes", owner=None)
    staging.create_slot(tmp_path, ref, OWNER)
    call(staging.save_staged, tmp_path, ref, b"bytes", owner=None)
    assert call(staging.load_staged, tmp_path, ref)[1] == b"bytes"


@pytest.mark.parametrize("operation", ["load", "save", "delete", "sweep"])
def test_symlink_slot_never_touches_an_outside_directory(tmp_path, operation):
    root, outside = tmp_path / "staging", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    sentinel = outside / "image.png"
    sentinel.write_bytes(b"unchanged")
    ref = staging.new_staged_ref("image.png")
    identity, _ = staging.parse_staged_ref(ref)
    (root / identity).symlink_to(outside, target_is_directory=True)
    os.utime(outside, (0, 0))
    if operation == "load":
        with pytest.raises((ValueError, FileNotFoundError)):
            call(staging.load_staged, root, ref)
    elif operation == "save":
        with pytest.raises((ValueError, FileNotFoundError)):
            call(staging.save_staged, root, ref, b"changed")
    elif operation == "delete":
        call(staging.delete_staged, root, ref)
    else:
        staging.sweep_expired(root, ttl_seconds=60)
    assert sentinel.read_bytes() == b"unchanged"


@pytest.mark.parametrize("name", ["image.png", ".owner.json"])
def test_slot_files_cannot_follow_symlinks(tmp_path, name):
    ref = staging.new_staged_ref("image.png")
    target = call(staging.save_staged, tmp_path, ref, b"bytes")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (target.parent / name).unlink(missing_ok=True)
    (target.parent / name).symlink_to(outside)
    with pytest.raises((ValueError, FileNotFoundError)):
        call(staging.load_staged, tmp_path, ref)
    with pytest.raises((ValueError, FileNotFoundError)):
        call(staging.save_staged, tmp_path, ref, b"new")
    assert outside.read_bytes() == b"outside"


def test_owner_filename_is_reserved_and_invalid_delete_is_a_noop(tmp_path):
    with pytest.raises(ValueError):
        staging.new_staged_ref(".owner.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_bytes(b"keep")
    call(staging.delete_staged, tmp_path / "staging", "staged:../outside:keep")
    assert sentinel.read_bytes() == b"keep"


def test_expired_slot_cannot_be_consumed(tmp_path, monkeypatch):
    ref = staging.new_staged_ref("image.png")
    call(staging.save_staged, tmp_path, ref, b"bytes")
    now = time.time()
    monkeypatch.setattr(staging.time, "time", lambda: now + staging.STAGED_TTL_SECONDS + 1)
    with pytest.raises(FileNotFoundError):
        call(staging.load_staged, tmp_path, ref)
