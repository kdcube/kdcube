# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/sdk/comm/emitters.py

# Facade first
from __future__ import annotations
from typing import Optional, Dict, Any,  Literal
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator


def _compose_md(md: str) -> Dict[str, Any]:
    return {"markdown": md, "compose": {"blocks": [{"type": "md", "text": md}]}}


class StartPayload(BaseModel):
    message: str
    queue_stats: Dict[str, Any] = Field(default_factory=dict)


class DeltaPayload(BaseModel):
    text: str
    index: int
    marker: Literal["thinking", "answer"] = "answer"
    completed: bool = False
    agent: Optional[str] = None

    # optional but very useful with your delta cache design
    format: Optional[str] = "markdown"
    artifact_name: Optional[str] = "Unknown"


class StepPayload(BaseModel):
    step: str
    type: Optional[str] = None
    status: Literal["started", "completed", "error", "skipped", "update"] = "completed"
    title: Optional[str] = None
    markdown: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    agent: Optional[str] = None
    broadcast: Optional[bool] = False
    timing: Optional[Dict[str, Any]] = None

class CompletePayload(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


class ErrorPayload(BaseModel):
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    agent: Optional[str] = None
    step: str = "chat.error"
    title: str = "Workflow Error"


class EventPayload(BaseModel):
    agent: Optional[str] = None
    type: str
    title: Optional[str] = None
    step: str = "event"
    status: str = "update"
    route: Optional[str] = None
    markdown: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    auto_markdown: bool = True
    broadcast: bool = False

class AIBEmitters:
    """
    Adapter that implements the ChatEmitter contract but also offers
    typed payload entrypoints.
    """
    def __init__(self, comm: ChatCommunicator):
        self.comm = comm

        # expose attrs expected elsewhere (FilteringCommunicator etc.)
        self.service = getattr(comm, "service", {})
        self.conversation = getattr(comm, "conversation", {})
        self.room = getattr(comm, "room", None)

    # ---------- dual-style methods ----------

    async def start(self, p: Optional[StartPayload] = None, **kwargs) -> None:
        if p is None:
            p = StartPayload(**kwargs)
        await self.comm.start(message=p.message, queue_stats=p.queue_stats)

    async def delta(self, p: Optional[DeltaPayload] = None, **kwargs) -> None:
        if p is None:
            p = DeltaPayload(**kwargs)
        extra = {}
        if p.format:
            extra["format"] = p.format
        if p.artifact_name:
            extra["artifact_name"] = p.artifact_name

        await self.comm.delta(
            text=p.text,
            index=p.index,
            marker=p.marker,
            completed=p.completed,
            agent=p.agent or "assistant",
            **extra,
        )

    async def step(self, p: Optional[StepPayload] = None, **kwargs) -> None:
        if p is None:
            p = StepPayload(**kwargs)

        data = dict(p.data or {})
        if p.markdown:
            data = {**_compose_md(p.markdown), **data}

        await self.comm.step(
            step=p.step,
            status=p.status,
            title=p.title,
            data=data,
            agent=p.agent,
        )

    async def complete(self, p: Optional[CompletePayload] = None, **kwargs) -> None:
        if p is None:
            p = CompletePayload(**kwargs)
        await self.comm.complete(data=p.data or {})

    async def error(self, p: Optional[ErrorPayload] = None, **kwargs) -> None:
        if p is None:
            p = ErrorPayload(**kwargs)
        await self.comm.error(
            message=p.message,
            data=p.data or {},
            agent=p.agent,
            step=p.step,
            title=p.title,
        )

    async def event(self, p: Optional[EventPayload] = None, **kwargs) -> None:
        if p is None:
            p = EventPayload(**kwargs)
        await self.comm.event(
            agent=p.agent,
            type=p.type,
            title=p.title,
            step=p.step,
            data=p.data or {},
            markdown=p.markdown,
            route=p.route,
            status=p.status,
            auto_markdown=p.auto_markdown,
            broadcast=p.broadcast,
        )

    # ---------- small semantic helpers ----------

    async def followups(self, items: list[str], *, agent: str = "answer_generator") -> None:
        await self.event(
            agent=agent,
            type="chat.followups",
            title="Follow-ups: User Shortcuts",
            status="completed",
            data={"items": items},
        )