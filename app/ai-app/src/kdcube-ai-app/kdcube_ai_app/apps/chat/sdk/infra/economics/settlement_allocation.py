# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PlanWalletSettlementInput:
    """
    Snapshot used to split one completed plan-lane request.

    ``*_available_*`` values are the current available capacities after other
    active reservations. ``*_reserved_*`` values are this request's own active
    reservations, which are added back so this request can spend what it held
    earlier without stealing capacity from concurrent requests.

    ``primary_funding_reserved_tokens`` preserves the pre-run split between the
    normal plan funding source and wallet overflow. Without it, a fresh budget
    read at settlement time can move wallet-reserved overflow back onto plan
    quota just because the project or subscription balance still has capacity.
    """

    actual_tokens: int
    actual_cost_usd: float

    quota_available_tokens: Optional[int]
    quota_reserved_tokens: int = 0

    primary_funding_available_usd: Optional[float] = 0.0
    primary_funding_reserved_usd: float = 0.0
    primary_funding_reserved_tokens: Optional[int] = None

    wallet_available_tokens: int = 0
    wallet_reserved_tokens: int = 0


@dataclass(frozen=True)
class PlanWalletSettlementAllocation:
    """
    Result of the plan-first, wallet-overflow settlement split.

    primary_funding is the normal plan funding source for the request
    (project budget for registered/free plan users, subscription budget for
    subscription users). project_absorption is always the project budget fallback
    for the residual left after wallet payment.
    """

    actual_tokens: int
    actual_cost_usd: float

    quota_capacity_tokens: int
    primary_funding_capacity_tokens: int
    wallet_capacity_tokens: int

    primary_funding_tokens: int
    wallet_tokens: int
    project_absorption_tokens: int
    quota_fallback_tokens: int

    quota_tokens: int

    primary_funding_usd: float
    wallet_usd: float
    project_absorption_usd: float
    quota_usd_equivalent: float

    @property
    def project_total_tokens_when_primary_is_project(self) -> int:
        return int(self.primary_funding_tokens) + int(self.project_absorption_tokens)

    @property
    def project_total_usd_when_primary_is_project(self) -> float:
        return float(self.primary_funding_usd) + float(self.project_absorption_usd)


def _non_negative_int(value: Optional[int]) -> int:
    if value is None:
        return 0
    return max(int(value), 0)


def _non_negative_float(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    return max(float(value), 0.0)


def _usd_to_token_capacity(*, usd: Optional[float], actual_tokens: int, actual_cost_usd: float) -> int:
    if actual_tokens <= 0:
        return 0
    if usd is None or math.isinf(float(usd)):
        return int(actual_tokens)
    amount = _non_negative_float(usd)
    if amount <= 0:
        return 0
    if actual_cost_usd <= 0:
        return int(actual_tokens)
    return max(min(int(math.floor((amount / float(actual_cost_usd)) * int(actual_tokens))), int(actual_tokens)), 0)


def _tokens_to_usd(*, tokens: int, actual_tokens: int, actual_cost_usd: float) -> float:
    if tokens <= 0 or actual_tokens <= 0 or actual_cost_usd <= 0:
        return 0.0
    return float(actual_cost_usd) * (float(tokens) / float(actual_tokens))


def allocate_plan_wallet_settlement(
    settlement: PlanWalletSettlementInput,
) -> PlanWalletSettlementAllocation:
    """
    Split actual usage using the product funding order:

    1. Consume the maximum request share from plan quota, but only while the
       normal plan funding source can pay that share.
    2. Charge wallet for the remaining overflow, up to wallet capacity.
    3. Charge any residual back to project budget, even when that sends the
       project budget negative. If quota remains, that residual also consumes
       quota; otherwise it is project-budget-only overage.

    Wallet-paid tokens never consume plan quota.
    """

    actual_tokens = _non_negative_int(settlement.actual_tokens)
    actual_cost_usd = _non_negative_float(settlement.actual_cost_usd)

    if actual_tokens <= 0:
        return PlanWalletSettlementAllocation(
            actual_tokens=0,
            actual_cost_usd=actual_cost_usd,
            quota_capacity_tokens=0,
            primary_funding_capacity_tokens=0,
            wallet_capacity_tokens=0,
            primary_funding_tokens=0,
            wallet_tokens=0,
            project_absorption_tokens=0,
            quota_fallback_tokens=0,
            quota_tokens=0,
            primary_funding_usd=0.0,
            wallet_usd=0.0,
            project_absorption_usd=0.0,
            quota_usd_equivalent=0.0,
        )

    if settlement.quota_available_tokens is None:
        quota_capacity_tokens = actual_tokens
    else:
        quota_capacity_tokens = min(
            _non_negative_int(settlement.quota_available_tokens)
            + _non_negative_int(settlement.quota_reserved_tokens),
            actual_tokens,
        )

    primary_funding_capacity_usd: Optional[float]
    if settlement.primary_funding_available_usd is None:
        primary_funding_capacity_usd = None
    else:
        primary_funding_capacity_usd = (
            _non_negative_float(settlement.primary_funding_available_usd)
            + _non_negative_float(settlement.primary_funding_reserved_usd)
        )
    primary_funding_capacity_tokens = _usd_to_token_capacity(
        usd=primary_funding_capacity_usd,
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
    )
    if settlement.primary_funding_reserved_tokens is not None:
        primary_funding_capacity_tokens = min(
            primary_funding_capacity_tokens,
            _non_negative_int(settlement.primary_funding_reserved_tokens),
        )

    wallet_capacity_tokens = min(
        _non_negative_int(settlement.wallet_available_tokens)
        + _non_negative_int(settlement.wallet_reserved_tokens),
        actual_tokens,
    )

    primary_funding_tokens = min(
        actual_tokens,
        quota_capacity_tokens,
        primary_funding_capacity_tokens,
    )

    wallet_candidate_tokens = max(actual_tokens - primary_funding_tokens, 0)
    wallet_tokens = min(wallet_candidate_tokens, wallet_capacity_tokens)

    residual_after_wallet_tokens = max(wallet_candidate_tokens - wallet_tokens, 0)
    quota_remaining_after_primary = max(quota_capacity_tokens - primary_funding_tokens, 0)
    quota_fallback_tokens = min(residual_after_wallet_tokens, quota_remaining_after_primary)

    quota_tokens = primary_funding_tokens + quota_fallback_tokens
    project_absorption_tokens = residual_after_wallet_tokens

    primary_funding_usd = _tokens_to_usd(
        tokens=primary_funding_tokens,
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
    )
    wallet_usd = _tokens_to_usd(
        tokens=wallet_tokens,
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
    )
    project_absorption_usd = _tokens_to_usd(
        tokens=project_absorption_tokens,
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
    )
    quota_usd_equivalent = _tokens_to_usd(
        tokens=quota_tokens,
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
    )

    return PlanWalletSettlementAllocation(
        actual_tokens=actual_tokens,
        actual_cost_usd=actual_cost_usd,
        quota_capacity_tokens=quota_capacity_tokens,
        primary_funding_capacity_tokens=primary_funding_capacity_tokens,
        wallet_capacity_tokens=wallet_capacity_tokens,
        primary_funding_tokens=primary_funding_tokens,
        wallet_tokens=wallet_tokens,
        project_absorption_tokens=project_absorption_tokens,
        quota_fallback_tokens=quota_fallback_tokens,
        quota_tokens=quota_tokens,
        primary_funding_usd=primary_funding_usd,
        wallet_usd=wallet_usd,
        project_absorption_usd=project_absorption_usd,
        quota_usd_equivalent=quota_usd_equivalent,
    )
