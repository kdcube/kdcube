# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Distinct refresh-purpose signing over the original durable input only."""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Any

from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    OriginalExchangeRefused, canonical,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import (
    REFRESH_SCHEMA, OriginalRefreshReservation, PostgresOriginalRefreshStore,
)


@dataclass(frozen=True)
class HmacOriginalRefreshSigner:
    """Host-bound protected-key resolver; no key is retained in this object.

    The host selects the qualified server-side key source and retains the
    original key through recovery. A changed/unavailable key never authorizes
    a different bearer when the original digest was sealed.
    """
    tenant: str
    project: str
    resolve_key: Callable[[], Awaitable[str | bytes]] = field(repr=False)

    async def sign(self, *, context: PlannedIssuanceContext, claims: Mapping[str, Any]) -> str:
        bound = PlannedIssuanceContext.from_context(context)
        if (bound.slot != "refresh" or (bound.tenant, bound.project) != (self.tenant, self.project)
                or claims.get("schema") != REFRESH_SCHEMA or claims.get("slot") != "refresh"
                or claims.get("transaction_id") != bound.transaction_id
                or claims.get("tenant") != bound.tenant or claims.get("project") != bound.project
                or claims.get("sub") != bound.credential_subject or claims.get("exp") != bound.expires_at):
            raise OriginalExchangeRefused("original_refresh_signing_context_invalid")
        try:
            key = await self.resolve_key()
            key = key.encode("utf-8") if type(key) is str else key
            if type(key) is not bytes or not 32 <= len(key) <= 4096:
                raise ValueError
            payload = canonical(dict(claims)).encode("ascii")
            signature = hmac.new(key, REFRESH_SCHEMA.encode("ascii") + b"\0" + payload, hashlib.sha256).digest()
        except Exception:
            raise OriginalExchangeRefused("original_refresh_signing_unavailable") from None
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
        # Neither this prefix nor this schema is a Bundle access credential.
        return "krt1." + encode(payload) + "." + encode(signature)


class OriginalRefreshIssuer:
    def __init__(self, *, store: PostgresOriginalRefreshStore, custody: Any,
                 signer: HmacOriginalRefreshSigner, card_kind: str, ttl_seconds: int):
        if (type(signer) is not HmacOriginalRefreshSigner
                or (signer.tenant, signer.project) != (store.tenant, store.project)):
            raise OriginalExchangeRefused("original_refresh_namespace_mismatch")
        self.store, self.custody, self.signer = store, custody, signer
        self.card_kind, self.ttl_seconds = card_kind, ttl_seconds

    def _args(self, plan):
        return {"plan": plan, "card_kind": self.card_kind, "ttl_seconds": self.ttl_seconds}

    async def prepare(self, *, plan) -> OriginalRefreshReservation:
        original = await self.store.reserve(**self._args(plan))
        bearer = await self.custody.get(secret_ref=original.secret_ref)
        if bearer is None:
            if original.state == "applied":
                raise OriginalExchangeRefused("original_refresh_custody_missing")
            # Deterministic re-signing uses the first durable signing input,
            # never a new id, timestamp, expiry or refresh generation.
            bearer = await self.signer.sign(context=original.context, claims=original.claims)
            original = await self.store.seal(original, hashlib.sha256(bearer.encode()).hexdigest())
            await self.custody.create(secret_ref=original.secret_ref, value=bearer,
                                      expires_at=original.context.expires_at)
            bearer = await self.custody.get(secret_ref=original.secret_ref)
        if (type(bearer) is not str or not bearer or original.bearer_sha256 is None
                or not hmac.compare_digest(hashlib.sha256(bearer.encode()).hexdigest(), original.bearer_sha256)):
            raise OriginalExchangeRefused("original_refresh_custody_mismatch")
        try:
            return await self.store.seal(original, original.bearer_sha256, ready=True)
        except OriginalExchangeRefused as exc:
            if str(exc) == "original_refresh_terminal":
                # Retirement won while external create/get yielded. Recovery
                # still owns this same reference if purge itself is uncertain.
                await self.custody.delete(secret_ref=original.secret_ref)
            raise

    async def read(self, *, plan) -> OriginalRefreshReservation:
        """No signing, custody or writes; a missing original is never prepared."""
        return await self.store.read(**self._args(plan))

    async def protect_applied(self, *, plan, result) -> None:
        await self.store.protect_applied(**self._args(plan), result=result)

    async def retire(self, *, plan, terminal) -> None:
        reference = await self.store.retire(**self._args(plan), terminal=terminal)
        if reference is not None:
            await self.custody.delete(secret_ref=reference)
