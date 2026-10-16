# SPDX-License-Identifier: MIT
"""The runtime secrets folder of a secrets-file runtime, exported as ``secrets/`` beside the descriptors (W885).

Since the move to folders (W670), app and per-user secrets live only in that folder, so an export that copies
the descriptor YAML alone loses them. Operator, 2026-10-16: the export is "something that can be used to
bootstrap the configuration part. and no. not the tokens that conn hub acuqires", with user data behind
"include-user-data". The layout is the one the services read, copied byte for byte:

    <bundle>/<key>.json                       app secrets
    <bundle>/users/<user>/<key>.json          per-user secrets (only with include_user_data)

Left out: runtime-record folders (<owner>/<purpose>/, e.g. login-attempts, card-credentials,
oauth-refresh-tokens), and per-user secrets that Connection Hub and its integrations acquire at runtime
(EXCLUDED_USER_PREFIXES, see docs/sdk/solutions/connections/connection-hub-token-storage-README.md). Folders
are written 0700 and files 0600; a symbolic link refuses the export; dot files are not records. Nothing here
prints; the caller reports counts only, never a name or a value.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

EXPORT_FOLDER = "secrets"
USERS_FOLDER = "users"
# Per-user keys holding tokens acquired at runtime. Operator, 2026-10-16, on email account tokens:
# "--include-user-data << consider \"add that\"", so email.accounts.* is exported with the flag.
EXCLUDED_USER_PREFIXES = ("connections.accounts.", "delegated_to_kdcube.", "remote_mcp.", "linkedin.accounts.")
_RECORD_SUFFIX = ".json"
_FOLDER_MODE, _FILE_MODE = 0o700, 0o600


class RuntimeSecretsExportError(SystemExit):
    """A refusal that names no secret path or value."""


@dataclass
class RuntimeSecretsExport:
    target: Path
    app_secrets: dict[str, dict[str, str]] = field(default_factory=dict)  # bundle -> key -> value
    record_count: int = 0
    user_record_count: int = 0
    user_records_left_out: int = 0
    acquired_tokens_left_out: int = 0
    runtime_record_folders_left_out: int = 0


def export_runtime_secrets_folder(
    source_root: Path, out_dir: Path, *, include_user_data: bool
) -> RuntimeSecretsExport:
    """Copy ``source_root`` to ``out_dir/secrets``. An absent source root exports an empty folder."""
    target = out_dir / EXPORT_FOLDER
    if target.exists() or target.is_symlink():
        raise RuntimeSecretsExportError(
            f"{target} already exists; remove it or choose another --out-dir, so the export holds only "
            "the current runtime secrets."
        )
    if source_root.is_symlink() or (source_root.exists() and not source_root.is_dir()):
        raise RuntimeSecretsExportError("The runtime secrets folder is not a plain folder; export refused.")
    result = RuntimeSecretsExport(target=target)
    out_dir.mkdir(parents=True, exist_ok=True)
    _make_folder(target)
    if not source_root.exists():
        return result
    for bundle in _entries(source_root):
        if not bundle.is_dir(follow_symlinks=False):
            continue
        bundle_target = target / bundle.name
        for entry in _entries(Path(bundle.path)):
            if entry.is_dir(follow_symlinks=False):
                if entry.name != USERS_FOLDER:
                    result.runtime_record_folders_left_out += 1
                    continue
                _copy_users(Path(entry.path), bundle_target / USERS_FOLDER, result,
                            include_user_data=include_user_data)
                continue
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(_RECORD_SUFFIX):
                continue
            data = _read_record(Path(entry.path))
            _ensure_folder(bundle_target)
            _write_record(bundle_target / entry.name, data)
            result.record_count += 1
            key = unquote(entry.name[: -len(_RECORD_SUFFIX)])
            result.app_secrets.setdefault(bundle.name, {})[key] = _record_value(data)
    return result


def _copy_users(source: Path, target: Path, result: RuntimeSecretsExport, *, include_user_data: bool) -> None:
    for person in _entries(source):
        if not person.is_dir(follow_symlinks=False):
            continue
        for entry in _entries(Path(person.path)):
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(_RECORD_SUFFIX):
                continue
            if unquote(entry.name[: -len(_RECORD_SUFFIX)]).startswith(EXCLUDED_USER_PREFIXES):
                result.acquired_tokens_left_out += 1
                continue
            if not include_user_data:
                result.user_records_left_out += 1
                continue
            person_target = target / person.name
            _ensure_folder(target.parent)
            _ensure_folder(target)
            _ensure_folder(person_target)
            _write_record(person_target / entry.name, _read_record(Path(entry.path)))
            result.record_count += 1
            result.user_record_count += 1


def _entries(folder: Path) -> list[os.DirEntry]:
    with os.scandir(folder) as scanned:
        entries = sorted(scanned, key=lambda item: item.name)
    for entry in entries:
        if entry.is_symlink():
            raise RuntimeSecretsExportError("The runtime secrets folder contains a symbolic link; export refused.")
    return [entry for entry in entries if not entry.name.startswith(".")]


def _make_folder(path: Path) -> None:
    os.mkdir(path, _FOLDER_MODE)
    os.chmod(path, _FOLDER_MODE)


def _ensure_folder(path: Path) -> None:
    if not path.exists():
        _make_folder(path)


def _read_record(source: Path) -> bytes:
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeSecretsExportError("The runtime secrets folder holds a record that is not a file.")
        chunks = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _record_value(data: bytes) -> str:
    try:
        record = json.loads(data)
    except ValueError:
        record = None
    if type(record) is not dict or type(record.get("value")) is not str:
        raise RuntimeSecretsExportError("The runtime secrets folder holds a record that is not readable.")
    return record["value"]


def _write_record(target: Path, data: bytes) -> None:
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(out, view):]
        os.fsync(out)
    finally:
        os.close(out)
    os.chmod(target, _FILE_MODE)


def _flatten(prefix: str, node: object, output: dict[str, object]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), value, output)
    else:
        output[prefix] = node


def merge_app_secrets(bundles_secrets: dict, app_secrets: dict[str, dict[str, str]]) -> dict:
    """``bundles.secrets.yaml`` with each app secret in its bundle item's ``secrets:``, so a provider-backed
    ``config import`` can write them into its store. Keys are written flat and dotted, exactly as the folder
    holds them: the importer flattens a mapping to the same dotted keys, and flat keys keep both ``token``
    and ``token.child`` (nesting would have to drop one). The folder value wins over an older YAML one."""
    document = dict(bundles_secrets or {})
    bundles = dict(document.get("bundles") or {"version": "1", "items": []})
    items = [dict(item) for item in (bundles.get("items") or []) if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in items}
    for bundle_id in sorted(app_secrets):
        item = by_id.get(bundle_id)
        if item is None:
            item = {"id": bundle_id}
            items.append(item)
            by_id[bundle_id] = item
        flat: dict[str, object] = {}
        if isinstance(item.get("secrets"), dict):
            _flatten("", item["secrets"], flat)
        flat.update(app_secrets[bundle_id])
        item["secrets"] = {key: flat[key] for key in sorted(flat)}
    bundles["items"] = items
    bundles.setdefault("version", "1")
    document["bundles"] = bundles
    return document


def strip_user_secrets(global_secrets: dict) -> tuple[dict, int]:
    """``secrets.yaml`` without its ``users`` root: a configuration bootstrap never takes user secrets from
    the legacy YAML (they would bypass include_user_data and the acquired-token exclusion). Returns the
    document and the number of user entries left out."""
    document = dict(global_secrets or {})
    root = document.get("secrets") if isinstance(document.get("secrets"), dict) else document
    users = root.get("users") if isinstance(root, dict) else None
    if users is None:
        return document, 0
    root = dict(root)
    root.pop("users")
    count = len(users) if isinstance(users, dict) else 1
    if isinstance(document.get("secrets"), dict):
        document["secrets"] = root
        return document, count
    return root, count
