# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import faulthandler
import logging
import multiprocessing
import os
import subprocess
import sys
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class _PsEntry:
    pid: int
    ppid: int
    pgid: int
    stat: str
    etime: str
    command: str


_LOGGED_KEYS: set[tuple[int, str]] = set()
_LOGGED_KEYS_LOCK = threading.Lock()


def _should_log_once(reason: str) -> bool:
    key = (os.getpid(), reason)
    with _LOGGED_KEYS_LOCK:
        if key in _LOGGED_KEYS:
            return False
        _LOGGED_KEYS.add(key)
        return True


def _parse_ps_output(stdout: str) -> dict[int, _PsEntry]:
    entries: dict[int, _PsEntry] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pgid = int(parts[2])
        except ValueError:
            continue
        entries[pid] = _PsEntry(
            pid=pid,
            ppid=ppid,
            pgid=pgid,
            stat=parts[3],
            etime=parts[4],
            command=parts[5],
        )
    return entries


def _collect_process_tree(entries: dict[int, _PsEntry], root_pid: int) -> list[_PsEntry]:
    descendants: list[_PsEntry] = []
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        entry = entries.get(current)
        if entry is not None:
            descendants.append(entry)
        children = sorted(pid for pid, item in entries.items() if item.ppid == current)
        pending.extend(reversed(children))
    return descendants


def _read_proc_filesystem() -> dict[int, _PsEntry]:
    """Read process info from /proc directly — works in any Linux container without ps."""
    entries: dict[int, _PsEntry] = {}
    try:
        proc_dir = "/proc"
        for name in os.listdir(proc_dir):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                status: dict[str, str] = {}
                with open(f"{proc_dir}/{pid}/status", "r") as f:
                    for line in f:
                        if ":" in line:
                            k, _, v = line.partition(":")
                            status[k.strip()] = v.strip()
                ppid = int(status.get("PPid", "-1"))
                # State line e.g. "S (sleeping)" → take first char
                raw_state = status.get("State", "?")
                stat = raw_state[0] if raw_state else "?"
                try:
                    with open(f"{proc_dir}/{pid}/cmdline", "rb") as f:
                        cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                except Exception:
                    cmdline = status.get("Name", "?")
                try:
                    pgid = os.getpgid(pid)
                except Exception:
                    pgid = -1
                entries[pid] = _PsEntry(
                    pid=pid, ppid=ppid, pgid=pgid, stat=stat, etime="?", command=cmdline or status.get("Name", "?")
                )
            except Exception:
                continue
    except Exception:
        pass
    return entries


def _collect_ps_entries() -> dict[int, _PsEntry]:
    # Try ps first; fall back to /proc filesystem (available in Linux containers).
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,pgid=,stat=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=True,
        )
        entries = _parse_ps_output(proc.stdout)
    except Exception:
        entries = _read_proc_filesystem()
    return entries


def _render_ps_lines(root_pid: int, *, entries: dict[int, _PsEntry] | None = None) -> list[str]:
    entries = entries or _collect_ps_entries()
    tree = _collect_process_tree(entries, root_pid)
    if not tree and root_pid in entries:
        tree = [entries[root_pid]]
    return [
        f"pid={item.pid} ppid={item.ppid} pgid={item.pgid} stat={item.stat} etime={item.etime} cmd={item.command}"
        for item in tree
    ]


def _render_parent_chain_lines(pid: int, *, entries: dict[int, _PsEntry]) -> list[str]:
    lines: list[str] = []
    current = pid
    seen: set[int] = set()
    while current > 0 and current not in seen:
        seen.add(current)
        item = entries.get(current)
        if item is None:
            break
        lines.append(
            f"parent_chain pid={item.pid} ppid={item.ppid} pgid={item.pgid} "
            f"stat={item.stat} etime={item.etime} cmd={item.command}"
        )
        if item.ppid <= 0 or item.ppid == current:
            break
        current = item.ppid
    return lines


def _render_immediate_children_lines(pid: int, *, entries: dict[int, _PsEntry]) -> list[str]:
    children = sorted(
        (item for item in entries.values() if item.ppid == pid),
        key=lambda item: item.pid,
    )
    return [
        f"child_of_{pid} pid={item.pid} ppid={item.ppid} pgid={item.pgid} "
        f"stat={item.stat} etime={item.etime} cmd={item.command}"
        for item in children
    ]


