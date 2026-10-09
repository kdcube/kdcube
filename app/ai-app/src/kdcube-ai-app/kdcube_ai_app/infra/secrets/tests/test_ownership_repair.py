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


def _present(monkeypatch, tmp_path, attrs):
    """attrs: relative path -> dict(mode=, uid=, nlink=, dev=) applied to that inode's stat results."""
    by_inode = {}
    for rel, values in attrs.items():
        by_inode[os.lstat(tmp_path / rel).st_ino] = values
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
    changed = []
    monkeypatch.setattr(repair.os, "fchown", lambda fd, uid, gid: changed.append(os.readlink(f"/proc/self/fd/{fd}")))
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
    assert capsys.readouterr().out.strip() == "secrets ownership repair: adopted=0"
    assert repair.main(["only-one"]) == 2
