# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── code_exec_tool.py ── the model-callable `run_python` tool ──
#
# A plain LangChain `@tool` both ported agents can bind: the model writes Python,
# this tool runs it in the isolated sandbox (via platform/code_exec.py), and every
# file the code produces is hosted into KDCube's conversation storage EXACTLY like
# a user attachment. The tool returns a CONCISE TEXT result — truncated
# stdout/stderr plus REFERENCES to the created files (rn + filename + mime). The
# file BYTES are never in the message, only the refs (the same discipline the
# React agent's `host_files` keeps).
#
# The tool is inert unless it runs inside an active `code_exec_scope` (entered by
# `execute_core` around the graph run). Offline / disabled, it fails open with a
# short message and the turn still answers.

from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.tools import tool

LOGGER = logging.getLogger("kdcube.ported_langgraph_agents.code_exec")

# Bytes cap on stdout/stderr folded into the model-visible result (the authoritative
# large outputs are the hosted files, not the message).
_STREAM_PREVIEW_MAX = 4000


def _truncate(text: str, limit: int = _STREAM_PREVIEW_MAX) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _format_result(result: dict) -> str:
    ok = bool(result.get("ok"))
    lines: List[str] = [f"Status: {'success' if ok else 'error'}"]

    err = str(result.get("error") or "").strip()
    if err and not ok:
        lines.append(f"Error: {err}")

    files = result.get("files") or []
    if files:
        lines.append(f"Created files ({len(files)}) — hosted, cite by rn:")
        for f in files:
            rn = str(f.get("rn") or "").strip()
            filename = str(f.get("filename") or "").strip() or "(file)"
            mime = str(f.get("mime") or "").strip() or "application/octet-stream"
            lines.append(f"- {filename} [{mime}] rn={rn}")
    else:
        lines.append("Created files: none")

    stdout = _truncate(str(result.get("stdout") or "").strip())
    if stdout:
        lines.append("stdout (tail):")
        lines.append(stdout)
    stderr = _truncate(str(result.get("stderr") or "").strip())
    if stderr:
        lines.append("stderr (tail):")
        lines.append(stderr)
    return "\n".join(lines).strip()


def build_run_python_tool() -> Any:
    """Return the `run_python` LangChain tool (a fresh object per call, so each
    agent binds its own instance)."""

    @tool
    async def run_python(code: str) -> str:
        """Run a Python 3 program in an isolated sandbox and keep the files it
        creates.

        Use this to compute, transform data, generate files (CSV, JSON, images,
        charts, reports), or explore files. Write any file you want to KEEP with a
        plain relative path — e.g. ``open("summary.csv", "w").write(...)`` — and it
        is hosted and delivered to the user automatically; the working directory is
        already the deliverables folder. Top-level ``await`` is allowed. Network
        access is disabled.

        Returns a short report: success/error, the created files as references
        (filename + mime + rn — cite files by their rn), and truncated
        stdout/stderr. Large outputs must be written to files, not printed.
        """
        # Lazy, package-relative import: keeps this module import-light and avoids
        # pulling the exec seam into offline tool-list construction.
        from .code_exec import run_code_and_host

        LOGGER.info("[ported-langgraph] run_python: model invoked the tool (code_len=%d)", len(code or ""))
        result = await run_code_and_host(code)
        report = _format_result(result)
        LOGGER.info(
            "[ported-langgraph] run_python: returning report to model (ok=%s files=%d report_len=%d)",
            bool(result.get("ok")), len(result.get("files") or []), len(report),
        )
        return report

    return run_python
