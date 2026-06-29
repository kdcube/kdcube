# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase-2 wiring tests: automation execution -> economic_preflight (verify + log).

Automation definitions carry the user identity, verify economic feasibility of
the pipeline, and log the economic limits. This is VERIFY-ONLY (no reserve/settle)
— the ReAct work routes through self.run() which meters the real cost under the
same user. `economic_preflight` is monkeypatched to drive allow/deny.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.automations import operations as ops
from kdcube_ai_app.apps.chat.sdk.identity_authority import normalize_execution_authority
from kdcube_ai_app.apps.chat.sdk.infra.economics import enforcement as enf
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException


class _Spec:
    id = "task-and-memo-app@1-0"


class _Config:
    tenant = "t"
    project = "p"
    ai_bundle_spec = _Spec()


class _FakeUser:
    def __init__(self, user_id=None, user_type=None):
        self.user_id = user_id
        self.user_type = user_type
        self.timezone = None
        self.roles = []
        self.permissions = []


class _FakeCtx:
    """Minimal comm_context with model_copy (stands in for the pydantic ctx)."""

    def __init__(self, user_type="registered"):
        self.routing = SimpleNamespace(session_id=None, conversation_id=None, turn_id=None)
        self.user = _FakeUser(user_type=user_type)
        self.actor = SimpleNamespace(tenant_id="t", project_id="p")
        self.bundle_call_context = None

    def model_copy(self, deep=False):
        return copy.deepcopy(self)


class _StubEP:
    """Minimal stand-in for an automation entrypoint for economics wiring tests."""

    def __init__(self, *, economics=True, reservation=0.50, pg_pool=None, user_type=None):
        self.cp_manager = object() if economics else None
        self.rl = object() if economics else None
        self.budget_limiter = object() if economics else None
        self.pg_pool = pg_pool
        self.config = _Config()
        self.settings = SimpleNamespace(TENANT="t", PROJECT="p")
        self.logger = None
        self.comm = None
        self._reservation = reservation
        self.comm_context = (
            SimpleNamespace(user=SimpleNamespace(user_type=user_type, timezone=None))
            if user_type is not None
            else None
        )

    def bundle_prop(self, path, default=None):
        if path == "economics.automation.reservation_amount_dollars":
            return self._reservation
        return default


def test_automation_economics_enabled_flag():
    assert ops._automation_economics_enabled(_StubEP(economics=True)) is True
    assert ops._automation_economics_enabled(_StubEP(economics=False)) is False


def test_automation_reservation_usd_from_config_and_default():
    assert ops._automation_reservation_usd(_StubEP(reservation=1.25)) == 1.25
    assert ops._automation_reservation_usd(_StubEP(reservation="nope")) == 0.50


def test_execution_authority_roles_promote_user_type():
    authority = normalize_execution_authority(
        {"user_type": "registered", "platform_roles": ["kdcube:role:super-admin"]},
        actor_user_id="telegram_42",
        economics_user_id="platform-user-1",
    )
    assert authority["actor_user_id"] == "telegram_42"
    assert authority["economics_user_id"] == "platform-user-1"
    assert authority["user_type"] == "privileged"
    assert authority["economics_user_type"] == "privileged"


async def test_automation_subject_does_not_treat_carried_user_type_as_authority():
    ep = _StubEP(pg_pool=None)
    subj = await ops._automation_econ_subject(ep, target_user="u1", source={"user_type": "privileged"})
    assert (subj.tenant, subj.project, subj.user_id) == ("t", "p", "u1")
    assert subj.budget_bypass is None
    assert subj.is_anonymous is False


async def test_automation_subject_can_use_platform_authority_user():
    ep = _StubEP(pg_pool=None)
    subj = await ops._automation_econ_subject(
        ep,
        target_user="telegram_42",
        source={
            "economics_user_id": "platform-user-1",
            "economics_user_type": "privileged",
            "platform_roles": ["kdcube:role:super-admin"],
        },
    )
    assert (subj.tenant, subj.project, subj.user_id) == ("t", "p", "platform-user-1")
    assert subj.budget_bypass is True
    assert subj.is_anonymous is False


async def test_automation_subject_falls_back_to_registered():
    ep = _StubEP(pg_pool=None)
    subj = await ops._automation_econ_subject(ep, target_user="u1", source={})
    assert subj.budget_bypass is None
    assert subj.is_anonymous is False


