# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W677 startup ownership repair: only root-owned private entries on a trusted, same-filesystem folder chain
are handed to the owner, through no-follow descriptors. SYNTHETIC: real files are presented as root-owned
with chosen modes, link counts and devices by intercepting stat/fstat (matched by inode); fchown is recorded,
never performed; geteuid is patched to 0. No real ownership change happens.
"""
from __future__ import annotations

import os
import stat as S

import pytest

from kdcube_ai_app.infra.secrets import ownership_repair as repair

OWNER = 1000


def _present(monkeypatch, tmp_path, attrs, refuse=None, refuse_mode=None):
    """attrs: relative path -> dict(mode=, uid=, nlink=, dev=) applied to that inode's stat results.
    refuse(values) / refuse_mode(values, mode) -> errno: the synthetic filesystem refuses that fchown / fchmod,
    as a sharing layer may. Entries created during the run are recorded as "<new>"."""
    by_inode = {}
    for rel, values in attrs.items():
        by_inode[os.lstat(tmp_path / rel).st_ino] = dict(values)  # one copy per inode
    real_stat, real_fstat = os.stat, os.fstat

    def shape(info):
        values = by_inode.get(info.st_ino)
        if values is None:
            return info
        kind = S.S_IFMT(info.st_mode)
        mode = kind | values.get("mode", S.S_IMODE(info.st_mode))
        return os.stat_result((mode, info.st_ino, values.get("dev", info.st_dev), values.get("nlink", info.st_nlink),
                               values.get("uid", info.st_uid), info.st_gid, info.st_size, 0, 0, 0))

    monkeypatch.setattr(repair.os, "stat", lambda *a, **k: shape(real_stat(*a, **k)))
    monkeypatch.setattr(repair.os, "fstat", lambda fd: shape(real_fstat(fd)))
    monkeypatch.setattr(repair.os, "geteuid", lambda: 0)
    # Portable (no /proc on macOS): the descriptor's inode names the path that would change owner.
    path_of = {os.lstat(path).st_ino: str(path) for path in tmp_path.rglob("*")}
    changed = []

    def fchown(fd, uid, gid):  # recorded, never performed; later stat calls see the new owner, as after a real one
        ino = real_fstat(fd).st_ino
        code = refuse(by_inode.setdefault(ino, {})) if refuse else None
        if code:
            raise PermissionError(code, os.strerror(code))
        changed.append(path_of.get(ino, "<new>"))
        by_inode[ino]["uid"] = uid

    def fchmod(fd, mode):  # recorded the same way: later stat calls see the new mode
        ino = real_fstat(fd).st_ino
        code = refuse_mode(by_inode.setdefault(ino, {}), mode) if refuse_mode else None
        if code:
            raise PermissionError(code, os.strerror(code))
        changed.append(f"{path_of.get(ino, '<new>')} mode={oct(mode)}")
        by_inode[ino]["mode"] = mode

    monkeypatch.setattr(repair.os, "fchown", fchown)
    monkeypatch.setattr(repair.os, "fchmod", fchmod)
    return changed


def _tree(tmp_path):
    root = tmp_path / "config" / "secrets"
    for rel in ("hub/ns", "hub/users/u", "foreign", "public", "mounted"):
        (root / rel).mkdir(parents=True)
    for rel in ("hub/ns/record.json", "hub/ns/tombstone.json", "hub/ns/broad.json", "hub/ns/linked.json",
                "hub/users/u/k.json", "foreign/inside.json", "public/inside.json", "mounted/inside.json"):
        (root / rel).write_text("{}")
    os.link(root / "hub/ns/linked.json", tmp_path / "outside-alias")
    (root / "hub/ns/link.json").symlink_to(root / "hub/ns/record.json")
    return root


def test_only_private_entries_on_a_trusted_chain_are_adopted(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    p = lambda rel: f"config/secrets/{rel}" if rel else "config/secrets"  # noqa: E731
    root_dir, root_file, tomb = {"mode": 0o700, "uid": 0}, {"mode": 0o600, "uid": 0, "nlink": 1}, {"mode": 0o400, "uid": 0, "nlink": 1}
    attrs = {p(""): root_dir, p("hub"): {"mode": 0o700, "uid": OWNER}, p("hub/ns"): root_dir,
             p("hub/users"): root_dir, p("hub/users/u"): root_dir,
             p("hub/ns/record.json"): root_file, p("hub/ns/tombstone.json"): tomb,
             p("hub/ns/broad.json"): {"mode": 0o644, "uid": 0, "nlink": 1},
             p("hub/ns/linked.json"): {"mode": 0o600, "uid": 0, "nlink": 2},
             p("hub/users/u/k.json"): root_file,
             p("foreign"): {"mode": 0o700, "uid": 4242}, p("foreign/inside.json"): root_file,
             p("public"): {"mode": 0o755, "uid": 0}, p("public/inside.json"): root_file,
             p("mounted"): {"mode": 0o700, "uid": 0, "dev": 999999}, p("mounted/inside.json"): root_file}
    changed = _present(monkeypatch, tmp_path, attrs)
    assert repair.repair_secrets_ownership(str(root), str(OWNER)) == 7
    adopted = sorted(os.path.relpath(path, root) for path in changed)
    assert adopted == sorted([".", "hub/ns", "hub/users", "hub/users/u", "hub/ns/record.json",
                              "hub/ns/tombstone.json", "hub/users/u/k.json"])
    # never: the 0644 file, the hard-linked file (its outside alias), the symlink, anything under a foreign-
    # owned, a 0755 or an other-filesystem folder, nor the owner-owned folder itself


@pytest.mark.parametrize("target", ["/", "", "relative/secrets", "/etc", "{root}/..", "{alias}", "{missing}"])
def test_unsafe_targets_are_refused_before_any_open(tmp_path, monkeypatch, target):
    root = _tree(tmp_path)
    (tmp_path / "alias").symlink_to(root)
    target = target.format(root=root, alias=tmp_path / "alias", missing=tmp_path / "missing" / "x")
    monkeypatch.setattr(repair.os, "geteuid", lambda: 0)

    def forbidden(*args, **kwargs):
        raise AssertionError("must not open or change anything")

    monkeypatch.setattr(repair.os, "open", forbidden)
    monkeypatch.setattr(repair.os, "fchown", forbidden)
    assert repair.repair_secrets_ownership(target, str(OWNER)) == 0


def test_an_untrusted_target_folder_or_non_root_caller_changes_nothing(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    changed = _present(monkeypatch, tmp_path, {"config/secrets": {"mode": 0o755, "uid": 0}})
    assert repair.repair_secrets_ownership(str(root), str(OWNER)) == 0 and changed == []
    monkeypatch.setattr(repair.os, "geteuid", lambda: OWNER)
    assert repair.repair_secrets_ownership(str(root), str(OWNER)) == 0
    for bad_owner in ("0", "", "abc", "-1"):
        monkeypatch.setattr(repair.os, "geteuid", lambda: 0)
        assert repair.repair_secrets_ownership(str(root), bad_owner) == 0


def test_an_entry_swapped_between_check_and_open_is_not_adopted(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    p = "config/secrets"
    attrs = {p: {"mode": 0o700, "uid": OWNER}, f"{p}/hub": {"mode": 0o700, "uid": OWNER},
             f"{p}/hub/ns": {"mode": 0o700, "uid": OWNER}, f"{p}/hub/ns/record.json": {"mode": 0o600, "uid": 0, "nlink": 1}}
    changed = _present(monkeypatch, tmp_path, attrs)
    real_open = os.open

    def swapping_open(name, flags, *args, **kwargs):  # the name now points to another inode
        if name == "record.json":
            return real_open(str(root / "hub/ns/broad.json"), os.O_RDONLY)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(repair.os, "open", swapping_open)
    assert repair.repair_secrets_ownership(str(root), str(OWNER)) == 0 and changed == []


def test_the_module_command_prints_only_a_count(tmp_path, capsys):
    assert repair.main([str(tmp_path), "1000"]) == 0
    assert capsys.readouterr().out.strip() == "secrets ownership repair: adopted=0 deferred=0"
    assert repair.main(["only-one"]) == 2


def test_a_transient_failure_is_deferred_and_retried_in_the_same_start(tmp_path, monkeypatch, capsys):
    """Live W677 follow-up: an entry the first pass could not finish is adopted by a repeat pass, and the
    command reports what stayed deferred instead of a silent partial count."""
    root = _tree(tmp_path)
    p = "config/secrets"
    attrs = {p: {"mode": 0o700, "uid": OWNER}, f"{p}/hub": {"mode": 0o700, "uid": OWNER},
             f"{p}/hub/ns": {"mode": 0o700, "uid": OWNER}, f"{p}/hub/ns/record.json": {"mode": 0o600, "uid": 0, "nlink": 1},
             f"{p}/hub/ns/tombstone.json": {"mode": 0o400, "uid": 0, "nlink": 1}}
    changed = _present(monkeypatch, tmp_path, attrs)
    real_open, failures = os.open, {"record.json": 1}

    def flaky_open(name, flags, *args, **kwargs):
        if failures.get(name):
            failures[name] -= 1
            raise OSError("synthetic transient I/O error")
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(repair.os, "open", flaky_open)
    assert repair.repair_secrets_ownership_report(str(root), str(OWNER)) == (2, 0)
    assert sorted(os.path.basename(path) for path in changed) == ["record.json", "tombstone.json"]


def test_a_persistent_failure_is_reported_as_deferred(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path)
    p = "config/secrets"
    attrs = {p: {"mode": 0o700, "uid": OWNER}, f"{p}/hub": {"mode": 0o700, "uid": OWNER},
             f"{p}/hub/ns": {"mode": 0o700, "uid": OWNER}, f"{p}/hub/ns/record.json": {"mode": 0o600, "uid": 0, "nlink": 1}}
    _present(monkeypatch, tmp_path, attrs)
    real_open = os.open

    def failing_open(name, flags, *args, **kwargs):
        if name == "record.json":
            raise OSError("synthetic persistent I/O error")
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(repair.os, "open", failing_open)
    assert repair.repair_secrets_ownership_report(str(root), str(OWNER)) == (0, 1)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == (
        "secrets ownership repair: adopted=0 deferred=1 reasons=file-open:OSError*1")


TOMBSTONE = b'{"expires_at":1760000000,"value":null}'  # exactly what runtime_file writes
TOMB = "0123456789abcdef0123456789abcdef.json"  # a runtime record name, in <root>/hub/ns/


def _tombstone_chain(tmp_path, content=TOMBSTONE, folder="hub/ns"):
    root = _tree(tmp_path)
    (root / folder).mkdir(parents=True, exist_ok=True)
    (root / folder / TOMB).write_bytes(content)
    p = "config/secrets"
    attrs = {p: {"mode": 0o700, "uid": OWNER}, f"{p}/hub/ns/record.json": {"mode": 0o600, "uid": 0, "nlink": 1},
             f"{p}/{folder}/{TOMB}": {"mode": 0o400, "uid": 0, "nlink": 1}}
    parts = folder.split("/")
    for depth in range(1, len(parts) + 1):
        attrs[f"{p}/{'/'.join(parts[:depth])}"] = {"mode": 0o700, "uid": OWNER}
    attrs[f"{p}/hub/ns"] = {"mode": 0o700, "uid": OWNER}
    return root, attrs, root / folder / TOMB


def _read_only_share(values):
    """Live W677 (Docker Desktop on macOS): re-owning a read-only file is refused; a writable one is not."""
    import errno
    return errno.EACCES if values.get("mode") == 0o400 else None


def _leftovers(folder):
    return [path.name for path in folder.iterdir() if path.name.startswith(".tombstone-repair-")]


def _names(changed):
    return [os.path.basename(entry) for entry in changed]


def test_a_read_only_tombstone_the_share_will_not_re_own_is_replaced_by_an_owned_0400_copy(
        tmp_path, monkeypatch, capsys):
    """Live W677 follow-up (deferred=2, both root-owned 0400 tombstones). The original is never re-moded: a
    complete owner-owned 0400 copy with the same bytes replaces it."""
    root, attrs, tomb = _tombstone_chain(tmp_path)
    original = os.lstat(tomb).st_ino
    changed = _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == "secrets ownership repair: adopted=2 deferred=0"
    now = repair.os.stat(tomb, follow_symlinks=False)
    assert now.st_ino != original and now.st_uid == OWNER and S.S_IMODE(now.st_mode) == 0o400
    assert tomb.read_bytes() == TOMBSTONE and _leftovers(tomb.parent) == []
    # only the new copy is ever re-moded, and only to 0400; the record is re-owned in place
    assert [entry for entry in changed if "mode=" in entry] == ["<new> mode=0o400"]
    assert sorted(_names(changed)) == ["<new>", "<new> mode=0o400", "record.json"]


def test_a_failed_replacement_leaves_the_original_and_stays_deferred_on_every_pass_and_start(
        tmp_path, monkeypatch, capsys):
    """CodeApp's PR347 finding: a failure after an ownership change must not print a clean start while an
    unsafe tombstone stays. Here the copy's 0400 step is refused: the original root-owned 0400 tombstone is
    untouched, the copy is removed, and the deferral is reported by every pass and every start."""
    import errno
    root, attrs, tomb = _tombstone_chain(tmp_path)
    original = os.lstat(tomb).st_ino
    changed = _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share,
                       refuse_mode=lambda values, mode: errno.EACCES)
    for start in range(2):
        assert repair.main([str(root), str(OWNER)]) == 0
        assert capsys.readouterr().out.strip() == (
            f"secrets ownership repair: adopted={1 - start} deferred=1 reasons=tombstone-replace:EACCES*1")
        now = repair.os.stat(tomb, follow_symlinks=False)
        assert now.st_ino == original and now.st_uid == 0 and S.S_IMODE(now.st_mode) == 0o400
        assert tomb.read_bytes() == TOMBSTONE and _leftovers(tomb.parent) == []
    assert not any(entry.startswith(TOMB) for entry in _names(changed))


@pytest.mark.parametrize("content", [
    b'{"expires_at":1760000000,"value":"synthetic"}',  # a value
    b'{"value":"synthetic-sensitive","value":null,"expires_at":1760000000}',  # CodeApp: duplicate key, last wins
    b'{"expires_at":1760000000,"val\\u0075e":null}',  # an escaped key
    b'{"value":null,"expires_at":1760000000}',  # other order
    b'{"expires_at": 1760000000, "value": null}',  # other spacing
    b'{"expires_at":1760000000,"value":null}\n',  # trailing bytes
], ids=["value", "duplicate-key", "escaped-key", "order", "spacing", "trailing"])
def test_only_the_exact_store_tombstone_bytes_are_ever_copied(tmp_path, monkeypatch, capsys, content):
    root, attrs, tomb = _tombstone_chain(tmp_path, content=content)
    changed = _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == (
        "secrets ownership repair: adopted=1 deferred=1 reasons=tombstone:not-value-free*1")
    assert _names(changed) == ["record.json"] and _leftovers(tomb.parent) == []
    assert tomb.read_bytes() == content


@pytest.mark.parametrize("folder", ["hub", "hub/users", "hub/users/u", "hub/ns/deeper"],
                         ids=["app-secret-level", "users-folder", "user-secret", "below-record-folder"])
def test_the_copy_route_is_only_for_runtime_record_folders(tmp_path, monkeypatch, capsys, folder):
    root, attrs, tomb = _tombstone_chain(tmp_path, folder=folder)
    changed = _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == (
        "secrets ownership repair: adopted=1 deferred=1 reasons=file-chown:EACCES*1")
    assert _names(changed) == ["record.json"] and _leftovers(tomb.parent) == []


def test_a_refused_owner_change_on_a_live_record_is_deferred_with_its_errno_and_mode_kept(
        tmp_path, monkeypatch, capsys):
    import errno
    root, attrs, tomb = _tombstone_chain(tmp_path)
    changed = _present(monkeypatch, tmp_path, attrs,
                       refuse=lambda values: errno.EPERM if values.get("uid") == 0 else None)
    assert repair.main([str(root), str(OWNER)]) == 0
    out = capsys.readouterr().out.strip()
    # the 0600 record is never re-moded or copied; the tombstone takes the copy route and is replaced
    assert out == "secrets ownership repair: adopted=1 deferred=1 reasons=file-chown:EPERM*1"
    assert not any("record.json" in entry for entry in changed)
    assert S.S_IMODE(repair.os.stat(root / "hub/ns/record.json").st_mode) == 0o600


def test_a_tombstone_name_that_changes_before_the_check_is_never_overwritten(tmp_path, monkeypatch, capsys):
    """The re-check covers a change before it; the interval after it is covered by the store protocol (module
    doc), not by this test."""
    root, attrs, tomb = _tombstone_chain(tmp_path)
    _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share)
    real_fsync, swapped = os.fsync, []

    def fsync(fd):  # between the copy's write and the re-check, another file is put under the name
        if not swapped:
            (tomb.parent / "newer").write_bytes(b"synthetic newer entry")
            os.rename(tomb.parent / "newer", tomb)
            swapped.append(True)
        real_fsync(fd)

    monkeypatch.setattr(repair.os, "fsync", fsync)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == "secrets ownership repair: adopted=1 deferred=0"
    assert tomb.read_bytes() == b"synthetic newer entry" and _leftovers(tomb.parent) == []


def test_the_copy_route_is_only_for_runtime_record_names(tmp_path, monkeypatch, capsys):
    root, attrs, tomb = _tombstone_chain(tmp_path)
    other = tomb.parent / "tombstone.json"
    other.write_bytes(TOMBSTONE)
    tomb.unlink()
    attrs = {key: value for key, value in attrs.items() if not key.endswith(TOMB)}
    attrs["config/secrets/hub/ns/tombstone.json"] = {"mode": 0o400, "uid": 0, "nlink": 1}
    changed = _present(monkeypatch, tmp_path, attrs, refuse=_read_only_share)
    assert repair.main([str(root), str(OWNER)]) == 0
    assert capsys.readouterr().out.strip() == (
        "secrets ownership repair: adopted=1 deferred=1 reasons=file-chown:EACCES*1")
    assert _names(changed) == ["record.json"] and _leftovers(tomb.parent) == []