def log_shutdown_diagnostics(
    logger: logging.Logger,
    *,
    reason: str,
    root_pid: int | None = None,
    include_traceback: bool = False,
) -> None:
    pid = int(root_pid or os.getpid())
    try:
        pgid = os.getpgid(pid)
    except Exception:
        pgid = -1
    logger.warning(
        "[shutdown.diagnostics] reason=%s pid=%s ppid=%s pgid=%s active_threads=%s",
        reason,
        pid,
        os.getppid(),
        pgid,
        threading.active_count(),
    )

    try:
        children = multiprocessing.active_children()
        if children:
            child_summary = [
                {
                    "pid": child.pid,
                    "name": child.name,
                    "alive": child.is_alive(),
                    "daemon": child.daemon,
                }
                for child in children
            ]
            logger.warning(
                "[shutdown.diagnostics] reason=%s multiprocessing_children=%s",
                reason,
                child_summary,
            )
    except Exception:
        logger.exception("[shutdown.diagnostics] Failed to inspect multiprocessing children")

    try:
        for thread in threading.enumerate():
            logger.warning(
                "[shutdown.diagnostics] reason=%s thread name=%s ident=%s daemon=%s alive=%s",
                reason,
                thread.name,
                thread.ident,
                thread.daemon,
                thread.is_alive(),
            )
    except Exception:
        logger.exception("[shutdown.diagnostics] Failed to enumerate threads")

    try:
        entries = _collect_ps_entries()
        pid1 = entries.get(1)
        if pid1 is not None:
            logger.warning(
                "[shutdown.diagnostics] reason=%s pid1 pid=%s ppid=%s pgid=%s stat=%s etime=%s cmd=%s",
                reason,
                pid1.pid,
                pid1.ppid,
                pid1.pgid,
                pid1.stat,
                pid1.etime,
                pid1.command,
            )

        for line in _render_parent_chain_lines(pid, entries=entries):
            logger.warning("[shutdown.diagnostics] reason=%s %s", reason, line)

        if pid != 1:
            for line in _render_immediate_children_lines(1, entries=entries):
                logger.warning("[shutdown.diagnostics] reason=%s %s", reason, line)

        lines = _render_ps_lines(pid, entries=entries)
        if not lines:
            logger.warning("[shutdown.diagnostics] reason=%s process_tree=empty", reason)
        else:
            for line in lines:
                logger.warning("[shutdown.diagnostics] reason=%s %s", reason, line)
    except Exception:
        logger.exception("[shutdown.diagnostics] Failed to collect process tree")

    if include_traceback:
        try:
            logger.warning("[shutdown.diagnostics] reason=%s python_thread_dump_begin", reason)
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            logger.warning("[shutdown.diagnostics] reason=%s python_thread_dump_end", reason)
        except Exception:
            logger.exception("[shutdown.diagnostics] Failed to dump Python thread stacks")


def install_uvicorn_shutdown_diagnostics(
    uvicorn_module,
    logger: logging.Logger,
    *,
    component: str,
) -> None:
    if getattr(uvicorn_module, "_kdcube_shutdown_diagnostics_installed", False):
        return
    setattr(uvicorn_module, "_kdcube_shutdown_diagnostics_installed", True)

    def _log_once(reason: str) -> None:
        if not _should_log_once(reason):
            return
        log_shutdown_diagnostics(
            logger,
            reason=reason,
            root_pid=os.getpid(),
            include_traceback=False,
        )

    try:
        import uvicorn.server

        original_server_handle_exit = getattr(uvicorn.server.Server, "handle_exit", None)
        if callable(original_server_handle_exit):
            def _server_handle_exit(self, *args, **kwargs):
                sig = args[0] if args else kwargs.get("sig")
                _log_once(f"{component}:server.handle_exit:sig={sig}")
                return original_server_handle_exit(self, *args, **kwargs)

            uvicorn.server.Server.handle_exit = _server_handle_exit
        else:
            logger.warning(
                "[shutdown.diagnostics] Uvicorn Server.handle_exit not available; "
                "server signal diagnostics disabled for component=%s",
                component,
            )
    except Exception:
        logger.exception(
            "[shutdown.diagnostics] Failed to patch Uvicorn server handle_exit for component=%s",
            component,
        )

    try:
        import uvicorn.supervisors.multiprocess

        multiprocess_cls = getattr(uvicorn.supervisors.multiprocess, "Multiprocess", None)
        if multiprocess_cls is None:
            logger.warning(
                "[shutdown.diagnostics] Uvicorn Multiprocess supervisor not available; "
                "parent signal diagnostics disabled for component=%s",
                component,
            )
            return

        original_multiprocess_handle_term = getattr(multiprocess_cls, "handle_term", None)
        if callable(original_multiprocess_handle_term):
            def _multiprocess_handle_term(self, *args, **kwargs):
                _log_once(f"{component}:multiprocess.handle_term")
                return original_multiprocess_handle_term(self, *args, **kwargs)

            multiprocess_cls.handle_term = _multiprocess_handle_term

        original_multiprocess_handle_int = getattr(multiprocess_cls, "handle_int", None)
        if callable(original_multiprocess_handle_int):
            def _multiprocess_handle_int(self, *args, **kwargs):
                _log_once(f"{component}:multiprocess.handle_int")
                return original_multiprocess_handle_int(self, *args, **kwargs)

            multiprocess_cls.handle_int = _multiprocess_handle_int
    except Exception:
        logger.exception(
            "[shutdown.diagnostics] Failed to patch Uvicorn multiprocess supervisor for component=%s",
            component,
        )
