from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.settlement_allocation import (
    PlanWalletSettlementInput,
    allocate_plan_wallet_settlement,
)


def _allocate(
    *,
    quota_available_tokens: int | None,
    quota_reserved_tokens: int = 0,
    primary_funding_available_usd: float | None,
    primary_funding_reserved_usd: float = 0.0,
    primary_funding_reserved_tokens: int | None = None,
    wallet_available_tokens: int = 0,
    wallet_reserved_tokens: int = 0,
    primary_is_separate_budget: bool = False,
):
    return allocate_plan_wallet_settlement(
        PlanWalletSettlementInput(
            actual_tokens=100,
            actual_cost_usd=10.0,
            quota_available_tokens=quota_available_tokens,
            quota_reserved_tokens=quota_reserved_tokens,
            primary_funding_available_usd=primary_funding_available_usd,
            primary_funding_reserved_usd=primary_funding_reserved_usd,
            primary_funding_reserved_tokens=primary_funding_reserved_tokens,
            wallet_available_tokens=wallet_available_tokens,
            wallet_reserved_tokens=wallet_reserved_tokens,
            primary_is_separate_budget=primary_is_separate_budget,
        )
    )


def test_plan_quota_and_primary_funding_cover_full_request():
    allocation = _allocate(
        quota_available_tokens=100,
        primary_funding_available_usd=10.0,
        wallet_available_tokens=100,
    )

    assert allocation.quota_tokens == 100
    assert allocation.primary_funding_tokens == 100
    assert allocation.wallet_tokens == 0
    assert allocation.project_absorption_tokens == 0
    assert allocation.primary_funding_usd == pytest.approx(10.0)


