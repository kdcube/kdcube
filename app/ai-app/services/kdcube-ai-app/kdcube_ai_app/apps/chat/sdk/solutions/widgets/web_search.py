# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
from typing import Awaitable, Callable, List, Optional


class WebSearchWidget:
    def __init__(
        self,
        *,
        emit_delta: Callable[..., Awaitable[None]],
        agent: str,
        title: str,
        artifact_name: str,
        search_id: Optional[str] = None,
    ):
        self.emit_delta = emit_delta
        self.agent = agent
        self.title = title
        self.artifact_name = artifact_name
        self.search_id = search_id
        self.filtered_idx = 0
        self.html_idx = 0

    def _artifact_name(self, suffix: str) -> str:
        return f"{self.artifact_name}.{suffix}" if suffix else self.artifact_name

    async def send_search_results(
        self,
        *,
        filtered_payload: List[dict],
        objective: str,
        queries: List[str],
    ) -> None:
        filtered_results = {
            "results": filtered_payload,
            "objective": objective,
            "queries": queries,
        }
        await self.emit_delta(
            json.dumps(filtered_results, ensure_ascii=True),
            index=self.filtered_idx,
            marker="subsystem",
            agent=self.agent,
            title=self.title,
            format="json",
            artifact_name=self._artifact_name("filtered_results"),
            sub_type="web_search.filtered_results",
            search_id=self.search_id,
        )
        self.filtered_idx += 1

    async def send_search_report(self, *, html_view: str) -> None:
        await self.emit_delta(
            html_view,
            index=self.html_idx,
            marker="subsystem",
            agent=self.agent,
            title=self.title,
            format="html",
            artifact_name=self._artifact_name("html_view"),
            sub_type="web_search.html_view",
            search_id=self.search_id,
        )
        self.html_idx += 1
