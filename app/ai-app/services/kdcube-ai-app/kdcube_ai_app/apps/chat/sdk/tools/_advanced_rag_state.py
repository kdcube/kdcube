# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Shared state module for the advanced-RAG SDK tool.

The orchestrator's entrypoint installs an `AdvancedRAGRuntime` here at boot;
the SK plugin (`kb_advanced_rag_tools.py`) reads it at call-time via the
importlib shared-module pattern (same approach as `_kdcube_code_graph_state`).
"""

from __future__ import annotations

from typing import Any, Optional

# Set by the bundle entrypoint; None when advanced RAG is unavailable.
RUNTIME: Optional[Any] = None
