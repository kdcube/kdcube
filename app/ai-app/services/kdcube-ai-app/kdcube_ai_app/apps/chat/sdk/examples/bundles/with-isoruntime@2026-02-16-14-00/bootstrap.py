# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import os
import pathlib
import re
import shutil
from typing import Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

DEFAULT_USER_WORKSPACE_ROOT = (
    "/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/"
    "kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/data/workspace"
)
DEFAULT_SANDBOX_ROOT = (
    "/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/"
    "kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/data/sandbox"
)


def _safe_user_id(user_id: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return "anonymous"
    uid = re.sub(r"[^A-Za-z0-9_.-]+", "_", uid)
    return uid or "anonymous"


def resolve_user_id(*, runtime_ctx_user_id: Optional[str], fallback_user: Optional[str]) -> str:
    return _safe_user_id(runtime_ctx_user_id or fallback_user or "anonymous")

def _sandbox_ignore_patterns() -> Optional[callable]:
    ignore_names = {
        "logs",
        "infra.log",
        "runtime.err.log",
        "docker.out.log",
        "docker.err.log",
        "supervisor.log",
        "executor.log",
        "delta_aggregates.json",
    }
    def _ignore(_src: str, names: list[str]):
        ignored: set[str] = set()
        for name in names:
            if name in ignore_names:
                ignored.add(name)
                continue
            if name.startswith(".infra_merged."):
                ignored.add(name)
                continue
            if name.startswith("exec_result_") and name.endswith(".json"):
                ignored.add(name)
        return ignored
    return _ignore


def bootstrap_user_sandbox(
    *,
    user_id: str,
    user_workspace_root: pathlib.Path,
    sandbox_root: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    """
    Copy user workspace into sandbox (full overwrite of sandbox).
    Returns sandbox user root path.
    """
    log = logger or AgentLogger("iso-runtime.bootstrap")
    user_id = _safe_user_id(user_id)
    user_workspace_root = user_workspace_root.expanduser().resolve()
    sandbox_root = sandbox_root.expanduser().resolve()

    src = user_workspace_root / user_id
    dest = sandbox_root / user_id

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    src_out = src / "out"
    dest_out = dest / "out"
    dest_out.mkdir(parents=True, exist_ok=True)

    if src_out.exists():
        shutil.copytree(src_out, dest_out, dirs_exist_ok=True, ignore=_sandbox_ignore_patterns())
        log.log(f"[iso-runtime] bootstrap sandbox out from {src_out} -> {dest_out}")
    else:
        log.log(f"[iso-runtime] user workspace out missing, starting empty: {src_out}", level="WARNING")
    return dest


def sync_user_sandbox(
    *,
    user_id: str,
    user_workspace_root: pathlib.Path,
    sandbox_root: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> None:
    """
    Copy sandbox back to user workspace (full overwrite of user workspace).
    """
    log = logger or AgentLogger("iso-runtime.bootstrap")
    user_id = _safe_user_id(user_id)
    user_workspace_root = user_workspace_root.expanduser().resolve()
    sandbox_root = sandbox_root.expanduser().resolve()

    src = sandbox_root / user_id
    dest = user_workspace_root / user_id
    src_out = src / "out"
    dest_out = dest / "out"
    if not src_out.exists():
        log.log(f"[iso-runtime] sandbox out missing, nothing to sync: {src_out}", level="WARNING")
        return

    if dest_out.exists():
        shutil.rmtree(dest_out, ignore_errors=True)
    dest_out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_out, dest_out, dirs_exist_ok=True, ignore=_sandbox_ignore_patterns())
    log.log(f"[iso-runtime] synced sandbox out -> workspace: {src_out} -> {dest_out}")


def resolve_roots() -> tuple[pathlib.Path, pathlib.Path]:
    user_workspace_root = pathlib.Path(
        os.environ.get("ISO_RUNTIME_USER_WORKSPACE_ROOT", DEFAULT_USER_WORKSPACE_ROOT)
    )
    sandbox_root = pathlib.Path(
        os.environ.get("ISO_RUNTIME_SANDBOX_ROOT", DEFAULT_SANDBOX_ROOT)
    )
    return user_workspace_root, sandbox_root
