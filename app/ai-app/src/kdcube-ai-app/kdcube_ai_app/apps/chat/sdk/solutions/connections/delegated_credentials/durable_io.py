# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Atomic JSON object IO for durable delegated-access storage.

Shared by the catalog and card stores. Absence, unreadability, and malformed
content are three distinct outcomes so callers can answer "confirmed absent"
and "unavailable" differently.

Filesystem work runs off the event loop. Publication is a temporary file
followed by a same-directory rename, which is atomic on local filesystems and
on shared mounts such as EFS.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any, Mapping


class DurableStorageError(RuntimeError):
    """Durable storage could not be read or written."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DurableDecodeError(ValueError):
    """A durable object exists but is not usable JSON."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def read_json_or_none(path: pathlib.Path) -> Any | None:
    """Parsed JSON, or ``None`` when the object is confirmed absent."""
    result = await asyncio.to_thread(_read_text, path)
    if result is None:
        return None
    if isinstance(result, OSError):
        raise DurableStorageError("read_failed")
    try:
        return json.loads(result)
    except ValueError as exc:
        raise DurableDecodeError("object_not_json") from exc


async def write_json_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        await asyncio.to_thread(_write_text_atomic, path, text)
    except OSError as exc:
        raise DurableStorageError("write_failed") from exc


async def list_child_names(path: pathlib.Path) -> list[str]:
    """Sorted child names, or an empty list when the directory is absent."""
    result = await asyncio.to_thread(_list_children, path)
    if isinstance(result, OSError):
        raise DurableStorageError("list_failed")
    return result


async def path_is_file(path: pathlib.Path) -> bool:
    return await asyncio.to_thread(path.is_file)


def _read_text(path: pathlib.Path) -> str | OSError | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return exc


def _write_text_atomic(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _list_children(path: pathlib.Path) -> list[str] | OSError:
    try:
        return sorted(child.name for child in path.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        return exc


__all__ = [
    "DurableDecodeError",
    "DurableStorageError",
    "list_child_names",
    "path_is_file",
    "read_json_or_none",
    "write_json_atomic",
]
