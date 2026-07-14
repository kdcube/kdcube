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
from typing import Any, List, Optional

from langchain_core.tools import tool

# NOTE (for later): the RUNTIME-NEUTRAL guidance below (write-to-files, network
# disabled, top-level await, results/logging, available packages) is copied from the
# React exec tool's description in `sdk/tools/exec_tools.py`. Only the packages block
# is currently DERIVED (`build_packages_installed_block`). The clean fix is to extract
# the neutral blocks into shared SDK builders and have BOTH the React tool and this
# wrapper compose from them, so there is one source of truth. Tracked as a future SDK
# refactor; this wrapper copies for now.

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

    if not ok:
        # Propagate the error CLASSIFICATION so the model reacts correctly: a runtime/
        # sandbox failure is a platform problem it may retry; a program error is its own
        # code and must be fixed. (See docs/exec/exec-logging-error-propagation-README.md.)
        kind = str(result.get("error_kind") or "").strip().lower()
        code = str(result.get("error") or "").strip()
        msg = str(result.get("error_message") or result.get("stderr") or "").strip()
        if kind == "runtime":
            lines.append(
                f"Runtime/sandbox error ({code or 'sandbox_execution_failed'}) — a PLATFORM problem, "
                f"not a defect in your code; usually transient, so you MAY RETRY."
            )
            if msg:
                lines.append(f"Details: {msg}")
        elif kind == "program":
            lines.append(
                f"Program error ({code or 'program_error'}) — YOUR code raised. Read the program log "
                f"below, fix the code, and retry."
            )
            if msg:
                lines.append(f"Details: {msg}")
        elif code or msg:
            lines.append(f"Error{f' ({code})' if code else ''}: {msg}".rstrip())

    files = result.get("files") or []
    if files:
        lines.append(
            f"Created files ({len(files)}) — hosted and delivered to the user. Reference each "
            f"by its link so the user can download it:"
        )
        for f in files:
            rn = str(f.get("rn") or "").strip()
            link = str(f.get("logical_path") or "").strip()
            filename = str(f.get("filename") or "").strip() or "(file)"
            mime = str(f.get("mime") or "").strip() or "application/octet-stream"
            # Prefer the downloadable `fi:conv_…` link (what the chat UI resolves);
            # fall back to the rn if the link is unavailable.
            ref = f"link={link}" if link else f"rn={rn}"
            lines.append(f"- {filename} [{mime}] {ref}")
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
    async def run_python(
        code: str,
        contract: Optional[list] = None,
        prog_name: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> str:
        """Execute a Python 3.11 program in an isolated sandbox and keep the files it
        produces.

        Use this to compute, transform data, generate files (CSV, JSON, images,
        charts, reports), or explore data. Top-level ``await`` is allowed. Network
        access is disabled.

        INPUTS
        - `code` (REQUIRED): the Python program (a module body / snippet). Unlike the
          React exec tool, the code is passed HERE as an argument, not via a channel.
        - `contract` (recommended): a list of the output files you plan to produce —
          each `{filepath, description, visibility?}`. `filepath` is the OUTPUT_DIR-
          relative path your code writes to, under `turn_<current>/files/<name>`;
          `visibility` is `external` (default: hosted AND shown to the user) or
          `internal` (hosted + pullable by you later, not shown). Declaring the
          contract lets you PLAN the deliverables and shows them in the exec panel.
          When you pass a contract, ONLY the contracted files are hosted — each
          `filepath` must byte-match the path your code writes to.
        - `prog_name` (optional): short label for the exec panel.
        - `timeout_s` (optional): wall-clock timeout.

        WITHOUT a contract, the working directory is already the deliverables folder:
        write files with plain relative paths (``open("summary.csv","w")…``) and every
        produced file is auto-hosted. Prefer a contract when you know your outputs.

        RESULTS
        - The result is a short report: status, any error (a runtime/sandbox failure
          is a PLATFORM issue you may retry; a program error is your code to fix), the
          created files as references (filename + mime + a downloadable link), and
          truncated stdout/stderr. Large outputs MUST be written to files, not printed;
          use ``print(...)`` or ``logging.getLogger("user")`` only for short progress.
        """
        # Lazy, package-relative import: keeps this module import-light and avoids
        # pulling the exec seam into offline tool-list construction.
        from .code_exec import run_code_and_host

        LOGGER.info(
            "[ported-langgraph] run_python: model invoked the tool (code_len=%d contract=%s prog_name=%r)",
            len(code or ""), len(contract or []) if isinstance(contract, list) else bool(contract), prog_name,
        )
        result = await run_code_and_host(code, contract=contract, prog_name=prog_name, timeout_s=timeout_s)
        report = _format_result(result)
        LOGGER.info(
            "[ported-langgraph] run_python: returning report to model (ok=%s files=%d report_len=%d)",
            bool(result.get("ok")), len(result.get("files") or []), len(report),
        )
        return report

    # Tell the model which packages the sandbox ships — the SAME `AVAILABLE PACKAGES`
    # block the React exec tool surfaces (`build_packages_installed_block`) — so it
    # writes imports that actually resolve instead of guessing. Appended to the tool
    # DESCRIPTION (what the model sees), computed at build time (per turn, so it
    # reflects the runtime this turn will use). Best-effort: on any failure the tool
    # still works, the model just lacks the hint.
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import build_packages_installed_block
        run_python.description = (
            (run_python.description or "").rstrip()
            + "\n\nAVAILABLE PACKAGES\n"
            + build_packages_installed_block()
            + "\n"
        )
    except Exception:
        LOGGER.info("[ported-langgraph] run_python: available-packages block unavailable (non-fatal)", exc_info=True)

    return run_python
