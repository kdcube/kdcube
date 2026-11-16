"""Synthetic root material; source-only inode checks, not a deployed-volume proof."""
from pathlib import Path
import os

import pytest

from kdcube_ai_app.infra.secrets.host_vault import keys
from kdcube_ai_app.infra.secrets.host_vault.protocol import ErrorCode, VaultError


@pytest.fixture
def provider(tmp_path, monkeypatch):
    result = keys.FileRootKeyProvider(tmp_path / "keys")
    result.rotate()
    # Exercise filesystem classification separately in test_runtime_contract;
    # these cases pin inode/key safety, not the machine's actual volume.
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: True)
    return result


def refused(operation):
    with pytest.raises(VaultError) as result:
        operation()
    assert result.value.code in {ErrorCode.BACKEND_UNAVAILABLE, ErrorCode.CORRUPT_RECORD}


def test_current_and_historical_material_survive_a_provider_restart(provider):
    previous = provider.current_key_id()
    expected = provider.key(previous)
    current = provider.rotate()
    fresh = keys.FileRootKeyProvider(provider._dir)
    assert fresh.qualify_custody() is None
    assert fresh.current_key_id() == current
    assert fresh.key(previous) == expected


def test_process_memory_keys_never_qualify():
    refused(keys.FakeInMemoryRootKeyProvider().qualify_custody)


@pytest.mark.parametrize("classified", [False, None, 1, "true"])
def test_unknown_or_transient_filesystem_never_qualifies(provider, monkeypatch, classified):
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: classified)
    refused(provider.qualify_custody)


@pytest.mark.parametrize("mode", [0o750, 0o755, 0o777])
def test_directory_must_be_private_at_every_read(provider, mode):
    current = provider.current_key_id()
    provider._dir.chmod(mode)
    refused(provider.current_key_id)
    refused(lambda: provider.key(current))
    refused(provider.qualify_custody)


