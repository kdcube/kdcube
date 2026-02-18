# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, Any

import semantic_kernel as sk

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
        out_dir = Path(os.environ.get("OUTPUT_DIR", "."))
        notes_dir = out_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-note.txt"
        path = notes_dir / filename
        path.write_text(text or "", encoding="utf-8")

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


# module-level exports for SK
kernel = sk.Kernel()
tools = LocalTools()
kernel.add_plugin(tools, "local_tools")
