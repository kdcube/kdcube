# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/rate_limit/policy.py
from dataclasses import dataclass
from typing import Optional, Dict

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

@dataclass
class PolicyTable:
    by_user_type: Dict[str, QuotaPolicy]
    default: Optional[QuotaPolicy] = None

    def for_user_type(self, user_type: Optional) -> Optional[QuotaPolicy]:
        if user_type and user_type in self.by_user_type:
            return self.by_user_type[user_type]
        return self.default
