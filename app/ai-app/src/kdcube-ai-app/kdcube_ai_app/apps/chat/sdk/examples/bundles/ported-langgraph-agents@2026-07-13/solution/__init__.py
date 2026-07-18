# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Preserved standalone agent packages this app hosts, one subpackage each.

The framework/domain boundary remains solution-owned. Deliberate async,
configuration, model-injection, package-import, and prompt-composition seams are
small, explicit, and documented by the app.

Both packages originate from the "before" instances of the KDCube port recipe and
preserve their framework/domain behavior behind the documented integration seams:

  - ``lg_solution``  — the hand-written research graph (KB retrieval + per-user
                       pgvector memory + a nested subagent; a dedicated answer node).
  - ``lg_prebuilt``  — ``langchain.agents.create_agent``
                       (a looping model node + a tools node; plain + MCP tools).

The multi-agent host (``entrypoint.py``) dispatches on ``agent_id`` to the right
package; neither package imports KDCube.
"""

__all__ = ["lg_solution", "lg_prebuilt"]
