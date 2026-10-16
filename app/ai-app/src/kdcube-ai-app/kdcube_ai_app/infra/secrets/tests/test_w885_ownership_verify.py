# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W885: on a Docker Desktop for macOS bind mount, fchown on the descriptor that created a file is ignored without
an error, while fchown on a reopened descriptor and a change by name (fchownat, no-follow) take effect.
Measured on dev-main 2026-10-16 with throwaway containers: the store's write path (mkstemp, fchown on that
descriptor, replace) left uid 0; os.chown(name, dir_fd=..., follow_symlinks=False) gave uid 1000. So every
record a root process wrote stayed root-owned, and the repair's tombstone copy did too ("adopted=43" in
chat-proc's log while 42 copied tombstones stayed uid 0).

SYNTHETIC: files are real, ownership is not. Every entry is presented as owned by uid 0 until a simulated
change; geteuid is patched to 0. ``sticky_fchown=False`` imitates the share: fchown is accepted and changes
nothing. ``chown_works=False`` makes the change by name fail too.
"""
from __future__ import annotations

import json
import os

import pytest

from kdcube_ai_app.infra.secrets import ownership_repair as repair
from kdcube_ai_app.infra.secrets import runtime_file

OWNER = 1000


def _share(monkeypatch, module, *, sticky_fchown: bool, chown_works: bool = True):
    owners: dict[int, int] = {}  # inode -> uid; absent means root (0)
    real_stat, real_fstat, real_lstat = os.stat, os.fstat, os.lstat
    calls = {"fchown": 0, "chown": 0}

    def shaped(info):
        uid = owners.get(info.st_ino, 0)
        values = list(info)
        values[4] = uid
        return os.stat_result(values)

    def stat(path, *, dir_fd=None, follow_symlinks=True):
        return shaped(real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks))

    def fchown(fd, uid, gid):
        calls["fchown"] += 1
        if sticky_fchown:
            owners[real_fstat(fd).st_ino] = uid

    def chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
        calls["chown"] += 1
        assert follow_symlinks is False, "a change by name never follows a link"
        if chown_works:
            owners[real_stat(path, dir_fd=dir_fd, follow_symlinks=False).st_ino] = uid

    monkeypatch.setattr(module.os, "stat", stat)
    monkeypatch.setattr(module.os, "fstat", lambda fd: shaped(real_fstat(fd)))
    monkeypatch.setattr(module.os, "lstat", lambda path: shaped(real_lstat(path)))
    monkeypatch.setattr(module.os, "fchown", fchown)
    monkeypatch.setattr(module.os, "chown", chown)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    owner_of = lambda path: owners.get(real_lstat(path).st_ino, 0)  # noqa: E731
    return owner_of, calls


def _tree(tmp_path):
    root = tmp_path / "config" / "secrets"
    for rel in ("hub", "hub/users", "hub/users/u", "hub/ns"):
        (root / rel).mkdir(parents=True, mode=0o700)
        os.chmod(root / rel, 0o700)
    os.chmod(root, 0o700)
    os.chmod(root.parent, 0o700)
    record = root / "hub" / "users" / "u" / "delegated_to_kdcube.credentials.c.json"
    record.write_text('{"value": "x"}')
    os.chmod(record, 0o600)
    return root, record


def test_an_fchown_the_share_ignores_is_completed_by_name_and_counted_only_then(tmp_path, monkeypatch, capsys):
    root, record = _tree(tmp_path)
    owner_of, calls = _share(monkeypatch, repair, sticky_fchown=False)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert owner_of(record) == OWNER, "the per-user record really ends owned by the secrets owner"
    for folder in (root, root / "hub", root / "hub" / "users", root / "hub" / "users" / "u", root / "hub" / "ns"):
        assert owner_of(folder) == OWNER
    assert calls["chown"] >= 6
    out = capsys.readouterr().out
    assert "adopted=6 deferred=0" in out, out


def test_with_a_working_fchown_nothing_is_changed_by_name(tmp_path, monkeypatch):
    root, record = _tree(tmp_path)
    owner_of, calls = _share(monkeypatch, repair, sticky_fchown=True)
    assert repair.repair_secrets_ownership(str(root), str(OWNER)) == 6
    assert owner_of(record) == OWNER and calls["chown"] == 0


def test_an_owner_that_cannot_be_set_is_deferred_never_counted_as_adopted(tmp_path, monkeypatch, capsys):
    root, record = _tree(tmp_path)
    owner_of, _calls = _share(monkeypatch, repair, sticky_fchown=False, chown_works=False)
    assert repair.main([str(root), str(OWNER)]) == 0
    out = capsys.readouterr().out
    assert "adopted=0" in out and "owner-unverified" in out, out
    assert owner_of(record) == 0


def test_a_replaced_tombstone_is_owned_by_the_owner_even_when_fchown_does_not_stick(tmp_path, monkeypatch, capsys):
    root, _record = _tree(tmp_path)
    tombstone = root / "hub" / "ns" / ("a" * 32 + ".json")
    tombstone.write_bytes(json.dumps({"value": None, "expires_at": 4102444800}, sort_keys=True,
                                     separators=(",", ":")).encode())
    os.chmod(tombstone, 0o400)
    owner_of, _calls = _share(monkeypatch, repair, sticky_fchown=False)
    real_fchown = repair.os.fchown

    def refusing(fd, uid, gid):  # the share refuses to re-own a read-only file, so the copy route runs
        if os.fstat(fd).st_mode & 0o777 == 0o400 and os.fstat(fd).st_size < 64:
            raise PermissionError(1, "Operation not permitted")
        real_fchown(fd, uid, gid)

    monkeypatch.setattr(repair.os, "fchown", refusing)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert owner_of(tombstone) == OWNER and oct(os.lstat(tombstone).st_mode & 0o777) == "0o400"
    assert "deferred=0" in capsys.readouterr().out


def test_adopt_completes_by_name_when_fchown_does_not_stick(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text("{}")
    owner_of, calls = _share(monkeypatch, runtime_file, sticky_fchown=False)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        runtime_file.adopt(descriptor, OWNER, path=str(path))
    finally:
        os.close(descriptor)
    assert owner_of(path) == OWNER and calls["chown"] == 1


def test_adopt_refuses_rather_than_leaving_a_root_owned_record(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text("{}")
    _share(monkeypatch, runtime_file, sticky_fchown=False, chown_works=False)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(PermissionError):
            runtime_file.adopt(descriptor, OWNER, path=str(path))
    finally:
        os.close(descriptor)


def test_every_store_write_passes_the_file_path_to_adopt():
    """The record writers name the file, so adopt can verify it (an fd-only call cannot)."""
    from pathlib import Path

    folder = Path(runtime_file.__file__).parent
    for name in ("runtime_file.py", "user_secret_files.py"):
        source = (folder / name).read_text()
        assert "adopt(stream.fileno()" in source
        assert all("path=temporary" in line for line in source.splitlines() if "adopt(stream.fileno()" in line)
