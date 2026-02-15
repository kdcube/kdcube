# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import time
from typing import Awaitable, Callable, Optional


class ConversationTurnWorkStatus:
    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        artifact_name: str = "conversation.turn.status",
        sub_type: str = "conversation.turn.status",
        title: str = "Conversation Turn Status",
        execution_id: Optional[str] = None,
    ):
        self.emit_delta = emit_delta
        self.agent = agent
        self.artifact_name = artifact_name
        self.sub_type = sub_type
        self.title = title
        self.execution_id = execution_id

    async def send(self, status: str) -> None:
        payload = {
            "status": str(status),
            "timestamp": time.time(),
        }
        await self.emit_delta(
            text=json.dumps(payload, ensure_ascii=True),
            index=0,
            marker="subsystem",
            agent=self.agent,
            title=self.title,
            format="json",
            artifact_name=self.artifact_name,
            sub_type=self.sub_type,
            execution_id=self.execution_id,
        )
