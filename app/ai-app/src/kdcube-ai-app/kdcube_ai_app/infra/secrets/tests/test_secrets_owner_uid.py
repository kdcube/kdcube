# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W677: one canonical owner uid for the secrets folder, whatever uid each container runs as.

The apps run as appuser (1000); docker exec sessions, the one-time migration and kdcube-secrets run as root.
Every process checks entries against KDCUBE_SECRETS_OWNER_UID; root hands what it creates to that owner
before publishing; any other uid is refused. Root is simulated (geteuid patched): a chown to one's own uid
is a real, permitted call here, so the hand-over path runs for real.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from kdcube_ai_app.infra.secrets import runtime_file
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileError, RuntimeFileStore
from kdcube_ai_app.infra.secrets import user_secret_files
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore

HUB, NS, REF = "connection-hub@1-0", "card-credentials", "d" * 32
ME = os.geteuid()


def _records(root):
    return RuntimeFileStore(root=root, namespace=NS, authorized_namespaces=(NS,), owner=HUB)


@pytest.fixture
def as_root(monkeypatch):
    calls = []
    real_fchown, real_chown = os.fchown, os.chown

    def fchown(fd, uid, gid):
        calls.append(("fchown", uid))
        return real_fchown(fd, uid, gid)

    def chown(path, uid, gid, follow_symlinks=True):
        calls.append(("chown", uid))
        return real_chown(path, uid, gid, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(runtime_file.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime_file.os, "fchown", fchown)
    monkeypatch.setattr(runtime_file.os, "chown", chown)
    return calls


def test_root_hands_folders_and_records_to_the_owner(tmp_path, monkeypatch, as_root):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    store = _records(tmp_path / "secrets")
    assert store.create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    assert ("chown", ME) in as_root and ("fchown", ME) in as_root  # folders and the record were handed over
    assert store.get(secret_ref=REF) == "synthetic"
    files = UserSecretFileStore(root=tmp_path / "secrets")
    files.set_app(bundle_id=HUB, key="k", value="synthetic-app")
    files.set(user_id="u", bundle_id=HUB, key="k", value="synthetic-user")
    assert files.get_app(bundle_id=HUB, key="k") == "synthetic-app"
    for path in (tmp_path / "secrets").rglob("*"):
        assert path.lstat().st_uid == ME


def test_owner_and_root_share_one_namespace_across_processes(tmp_path, monkeypatch):
    """Two processes: the owner (as itself) and a root session (simulated) create, read and delete."""
    root = tmp_path / "secrets"
    script = (
        "import os,sys,time\n"
        "from kdcube_ai_app.infra.secrets import runtime_file\n"
        "if sys.argv[2]=='root': runtime_file.os.geteuid=lambda: 0\n"
        "from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore\n"
        "from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore\n"
        "s=RuntimeFileStore(root=sys.argv[1],namespace='card-credentials',authorized_namespaces=('card-credentials',),"
        "owner='connection-hub@1-0')\n"
        "f=UserSecretFileStore(root=sys.argv[1])\n"
        "op=sys.argv[3]\n"
        "if op=='create': print(s.create(secret_ref=sys.argv[4],value='v-'+sys.argv[2],expires_at=int(time.time())+60));"
        " f.set_app(bundle_id='connection-hub@1-0',key='k-'+sys.argv[2],value='a-'+sys.argv[2])\n"
        "elif op=='read': print(s.get(secret_ref=sys.argv[4]), f.get_app(bundle_id='connection-hub@1-0',key=sys.argv[5]))\n"
        "elif op=='delete': s.delete(secret_ref=sys.argv[4]); f.delete_app(bundle_id='connection-hub@1-0',key=sys.argv[5]);"
        " print('deleted')\n"
    )
    env = {**os.environ, "KDCUBE_SECRETS_OWNER_UID": str(ME)}

    def run(*args):
        result = subprocess.run([sys.executable, "-c", script, str(root), *args], env=env, capture_output=True,
                                text=True, timeout=30)
        assert result.returncode == 0, result.stderr[-400:]
        return result.stdout.strip()

    assert run("owner", "create", "1" * 32) == "True"
    assert run("root", "create", "2" * 32) == "True"
    assert run("root", "read", "1" * 32, "k-owner") == "v-owner a-owner"
    assert run("owner", "read", "2" * 32, "k-root") == "v-root a-root"
    assert run("owner", "delete", "2" * 32, "k-root") == "deleted"
    assert run("root", "delete", "1" * 32, "k-owner") == "deleted"
    assert run("owner", "read", "1" * 32, "k-owner") == "None None"


def test_a_non_owner_non_root_process_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    _records(tmp_path / "secrets").create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    UserSecretFileStore(root=tmp_path / "secrets").set_app(bundle_id=HUB, key="k", value="synthetic")
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME + 4242))  # this process is neither owner nor root
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _records(tmp_path / "secrets").get(secret_ref=REF)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        UserSecretFileStore(root=tmp_path / "secrets").get_app(bundle_id=HUB, key="k")
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        UserSecretFileStore(root=tmp_path / "secrets").set_app(bundle_id=HUB, key="other", value="v")


