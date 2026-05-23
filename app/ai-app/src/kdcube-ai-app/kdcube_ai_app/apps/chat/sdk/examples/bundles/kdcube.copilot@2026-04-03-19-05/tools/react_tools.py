# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools/react_tools.py ──
# Bundle-local tool: react.search_knowledge
#
# Provides the LLM agent with the ability to search the knowledge space
# index (index.json) for relevant docs by keyword matching. Registered
# as a Semantic Kernel plugin with alias "react" in tools_descriptor.py,
# so the tool ID becomes "react.search_knowledge".
#
# The bundle package is loaded under a real dynamic package root, so normal
# same-bundle relative imports work here. Proc-side tool binding seeds
# KNOWLEDGE_ROOT from the dedicated materialized knowledge root under bundle
# storage; isolated exec falls back to BUNDLE_STORAGE_DIR and resolves the same
# sub-root.

from __future__ import annotations

import inspect
import pathlib
import time
from typing import Annotated, Callable, Optional, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
from ..knowledge import resolver as knowledge_resolver

_TOOL_SUBSYSTEM: Any = None


def _seed_knowledge_root_from_tool_context() -> None:
    global _TOOL_SUBSYSTEM
    if _TOOL_SUBSYSTEM is None:
        return
    if getattr(knowledge_resolver, "KNOWLEDGE_ROOT", None):
        return
    spec = getattr(_TOOL_SUBSYSTEM, "bundle_spec", None)
    comm = getattr(_TOOL_SUBSYSTEM, "comm", None)
    tenant = getattr(comm, "tenant", None)
    project = getattr(comm, "project", None)
    root = storage_for_spec(
        spec=spec,
        tenant=tenant,
        project=project,
        ensure=True,
    )
    if root is not None:
        knowledge_resolver.KNOWLEDGE_ROOT = pathlib.Path(root).resolve() / "knowledge"


def bind_integrations(integrations):
    global _TOOL_SUBSYSTEM
    _TOOL_SUBSYSTEM = (integrations or {}).get("tool_subsystem")
    _seed_knowledge_root_from_tool_context()


def ensure_knowledge_root(*, storage_root: str | pathlib.Path | None = None) -> pathlib.Path | None:
    _seed_knowledge_root_from_tool_context()
    if storage_root is not None:
        knowledge_resolver.KNOWLEDGE_ROOT = pathlib.Path(storage_root).resolve()
    root = getattr(knowledge_resolver, "KNOWLEDGE_ROOT", None)
    if root is None:
        return None
    return pathlib.Path(root).resolve()


async def search_knowledge_docs(
    *,
    query: str,
    root: Optional[str] = "ks:docs",
    keywords: Optional[list[str]] = None,
    top_k: int = 20,
    storage_root: str | pathlib.Path | None = None,
) -> list[dict]:
    if not query or not str(query).strip():
        return []
    ensure_knowledge_root(storage_root=storage_root)
    try:
        result = knowledge_resolver.search_knowledge(
            query=str(query).strip(),
            root=(root or "ks:docs").strip(),
            max_hits=int(top_k or 20),
            keywords=keywords,
        )
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return list(result.get("hits") or [])
        return []
    except Exception:
        return []


async def read_knowledge_doc(
    *,
    path: str,
    storage_root: str | pathlib.Path | None = None,
) -> dict:
    if not path or not str(path).strip():
        return {"missing": True}
    ensure_knowledge_root(storage_root=storage_root)
    try:
        result = knowledge_resolver.read_knowledge(path=str(path).strip())
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, dict):
            return result
        return {"missing": True}
    except Exception:
        return {"missing": True}


