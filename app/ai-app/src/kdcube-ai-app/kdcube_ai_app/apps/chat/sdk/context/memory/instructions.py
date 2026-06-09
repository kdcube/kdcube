# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations


MEMORY_REACT_ADDITIONAL_INSTRUCTIONS = """
[MEMORY CONTEXT]
`mem:<id>` points to one saved user memory.

If the visible memory text is enough for the task, use it directly.

When a memory block shows `object_ref: mem:<id>` and exact saved memory
content is needed, import that object ref with `react.pull(paths=["mem:<id>"])`,
then inspect the returned `fi:` logical path or physical path.
""".strip()


__all__ = ["MEMORY_REACT_ADDITIONAL_INSTRUCTIONS"]
