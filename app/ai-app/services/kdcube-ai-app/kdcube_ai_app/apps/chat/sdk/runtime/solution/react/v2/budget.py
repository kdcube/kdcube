# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/budget.py

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StrategyBudget:
    name: str
    budget: int = 0
    used: int = 0

    def remaining(self) -> int:
        return max(0, int(self.budget) - int(self.used))


@dataclass
class BudgetStateV2:
    exploration_budget: int = 0
    exploitation_budget: int = 0
    explore_used: int = 0
    exploit_used: int = 0
    max_iterations: int = 0
    decision_rounds_used: int = 0
    strategies: List[StrategyBudget] = field(default_factory=list)

    def ensure_strategies(self) -> None:
        if self.strategies:
            return
        self.strategies = [
            StrategyBudget(name="explore", budget=int(self.exploration_budget or 0), used=int(self.explore_used or 0)),
            StrategyBudget(name="exploit", budget=int(self.exploitation_budget or 0), used=int(self.exploit_used or 0)),
        ]

    def remaining_explore(self) -> int:
        return max(0, int(self.exploration_budget) - int(self.explore_used))

    def remaining_exploit(self) -> int:
        return max(0, int(self.exploitation_budget) - int(self.exploit_used))

    def remaining_rounds(self) -> int:
        if not self.max_iterations:
            return 0
        return max(0, int(self.max_iterations) - int(self.decision_rounds_used))

    def format_for_llm(self) -> str:
        self.ensure_strategies()
        strat_lines = []
        for s in self.strategies:
            strat_lines.append(
                f"- {s.name}: {int(s.used)}/{int(s.budget)} used (remaining {s.remaining()})"
            )
        return "\n".join(
            [
                "Budget v2 (turn-level)",
                *strat_lines,
                f"- decision rounds: {int(self.decision_rounds_used)}/{int(self.max_iterations)} used "
                f"(remaining {self.remaining_rounds()})",
            ]
        )