def test_symlink_directory_never_becomes_a_qualified_parent(provider, tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(provider._dir, target_is_directory=True)
    refused(keys.FileRootKeyProvider(link).qualify_custody)


def test_foreign_service_uid_never_qualifies(provider, monkeypatch):
    original = os.geteuid()
    monkeypatch.setattr(keys.os, "geteuid", lambda: original + 1)
    refused(provider.qualify_custody)


@pytest.mark.parametrize("entry", ["CURRENT", "key"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_special_or_shared_inodes_refuse_without_blocking(provider, tmp_path, entry, kind):
    current = provider.current_key_id()
    path = provider._dir / ("CURRENT" if entry == "CURRENT" else current + ".key")
    safe = tmp_path / "synthetic-target"
    safe.write_bytes(path.read_bytes())
    safe.chmod(0o400)
    path.unlink()
    if kind == "symlink":
        path.symlink_to(safe)
    elif kind == "hardlink":
        os.link(safe, path)
    else:
        os.mkfifo(path, 0o600)
    refused(provider.qualify_custody)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o700])
def test_every_historical_key_must_retain_private_nonexecutable_mode(provider, mode):
    old = provider.current_key_id()
    provider.rotate()
    (provider._dir / (old + ".key")).chmod(mode)
    refused(provider.qualify_custody)


@pytest.mark.parametrize("length", [0, 31, 33])
def test_invalid_historical_key_lengths_refuse(provider, length):
    old = provider.current_key_id()
    provider.rotate()
    target = provider._dir / (old + ".key")
    target.chmod(0o600)
    target.write_bytes(b"x" * length)
    target.chmod(0o400)
    refused(provider.qualify_custody)


@pytest.mark.parametrize("marker", [b"x" * 65, b"\xff", b"not/a/key"])
def test_malformed_or_oversized_current_marker_refuses(provider, marker):
    target = provider._dir / "CURRENT"
    target.write_bytes(marker)
    refused(provider.qualify_custody)


def test_missing_current_key_refuses(provider):
    current = provider.current_key_id()
    (provider._dir / (current + ".key")).unlink()
    refused(provider.qualify_custody)


def test_filesystem_probe_failure_is_a_fixed_code(provider, monkeypatch):
    def fail(root):
        raise OSError("synthetic-path-canary")
    monkeypatch.setattr(keys, "persistent_filesystem", fail)
    with pytest.raises(VaultError) as result:
        provider.qualify_custody()
    assert result.value.code is ErrorCode.BACKEND_UNAVAILABLE
    assert str(result.value) == "backend_unavailable"
    assert result.value.message == "The vault storage is unavailable."


@pytest.mark.parametrize("changed", ["directory_mode", "directory_inode", "key_mode"])
def test_private_key_read_rechecks_the_opened_inodes_before_return(provider, monkeypatch, changed):
    current = provider.current_key_id()
    path = provider._dir / (current + ".key")
    read = keys.os.read
    altered = False

    def change_during_read(fd, bound):
        nonlocal altered
        data = read(fd, bound)
        if not altered:
            altered = True
            if changed == "directory_mode":
                provider._dir.chmod(0o755)
            elif changed == "directory_inode":
                provider._dir.rename(provider._dir.with_name("previous-keys"))
                provider._dir.mkdir(mode=0o700)
            else:
                path.chmod(0o644)
        return data

    monkeypatch.setattr(keys.os, "read", change_during_read)
    refused(lambda: provider.key(current))


# ── operator-selected root-key provider (KDCUBE_HOST_VAULT_ROOT_KEY_PROVIDER) ──

import sys as _sys
import types as _types

from kdcube_ai_app.infra.secrets.host_vault import keys as _keys
from kdcube_ai_app.infra.secrets.host_vault.protocol import ErrorCode as _ErrorCode
from kdcube_ai_app.infra.secrets.host_vault.protocol import VaultError as _VaultError


def _install_module(monkeypatch, name, **attrs):
    module = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(module, k, v)
    monkeypatch.setitem(_sys.modules, name, module)


def test_root_key_provider_defaults_to_files(tmp_path, monkeypatch):
    monkeypatch.delenv(_keys.ROOT_KEY_PROVIDER_ENV, raising=False)
    provider = _keys.load_root_key_provider(tmp_path)
    assert isinstance(provider, _keys.FileRootKeyProvider)
    assert provider._dir == tmp_path / "root-keys"
    monkeypatch.setenv(_keys.ROOT_KEY_PROVIDER_ENV, "  file ")
    assert isinstance(_keys.load_root_key_provider(tmp_path), _keys.FileRootKeyProvider)


def test_root_key_provider_from_operator_factory(tmp_path, monkeypatch):
    seen = {}

    def factory(home):
        seen["home"] = home
        return _keys.FakeInMemoryRootKeyProvider()

    _install_module(monkeypatch, "fake_custody_plugin", provider=factory)
    monkeypatch.setenv(_keys.ROOT_KEY_PROVIDER_ENV, "fake_custody_plugin:provider")
    provider = _keys.load_root_key_provider(tmp_path)
    assert isinstance(provider, _keys.FakeInMemoryRootKeyProvider)
    assert seen["home"] == tmp_path
    # an explicit spec wins over the environment
    assert isinstance(_keys.load_root_key_provider(tmp_path, "file"), _keys.FileRootKeyProvider)


@pytest.mark.parametrize("spec", ["nope", "missing_module_xyz:provider", "fake_custody_plugin:absent",
                                  "fake_custody_plugin:not_a_provider", ":provider", "fake_custody_plugin:"])
def test_root_key_provider_refuses_instead_of_falling_back(tmp_path, monkeypatch, spec):
    _install_module(monkeypatch, "fake_custody_plugin", not_a_provider=lambda home: object())
    with pytest.raises(_VaultError) as err:
        _keys.load_root_key_provider(tmp_path, spec)
    assert err.value.code is _ErrorCode.BACKEND_UNAVAILABLE
    assert not (tmp_path / "root-keys").exists(), "never silently falls back to file custody"