# --------------------------------------------------------------------------
# Verify (economic_preflight) — verify-only, no reserve/settle
# --------------------------------------------------------------------------
async def test_verify_economics_disabled_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("preflight must not run when economics disabled")

    monkeypatch.setattr(enf, "economic_preflight", _boom)
    subj, dec = await ops._automation_verify_economics(_StubEP(economics=False), target_user="u1", source={})
    assert (subj, dec) == (None, None)


async def test_verify_economics_preflight_ok(monkeypatch):
    seen = {}

    async def _pf(entrypoint, *, subject, estimate, flow, policy=None):
        seen["flow"] = flow
        seen["budget_bypass"] = subject.budget_bypass
        seen["is_anonymous"] = subject.is_anonymous
        seen["res"] = estimate.reservation_usd
        seen["concurrency"] = policy.enforce_concurrency
        return SimpleNamespace(lane="plan", plan_id="free", funding_source="project", est_turn_usd=0.5, admit=None)

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    ep = _StubEP(economics=True, reservation=0.5)
    subj, dec = await ops._automation_verify_economics(ep, target_user="u1", source={"user_type": "paid"})
    assert subj.budget_bypass is None
    assert subj.is_anonymous is False
    assert dec.funding_source == "project"
    assert seen == {
        "flow": "automations",
        "budget_bypass": None,
        "is_anonymous": False,
        "res": 0.5,
        "concurrency": False,
    }


async def test_verify_economics_denied_raises(monkeypatch):
    async def _pf(*a, **k):
        raise EconomicsLimitException("rate limited", code="rate_limited")

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    with pytest.raises(EconomicsLimitException):
        await ops._automation_verify_economics(_StubEP(economics=True), target_user="u1", source={})


def test_automation_economics_metadata_logs_limits():
    admit = SimpleNamespace(snapshot={"tok_month": 100, "req_day": 5})
    decision = SimpleNamespace(
        lane="plan", plan_id="free", funding_source="project", est_turn_usd=0.5, admit=admit,
    )
    meta = ops._automation_economics_metadata(decision)
    assert meta["economics"]["verified"] is True
    assert meta["economics"]["funding_source"] == "project"
    assert meta["economics"]["limits"] == {"tok_month": 100, "req_day": 5}
    assert ops._automation_economics_metadata(None) == {}


# --------------------------------------------------------------------------
# Point (2): resolved role propagated into the scoped context for inner run()
# --------------------------------------------------------------------------
def test_scoped_context_propagates_resolved_role():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_automation_scoped_context(
        ep, target_user="u1", run_conversation_id="conv", turn_id="turn_e1",
        bundle_call_context={}, resolved_user_type="paid",
    )
    assert ctx.user.user_id == "u1"
    assert ctx.user.user_type == "paid"  # inner run() now bills the correct plan


def test_scoped_context_keeps_actor_and_carries_platform_roles():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_automation_scoped_context(
        ep,
        target_user="telegram_42",
        run_conversation_id="conv",
        turn_id="turn_e1",
        bundle_call_context={
            "source": {
                "economics_user_id": "platform-user-1",
                "economics_user_type": "privileged",
                "platform_roles": ["kdcube:role:super-admin"],
            }
        },
        resolved_user_type="privileged",
    )
    assert ctx.user.user_id == "telegram_42"
    assert ctx.user.user_type == "privileged"
    assert ctx.user.roles == ["kdcube:role:super-admin"]


def test_scoped_context_prefers_cross_runtime_identity_authority():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_automation_scoped_context(
        ep,
        target_user="telegram_42",
        run_conversation_id="conv",
        turn_id="turn_e1",
        bundle_call_context={
            "source": {"user_type": "registered"},
            "identity_authority": {
                "actor_user_id": "telegram_42",
                "economics_user_id": "platform-user-1",
                "user_type": "privileged",
                "platform_roles": ["kdcube:role:super-admin"],
                "platform_permissions": ["kdcube:*:chat:*;read;write"],
            },
        },
        resolved_user_type=None,
    )
    assert ctx.user.user_id == "telegram_42"
    assert ctx.user.user_type == "privileged"
    assert ctx.user.roles == ["kdcube:role:super-admin"]
    assert ctx.user.permissions == ["kdcube:*:chat:*;read;write"]


def test_scoped_context_keeps_role_when_unresolved():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_automation_scoped_context(
        ep, target_user="u1", run_conversation_id="conv", turn_id="turn_e1",
        bundle_call_context={}, resolved_user_type=None,
    )
    assert ctx.user.user_type == "registered"  # unchanged
