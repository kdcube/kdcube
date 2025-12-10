# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/comm/contract.py
from __future__ import annotations

from typing import Protocol, Optional, Dict, Any, runtime_checkable, Tuple


@runtime_checkable
class ChatEmitter(Protocol):
    tenant: str
    project: str
    service: Dict[str, Any]
    conversation: Dict[str, Any]
    room: Optional[str]
    target_sid: Optional[str]

    async def start(self, *, message: str, queue_stats: Optional[dict] = None) -> None: ...

    async def step(
            self,
            *,
            step: str,
            status: str,
            title: Optional[str] = None,
            agent: Optional[str] = None,
            data: Optional[dict] = None,
            markdown: Optional[str] = None,
    ) -> None: ...

    async def delta(
            self,
            *,
            text: str,
            index: int,
            marker: str = "answer",
            agent: str = "assistant",
            completed: bool = False,
            **kwargs,
    ) -> None: ...

    async def complete(self, *, data: dict) -> None: ...

    async def error(
            self,
            *,
            message: str,
            data: Optional[dict] = None,
            agent: Optional[str] = None,
            step: str = "chat.error",
            title: str = "Workflow Error",
    ) -> None: ...

    async def event(
            self,
            *,
            agent: str | None,
            type: str,
            title: str | None = None,
            step: str = "event",
            data: dict | None = None,
            markdown: str | None = None,
            route: str | None = None,
            status: str = "update",
            auto_markdown: bool = True,
            broadcast: bool = False,
    ) -> None: ...
