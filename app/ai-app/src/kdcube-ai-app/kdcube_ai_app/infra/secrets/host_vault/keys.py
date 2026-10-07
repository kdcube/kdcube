# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Root-key custody and envelope encryption for the host vault.

Custody stays behind ``RootKeyProvider``: the vault never reads the root key
from a KDCube descriptor, the runtime workdir, an environment variable, or a
test fixture. Two providers ship:

- ``FileRootKeyProvider``: root keys in a service-owned directory outside the
  runtime workdir (the platform installer creates it, mode 0700/0400). This
  is the reference host adapter; a hardware or OS keystore adapter can
  replace it behind the same interface.
- ``FakeInMemoryRootKeyProvider``: LABELED FAKE, for portable tests only.

Encryption is envelope-style: each committed record carries its value under a
fresh 256-bit data key (AES-256-GCM), and that data key is wrapped by the
root key of a named version. Rotating the root key rewraps data keys; values
are never re-encrypted and never appear in memory during rotation. Every
ciphertext binds its record identity (reference digest + generation) as
associated data, so a record copied to another reference fails to open.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kdcube_ai_app.infra.secrets.host_vault.protocol import ErrorCode, VaultError
from kdcube_ai_app.infra.secrets.runtime_contract import persistent_filesystem

try:  # pragma: no cover - import guard
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception as exc:  # noqa: BLE001
    AESGCM = None  # type: ignore[assignment]
    _CRYPTO_IMPORT_ERROR: BaseException | None = exc
else:
    _CRYPTO_IMPORT_ERROR = None

KEY_BYTES = 32
NONCE_BYTES = 12
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _require_crypto() -> None:
    if AESGCM is None:
        raise VaultError(
            ErrorCode.BACKEND_UNAVAILABLE,
            detail=f"cryptography unavailable: {type(_CRYPTO_IMPORT_ERROR).__name__}",
        )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:
        raise VaultError(ErrorCode.CORRUPT_RECORD, detail="base64") from exc


class RootKeyProvider(Protocol):
    """Where root keys live. ``current`` names the key new records wrap with;
    ``key`` returns an older version so committed records stay readable
    across rotations; ``rotate`` mints a new current key (custody-specific)."""

    def current_key_id(self) -> str: ...

    def key(self, key_id: str) -> bytes: ...

    def rotate(self) -> str: ...

    def qualify_custody(self) -> None: ...


class FakeInMemoryRootKeyProvider:
    """FAKE root-key provider for portable tests. Keys live in process memory
    and vanish with it. Never a custody choice for a deployment."""

    is_fake = True

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}
        self._current = ""
        self.rotate()

    def current_key_id(self) -> str:
        return self._current

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="unknown root key version") from exc

    def rotate(self) -> str:
        key_id = f"fake-{len(self._keys) + 1:03d}"
        self._keys[key_id] = secrets.token_bytes(KEY_BYTES)
        self._current = key_id
        return key_id

    def qualify_custody(self) -> None:
        raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)


