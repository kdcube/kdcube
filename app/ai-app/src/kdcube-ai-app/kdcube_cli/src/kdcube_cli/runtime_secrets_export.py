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
    platform_values: dict[str, str] = field(default_factory=dict)  # provider exports only


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


def _without_acquired(secrets: object, removed: list[int]) -> object:
    if not isinstance(secrets, dict):
        return secrets
    flat: dict[str, object] = {}
    _flatten("", secrets, flat)
    kept = {key: value for key, value in flat.items() if not key.startswith(EXCLUDED_USER_PREFIXES)}
    removed[0] += len(flat) - len(kept)
    return {key: kept[key] for key in sorted(kept)}


def filter_user_secrets(global_secrets: dict, *, include_user_data: bool) -> tuple[dict, int, int]:
    """``secrets.yaml`` for the bootstrap: without include_user_data its ``users`` root is left out; with it,
    each user keeps its secrets except the tokens Connection Hub acquires. Returns the document, the number
    of user entries left out and the number of acquired tokens left out."""
    document = dict(global_secrets or {})
    wrapped = isinstance(document.get("secrets"), dict)
    root = dict(document["secrets"]) if wrapped else document
    users = root.get("users")
    if not isinstance(users, dict):
        return document, 0, 0
    if not include_user_data:
        root.pop("users")
        left_out, acquired = len(users), 0
    else:
        removed = [0]
        filtered = {}
        for user_id, entry in users.items():
            entry = dict(entry) if isinstance(entry, dict) else entry
            if isinstance(entry, dict):
                if "secrets" in entry:
                    entry["secrets"] = _without_acquired(entry["secrets"], removed)
                if isinstance(entry.get("bundles"), dict):
                    entry["bundles"] = {
                        bundle_id: ({**bundle, "secrets": _without_acquired(bundle.get("secrets"), removed)}
                                    if isinstance(bundle, dict) and "secrets" in bundle else bundle)
                        for bundle_id, bundle in entry["bundles"].items()
                    }
            filtered[user_id] = entry
        root["users"] = filtered
        left_out, acquired = 0, removed[0]
    if wrapped:
        document["secrets"] = root
        return document, left_out, acquired
    return root, left_out, acquired


# The file store's on-disk encoding (kdcube_ai_app.infra.secrets.user_secret_files.encode_segment): lowercase
# letters, digits and ``._@-`` stay, everything else (and a leading dot) is percent-encoded per UTF-8 byte.
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._@-")


def encode_segment(text: str) -> str:
    encoded = "".join(
        char if char in _SAFE and not (index == 0 and char == ".")
        else "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
        for index, char in enumerate(text)
    )
    if not text or len(encoded) > 200:
        raise RuntimeSecretsExportError("A secret name cannot be represented in the secrets folder.")
    return encoded


def record_bytes(value: str) -> bytes:
    """The bytes the file store writes for one record."""
    return json.dumps({"value": value}, ensure_ascii=True).encode("ascii")


def write_provider_values(
    out_dir: Path, values: list[tuple[str, str, str, str, str]], *, include_user_data: bool
) -> RuntimeSecretsExport:
    """W885: approved provider values (scope, bundle_id, user_id, key, value) in the same ``secrets/`` layout a
    secrets-file export copies, so every source store gives the same output. Platform values are returned in
    ``platform_values`` for ``secrets.yaml``; a user secret with no bundle has no folder home and refuses."""
    target = out_dir / EXPORT_FOLDER
    if target.exists() or target.is_symlink():
        raise RuntimeSecretsExportError(
            f"{target} already exists; remove it or choose another --out-dir, so the export holds only "
            "the current runtime secrets."
        )
    bundle_less = sum(1 for scope, bundle_id, *_rest in values if scope == "user" and not bundle_id)
    if bundle_less:
        raise RuntimeSecretsExportError(
            f"{bundle_less} user secrets belong to no app and have no place in the secrets folder; export refused."
        )
    result = RuntimeSecretsExport(target=target)
    out_dir.mkdir(parents=True, exist_ok=True)
    _make_folder(target)
    for scope, bundle_id, user_id, key, value in sorted(values):
        if scope == "platform":
            result.platform_values[key] = value
            continue
        if scope == "user":
            if key.startswith(EXCLUDED_USER_PREFIXES):
                result.acquired_tokens_left_out += 1
                continue
            if not include_user_data:
                result.user_records_left_out += 1
                continue
            folder = target / bundle_id / USERS_FOLDER / encode_segment(user_id)
            for part in (target / bundle_id, target / bundle_id / USERS_FOLDER, folder):
                _ensure_folder(part)
            _write_record(folder / (encode_segment(key) + _RECORD_SUFFIX), record_bytes(value))
            result.record_count += 1
            result.user_record_count += 1
            continue
        _ensure_folder(target / bundle_id)
        _write_record(target / bundle_id / (encode_segment(key) + _RECORD_SUFFIX), record_bytes(value))
        result.record_count += 1
        result.app_secrets.setdefault(bundle_id, {})[key] = value
    return result
