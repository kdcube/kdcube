# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Phase-2 wiring tests: task execution -> economic_preflight (verify + log).

Task definition: tasks carry the user identity, verify economic feasibility of
the pipeline, and log the economic limits. This is VERIFY-ONLY (no reserve/settle)
— the ReAct work routes through self.run() which meters the real cost under the
same user. `economic_preflight` is monkeypatched to drive allow/deny.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.tasks import operations as ops
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
    """Minimal stand-in for a task entrypoint for economics wiring tests."""

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
        if path == "economics.task.reservation_amount_dollars":
            return self._reservation
        return default


def test_task_economics_enabled_flag():
    assert ops._task_economics_enabled(_StubEP(economics=True)) is True
    assert ops._task_economics_enabled(_StubEP(economics=False)) is False


def test_task_reservation_usd_from_config_and_default():
    assert ops._task_reservation_usd(_StubEP(reservation=1.25)) == 1.25
    assert ops._task_reservation_usd(_StubEP(reservation="nope")) == 0.50


async def test_task_subject_uses_carried_role_when_no_pg():
    ep = _StubEP(pg_pool=None)
    subj = await ops._task_econ_subject(ep, target_user="u1", source={"user_type": "privileged"})
    assert (subj.tenant, subj.project, subj.user_id) == ("t", "p", "u1")
    assert subj.user_type == "privileged"  # privileged preserved without DB re-resolve


async def test_task_subject_falls_back_to_registered():
    ep = _StubEP(pg_pool=None)
    subj = await ops._task_econ_subject(ep, target_user="u1", source={})
    assert subj.user_type == "registered"


# --------------------------------------------------------------------------
# Verify (economic_preflight) — verify-only, no reserve/settle
# --------------------------------------------------------------------------
async def test_verify_economics_disabled_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("preflight must not run when economics disabled")

    monkeypatch.setattr(enf, "economic_preflight", _boom)
    subj, dec = await ops._task_verify_economics(_StubEP(economics=False), target_user="u1", source={})
    assert (subj, dec) == (None, None)


async def test_verify_economics_preflight_ok(monkeypatch):
    seen = {}

    async def _pf(entrypoint, *, subject, estimate, flow, policy=None):
        seen["flow"] = flow
        seen["role"] = subject.user_type
        seen["res"] = estimate.reservation_usd
        seen["concurrency"] = policy.enforce_concurrency
        return SimpleNamespace(lane="plan", plan_id="free", funding_source="project", est_turn_usd=0.5, admit=None)

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    ep = _StubEP(economics=True, reservation=0.5)
    subj, dec = await ops._task_verify_economics(ep, target_user="u1", source={"user_type": "paid"})
    assert subj.user_type == "paid"
    assert dec.funding_source == "project"
    assert seen == {"flow": "tasks", "role": "paid", "res": 0.5, "concurrency": False}


async def test_verify_economics_denied_raises(monkeypatch):
    async def _pf(*a, **k):
        raise EconomicsLimitException("rate limited", code="rate_limited")

    monkeypatch.setattr(enf, "economic_preflight", _pf)
    with pytest.raises(EconomicsLimitException):
        await ops._task_verify_economics(_StubEP(economics=True), target_user="u1", source={})


def test_task_economics_metadata_logs_limits():
    admit = SimpleNamespace(snapshot={"tok_month": 100, "req_day": 5})
    decision = SimpleNamespace(
        lane="plan", plan_id="free", funding_source="project", est_turn_usd=0.5, admit=admit,
    )
    meta = ops._task_economics_metadata(decision)
    assert meta["economics"]["verified"] is True
    assert meta["economics"]["funding_source"] == "project"
    assert meta["economics"]["limits"] == {"tok_month": 100, "req_day": 5}
    assert ops._task_economics_metadata(None) == {}


# --------------------------------------------------------------------------
# Point (2): resolved role propagated into the scoped context for inner run()
# --------------------------------------------------------------------------
def test_scoped_context_propagates_resolved_role():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_task_scoped_context(
        ep, target_user="u1", run_conversation_id="conv", turn_id="turn_e1",
        bundle_call_context={}, resolved_user_type="paid",
    )
    assert ctx.user.user_id == "u1"
    assert ctx.user.user_type == "paid"  # inner run() now bills the correct plan


def test_scoped_context_keeps_role_when_unresolved():
    ep = _StubEP(economics=True)
    ep.comm_context = _FakeCtx(user_type="registered")
    ctx = ops._build_task_scoped_context(
        ep, target_user="u1", run_conversation_id="conv", turn_id="turn_e1",
        bundle_call_context={}, resolved_user_type=None,
    )
    assert ctx.user.user_type == "registered"  # unchanged
