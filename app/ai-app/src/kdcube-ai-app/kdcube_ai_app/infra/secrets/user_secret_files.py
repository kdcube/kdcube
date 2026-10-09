# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Per-user secrets of the secrets-file provider, one private file per secret under the owning bundle.

Operator, 2026-10-09: "step 2 should not stay in secrets.yaml. it should be also in folder" / "i would place
the secrets that were created even if for users but by connection hub - there" / "this makes sense. not in
platform." Layout, under the runtime secrets root:

    <root>/<bundle id>/users/<user id>/<key>.json      for users.<user>.bundles.<bundle>.secrets.<key>

A bundle-less user secret (``users.<user>.secrets.<key>``) has no place here and is refused. Set writes a
private temporary file, fsyncs it and replaces the record atomically; delete unlinks it; there is no lock.
User and key segments keep ``[A-Za-z0-9._@-]`` and percent-encode everything else (a leading dot too), so no
name can traverse or hide. Values are never logged.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import unquote

from kdcube_ai_app.infra.secrets.runtime_file import PLATFORM_OWNER

USERS_FOLDER = "users"
# Lowercase only: the host share can be case-insensitive (APFS), so every on-disk name must stay unique
# under case folding. Uppercase letters are percent-encoded (escapes use uppercase hex and a literal "%"
# is always escaped, so two encoded names never differ only by case).
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._@-")
_MAX_SEGMENT_CHARS = 200
_MAX_VALUE_BYTES = 65536
_MAX_FILE_BYTES = 6 * _MAX_VALUE_BYTES + 64
_SUFFIX = ".json"
# The only refusal reasons the host command prints; anything else is reported as migration_unavailable.
_MIGRATION_REASONS = frozenset({
    "user_secret_migration_bundle_less_source", "user_secret_migration_bundle_invalid",
    "user_secret_migration_destination_conflict", "user_secret_migration_readback_mismatch",
    "user_secret_storage_unavailable", "user_secret_bundle_invalid", "user_secret_bundle_required",
    "user_secret_key_invalid", "user_secret_value_invalid", "runtime_secret_storage_must_be_separate",
})
_BUNDLE = re.compile(r"[a-z0-9][a-z0-9._@-]{0,127}")  # lowercase: case-insensitive host shares


class UserSecretFileError(RuntimeError):
    """Finite failure without secret material or filesystem paths."""


def encode_segment(text: str) -> str:
    if type(text) is not str or not text:
        raise UserSecretFileError("user_secret_key_invalid")
    encoded = "".join(
        char if char in _SAFE and not (index == 0 and char == ".")
        else "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
        for index, char in enumerate(text)
    )
    if len(encoded) > _MAX_SEGMENT_CHARS:
        raise UserSecretFileError("user_secret_key_invalid")
    return encoded


def decode_segment(name: str) -> str:
    text = unquote(name, errors="strict")
    if encode_segment(text) != name:
        raise UserSecretFileError("user_secret_storage_unavailable")
    return text


def user_secret_key(user_id: str, bundle_id: str, key: str) -> str:
    return f"users.{user_id}.bundles.{bundle_id}.secrets.{key}"


def app_secret_key(bundle_id: str, key: str) -> str:
    return f"bundles.{bundle_id}.secrets.{key}"