def build_doc_reader_mcp_app(
    *,
    name: str,
    storage_root_provider: Callable[[], str | pathlib.Path | None] | None = None,
    refresh_knowledge_space: Callable[[], Any] | None = None,
    on_tool_call: Callable[..., Any] | None = None,
):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from e

    mcp = FastMCP(name, stateless_http=True)

    async def _prepare() -> pathlib.Path | None:
        if refresh_knowledge_space is not None:
            result = refresh_knowledge_space()
            if inspect.isawaitable(result):
                await result
        storage_root = storage_root_provider() if storage_root_provider is not None else None
        if inspect.isawaitable(storage_root):
            storage_root = await storage_root
        return ensure_knowledge_root(storage_root=storage_root)

    async def _notify_tool_call(
        *,
        tool: str,
        status: str,
        started_at: float,
        data: dict[str, Any],
    ) -> None:
        if on_tool_call is None:
            return
        payload = {
            "mcp_name": name,
            "tool": tool,
            "status": status,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            **data,
        }
        try:
            result = on_tool_call(payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    @mcp.tool(
        name="search_knowledge",
        description=(
            "Search the kdcube.copilot knowledge space. "
            "Use this before external search for platform, SDK, and deployment docs."
        ),
    )
    async def _search_knowledge(
        query: str,
        root: str = "ks:docs",
        keywords: Optional[list[str]] = None,
        top_k: int = 20,
    ) -> list[dict]:
        started_at = time.perf_counter()
        try:
            await _prepare()
            result = await search_knowledge_docs(
                query=query,
                root=root,
                keywords=keywords,
                top_k=top_k,
            )
            await _notify_tool_call(
                tool="search_knowledge",
                status="completed",
                started_at=started_at,
                data={
                    "query_len": len(str(query or "")),
                    "root": root,
                    "top_k": top_k,
                    "result_count": len(result or []),
                },
            )
            return result
        except Exception as exc:
            await _notify_tool_call(
                tool="search_knowledge",
                status="error",
                started_at=started_at,
                data={"query_len": len(str(query or "")), "root": root, "error": str(exc)[:300]},
            )
            raise

    @mcp.tool(
        name="read_knowledge",
        description=(
            "Read an exact ks: path from the kdcube.copilot knowledge space. "
            "Use this when you already know the concrete document or source path."
        ),
    )
    async def _read_knowledge(path: str) -> dict:
        started_at = time.perf_counter()
        try:
            await _prepare()
            result = await read_knowledge_doc(path=path)
            await _notify_tool_call(
                tool="read_knowledge",
                status="completed",
                started_at=started_at,
                data={
                    "path": str(path or "")[:300],
                    "missing": bool((result or {}).get("missing")),
                },
            )
            return result
        except Exception as exc:
            await _notify_tool_call(
                tool="read_knowledge",
                status="error",
                started_at=started_at,
                data={"path": str(path or "")[:300], "error": str(exc)[:300]},
            )
            raise

    return mcp


class KDCubeCopilotTools:
    """SK plugin exposing knowledge-space search to the ReAct agent."""
    @kernel_function(
        name="search_knowledge",
        description=(
            "Search the local knowledge space exposed by this bundle. "
            "Use this for product/architecture questions before external search."
        ),
    )
    async def search_knowledge(
        self,
        query: Annotated[str, "Search query for docs/knowledge index."],
        root: Annotated[Optional[str], "Optional subtree selector under ks:, for example ks:docs or ks:deployment (default: ks:docs)."] = "ks:docs",
        keywords: Annotated[Optional[list[str]], "Optional keywords/tags to bias ranking."] = None,
        top_k: Annotated[int, "Max hits to return (default 20)."] = 20,
    ) -> Annotated[list[dict] | None, "List of hits with ks: paths + titles."]:
        return await search_knowledge_docs(
            query=query,
            root=root,
            keywords=keywords,
            top_k=top_k,
        )


# Module-level exports for SK + tool subsystem.
# The tool loader expects a `kernel` with registered plugins at module scope.
kernel = sk.Kernel()
tools = KDCubeCopilotTools()
kernel.add_plugin(tools, "kdcube_copilot_tools")
