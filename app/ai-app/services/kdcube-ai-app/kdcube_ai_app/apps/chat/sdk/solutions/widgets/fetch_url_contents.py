# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional


class FetchWebResourceWidget:
    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        artifact_name: str = "web_fetch.results",
        sub_type: str = "web_fetch.results",
        title: str = "Fetch Results",
        execution_id: Optional[str] = None,
    ):
        self.emit_delta = emit_delta
        self.agent = agent
        self.artifact_name = artifact_name
        self.sub_type = sub_type
        self.title = title
        self.execution_id = execution_id

    async def send(self, data: dict) -> None:
        await self.emit_delta(
            text=json.dumps(data, ensure_ascii=True),
            index=0,
            marker="subsystem",
            agent=self.agent,
            title=self.title,
            format="json",
            artifact_name=self.artifact_name,
            sub_type=self.sub_type,
            execution_id=self.execution_id,
        )