class FileRootKeyProvider:
    """Root keys as files in a service-owned directory.

    Layout: ``<dir>/<key_id>.key`` (raw 32 bytes, mode 0400) and
    ``<dir>/CURRENT`` naming the active id. The directory must be outside the
    KDCube runtime workdir and owned by the vault service user; this class
    refuses group/other-readable key files so a misplaced key fails closed
    rather than silently serving."""

    is_fake = False

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def _read_owned_file(self, name: str, *, bound: int, secret: bool,
                         missing: ErrorCode = ErrorCode.BACKEND_UNAVAILABLE) -> bytes:
        """Read a bounded private inode, never a symlink, device or FIFO.

        The directory descriptor pins the parent while the file is opened.
        A shared UID can still replace its own files; that is not an isolation
        boundary this adapter claims to supply.
        """
        directory_fd = file_fd = None
        try:
            if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            directory_fd = os.open(self._dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory = os.fstat(directory_fd)
            entry = self._dir.lstat()
            if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                    or stat.S_IMODE(directory.st_mode) != 0o700
                    or (directory.st_dev, directory.st_ino) != (entry.st_dev, entry.st_ino)):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=directory_fd)
            metadata = os.fstat(file_fd)
            mode = stat.S_IMODE(metadata.st_mode)
            allowed_modes = {0o400, 0o600} if secret else {0o400, 0o600, 0o644}
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink != 1 or mode not in allowed_modes
                    or metadata.st_size > bound
                    or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            value = bytearray()
            while len(value) <= bound:
                part = os.read(file_fd, bound + 1 - len(value))
                if not part:
                    break
                value.extend(part)
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            final_file = os.fstat(file_fd)
            final_directory = os.fstat(directory_fd)
            directory_entry = self._dir.lstat()
            if (len(value) > bound
                    or not stat.S_ISREG(final_file.st_mode)
                    or final_file.st_uid != os.geteuid()
                    or final_file.st_nlink != 1
                    or stat.S_IMODE(final_file.st_mode) not in allowed_modes
                    or final_file.st_size > bound
                    or (metadata.st_dev, metadata.st_ino) != (final_file.st_dev, final_file.st_ino)
                    or (final_file.st_dev, final_file.st_ino) != (entry.st_dev, entry.st_ino)
                    or not stat.S_ISDIR(final_directory.st_mode)
                    or final_directory.st_uid != os.geteuid()
                    or stat.S_IMODE(final_directory.st_mode) != 0o700
                    or (directory.st_dev, directory.st_ino) != (final_directory.st_dev, final_directory.st_ino)
                    or (final_directory.st_dev, final_directory.st_ino) != (directory_entry.st_dev, directory_entry.st_ino)):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            return bytes(value)
        except FileNotFoundError:
            raise VaultError(missing) from None
        except OSError:
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _key_path(self, key_id: str) -> Path:
        if type(key_id) is not str or not _KEY_ID_RE.fullmatch(key_id):
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="key id grammar")
        return self._dir / f"{key_id}.key"

    def current_key_id(self) -> str:
        try:
            key_id = self._read_owned_file("CURRENT", bound=64, secret=False).decode("utf-8").strip()
        except UnicodeError:
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None
        if not _KEY_ID_RE.fullmatch(key_id):
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE, detail="current marker grammar")
        return key_id

    def key(self, key_id: str) -> bytes:
        path = self._key_path(key_id)
        data = self._read_owned_file(path.name, bound=KEY_BYTES, secret=True,
                                    missing=ErrorCode.CORRUPT_RECORD)
        if len(data) != KEY_BYTES:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="root key length")
        return data

    def qualify_custody(self) -> None:
        """Check current AND historical root-key material on durable storage.

        This is a local source-level gate, not proof that a deployed mount
        survives host replacement, or that namespace credentials are scoped.
        The common runtime service must separately establish those legs.
        """
        try:
            current = self.current_key_id()
            if persistent_filesystem(self._dir) is not True:
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            names = []
            with os.scandir(self._dir) as entries:
                for entry in entries:
                    if len(names) >= 1024:
                        raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
                    names.append(entry.name)
            if f"{current}.key" not in names:
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            for name in names:
                if name.endswith(".key"):
                    self.key(name[:-4])
        except (OSError, ValueError, TypeError):
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None

    def rotate(self) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        existing = sorted(p.stem for p in self._dir.glob("*.key"))
        key_id = f"root-{len(existing) + 1:04d}"
        path = self._key_path(key_id)
        tmp = path.with_suffix(".key.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(fd, secrets.token_bytes(KEY_BYTES))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        marker = self._dir / "CURRENT"
        marker_tmp = self._dir / "CURRENT.tmp"
        marker_tmp.write_text(key_id, encoding="utf-8")
        os.replace(marker_tmp, marker)
        _fsync_dir(self._dir)
        return key_id


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class SealedValue:
    """A value at rest: ciphertext under a data key, the data key wrapped by a
    named root-key version. Serializable without secrets."""

    root_key_id: str
    wrapped_data_key: str  # base64: nonce || AESGCM(root, data_key, aad)
    ciphertext: str  # base64: nonce || AESGCM(data_key, value, aad)

    def to_dict(self) -> dict[str, str]:
        return {
            "root_key_id": self.root_key_id,
            "wrapped_data_key": self.wrapped_data_key,
            "ciphertext": self.ciphertext,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SealedValue:
        try:
            return cls(
                root_key_id=str(data["root_key_id"]),
                wrapped_data_key=str(data["wrapped_data_key"]),
                ciphertext=str(data["ciphertext"]),
            )
        except (KeyError, TypeError) as exc:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="sealed value shape") from exc


class Envelope:
    """Seal and open values with a root-key provider."""

    def __init__(self, keys: RootKeyProvider) -> None:
        _require_crypto()
        self._keys = keys

    @staticmethod
    def _aad(record_id: str) -> bytes:
        return json.dumps({"v": 1, "record": record_id}, sort_keys=True).encode("utf-8")

    def seal(self, value: bytes, *, record_id: str) -> SealedValue:
        data_key = secrets.token_bytes(KEY_BYTES)
        aad = self._aad(record_id)
        value_nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(value_nonce, value, aad)
        root_id = self._keys.current_key_id()
        wrap_nonce = secrets.token_bytes(NONCE_BYTES)
        wrapped = AESGCM(self._keys.key(root_id)).encrypt(wrap_nonce, data_key, aad)
        return SealedValue(
            root_key_id=root_id,
            wrapped_data_key=_b64(wrap_nonce + wrapped),
            ciphertext=_b64(value_nonce + ciphertext),
        )

    def _unwrap(self, sealed: SealedValue, *, record_id: str) -> bytes:
        aad = self._aad(record_id)
        blob = _unb64(sealed.wrapped_data_key)
        if len(blob) <= NONCE_BYTES:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="wrapped key length")
        try:
            return AESGCM(self._keys.key(sealed.root_key_id)).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
        except VaultError:
            raise
        except Exception as exc:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="data key unwrap") from exc

    def open(self, sealed: SealedValue, *, record_id: str) -> bytes:
        data_key = self._unwrap(sealed, record_id=record_id)
        aad = self._aad(record_id)
        blob = _unb64(sealed.ciphertext)
        if len(blob) <= NONCE_BYTES:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="ciphertext length")
        try:
            return AESGCM(data_key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
        except Exception as exc:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="value open") from exc

    def rewrap(self, sealed: SealedValue, *, record_id: str) -> SealedValue:
        """Root-key rotation for one record: unwrap the data key with its
        recorded root version, wrap it again with the current one. The value
        ciphertext is untouched and never decrypted."""
        current = self._keys.current_key_id()
        if sealed.root_key_id == current:
            return sealed
        data_key = self._unwrap(sealed, record_id=record_id)
        aad = self._aad(record_id)
        wrap_nonce = secrets.token_bytes(NONCE_BYTES)
        wrapped = AESGCM(self._keys.key(current)).encrypt(wrap_nonce, data_key, aad)
        return SealedValue(
            root_key_id=current,
            wrapped_data_key=_b64(wrap_nonce + wrapped),
            ciphertext=sealed.ciphertext,
        )


__all__ = [
    "KEY_BYTES",
    "Envelope",
    "FakeInMemoryRootKeyProvider",
    "FileRootKeyProvider",
    "RootKeyProvider",
    "SealedValue",
]