class UserSecretFileStore:
    """Private per-user secret files below one dedicated, per-project root."""

    def __init__(self, *, root: str | Path):
        self._root = Path(root)
        if not self._root.is_absolute() or self._root == Path("/"):
            raise UserSecretFileError("user_secret_storage_unavailable")

    @staticmethod
    def _owner(bundle_id: str | None) -> str:
        """The bundle folder: the bundle id itself (dots allowed, e.g. ``kdcube.copilot@2026-04-03``), one
        segment, never "platform", ".", ".." or hidden."""
        if bundle_id is None:
            raise UserSecretFileError("user_secret_bundle_required")
        if (type(bundle_id) is not str or _BUNDLE.fullmatch(bundle_id) is None
                or bundle_id in {PLATFORM_OWNER, ".", ".."}):
            raise UserSecretFileError("user_secret_bundle_invalid")
        return bundle_id

    def _chain(self, bundle_id: str, user_id: str | None = None) -> list[Path]:
        chain = [self._root, self._root / self._owner(bundle_id), self._root / self._owner(bundle_id) / USERS_FOLDER]
        if user_id is not None:
            chain.append(chain[-1] / encode_segment(user_id))
        return chain

    def _verify(self, folder: Path) -> bool:
        """True for a private folder of ours, False when absent; anything else is refused."""
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise UserSecretFileError("user_secret_storage_unavailable")
        return True

    def _existing(self, chain: list[Path]) -> bool:
        for folder in chain:
            if not self._verify(folder):
                return False
        if chain[-1].resolve() != chain[-1]:
            raise UserSecretFileError("user_secret_storage_unavailable")
        return True

    def _create(self, chain: list[Path]) -> None:
        for folder in chain:
            try:
                folder.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                created = False
            if not self._verify(folder):
                raise UserSecretFileError("user_secret_storage_unavailable")
            if created:  # the new entry is durable in its parent before anything is published inside it
                parent = os.open(folder.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        if chain[-1].resolve() != chain[-1]:
            raise UserSecretFileError("user_secret_storage_unavailable")

    @staticmethod
    def _check_record(path: Path) -> bool:
        """True for an existing private record, False when absent; an unsafe one is refused, never repaired."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise UserSecretFileError("user_secret_storage_unavailable")
        return True

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise UserSecretFileError("user_secret_storage_unavailable")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError
        record = json.loads(raw)
        if type(record) is not dict or set(record) != {"value"} or type(record["value"]) is not str:
            raise ValueError
        return record["value"]

    @staticmethod
    def _fsync_folder(folder: Path) -> None:
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def get(self, *, user_id: str, bundle_id: str | None, key: str) -> str | None:
        chain = self._chain(bundle_id, user_id)
        name = encode_segment(key) + _SUFFIX
        try:
            if not self._existing(chain):
                return None
            return self._read(chain[-1] / name)
        except (OSError, UnicodeError, ValueError):
            raise UserSecretFileError("user_secret_storage_unavailable") from None

    def set(self, *, user_id: str, bundle_id: str | None, key: str, value: str) -> None:
        self._write(self._chain(bundle_id, user_id), encode_segment(key) + _SUFFIX, value)

    def _write(self, chain: list[Path], name: str, value: str) -> None:
        try:
            if type(value) is not str or len(value.encode("utf-8")) > _MAX_VALUE_BYTES:
                raise ValueError
        except (ValueError, UnicodeError):
            raise UserSecretFileError("user_secret_value_invalid") from None
        encoded = json.dumps({"value": value}, ensure_ascii=True).encode("ascii")
        try:
            self._create(chain)
            folder = chain[-1]
            self._check_record(folder / name)  # an unsafe existing record is refused, not replaced
            descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=folder)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, folder / name)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            self._fsync_folder(folder)
        except OSError:
            raise UserSecretFileError("user_secret_storage_unavailable") from None

    def delete(self, *, user_id: str, bundle_id: str | None, key: str) -> None:
        self._unlink(self._chain(bundle_id, user_id), encode_segment(key) + _SUFFIX)

    def _unlink(self, chain: list[Path], name: str) -> None:
        try:
            if not self._existing(chain) or not self._check_record(chain[-1] / name):
                return
            try:
                os.unlink(chain[-1] / name)
            except FileNotFoundError:
                return
            self._fsync_folder(chain[-1])
        except OSError:
            raise UserSecretFileError("user_secret_storage_unavailable") from None

    def user_path(self, *, user_id: str, bundle_id: str | None, key: str) -> Path:
        """The record file of one per-user secret (no I/O)."""
        return self._chain(bundle_id, user_id)[-1] / (encode_segment(key) + _SUFFIX)

    def app_path(self, *, bundle_id: str | None, key: str) -> Path:
        """The record file of one app secret (no I/O)."""
        return self._chain(bundle_id)[1] / (encode_segment(key) + _SUFFIX)

    # App (bundle) secrets: <root>/<bundle>/<key path>.json. Every record file ends in .json; users/ and the
    # runtime purpose folders are directories without a suffix, so a key path can never collide with them.
    def get_app(self, *, bundle_id: str | None, key: str) -> str | None:
        chain = self._chain(bundle_id)[:2]
        name = encode_segment(key) + _SUFFIX
        try:
            if not self._existing(chain):
                return None
            return self._read(chain[-1] / name)
        except (OSError, UnicodeError, ValueError):
            raise UserSecretFileError("user_secret_storage_unavailable") from None

    def set_app(self, *, bundle_id: str | None, key: str, value: str) -> None:
        self._write(self._chain(bundle_id)[:2], encode_segment(key) + _SUFFIX, value)

    def delete_app(self, *, bundle_id: str | None, key: str) -> None:
        self._unlink(self._chain(bundle_id)[:2], encode_segment(key) + _SUFFIX)

    def list_app_keys(self, *, bundle_id: str | None = None) -> list[str]:
        """``bundles.<bundle>.secrets.<key>`` keys from the bundle folders (one bundle, or every bundle)."""
        try:
            if not self._verify(self._root):
                return []
            owners = [bundle_id] if bundle_id is not None else [
                entry.name for entry in os.scandir(self._root) if not entry.is_file(follow_symlinks=False)]
            keys: list[str] = []
            for owner in owners:
                try:
                    chain = self._chain(owner)[:2]
                except UserSecretFileError:
                    if bundle_id is not None:
                        raise
                    continue  # not a bundle folder (e.g. platform/)
                if not self._existing(chain):
                    continue
                for entry in os.scandir(chain[-1]):
                    if entry.name.startswith(".") or not entry.name.endswith(_SUFFIX) or entry.is_dir(
                            follow_symlinks=False):
                        continue
                    self._check_record(chain[-1] / entry.name)
                    keys.append(app_secret_key(owner, decode_segment(entry.name[:-len(_SUFFIX)])))
            return sorted(keys)
        except (OSError, UnicodeError, ValueError):
            raise UserSecretFileError("user_secret_storage_unavailable") from None

    def _user_keys(self, bundle_id: str, user_folder: Path) -> list[str]:
        user_id = decode_segment(user_folder.name)
        keys = []
        for entry in os.scandir(user_folder):
            if entry.name.startswith(".") or not entry.name.endswith(_SUFFIX):
                continue
            self._check_record(user_folder / entry.name)
            keys.append(user_secret_key(user_id, bundle_id, decode_segment(entry.name[:-len(_SUFFIX)])))
        return keys

    def list_keys(self, *, user_id: str | None = None, bundle_id: str | None = None) -> list[str]:
        """Provider keys from the folder listing: one user and bundle, or every user secret (``users.__keys``)."""
        try:
            if user_id is not None:
                if bundle_id is None:
                    return []  # a bundle-less user secret cannot exist here
                chain = self._chain(bundle_id, user_id)
                return sorted(self._user_keys(bundle_id, chain[-1])) if self._existing(chain) else []
            if not self._verify(self._root):
                return []
            keys: list[str] = []
            for owner in os.scandir(self._root):
                if owner.is_file(follow_symlinks=False):
                    continue
                try:
                    chain = self._chain(owner.name)
                except UserSecretFileError:
                    continue  # not a bundle folder (e.g. platform/)
                if not self._existing(chain):
                    continue
                for user in os.scandir(chain[-1]):
                    if user.name.startswith("."):
                        continue
                    folder = chain[-1] / user.name
                    if self._verify(folder):
                        keys.extend(self._user_keys(owner.name, folder))
            return sorted(keys)
        except (OSError, UnicodeError, ValueError):
            raise UserSecretFileError("user_secret_storage_unavailable") from None


def main(argv: list[str] | None = None) -> int:
    """Move per-user secrets out of the descriptor yaml (counts only, never a value).

    On the host, with kdcube stopped and a backup taken::

        python -m kdcube_ai_app.infra.secrets.user_secret_files migrate --config-dir <workdir>/config --dry-run
        python -m kdcube_ai_app.infra.secrets.user_secret_files migrate --config-dir <workdir>/config

    ``--config-dir`` selects ``<dir>/secrets.yaml`` (and ``<dir>/bundles.secrets.yaml`` when present) and
    the default root ``<dir>/secrets``; ``--global-secrets-yaml`` / ``--bundle-secrets-yaml`` /
    ``--runtime-root`` override them. A path option is required; a named descriptor that is missing refuses.
    """
    import argparse
    import asyncio

    from kdcube_ai_app.infra.secrets.manager import (
        SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerError,
    )

    parser = argparse.ArgumentParser(prog="user_secret_files")
    parser.add_argument("command", choices=["migrate"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config-dir")
    parser.add_argument("--global-secrets-yaml")
    parser.add_argument("--bundle-secrets-yaml")
    parser.add_argument("--runtime-root")
    arguments = parser.parse_args(argv)
    global_yaml, bundle_yaml = arguments.global_secrets_yaml, arguments.bundle_secrets_yaml
    if arguments.config_dir:
        config_dir = Path(arguments.config_dir).expanduser().resolve()
        global_yaml = global_yaml or str(config_dir / "secrets.yaml")
        if not bundle_yaml and (config_dir / "bundles.secrets.yaml").is_file():
            bundle_yaml = str(config_dir / "bundles.secrets.yaml")
    if not (global_yaml or bundle_yaml):
        print(json.dumps({"status": "refused", "reason": "config_paths_required"}))
        return 2
    if global_yaml or bundle_yaml or arguments.runtime_root:
        if not global_yaml or not Path(global_yaml).expanduser().is_file():
            print(json.dumps({"status": "refused", "reason": "global_secrets_yaml_not_found"}))
            return 1
        if bundle_yaml and not Path(bundle_yaml).expanduser().is_file():
            print(json.dumps({"status": "refused", "reason": "bundle_secrets_yaml_not_found"}))
            return 1
        manager = SecretsFileSecretsManager(SecretsManagerConfig(
            provider="secrets-file", component="migration",
            global_secrets_yaml=Path(global_yaml).expanduser().resolve().as_uri(),
            bundle_secrets_yaml=Path(bundle_yaml).expanduser().resolve().as_uri() if bundle_yaml else None,
            runtime_secrets_root=(str(Path(arguments.runtime_root).expanduser().resolve())
                                  if arguments.runtime_root else None),
        ))
    try:
        counts = asyncio.run(manager.migrate_user_secrets(dry_run=arguments.dry_run))
    except Exception as exc:  # a fixed reason only: never a value, a path or parser text
        reason = str(exc) if isinstance(exc, SecretsManagerError) and str(exc) in _MIGRATION_REASONS else (
            "migration_unavailable")
        print(json.dumps({"status": "refused", "reason": reason}))
        return 1
    print(json.dumps({"status": "ok", "dry_run": arguments.dry_run, **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["USERS_FOLDER", "UserSecretFileError", "UserSecretFileStore", "app_secret_key", "decode_segment",
           "encode_segment", "user_secret_key"]
