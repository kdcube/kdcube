# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
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


def adopt_root_owned_entries(root: Optional[str] = None, owner: Optional[str] = None) -> int:
    """W677: hand root-owned private entries of the secrets folder to the one owner uid before serving.

    The same repair chat-proc's entrypoint runs before dropping to appuser; the implementation and its
    guards live in kdcube_ai_app.infra.secrets.ownership_repair (copied into this image).
    """
    from kdcube_ai_app.infra.secrets.ownership_repair import repair_secrets_ownership

    root = root if root is not None else (os.environ.get("KDCUBE_SECRETS_RUNTIME_ROOT") or "").strip()
    owner = owner if owner is not None else (os.environ.get("KDCUBE_SECRETS_OWNER_UID") or "").strip()
    try:
        return repair_secrets_ownership(root, owner)
    except Exception:  # never prevent the service from starting; the stores still refuse unsafe entries
        return 0


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
