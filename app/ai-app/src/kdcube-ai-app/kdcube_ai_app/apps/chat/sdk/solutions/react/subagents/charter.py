# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The subagent charter: the assignment prompt a parent hands a child.

A charter is a single prompt written by the delegating agent — the goal and
what to send back live in the prompt text — plus the runtime facts the
platform attaches: the round budget (config's business, never the model's)
and the helper alias the child runs as. It travels as data end to end:
react.delegate params -> launch request -> the authored charter event on the
child lane -> the child's contribute/converge report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SUBAGENT_MAX_ROUNDS = 8
MAX_SUBAGENT_MAX_ROUNDS = 30

# The human display name a helper runs under when the delegating agent names
# none. The delegating agent sets a specific title (react.delegate
# `agent_title`) so the user knows who the helper is.
DEFAULT_SUBAGENT_TITLE = "Helper agent"


@dataclass
class SubagentCharter:
    # The whole assignment prompt (goal + expected send-backs, as written by
    # the delegating agent).
    goal: str
    max_rounds: int = DEFAULT_SUBAGENT_MAX_ROUNDS
    # The helper alias the parent picked (react.delegate `agent_alias`).
    agent_alias: str = ""
    # The human display name the delegating agent gave the helper
    # (react.delegate `agent_title`), shown to the user so they know who the
    # helper is. Omitted at the call site -> the default title.
    agent_title: str = DEFAULT_SUBAGENT_TITLE
    # Fields kept for charters persisted under the earlier object contract
    # ({goal, deliverables, contribute}); read on parse, rendered when
    # present, absent from freshly written charters.
    deliverables: List[str] = field(default_factory=list)
    contribute: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "goal": self.goal,
            "max_rounds": int(self.max_rounds or DEFAULT_SUBAGENT_MAX_ROUNDS),
            "agent_alias": self.agent_alias,
            "agent_title": self.agent_title or DEFAULT_SUBAGENT_TITLE,
        }
        if self.deliverables:
            out["deliverables"] = list(self.deliverables)
        if self.contribute:
            out["contribute"] = self.contribute
        return out

    def summary_line(self, *, max_chars: int = 140) -> str:
        """A short caption derived from the prompt's first line/sentence."""
        first_line = next(
            (line.strip() for line in str(self.goal or "").splitlines() if line.strip()),
            "",
        )
        sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
        caption = " ".join((sentence or first_line).split())
        if len(caption) > max_chars:
            caption = caption[: max_chars - 1] + "…"
        return caption

    def charter_text(self) -> str:
        """The model-facing charter statement (the child's task)."""
        lines = [
            "[SUBAGENT CHARTER]",
            "You are a subagent: an agent working a scoped assignment inside "
            "your own conversation. The timeline above this charter is a "
            "fork: a copy of what the delegating agent saw when it opened "
            "this assignment (its in-progress turn plus the conversation's "
            "working summaries). It is context; your assignment is below.",
            "",
            "ASSIGNMENT:",
            str(self.goal or "").strip(),
        ]
        if self.deliverables:
            lines.append("DELIVERABLES:")
            lines.extend(f"- {item}" for item in self.deliverables)
        if self.contribute:
            lines.append(f"CONTRIBUTE BACK: {self.contribute}")
        lines.extend([
            "",
            f"BUDGET: at most {int(self.max_rounds or DEFAULT_SUBAGENT_MAX_ROUNDS)} rounds.",
            "Report results with react.contribute(refs=[...], report=...). Refs you "
            "contribute must be logical paths from THIS conversation; they are "
            "delivered to the delegating agent in a cross-conversation form it can "
            "pull. Your final answer is delivered back automatically when you "
            "finish; contribute earlier when a partial result is already useful.",
        ])
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, raw: Any) -> "SubagentCharter":
        """Rehydrate a persisted charter (dual-read: ``agent_alias`` with the
        stored ``model`` key as the earlier spelling)."""
        raw = raw if isinstance(raw, dict) else {}
        deliverables = raw.get("deliverables")
        if isinstance(deliverables, str):
            deliverables = [deliverables]
        return cls(
            goal=str(raw.get("goal") or "").strip(),
            max_rounds=_clamp_rounds(raw.get("max_rounds") or raw.get("budget")),
            agent_alias=str(raw.get("agent_alias") or raw.get("model") or "").strip(),
            agent_title=str(raw.get("agent_title") or "").strip() or DEFAULT_SUBAGENT_TITLE,
            deliverables=[str(d).strip() for d in (deliverables or []) if str(d or "").strip()],
            contribute=str(raw.get("contribute") or "").strip(),
        )


def _clamp_rounds(value: Any) -> int:
    try:
        rounds = int(value)
    except Exception:
        rounds = DEFAULT_SUBAGENT_MAX_ROUNDS
    if rounds <= 0:
        rounds = DEFAULT_SUBAGENT_MAX_ROUNDS
    return min(rounds, MAX_SUBAGENT_MAX_ROUNDS)


def configured_max_rounds(defaults: Any) -> int:
    """The round budget every charter runs on: ``subagents.max_rounds`` when
    the admin sets it, else :data:`DEFAULT_SUBAGENT_MAX_ROUNDS`, capped at
    :data:`MAX_SUBAGENT_MAX_ROUNDS`. The budget is config's business — the
    delegating model never sets it."""
    raw = (defaults or {}).get("max_rounds") if isinstance(defaults, dict) else None
    if raw is None:
        return DEFAULT_SUBAGENT_MAX_ROUNDS
    return _clamp_rounds(raw)


def parse_charter(
    params: Any,
    *,
    max_rounds: Optional[int] = None,
) -> Tuple[Optional[SubagentCharter], str]:
    """Parse react.delegate params into a charter.

    ``charter`` is the assignment prompt (a string). The tolerated earlier
    object form ({goal, deliverables, contribute}) folds into the prompt
    text. ``max_rounds`` is the configured budget (see
    :func:`configured_max_rounds`); round counts arriving in the params are
    ignored. Returns ``(charter, "")`` or ``(None, error_code)``.
    """
    params = params if isinstance(params, dict) else {}
    raw = params.get("charter")
    alias = str(params.get("agent_alias") or params.get("model") or "").strip()
    title = str(params.get("agent_title") or "").strip()
    if isinstance(raw, str):
        goal = raw.strip()
    else:
        source = raw if isinstance(raw, dict) else params
        legacy = SubagentCharter.from_dict(source)
        goal = legacy.goal
        if goal:
            parts = [goal]
            if legacy.deliverables:
                parts.append(
                    "Deliverables:\n" + "\n".join(f"- {d}" for d in legacy.deliverables)
                )
            if legacy.contribute:
                parts.append(f"Send back: {legacy.contribute}")
            goal = "\n\n".join(parts)
        alias = alias or legacy.agent_alias
        title = title or (legacy.agent_title if legacy.agent_title != DEFAULT_SUBAGENT_TITLE else "")
    if not goal:
        return None, "missing_goal"
    return SubagentCharter(
        goal=goal,
        max_rounds=_clamp_rounds(max_rounds if max_rounds is not None else DEFAULT_SUBAGENT_MAX_ROUNDS),
        agent_alias=alias,
        agent_title=title or DEFAULT_SUBAGENT_TITLE,
    ), ""
