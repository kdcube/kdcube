# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.tool_traits import (
    STRATEGY_TRAIT,
    UNKNOWN_STRATEGY,
    strategies_compatible,
    strategy_values,
)
from kdcube_ai_app.apps.chat.sdk.streaming.stream_policy import StreamPolicyViolation


EmitDelta = Callable[..., Awaitable[None]]
TraitsResolver = Callable[[str], Mapping[str, Any]]
DEFAULT_MAX_ACTIONS_PER_ROUND = 2


class ActionStreamGate:
    """Buffered output gate for one observed action lane."""

    def __init__(self, *, emit_delta: EmitDelta, action_index: int, lane: str = "action") -> None:
        self._emit_delta = emit_delta
        self.action_index = int(action_index or 0)
        self.lane = str(lane or "action")
        self._status = "pending"
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def status(self) -> str:
        return self._status

    async def emit_delta(self, **kwargs: Any) -> None:
        async with self._lock:
            if self._status == "denied":
                return
            if self._status != "allowed":
                self._buffer.append(dict(kwargs))
                return
        await self._emit_delta(**kwargs)

    async def allow(self) -> None:
        async with self._lock:
            if self._status != "pending":
                return
            self._status = "allowed"
            buffered = list(self._buffer)
            self._buffer.clear()
        for item in buffered:
            await self._emit_delta(**item)

    async def deny(self) -> None:
        async with self._lock:
            self._status = "denied"
            self._buffer.clear()


@dataclass(frozen=True)
class ObservedAction:
    index: int
    action: str
    tool_id: str
    traits: dict[str, Any]

    @property
    def strategies(self) -> set[str]:
        return strategy_values(self.traits)

    @property
    def is_tool(self) -> bool:
        return self.action == "call_tool"

    @property
    def is_final(self) -> bool:
        return self.action in {"complete", "exit"}

    @property
    def is_neutral_tool(self) -> bool:
        strategies = self.strategies
        return bool(strategies) and UNKNOWN_STRATEGY not in strategies and strategies == {"neutral"}

    @property
    def answer_lane_allowed(self) -> bool:
        if self.is_final:
            return True
        return False


class RoundActionOverseer:
    """External per-round compatibility policy for streamed action instances."""

    def __init__(self, *, resolve_traits: TraitsResolver, max_actions: int = DEFAULT_MAX_ACTIONS_PER_ROUND) -> None:
        self._resolve_traits = resolve_traits
        self._max_actions = max(1, int(max_actions or DEFAULT_MAX_ACTIONS_PER_ROUND))
        self._observed: list[ObservedAction] = []
        self._lock = asyncio.Lock()

    def gate_for(self, *, action_index: int, emit_delta: EmitDelta, lane: str = "action") -> ActionStreamGate:
        return ActionStreamGate(emit_delta=emit_delta, action_index=action_index, lane=lane)

    async def observe_action_signal(
        self,
        *,
        action_index: int,
        action: str,
        tool_id: str,
        action_gate: ActionStreamGate,
        answer_gate: Optional[ActionStreamGate] = None,
    ) -> ObservedAction:
        action_text = str(action or "").strip()
        tool_id_text = str(tool_id or "").strip()
        traits = self._traits_for(action_text, tool_id_text)
        observed = ObservedAction(
            index=int(action_index or 0),
            action=action_text,
            tool_id=tool_id_text,
            traits=traits,
        )

        async with self._lock:
            previous_same_index = next((item for item in self._observed if item.index == observed.index), None)
            if previous_same_index is not None:
                action_allowed = True
                answer_allowed = self._answer_gate_allowed(previous_same_index)
                violation: tuple[str, dict[str, Any]] | None = None
            else:
                violation = self._violation_for(observed)
                action_allowed = violation is None
                answer_allowed = action_allowed and self._answer_gate_allowed(observed)
                if action_allowed:
                    self._observed.append(observed)

        if action_allowed:
            await action_gate.allow()
            if answer_gate is not None:
                if answer_allowed:
                    await answer_gate.allow()
                else:
                    await answer_gate.deny()
            return previous_same_index or observed

        await action_gate.deny()
        if answer_gate is not None:
            await answer_gate.deny()
        assert violation is not None
        code, extra = violation
        raise StreamPolicyViolation(code=code, extra=extra)

    def _traits_for(self, action: str, tool_id: str) -> dict[str, Any]:
        if action in {"complete", "exit"}:
            return {STRATEGY_TRAIT: ["neutral"]}
        if action == "call_tool" and tool_id:
            return dict(self._resolve_traits(tool_id) or {})
        return {}

    def _violation_for(self, observed: ObservedAction) -> tuple[str, dict[str, Any]] | None:
        if not observed.is_tool and not observed.is_final:
            return (
                "multi_action_bundle_mixed_actions",
                {"index": observed.index, "action": observed.action},
            )

        if not self._observed:
            return None

        if len(self._observed) >= self._max_actions:
            return (
                "multi_action_bundle_too_many_actions",
                {
                    "index": observed.index,
                    "action": observed.action,
                    "tool_id": observed.tool_id,
                    "max_actions": self._max_actions,
                },
            )

        if observed.is_final:
            non_neutral = next((item for item in self._observed if not item.is_final and not item.is_neutral_tool), None)
            if non_neutral is not None:
                return (
                    "multi_action_bundle_final_answer_after_non_neutral",
                    {
                        "index": observed.index,
                        "action": observed.action,
                        "first_index": non_neutral.index,
                        "first_tool_id": non_neutral.tool_id,
                        "first_strategy": sorted(non_neutral.strategies),
                    },
                )
            return None

        current_strategies = observed.strategies
        if not current_strategies:
            return (
                "multi_action_bundle_unsafe_tool",
                {
                    "index": observed.index,
                    "tool_id": observed.tool_id,
                    "strategy": sorted(current_strategies or {UNKNOWN_STRATEGY}),
                },
            )

        for previous in self._observed:
            if previous.is_final and not observed.is_neutral_tool:
                return (
                    "multi_action_bundle_non_neutral_after_final_answer",
                    {
                        "index": observed.index,
                        "tool_id": observed.tool_id,
                        "strategy": sorted(current_strategies),
                        "first_index": previous.index,
                        "first_action": previous.action,
                    },
                )
            if previous.is_final:
                continue
            if not strategies_compatible(previous.traits, observed.traits):
                return (
                    "multi_action_bundle_strategy_incompatible",
                    {
                        "index": observed.index,
                        "tool_id": observed.tool_id,
                        "strategy": sorted(current_strategies),
                        "first_index": previous.index,
                        "first_tool_id": previous.tool_id,
                        "first_strategy": sorted(previous.strategies),
                    },
                )
        return None

    def _answer_gate_allowed(self, observed: ObservedAction) -> bool:
        if not observed.answer_lane_allowed:
            return False
        if observed.is_final:
            return all(item.is_final or item.is_neutral_tool for item in self._observed)
        return True


__all__ = ["ActionStreamGate", "DEFAULT_MAX_ACTIONS_PER_ROUND", "ObservedAction", "RoundActionOverseer"]
