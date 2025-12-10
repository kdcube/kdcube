# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/envelope.py
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.accounting import AccountingSystem, _new_context_with, _context_var, _storage_var, SystemResource
from contextlib import contextmanager

@dataclass
class AccountingEnvelope:
    # core context
    user_id: Optional[str]
    session_id: Optional[str]
    tenant_id: Optional[str]
    project_id: Optional[str]
    request_id: Optional[str]
    component: Optional[str]
    app_bundle_id: Optional[str]
    timezone: Optional[str]

    # optional enrichment you might want to carry
    metadata: Dict[str, Any] = field(default_factory=dict)
    seed_system_resources: List[SystemResource] = field(default_factory=list)
    user_session: UserSession = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # SystemResource is dataclass; make it json-friendly
        d["seed_system_resources"] = [
            {
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "rn": r.rn,
                "resource_version": r.resource_version,
                "metadata": r.metadata,
            } for r in self.seed_system_resources
        ]
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AccountingEnvelope":
        seeds = [
            SystemResource(
                resource_type=s["resource_type"],
                resource_id=s["resource_id"],
                rn=s["rn"],
                resource_version=s.get("resource_version"),
                metadata=s.get("metadata", {}),
            ) for s in (d.get("seed_system_resources") or [])
        ]
        return AccountingEnvelope(
            user_id=d.get("user_id"),
            session_id=d.get("session_id"),
            tenant_id=d.get("tenant_id"),
            project_id=d.get("project_id"),
            request_id=d.get("request_id"),
            component=d.get("component"),
            metadata=d.get("metadata") or {},
            seed_system_resources=seeds,
            app_bundle_id=d.get("app_bundle_id"),
            timezone=d.get("timezone"),
        )

def build_envelope_from_session(session, *, tenant_id,
                                project_id, request_id, component,
                                app_bundle_id=None, metadata=None, seeds=None) -> AccountingEnvelope:
    return AccountingEnvelope(
        user_id=getattr(session, "user_id", None),
        session_id=getattr(session, "session_id", None),
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=request_id,
        component=component,
        metadata=metadata or {},
        seed_system_resources=seeds or [],
        app_bundle_id=app_bundle_id,
        timezone=getattr(session, "timezone", None),
    )

@asynccontextmanager
async def bind_accounting(envelope: AccountingEnvelope, storage_backend, *, enabled: bool = True):
    """
    Init storage + set base context for the current task.
    Clears context on exit.
    """
    AccountingSystem.init_storage(storage_backend, enabled)
    # When we spawn a task with asyncio.create_task(...), the current ContextVars are copied.
    # That copy still references the same AccountingContext object so we set a new one.
    # Create a brand-new AccountingContext for this bind scope
    ctx = _new_context_with(
        user_id=envelope.user_id,
        session_id=envelope.session_id,
        tenant_id=envelope.tenant_id,
        project_id=envelope.project_id,
        request_id=envelope.request_id,
        component=envelope.component,
        app_bundle_id=envelope.app_bundle_id,
        timezone=envelope.timezone
    )
    # Push it with a ContextVar token so we can restore precisely
    ctx_token = _context_var.set(ctx)
    # Optionally also isolate storage if you use different backends per task
    store_token = _storage_var.set(_storage_var.get())  # no-op isolation, or set a specific one
    # seed enrichment
    ctx.event_enrichment = {
        "metadata": dict(envelope.metadata or {}),
        "seed_system_resources": envelope.seed_system_resources or [],
    }
    try:
        yield
    finally:
        # restore previous values atomically
        _context_var.reset(ctx_token)
        _storage_var.reset(store_token)

@contextmanager
def bind_accounting_sync(envelope: AccountingEnvelope, storage_backend, *, enabled: bool = True):
    """
    Same behavior as bind_accounting but for sync code.
    """
    AccountingSystem.init_storage(storage_backend, enabled)
    AccountingSystem.set_context(
        user_id=envelope.user_id,
        session_id=envelope.session_id,
        tenant_id=envelope.tenant_id,
        project_id=envelope.project_id,
        request_id=envelope.request_id,
        component=envelope.component,
        app_bundle_id=envelope.app_bundle_id,
        timezone=envelope.timezone
    )
    from kdcube_ai_app.infra.accounting import _get_context
    _get_context().event_enrichment = {
        "metadata": dict(envelope.metadata or {}),
        "seed_system_resources": envelope.seed_system_resources or [],
    }
    try:
        yield
    finally:
        AccountingSystem.clear_context()