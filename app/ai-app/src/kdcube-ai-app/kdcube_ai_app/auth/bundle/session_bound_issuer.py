# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Recover one fixed session through durable reservation and secret custody."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from kdcube_ai_app.auth.bundle.session_issuance import (
    IssuanceContext,
    SessionIssuanceReceipt,
    SessionIssuanceRefused,
)


class IssuanceSecretCustody(Protocol):
    """A host-injected durable, authorized create-only secret store."""

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool: ...
    async def get(self, *, secret_ref: str) -> str | None: ...
    async def delete(self, *, secret_ref: str) -> None: ...


def _valid_text(value: object, *, maximum_bytes: int) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        return (len(value.encode("utf-8")) <= maximum_bytes
                and not any(ord(character) < 32 or ord(character) == 127 for character in value))
    except UnicodeError:
        return False


def _authority_values(values: Sequence[str]) -> list[str]:
    if (not isinstance(values, (list, tuple)) or len(values) > 256
            or any(not _valid_text(value, maximum_bytes=2048) for value in values)):
        raise SessionIssuanceRefused("issuance_authority_invalid")
    return sorted(set(values))


async def _store_call(call: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    try:
        return await call(*args, **kwargs)
    except SessionIssuanceRefused:
        raise
    except Exception:
        # A write may have committed even when its response was lost. A named
        # unavailable outcome does not authorize a fresh identity on retry.
        raise SessionIssuanceRefused("issuance_store_unavailable") from None


async def _signed(sign: Callable[[Mapping[str, Any]], Awaitable[str]], claims: Mapping[str, Any]) -> str:
    try:
        return await sign(claims)
    except Exception:
        raise SessionIssuanceRefused("issuance_signing_unavailable") from None


def _inputs_digest(bound: Any, *, user_id: str, roles: Sequence[str],
                   permissions: Sequence[str]) -> str:
    """One canonical input contract for preparation and read-only recovery."""
    if not _valid_text(user_id, maximum_bytes=1024):
        raise SessionIssuanceRefused("issuance_user_invalid")
    return hashlib.sha256(json.dumps({
        "context": bound.to_record(), "user_id": user_id,
        "roles": _authority_values(roles), "permissions": _authority_values(permissions),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


async def _custody_call(call: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
    try:
        return await call(**kwargs)
    except SessionIssuanceRefused as exc:
        if exc.reason in {"issuance_custody_invalid", "issuance_custody_expired",
                          "issuance_custody_not_durable"}:
            raise SessionIssuanceRefused(exc.reason) from None
        raise SessionIssuanceRefused("issuance_custody_unavailable") from None
    except Exception:
        raise SessionIssuanceRefused("issuance_custody_unavailable") from None


async def issue_bound_session(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    custody: IssuanceSecretCustody,
    sign: Callable[[Mapping[str, Any]], Awaitable[str]],
) -> SessionIssuanceReceipt:
    bound = IssuanceContext.from_context(context)
    reservation = await _prepare_bound_session(
        bound, tenant=tenant, project=project, store=store, user_id=user_id,
        roles=roles, permissions=permissions, custody=custody, sign=sign,
    )
    active = await _store_call(store.activate_reserved, bound.identity)
    return SessionIssuanceReceipt(
        session_id=active.session_id, secret_ref=active.secret_ref,
        bearer_sha256=active.record["token_sha256"],
        outcome="issued" if reservation.created else "recovered",
    ).validated()


async def _prepare_bound_session(
    bound: Any, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    custody: IssuanceSecretCustody,
    sign: Callable[[Mapping[str, Any]], Awaitable[str]], planned: bool = False,
) -> Any:
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not _valid_text(user_id, maximum_bytes=1024):
        raise SessionIssuanceRefused("issuance_user_invalid")
    granted_roles = _authority_values(roles)
    granted_permissions = _authority_values(permissions)
    fingerprint = _inputs_digest(bound, user_id=user_id, roles=granted_roles,
                                 permissions=granted_permissions)
    if (store is None or not all(callable(getattr(store, name, None)) for name in
            ("read_issuance", "reserve_issuance", "activate_reserved", "get_login_state"))):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    if not all(callable(getattr(custody, name, None)) for name in ("create", "get")):
        raise SessionIssuanceRefused("issuance_custody_unavailable")

    reservation = await _store_call(store.read_issuance, bound.identity)
    if reservation is not None and reservation.inputs_digest != fingerprint:
        # This comes before user/key/custody access and before any mutation.
        raise SessionIssuanceRefused("issuance_identity_conflict")
    if bound.expires_at <= int(time.time()):
        raise SessionIssuanceRefused("issuance_expired")
    if planned:
        if reservation is not None and (
            reservation.delivery_deadline != bound.delivery_deadline
            or reservation.reserved_until != bound.reserved_until
            or reservation.expires_at != bound.expires_at
            or reservation.record.get("issuance_plan") != bound.to_record()
        ):
            raise SessionIssuanceRefused("issuance_identity_conflict")
        if bound.delivery_deadline <= int(time.time()) or bound.reserved_until <= int(time.time()):
            raise SessionIssuanceRefused("issuance_delivery_expired")
        if user_id != bound.credential_subject:
            raise SessionIssuanceRefused("issuance_user_invalid")
        if reservation is not None:
            # Reuse the original reservation under its identity lock, checking
            # PostgreSQL time even when no new reservation is needed.
            reservation = await _store_call(
                store.reserve_issuance, bound.identity, fingerprint,
                reservation.session_id, reservation.secret_ref, reservation.expires_at,
                session_record=reservation.record, expected_version=reservation.expected_version,
                user_record=reservation.user_record,
                delivery_deadline=bound.delivery_deadline, reserved_until=bound.reserved_until,
            )
    candidate = None
    if reservation is None:
        login = await _store_call(store.get_login_state, user_id)
        version = login.version if login is not None else 1
        user_revision = login.user_revision if login is not None else 0
        if login is not None and login.user.get("disabled"):
            raise SessionIssuanceRefused("issuance_authority_moved")
        now = int(time.time())
        sid = "bsn_" + uuid.uuid4().hex
        secret_ref = uuid.uuid4().hex
        provider = "integration" if user_id.startswith("integration:") else None
        claims = {
            "schema": "kdcube.session_token.v1", "iss": "kdcube-bundle-session",
            "sid": sid, "sub": user_id, "provider": provider,
            "provider_subject": None, "ver": version, "iat": now, "exp": bound.expires_at,
        }
        candidate = await _signed(sign, claims)
        record = {
            "schema": claims["schema"], "session_id": sid, "sub": user_id,
            "provider": provider, "provider_subject": None,
            "token_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            "version": version, "active": True, "metadata": {}, "claims": claims,
            "iat": now, "exp": bound.expires_at, "max_exp": bound.expires_at,
            "last_seen": now,
        }
        if planned:
            record["issuance_plan"] = bound.to_record()
        reservation = await _store_call(
            store.reserve_issuance,
            bound.identity, fingerprint, sid, secret_ref, bound.expires_at,
            session_record=record, expected_version=version,
            expected_user_revision=user_revision,
            user_record={
                "sub": user_id, "username": user_id, "provider": provider,
                "provider_subject": None, "roles": granted_roles,
                "permissions": granted_permissions, "disabled": False,
                "created_at": now, "updated_at": now,
            },
            **({"delivery_deadline": bound.delivery_deadline,
                "reserved_until": bound.reserved_until} if planned else {}),
        )
        if reservation.session_id != sid:
            candidate = None  # a concurrent reservation owns the original

    original = await _custody_call(custody.get, secret_ref=reservation.secret_ref)
    if original is None:
        if reservation.state == "active":
            raise SessionIssuanceRefused("issuance_custody_missing")
        if candidate is None:
            # The original signed input was reserved before activation. A
            # restart re-signs exactly that input, never new claims or an id.
            candidate = await _signed(sign, reservation.record["claims"])
        if hashlib.sha256(candidate.encode("utf-8")).hexdigest() != reservation.record["token_sha256"]:
            raise SessionIssuanceRefused("issuance_custody_unrecoverable")
        await _custody_call(
            custody.create, secret_ref=reservation.secret_ref, value=candidate,
            expires_at=reservation.expires_at,
        )
        # A false create or an uncertain concurrent outcome must read the
        # winner. No candidate bearer is trusted just because we made it.
        original = await _custody_call(custody.get, secret_ref=reservation.secret_ref)
    if type(original) is not str or not original:
        raise SessionIssuanceRefused("issuance_custody_missing")
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != reservation.record["token_sha256"]:
        raise SessionIssuanceRefused("issuance_custody_mismatch")

    if planned:
        # Retirement can commit while an external custody write is in flight.
        # An absent-reference provider delete may not fence that first create.
        # Recheck the durable no-mint record before returning preparation, and
        # retire any late original value. If this process dies before the
        # recheck, the terminal retry still owns this exact reference.
        try:
            await _store_call(store.read_issuance, bound.identity)
        except SessionIssuanceRefused as exc:
            if exc.reason == "issuance_terminal":
                delete = getattr(custody, "delete", None)
                if not callable(delete):
                    raise SessionIssuanceRefused("issuance_custody_unavailable") from None
                await _custody_call(delete, secret_ref=reservation.secret_ref)
            raise
    return reservation