def test_wallet_pays_overflow_when_primary_funding_is_short():
    allocation = _allocate(
        quota_available_tokens=100,
        primary_funding_available_usd=5.0,
        wallet_available_tokens=50,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 50
    assert allocation.project_absorption_tokens == 0
    assert allocation.quota_tokens == 50
    assert allocation.wallet_usd == pytest.approx(5.0)


def test_project_absorbs_wallet_shortfall_and_remaining_quota_is_consumed():
    allocation = _allocate(
        quota_available_tokens=100,
        primary_funding_available_usd=5.0,
        wallet_available_tokens=20,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 20
    assert allocation.project_absorption_tokens == 30
    assert allocation.quota_fallback_tokens == 30
    assert allocation.quota_tokens == 80
    assert allocation.project_total_tokens_when_primary_is_project == 80


def test_wallet_pays_overflow_when_quota_is_short():
    allocation = _allocate(
        quota_available_tokens=50,
        primary_funding_available_usd=10.0,
        wallet_available_tokens=50,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 50
    assert allocation.project_absorption_tokens == 0
    assert allocation.quota_tokens == 50


def test_project_absorbs_wallet_shortfall_when_quota_is_exhausted():
    allocation = _allocate(
        quota_available_tokens=50,
        primary_funding_available_usd=10.0,
        wallet_available_tokens=20,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 20
    assert allocation.project_absorption_tokens == 30
    assert allocation.quota_fallback_tokens == 0
    assert allocation.quota_tokens == 50
    assert allocation.project_absorption_usd == pytest.approx(3.0)


def test_project_absorbs_residual_and_consumes_remaining_quota_when_wallet_absent():
    allocation = _allocate(
        quota_available_tokens=100,
        primary_funding_available_usd=5.0,
        wallet_available_tokens=0,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 0
    assert allocation.project_absorption_tokens == 50
    assert allocation.quota_fallback_tokens == 50
    assert allocation.quota_tokens == 100
    assert allocation.project_total_usd_when_primary_is_project == pytest.approx(10.0)


def test_own_reservations_are_added_to_current_capacities():
    allocation = _allocate(
        quota_available_tokens=20,
        quota_reserved_tokens=30,
        primary_funding_available_usd=2.0,
        primary_funding_reserved_usd=3.0,
        wallet_available_tokens=0,
        wallet_reserved_tokens=50,
    )

    assert allocation.quota_capacity_tokens == 50
    assert allocation.primary_funding_capacity_tokens == 50
    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 50
    assert allocation.quota_tokens == 50


def test_plan_project_share_is_capped_by_pre_run_reservation_split():
    allocation = allocate_plan_wallet_settlement(
        PlanWalletSettlementInput(
            actual_tokens=55_833,
            actual_cost_usd=0.83750078,
            quota_available_tokens=102_095,
            quota_reserved_tokens=115_943,
            primary_funding_available_usd=0.20,
            primary_funding_reserved_usd=0.67,
            primary_funding_reserved_tokens=38_559,
            wallet_available_tokens=55_949,
            wallet_reserved_tokens=77_384,
        )
    )

    assert allocation.primary_funding_tokens == 38_559
    assert allocation.wallet_tokens == 17_274
    assert allocation.project_absorption_tokens == 0
    assert allocation.quota_tokens == 38_559
    assert allocation.wallet_usd == pytest.approx(0.2591, rel=1e-3)


def test_zero_pre_run_plan_share_keeps_actual_spend_on_wallet_first():
    allocation = _allocate(
        quota_available_tokens=100,
        primary_funding_available_usd=10.0,
        primary_funding_reserved_tokens=0,
        wallet_available_tokens=100,
    )

    assert allocation.primary_funding_tokens == 0
    assert allocation.wallet_tokens == 100
    assert allocation.project_absorption_tokens == 0
    assert allocation.quota_tokens == 0


def test_unlimited_quota_still_respects_primary_funding_before_wallet():
    allocation = _allocate(
        quota_available_tokens=None,
        primary_funding_available_usd=5.0,
        wallet_available_tokens=20,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 20
    assert allocation.project_absorption_tokens == 30
    assert allocation.quota_fallback_tokens == 30
    assert allocation.quota_tokens == 80


def test_separate_primary_budget_covers_overquota_remainder_before_project():
    # Model S4: subscription primary, quota cap 60, wallet short by 10. The
    # over-quota remainder the wallet cannot cover falls back to the primary's
    # OWN remaining funds (it has ample balance), NOT the project.
    allocation = _allocate(
        quota_available_tokens=60,
        primary_funding_available_usd=50.0,          # ample primary funds
        primary_funding_reserved_usd=6.0,
        primary_funding_reserved_tokens=60,          # pre-run split: 60 plan / 40 wallet
        wallet_available_tokens=30,                  # wallet short: covers 30 of the 40 overflow
        primary_is_separate_budget=True,
    )

    assert allocation.primary_funding_tokens == 60   # quota-capped
    assert allocation.wallet_tokens == 30
    assert allocation.primary_overage_tokens == 10   # subscription covers the wallet shortfall
    assert allocation.project_absorption_tokens == 0  # project untouched
    assert allocation.primary_overage_usd == pytest.approx(1.0)
    assert allocation.project_absorption_usd == pytest.approx(0.0)
    # primary funds left after the planned split (real cap 100 - 60 funded - 10 overage):
    # a runtime wallet shortfall would draw from this before the project.
    assert allocation.primary_overage_headroom_tokens == 30


def test_project_absorbs_only_when_separate_primary_budget_is_exhausted():
    # Model S5: subscription primary funds exhausted (capacity == quota share),
    # wallet absent -> the project absorbs the over-quota remainder.
    allocation = _allocate(
        quota_available_tokens=50,
        primary_funding_available_usd=5.0,           # only enough for the 50-token quota share
        primary_funding_reserved_tokens=50,
        wallet_available_tokens=0,
        primary_is_separate_budget=True,
    )

    assert allocation.primary_funding_tokens == 50
    assert allocation.wallet_tokens == 0
    assert allocation.primary_overage_tokens == 0    # primary has no funds beyond its quota share
    assert allocation.project_absorption_tokens == 50
    assert allocation.project_absorption_usd == pytest.approx(5.0)
    assert allocation.primary_overage_headroom_tokens == 0  # no headroom for a runtime shortfall either


def test_project_primary_never_routes_overage_to_primary_overage():
    # Same shape as S4 but project-primary (separate_budget off): the over-quota
    # remainder stays project absorption, byte-identical to the prior behavior.
    allocation = _allocate(
        quota_available_tokens=60,
        primary_funding_available_usd=50.0,
        primary_funding_reserved_usd=6.0,
        primary_funding_reserved_tokens=60,
        wallet_available_tokens=30,
        primary_is_separate_budget=False,
    )

    assert allocation.primary_funding_tokens == 60
    assert allocation.wallet_tokens == 30
    assert allocation.primary_overage_tokens == 0
    assert allocation.project_absorption_tokens == 10


def test_zero_actual_tokens_produces_empty_allocation():
    allocation = allocate_plan_wallet_settlement(
        PlanWalletSettlementInput(
            actual_tokens=0,
            actual_cost_usd=10.0,
            quota_available_tokens=100,
            primary_funding_available_usd=10.0,
            wallet_available_tokens=100,
        )
    )

    assert allocation.quota_tokens == 0
    assert allocation.primary_funding_tokens == 0
    assert allocation.wallet_tokens == 0
    assert allocation.project_absorption_tokens == 0
