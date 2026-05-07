# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools/local_tools.py ──
# Bundle-local tool module. Tools defined here are available ONLY to this bundle.
#
# How to create a bundle-local tool:
#   1. Create a class with methods decorated with @kernel_function (Semantic Kernel)
#   2. Each method becomes a tool the LLM agent can call
#   3. At module level, create a Kernel + register the class as a plugin
#   4. Reference this file in tools_descriptor.py via "ref": "tools/local_tools.py"
#
# The @kernel_function decorator exposes the method to the LLM with:
#   - name:        tool ID (used as "<alias>.<name>", e.g. "local_tools.write_note")
#   - description: shown to the LLM so it knows when/how to use the tool
#
# Method parameters use Annotated[type, "description"] so the LLM
# knows what each parameter expects.

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Dict, Any

import semantic_kernel as sk
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function


class LocalTools:
    @kernel_function(
        name="write_note",
        description="Write a note into OUTPUT_DIR/notes/<timestamp>-note.txt and return its path.",
    )
    async def write_note(
        self,
        text: Annotated[str, "Note text to write"],
    ) -> Dict[str, Any]:
        """
        Example tool: writes a timestamped text note to the output directory.
        The runtime binds OUTDIR_CV for this tool call; resolve_output_dir()
        reads that per-call context instead of process-global environment.
        """
        out_dir = resolve_output_dir()
        notes_dir = out_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-note.txt"
        path = notes_dir / filename
        path.write_text(text or "", encoding="utf-8")

        # Return relative path so the caller doesn't depend on absolute paths
        rel_path = None
        try:
            rel_path = str(path.relative_to(out_dir))
        except Exception:
            rel_path = str(path)

        return {
            "ok": True,
            "path": rel_path,
            "filename": filename,
            "bytes": path.stat().st_size,
        }


# ── Module-level exports for Semantic Kernel ──
# The tool subsystem imports this module and picks up `kernel` to discover tools.
kernel = sk.Kernel()
tools = LocalTools()
kernel.add_plugin(tools, "local_tools")
