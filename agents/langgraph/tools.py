"""KDCube conversation, web, code-execution, and rendering tools for LangGraph."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (
    DirectToolRuntime,
)

WEB_SEARCH_TOOL_ID = "web_tools.web_search"
WEB_FETCH_TOOL_ID = "web_tools.web_fetch"
CONVERSATION_SEARCH_TOOL_ID = "conversation_tools.search"
EXEC_TOOL_ID = "exec_tools.execute_code_python"
RENDER_TOOL_IDS = {
    "write_pdf": "rendering_tools.write_pdf",
    "write_docx": "rendering_tools.write_docx",
    "write_pptx": "rendering_tools.write_pptx",
}


def _normalise_results(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if isinstance(value, dict):
        value = value.get("ret") or value.get("results") or value.get("items") or []
    rows: list[dict[str, str]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": str(item.get("title") or "Untitled"),
                "body": str(
                    item.get("body") or item.get("snippet") or item.get("text") or ""
                ),
                "url": str(item.get("href") or item.get("url") or ""),
            }
        )
    return rows


def build_tools(
    runtime: DirectToolRuntime | None,
    *,
    configured_ids: set[str],
) -> list[BaseTool]:
    """Adapt descriptor-selected KDCube tools to LangChain tool objects."""

    def require_runtime() -> DirectToolRuntime:
        if runtime is None:
            raise RuntimeError("this tool requires an active direct-agent turn")
        return runtime

    @tool
    async def web_search(
        queries: str | list[str],
        objective: str = "",
        n: int = 5,
        fetch_content: bool = False,
        use_llm: bool = False,
    ) -> str:
        """Search with KDCube Web Search. Returns title, excerpt, and URL rows."""
        limit = max(1, min(int(n or 5), 8))
        try:
            rows = _normalise_results(
                await require_runtime().invoke_tool(
                    tool_id=WEB_SEARCH_TOOL_ID,
                    params={
                        "queries": queries,
                        "objective": objective or None,
                        "n": limit,
                        "fetch_content": bool(fetch_content),
                        "use_llm": bool(use_llm),
                    },
                    call_reason="Search the public web",
                )
            )
            return json.dumps(
                {"ok": True, "queries": queries, "results": rows},
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {
                    "ok": False,
                    "queries": queries,
                    "error": str(exc),
                    "results": [],
                }
            )

    @tool
    async def web_fetch(
        urls: str | list[str],
        objective: str = "",
        refinement: str = "none",
    ) -> str:
        """Fetch selected URLs through KDCube's governed Web Fetch."""
        try:
            fetched = await require_runtime().invoke_tool(
                tool_id=WEB_FETCH_TOOL_ID,
                params={
                    "urls": urls,
                    "objective": objective or None,
                    "refinement": refinement,
                },
                call_reason="Fetch a selected web source",
            )
            return json.dumps(
                fetched,
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            return json.dumps(
                {"ok": False, "urls": urls, "error": str(exc), "result": None}
            )

    @tool
    async def conversation_search(
        query: str,
        scope: str = "user",
        targets: list[str] | None = None,
        top_k: int = 5,
        days: int = 365,
        include_recovery_sessions: bool = False,
    ) -> str:
        """Search this user's current or earlier KDCube conversations."""
        try:
            result = await require_runtime().invoke_tool(
                tool_id=CONVERSATION_SEARCH_TOOL_ID,
                params={
                    "query": query,
                    "scope": scope,
                    "targets": targets
                    or ["assistant", "user", "attachment", "summary"],
                    "top_k": top_k,
                    "days": days,
                    "include_recovery_sessions": include_recovery_sessions,
                },
                call_reason="Search this user's conversation history",
            )
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps(
                {"ok": False, "error": str(exc), "ret": None},
                ensure_ascii=False,
            )

    @tool
    async def execute_python(
        code: str,
        artifacts: list[dict[str, Any]],
        program_name: str = "Agent-generated Python",
        timeout_s: int = 600,
    ) -> str:
        """Execute your Python in KDCube's isolated runtime and keep contracted files."""
        active = require_runtime()
        return active.tool_report(
            await active.execute_python(
                code=code,
                artifacts=artifacts,
                program_name=program_name,
                timeout_s=timeout_s,
            )
        )

    @tool
    async def write_pdf(source_path: str, output_path: str, title: str = "") -> str:
        """Render a current-turn HTML file to PDF with KDCube rendering tools."""
        active = require_runtime()
        return active.tool_report(
            await active.write_pdf(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    @tool
    async def write_docx(source_path: str, output_path: str, title: str = "") -> str:
        """Render a current-turn Markdown file to DOCX with KDCube rendering tools."""
        active = require_runtime()
        return active.tool_report(
            await active.write_docx(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    @tool
    async def write_pptx(source_path: str, output_path: str, title: str = "") -> str:
        """Render current-turn section-based HTML to PPTX with KDCube rendering tools."""
        active = require_runtime()
        return active.tool_report(
            await active.write_pptx(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    available = [
        (WEB_SEARCH_TOOL_ID, web_search),
        (WEB_FETCH_TOOL_ID, web_fetch),
        (CONVERSATION_SEARCH_TOOL_ID, conversation_search),
        (EXEC_TOOL_ID, execute_python),
        (RENDER_TOOL_IDS["write_pdf"], write_pdf),
        (RENDER_TOOL_IDS["write_docx"], write_docx),
        (RENDER_TOOL_IDS["write_pptx"], write_pptx),
    ]
    return [item for canonical_id, item in available if canonical_id in configured_ids]


__all__ = [
    "CONVERSATION_SEARCH_TOOL_ID",
    "EXEC_TOOL_ID",
    "RENDER_TOOL_IDS",
    "WEB_FETCH_TOOL_ID",
    "WEB_SEARCH_TOOL_ID",
    "build_tools",
]
