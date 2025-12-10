# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/comm_ctx.py
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, Dict, Any

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator

# Public: holds the communicator for the *current execution context*
COMM_CV: ContextVar[object | None] = ContextVar("COMM_CV", default=None)

def set_comm(comm: object) -> None:
    """Register the ChatCommunicator singleton for this context."""
    COMM_CV.set(comm)

def get_comm() -> Optional[object|ChatCommunicator]:
    """Get the ChatCommunicator singleton (or None if not initialized)."""
    return COMM_CV.get()

async def delta(text: str, index: int, marker: str = "answer", completed: bool = False, **kwargs) -> None:
    """Convenience: emit a delta if communicator is present."""
    comm = get_comm()
    if comm is None:
        return
    await comm.delta(text=text, index=index, marker=marker, completed=completed, **kwargs)

async def step(step: str, status: str, **payload) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.step(step=step, status=status, **payload)

async def complete(data: Dict[str, Any] | None = None) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.complete(data=data or {})

async def error(message: str, data: Dict[str, Any] | None = None) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.error(message=message, data=data or {})

# We rebuild the communicator from PORTABLE_SPEC in the child, so we do NOT
# serialize the COMM_CV object itself (not JSON-safe). Keep tiny helpers:

def snapshot_ctxvars() -> dict:
    # marker only; actual communicator is rebuilt from spec
    return {"COMM_PRESENT": COMM_CV.get() is not None}

def restore_ctxvars(payload: dict) -> None:
    # no-op: communicator is reconstructed by bootstrap and set via set_comm(...)
    return