def test_a_foreign_owned_entry_is_refused_even_for_root(tmp_path, monkeypatch, as_root):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    _records(tmp_path / "secrets").create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME + 4242))  # the entries now belong to someone else
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _records(tmp_path / "secrets").get(secret_ref=REF)


@pytest.mark.parametrize("bad", ["abc", "-1", "1.0"])
def test_a_malformed_owner_setting_refuses(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", bad)
    with pytest.raises(RuntimeFileError):
        _records(tmp_path / "secrets").create(secret_ref=REF, value="v", expires_at=int(time.time()) + 60)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        UserSecretFileStore(root=tmp_path / "secrets").set_app(bundle_id=HUB, key="k", value="v")


def test_broad_modes_stay_refused_for_the_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    files = UserSecretFileStore(root=tmp_path / "secrets")
    files.set_app(bundle_id=HUB, key="k", value="synthetic")
    (tmp_path / "secrets" / HUB / "k.json").chmod(0o644)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        files.get_app(bundle_id=HUB, key="k")
    (tmp_path / "secrets" / HUB).chmod(0o755)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        files.list_app_keys(bundle_id=HUB)


def test_unset_owner_keeps_the_process_euid():
    os.environ.pop("KDCUBE_SECRETS_OWNER_UID", None)
    assert runtime_file.secrets_owner_uid() == os.geteuid()


def test_the_sandbox_owns_its_private_copy(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.runtime.isolated import py_code_exec_entry

    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", "1000")
    for name in ("KDCUBE_RUNTIME_ASSEMBLY_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_YAML_B64", "KDCUBE_RUNTIME_GATEWAY_YAML_B64",
                 "KDCUBE_RUNTIME_SECRETS_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64"):
        monkeypatch.delenv(name, raising=False)
    py_code_exec_entry._materialize_runtime_descriptor_payloads(type("L", (), {"log": lambda self, *a: None})())
    assert os.environ["KDCUBE_SECRETS_OWNER_UID"] == str(os.geteuid())


def test_the_exec_supervisor_owns_the_materialized_secrets_and_the_executor_cannot_read_them(tmp_path, monkeypatch):
    """The exec container: the root supervisor materializes the records; generated code (uid 1001) is refused."""
    import base64
    import json

    from kdcube_ai_app.apps.chat.sdk.runtime.isolated import py_code_exec_entry

    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", "1000")  # even if compose's value leaked into the sandbox
    monkeypatch.setattr(runtime_file.os, "geteuid", lambda: 0)  # the supervisor runs as root
    monkeypatch.setattr(runtime_file.os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(runtime_file.os, "fchown", lambda *a, **k: None)
    monkeypatch.setattr(py_code_exec_entry.os, "geteuid", lambda: 0)
    for name in ("KDCUBE_RUNTIME_ASSEMBLY_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_YAML_B64", "KDCUBE_RUNTIME_GATEWAY_YAML_B64",
                 "KDCUBE_RUNTIME_SECRETS_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64"):
        monkeypatch.delenv(name, raising=False)
    py_code_exec_entry._materialize_runtime_descriptor_payloads(type("L", (), {"log": lambda self, *a: None})())
    assert os.environ["KDCUBE_SECRETS_OWNER_UID"] == "0"  # the sandbox's private copy belongs to its supervisor
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))  # real files here are owned by this test user
    payload = base64.b64encode(json.dumps({"records": {
        f"bundles.{HUB}.secrets.k": "synthetic-app", f"users.u.bundles.{HUB}.secrets.k": "synthetic-user"}}).encode()).decode()
    runtime_dir = tmp_path / "exec"
    runtime_dir.mkdir(mode=0o700)
    py_code_exec_entry._materialize_secret_records(runtime_dir, type("L", (), {"log": lambda self, *a: None})(), payload)
    store = UserSecretFileStore(root=runtime_dir / "secrets")
    assert store.get_app(bundle_id=HUB, key="k") == "synthetic-app"
    for path in (runtime_dir / "secrets").rglob("*"):
        assert path.stat().st_mode & 0o077 == 0  # no group/other bits: uid 1001 has no OS access
    monkeypatch.setattr(runtime_file.os, "geteuid", lambda: 1001)  # the executor
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        store.get_app(bundle_id=HUB, key="k")
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        store.get(user_id="u", bundle_id=HUB, key="k")


def test_kdcube_secrets_startup_reowns_only_root_owned_entries(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[6] / "deployment/docker/all_in_one_kdcube/secrets/secrets_service_entrypoint.py"
    spec = importlib.util.spec_from_file_location("secrets_service_entrypoint_w677", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    root = tmp_path / "secrets"
    (root / HUB).mkdir(parents=True)
    (root / HUB / "root.json").write_text("{}")
    (root / HUB / "foreign.json").write_text("{}")
    os.symlink(root / HUB / "root.json", root / HUB / "link.json")
    real_lstat, chowned = os.lstat, []

    def lstat(p, *a, **k):
        info = real_lstat(p, *a, **k)
        if str(p).endswith("foreign.json"):
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 4242, info.st_gid,
                                   info.st_size, 0, 0, 0))
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 0, info.st_gid,
                               info.st_size, 0, 0, 0))

    monkeypatch.setattr(entry.os, "lstat", lstat)
    monkeypatch.setattr(entry.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entry.os, "chown", lambda p, uid, gid, follow_symlinks=True: chowned.append((Path(p).name, uid)))
    assert entry.adopt_root_owned_entries(str(root), "1000") == 3  # root folder, bundle folder, root.json
    assert sorted(chowned) == sorted([("secrets", 1000), (HUB, 1000), ("root.json", 1000)])
    monkeypatch.setattr(entry.os, "geteuid", lambda: 1000)
    assert entry.adopt_root_owned_entries(str(root), "1000") == 0  # only root repairs


