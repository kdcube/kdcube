# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/infra/economics/rate_limit/policy.py
from dataclasses import dataclass
from typing import Optional, Dict

"""
User quota policy (track tokens/requests).
"""

@dataclass(frozen=True)
class QuotaPolicy:
    # concurrency (per subject)
    max_concurrent: int = 1
    # request quotas
    requests_per_day: Optional[int] = None
    requests_per_month: Optional[int] = None
    total_requests: Optional[int] = None
    # token quotas (post-paid check; enforced against *previous* committed turns at admit time)
    tokens_per_hour: Optional[int] = None
    tokens_per_day: Optional[int] = None
    tokens_per_month: Optional[int] = None

    def effective_allowed_tokens(self):
        if self.tokens_per_hour is not None:
            return self.tokens_per_hour, "hour"
        if self.tokens_per_day is not None:
            return self.tokens_per_day, "day"
        if self.tokens_per_month is not None:
            return self.tokens_per_month, "month"
        return None, None

@dataclass
class PolicyTable:
    by_user_type: Dict[str, QuotaPolicy]
    default: Optional[QuotaPolicy] = None

    def for_user_type(self, user_type: Optional) -> Optional[QuotaPolicy]:
        if user_type and user_type in self.by_user_type:
            return self.by_user_type[user_type]
        return self.default

"""
Application-level budget policies for providers.
Track USD spending per provider across all users.
"""

@dataclass(frozen=True)
class ProviderBudgetPolicy:
    """
    Budget limits for a single provider (anthropic, openai, brave, etc.).

    All limits are in USD.
    None = unlimited for that dimension.
    """
    provider: str

    # Spending limits in USD
    usd_per_hour: Optional[float] = None
    usd_per_day: Optional[float] = None
    usd_per_month: Optional[float] = None

    def __post_init__(self):
        # Validate that at least one limit is set
        if all(x is None for x in [self.usd_per_hour, self.usd_per_day, self.usd_per_month]):
            # All None = unlimited, which is valid
            pass

        # Validate positive values
        for attr in ['usd_per_hour', 'usd_per_day', 'usd_per_month']:
            val = getattr(self, attr)
            if val is not None and val < 0:
                raise ValueError(f"{attr} must be non-negative, got {val}")


@dataclass(frozen=True)
class BudgetInsight:
    """
    Application-level budget status for a provider.

    Similar to QuotaInsight but for USD spending across all users.
    """
    provider: str
    limits: dict[str, Optional[float]]      # {usd_per_hour, usd_per_day, usd_per_month}
    remaining: dict[str, Optional[float]]   # remaining USD in each window
    violations: list[str]                    # e.g., ["usd_per_day", "usd_per_month"]
    retry_after_sec: Optional[int]
    retry_scope: Optional[str]               # "hour" | "day" | "month" | None


def estimate_turn_token_upper_bound(policy: QuotaPolicy) -> int:
    """
    Heuristic upper bound used ONLY for personal-credit reservations.
    We want to block obvious oversubscription, not perfectly predict usage.

    Tune if needed.
    """
    candidates = [100_000]

    if policy.tokens_per_hour:
        candidates.append(min(int(policy.tokens_per_hour), 1_000_000))

    if policy.tokens_per_day and policy.requests_per_day and policy.requests_per_day > 0:
        candidates.append(int(policy.tokens_per_day // max(int(policy.requests_per_day), 1)))

    if policy.tokens_per_month and policy.requests_per_month and policy.requests_per_month > 0:
        candidates.append(int(policy.tokens_per_month // max(int(policy.requests_per_month), 1)))

    # keep within a sane ceiling
    return max(50_000, min(max(candidates), 1_500_000))

def paid_policy(base_policy: QuotaPolicy, tier_balance) -> QuotaPolicy:
    # Concurrency still enforced (shared across both lanes)
    maxc = int(base_policy.max_concurrent or 1)
    if tier_balance and tier_balance.max_concurrent is not None:
        maxc = max(maxc, int(tier_balance.max_concurrent))

    return QuotaPolicy(
        max_concurrent=maxc,
        requests_per_day=None,
        requests_per_month=None,
        total_requests=None,
        tokens_per_hour=None,
        tokens_per_day=None,
        tokens_per_month=None,
    )

class EconomicsLimitException(RuntimeError):
    def __init__(self, message: str, *, code: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}
