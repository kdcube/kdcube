# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Agent-neutral composition of an instruction body from config tokens.

One vocabulary, shared by any agent that composes its instruction body from a
configured list (a profile's ``blocks``, or ``instruction_blocks``). Each item
is resolved independently and all are joined with a blank line **in the order
listed**:

- ``full`` (optionally ``full:<anything>``) → the agent's complete default body,
  supplied by the caller through ``full_body_provider``. The full set is
  monolithic (not capability-scoped), so any suffix is ignored. With no provider
  a ``full`` token contributes nothing.
- ``lite:<profile>`` → a whole moderate profile body (``core``, ``workspace``,
  ``workspace_exec``, ``document``, ``web``, ``all_capabilities``).
- ``xlite:<profile>`` → a whole extra-lite profile body (same profile names).
- ``REACT_LITE_*`` / ``REACT_XLITE_*`` → a single named block.
- anything else → literal instruction text, used verbatim.

This module owns only the vocabulary. The agent's full/default body is injected
(``full_body_provider``) so nothing here is agent-specific; the ReAct harness,
or any other agent, wraps this with its own default-body builder. The runtime
protocol and tool/skill catalogs are added by the agent around this body, not
here.

Direction: this package is the seam for externally-managed, versioned
instruction sets — a block/profile referenced by name here will resolve, in
time, from a managed store (with an id and a version) rather than only from the
in-tree ``shared_instructions_lite`` / ``instructions_extra_lite`` registries.
Keep the resolution behind this composer so that move needs no caller change.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
    resolve_lite_item,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.instructions_extra_lite import (
    resolve_extra_lite_item,
)


def compose_instruction_body(
    items: Optional[Iterable[str]],
    *,
    workspace_implementation: str = "custom",
    full_body_provider: Optional[Callable[[], str]] = None,
    exclude_blocks: Optional[Iterable[str]] = None,
) -> str:
    """Compose a config token list into one instruction body (order-preserving).

    ``workspace_implementation`` is passed to profile expansion so
    ``lite:``/``xlite:`` honor git mode. ``full_body_provider`` supplies the
    agent's complete default body for a ``full`` token.
    """
    if isinstance(items, str):
        items = [items]
    excluded = {str(name or "").strip() for name in (exclude_blocks or [])}
    resolved: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        if text in excluded:
            continue
        low = text.lower()
        if low == "full" or low.startswith("full:"):
            body = (full_body_provider() if full_body_provider is not None else "").strip()
            if body:
                resolved.append(body)
            continue
        xlite = resolve_extra_lite_item(
            text,
            workspace_implementation=workspace_implementation,
            exclude_blocks=excluded,
        )
        if xlite is not None:
            resolved.append(xlite)
            continue
        lite = resolve_lite_item(text, exclude_blocks=excluded)
        if lite is not None:
            resolved.append(lite)
            continue
        resolved.append(text)  # literal instruction fragment
    return "\n\n".join(part for part in (p.strip() for p in resolved) if part).strip()


__all__ = ["compose_instruction_body"]
