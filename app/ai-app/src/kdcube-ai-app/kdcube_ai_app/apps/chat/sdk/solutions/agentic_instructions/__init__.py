# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Agentic instruction composition — the agent-neutral instruction-body seam.

Composes an agent's instruction body from a configured token list with one
vocabulary shared across agents (``full`` | ``lite:<profile>`` |
``xlite:<profile>`` | single ``REACT_LITE_*``/``REACT_XLITE_*`` blocks | literal
text). Intended to grow into the home for externally-managed, versioned
instruction sets referenced by id + version.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.agentic_instructions.compose import (
    compose_instruction_body,
)

__all__ = ["compose_instruction_body"]
