# SPDX-License-Identifier: MIT
"""Per-user directory names for automation storage.

Every automation tool is scoped to one user by a single thing: the directory
its store lives in (``<root>/automations/<segment>`` and
``<root>/automation_executions/<segment>``). That directory name therefore has
to be a one-to-one function of the user id, and a directory name found on disk
has to map back to exactly one user id.

The original segment, ``re.sub(r"[^a-zA-Z0-9_.-]+", "-", user_id)``, is neither:
``tenant:user:42`` and ``tenant-user-42`` both become ``tenant-user-42``, so the
two ids read and write one store; and a scheduler that lists directory names
and reopens each one by name opens the store as ``tenant-user-42`` -- an id that
is not the owner.

:func:`owner_segment` is injective and reversible:

* an id made only of ``[A-Za-z0-9_.-]`` (not starting with ``.`` or ``-``, not
  ending with ``-``) is its own segment -- exactly the name the original scheme
  gave it, so these stores do not move;
* any other id becomes ``<readable>~<base32(id)>``. ``~`` never occurs in a
  plain id, so the two forms cannot collide, and the base32 token decodes back
  to the full id.

:func:`owner_from_segment` is the inverse; ``owner_segment(owner_from_segment(n)) == n``
for every name it accepts, so reopening a store found by a directory scan is a
pure lookup that can never land in a different directory.

Stores written under the original scheme by an id that is not plain are moved to
the new name the first time that store is opened, or when the scheduler scans
the root -- but only when every record in the old directory was written by that
same id (``owner_user_id`` is stamped from the store's own user id, never from
tool input). A directory whose records name more than one owner was already
shared by colliding ids; it is left in place and reported, because no automatic
rule can split it correctly.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Iterable

import yaml

logger = logging.getLogger(__name__)

_PLAIN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_ENCODED_SEPARATOR = "~"
_READABLE_PREFIX_CHARS = 32
#: Most filesystems cap one path component at 255 bytes.
_MAX_SEGMENT_CHARS = 255

AUTOMATIONS_DIR = "automations"
DEFAULT_EXECUTIONS_NAMESPACE = "automation_executions"
INDEXES_DIR = "indexes"

_REPORTED_SHARED: set[tuple[str, str]] = set()


def legacy_segment(user_id: str, *, fallback: str = "anonymous") -> str:
    """The directory name the original, non-injective scheme gave ``user_id``."""
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", user_id or "").strip("-")
    return value or fallback


def _is_plain(user_id: str) -> bool:
    return (
        bool(_PLAIN_ID.fullmatch(user_id))
        and not user_id.endswith("-")
        and len(user_id) <= _MAX_SEGMENT_CHARS
    )


def _b32(raw: str) -> str:
    return base64.b32encode(raw.encode("utf-8")).decode("ascii").rstrip("=").lower()


def owner_segment(user_id: str) -> str:
    """The storage directory name that belongs to ``user_id`` alone."""
    uid = str(user_id or "")
    if not uid:
        raise ValueError("user id is required")
    if _is_plain(uid):
        return uid
    token = _b32(uid)
    readable = legacy_segment(uid, fallback="")[:_READABLE_PREFIX_CHARS].lstrip(".")
    segment = f"{readable}{_ENCODED_SEPARATOR}{token}"
    if len(segment) > _MAX_SEGMENT_CHARS:
        segment = f"{_ENCODED_SEPARATOR}{token}"
    if len(segment) > _MAX_SEGMENT_CHARS:
        raise ValueError("user id is too long to name an automation store")
    return segment


def owner_from_segment(name: str) -> str | None:
    """The user id whose store is directory ``name``; ``None`` if no id maps to it."""
    name = str(name or "")
    if not name:
        return None
    if _ENCODED_SEPARATOR in name:
        token = name.rsplit(_ENCODED_SEPARATOR, 1)[1]
        try:
            uid = base64.b32decode(token.upper() + "=" * (-len(token) % 8)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    else:
        uid = name
    try:
        return uid if owner_segment(uid) == name else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Provenance of a legacy directory
# ---------------------------------------------------------------------------

def _frontmatter_owner(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return ""
    try:
        meta = yaml.safe_load(parts[0][3:]) or {}
    except yaml.YAMLError:
        return ""
    return str(meta.get("owner_user_id") or "").strip() if isinstance(meta, dict) else ""


def _execution_owner(path: Path) -> str:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(record.get("owner_user_id") or "").strip() if isinstance(record, dict) else ""


def automation_record_owners(store_dir: Path) -> set[str]:
    """``owner_user_id`` of every automation asset in ``store_dir``; ``""`` marks a record without one."""
    return {_frontmatter_owner(path) for path in store_dir.glob("*.md")}


def execution_record_owners(store_dir: Path) -> set[str]:
    """``owner_user_id`` of every execution record in ``store_dir``; ``""`` marks a record without one."""
    return {_execution_owner(path) for path in store_dir.glob("*/*.json")}


def _record_owners(root: Path, segment: str, namespaces: Iterable[str]) -> set[str]:
    owners: set[str] = set()
    automations_dir = root / AUTOMATIONS_DIR / segment
    if automations_dir.is_dir():
        owners |= automation_record_owners(automations_dir)
    for namespace in namespaces:
        executions_dir = root / namespace / segment
        if executions_dir.is_dir():
            owners |= execution_record_owners(executions_dir)
    return owners


# ---------------------------------------------------------------------------
# Migration of directories written under the legacy scheme
# ---------------------------------------------------------------------------

def _move_dir(src: Path, dst: Path) -> bool:
    if not src.is_dir() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except FileNotFoundError:
        return False  # another process moved it first
    except OSError as exc:
        logger.warning("[automation-storage] could not move %s to %s: %s", src, dst, exc)
        return False
    return True


def adopt_legacy_store(
    root: Path,
    user_id: str,
    *,
    subtrees: Iterable[str] = (AUTOMATIONS_DIR, DEFAULT_EXECUTIONS_NAMESPACE),
    owners_of: Callable[[Path, str, Iterable[str]], set[str]] | None = None,
) -> bool:
    """Move ``user_id``'s legacy-named directories to :func:`owner_segment`.

    A no-op for plain ids (their name did not change), when nothing exists under
    the legacy name, and when the destination already exists. The move happens
    only when every record under the legacy name was written by ``user_id``;
    the search indexes for the legacy name are dropped (they are rebuilt from
    the moved records). Returns True when anything was moved.
    """
    root = Path(root)
    new = owner_segment(user_id)
    old = legacy_segment(user_id)
    if new == old or old in {".", ".."}:
        return False
    subtrees = tuple(subtrees)
    if not any((root / subtree / old).is_dir() for subtree in subtrees):
        return False
    namespaces = [subtree for subtree in subtrees if subtree != AUTOMATIONS_DIR]
    owners = (owners_of or _record_owners)(root, old, namespaces)
    if owners != {user_id}:
        if user_id in owners:
            key = (str(root), old)
            if key not in _REPORTED_SHARED:
                _REPORTED_SHARED.add(key)
                logger.warning(
                    "[automation-storage] legacy store %r holds records of several owners %r; "
                    "left in place for an operator to split. %r now uses %r.",
                    old, sorted(owners), user_id, new,
                )
        return False
    moved = False
    for subtree in subtrees:
        src, dst = root / subtree / old, root / subtree / new
        if src.is_dir() and dst.exists():
            key = (str(root), f"{subtree}/{old}")
            if key not in _REPORTED_SHARED:
                _REPORTED_SHARED.add(key)
                logger.warning(
                    "[automation-storage] both the legacy %s and the current %s exist for %r; "
                    "the legacy directory is left in place for an operator to merge",
                    src, dst, user_id,
                )
            continue
        if _move_dir(src, dst):
            moved = True
            shutil.rmtree(root / INDEXES_DIR / subtree / old, ignore_errors=True)
    if moved:
        logger.info("[automation-storage] moved legacy store %r of %r to %r", old, user_id, new)
    return moved


def store_owner_ids(root: Path) -> list[str]:
    """The user id of every automation store under ``root``.

    Each directory maps back through :func:`owner_from_segment`. A plain-named
    directory whose records were all written by one id that the legacy scheme
    mapped onto that name belongs to that id (it is moved when the store is
    opened). Directories no id maps to are skipped.
    """
    root = Path(root)
    automations_root = root / AUTOMATIONS_DIR
    if not automations_root.is_dir():
        return []
    owners: list[str] = []
    for path in sorted(automations_root.iterdir()):
        if not path.is_dir():
            continue
        owner = owner_from_segment(path.name)
        if _ENCODED_SEPARATOR not in path.name:
            recorded = automation_record_owners(path)
            if len(recorded) == 1:
                (only,) = recorded
                if only and only != path.name and legacy_segment(only) == path.name:
                    owner = only
        if owner is None:
            logger.debug("[automation-storage] skipping %s: no user id maps to it", path)
            continue
        if owner not in owners:
            owners.append(owner)
    return owners
