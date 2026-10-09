# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import stat
import sys
from typing import Optional


SERVER_PATHS = {
    "ephemeral": "/app/secrets_server.py",
    "host-vault": "/app/host_vault/broker_server.py",
}


def selected_server_path(raw_backend: Optional[str] = None) -> str:
    backend = (
        str(
            raw_backend
            if raw_backend is not None
            else os.getenv("KDCUBE_SECRETS_SERVICE_BACKEND", "ephemeral")
        )
        .strip()
        .lower()
        .replace("_", "-")
    )
    if backend in {"memory", "transient", "ephemeral-memory"}:
        backend = "ephemeral"
    try:
        return SERVER_PATHS[backend]
    except KeyError as exc:
        supported = ", ".join(sorted(SERVER_PATHS))
        raise ValueError(
            f"unsupported secrets service backend; expected one of: {supported}"
        ) from exc


def safe_repair_root(root: str) -> bool:
    """The repair target must be an explicit secrets directory: absolute, canonical (no symlink anywhere on
    the path, no ".."), not the filesystem root, at least two levels deep, and an existing directory.
    Anything else is refused before any traversal or chown (review P1)."""
    if not isinstance(root, str) or not root.startswith("/"):
        return False
    normal = os.path.normpath(root)
    if normal == "/" or normal != root.rstrip("/") or len([part for part in normal.split("/") if part]) < 2:
        return False
    if os.path.realpath(normal) != normal or not os.path.isdir(normal) or os.path.islink(normal):
        return False
    return True


def adopt_root_owned_entries(root: Optional[str] = None, owner: Optional[str] = None) -> int:
    """W677: hand root-owned entries under the secrets folder to the one owner uid before serving.

    The same repair chat-proc's entrypoint runs before dropping to appuser: only entries owned by uid 0
    (directories and regular files, never symlinks, never across filesystems) are re-owned; modes are not
    touched, and an entry owned by any other uid is left for the application to refuse.
    """
    root = root if root is not None else (os.environ.get("KDCUBE_SECRETS_RUNTIME_ROOT") or "").strip()
    owner = owner if owner is not None else (os.environ.get("KDCUBE_SECRETS_OWNER_UID") or "").strip()
    if not root or not owner.isdigit() or int(owner) == 0 or os.geteuid() != 0:
        return 0
    if not safe_repair_root(root):
        return 0
    root = os.path.normpath(root)
    device, changed = os.lstat(root).st_dev, 0
    paths = [root]
    for current, dirs, files in os.walk(root, followlinks=False):
        paths.extend(os.path.join(current, name) for name in (*files, *dirs))
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(current, name))]
    for path in paths:  # one pass over the root and every entry below it
        try:
            info = os.lstat(path)
        except OSError:
            continue
        mode = stat.S_IMODE(info.st_mode)
        private = ((stat.S_ISDIR(info.st_mode) and mode == 0o700)
                   or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and mode in (0o600, 0o400)))
        if info.st_dev != device or info.st_uid != 0 or not private:
            continue  # only private, single-link, root-owned entries; anything else stays for the app to refuse
        try:
            os.chown(path, int(owner), -1, follow_symlinks=False)
            changed += 1
        except OSError:
            pass
    return changed


def main() -> int:
    adopt_root_owned_entries()
    try:
        server_path = selected_server_path()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    os.execv(sys.executable, [sys.executable, server_path])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