# W677 acceptance: both creation orders, every runtime namespace, qualify/create/read/delete, then the
# negative cases. SYNTHETIC uids: "root" is os.geteuid patched to 0 (a chown to one's own uid is a real call),
# the owner is this test user; no test runs with real uid changes.
NAMESPACES = ("card-credentials", "login-attempts", "oauth-refresh-tokens")


def _ns_store(root, namespace):
    return RuntimeFileStore(root=root, namespace=namespace, authorized_namespaces=NAMESPACES, owner=HUB)


@pytest.mark.parametrize("namespace", NAMESPACES)
@pytest.mark.parametrize("first", ["owner", "root"])
def test_both_creation_orders_share_every_namespace(tmp_path, monkeypatch, namespace, first):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    monkeypatch.setattr("kdcube_ai_app.infra.secrets.runtime_contract.persistent_filesystem", lambda root: True)
    root = tmp_path / "secrets"
    order = [first, "root" if first == "owner" else "owner"]

    def act_as(who):
        monkeypatch.setattr(runtime_file.os, "geteuid", (lambda: 0) if who == "root" else (lambda: ME))

    for index, who in enumerate(order):
        act_as(who)
        store = _ns_store(root, namespace)
        store.qualify()
        assert store.create(secret_ref=f"{index + 1:032x}", value=f"synthetic-{who}", expires_at=int(time.time()) + 60)
    for who in order:  # each reads the other's record and its own
        act_as(who)
        store = _ns_store(root, namespace)
        assert {store.get(secret_ref=f"{i + 1:032x}") for i in range(2)} == {"synthetic-owner", "synthetic-root"}
    act_as(order[1])
    _ns_store(root, namespace).delete(secret_ref=f"{1:032x}")  # the second process deletes the first one's record
    act_as(order[0])
    assert _ns_store(root, namespace).get(secret_ref=f"{1:032x}") is None
    assert not _ns_store(root, namespace).create(secret_ref=f"{1:032x}", value="again", expires_at=int(time.time()) + 60)
    for path in root.rglob("*"):
        assert path.lstat().st_uid == ME and path.lstat().st_mode & 0o077 == 0


def test_symlink_traversal_and_unsafe_modes_stay_refused_under_the_owner_model(tmp_path, monkeypatch, as_root):
    monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(ME))
    root = tmp_path / "secrets"
    store = _ns_store(root, "login-attempts")
    assert store.create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (root / HUB / "oauth-refresh-tokens").symlink_to(elsewhere)  # a symlinked purpose folder
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _ns_store(root, "oauth-refresh-tokens").create(secret_ref=REF, value="v", expires_at=int(time.time()) + 60)
    assert list(elsewhere.iterdir()) == []
    with pytest.raises(RuntimeFileError, match="runtime_secret_owner_invalid"):
        RuntimeFileStore(root=root, namespace="card-credentials", authorized_namespaces=NAMESPACES, owner="../escape")
    (root / HUB / "login-attempts").chmod(0o750)
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        store.get(secret_ref=REF)
    assert (root / HUB / "login-attempts").stat().st_mode & 0o777 == 0o750  # refused, not repaired
