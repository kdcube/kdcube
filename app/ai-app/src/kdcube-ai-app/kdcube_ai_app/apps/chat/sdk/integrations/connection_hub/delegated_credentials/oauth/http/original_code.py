# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Original-pair HTTP delivery through a trusted, request-bound host capability.

The hosting app binds the exchange implementation, durable ledger, original
decision readers, live target fence and qualified custody. HTTP supplies only
the code/client/redirect/PKCE proof. This transport neither mints credentials
nor authenticates caller-supplied plans or results.
"""
from __future__ import annotations

import hmac
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Any

from starlette.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    CodeExchangeProof, OriginalExchangeRefused, hex_digest, text,
)


LOGGER = logging.getLogger("kdcube.connection_hub.oauth")
# A finite reason code (``original_exchange_candidate_mismatch``): a known prefix, then letters-only
# segments. Free text may carry provider details, and a bare lowercase token (hex, a key) must never
# pass as a code, so digits and unknown prefixes are refused.
_REASON_CODE = re.compile(r"(?:original_exchange|oauth|issuance|card)(?:_[a-z]{1,24}){1,8}")


def _reason(exc: BaseException) -> str:
    """The exception's class and, only when it is a bare reason code, its message; never free text."""
    message = str(exc)
    return type(exc).__name__ + (f":{message}" if _REASON_CODE.fullmatch(message) else "")


def _unavailable(reason: str) -> JSONResponse:
    """W929: every unavailable answer names its cause in the log, so a deterministic failure is visible."""
    LOGGER.error("[connection-hub.oauth] original code exchange unavailable reason=%s", reason)
    return _error("temporarily_unavailable", 503)


def oauth_tenant_project(request: Any) -> tuple[str, str]:
    config = oauth_delegated_config(request)
    return config.tenant, config.project


@dataclass(frozen=True)
class OriginalTokenPair:
    """Private delivery value; original authorization is verified by the host."""

    proof_fingerprint: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: int
    delivery_deadline: int
    scopes: tuple[str, ...]
    access_id: str
    card_kind: str

    def response(self, proof: CodeExchangeProof) -> dict[str, Any]:
        hex_digest(self.proof_fingerprint)
        if not hmac.compare_digest(self.proof_fingerprint, proof.fingerprint):
            raise OriginalExchangeRefused("original_exchange_proof_mismatch")
        for value in (self.access_token, self.refresh_token):
            text(value, 16384)
        for value in (self.access_id, self.card_kind):
            text(value)
        now = int(time.time())
        if any(type(value) is not int or value <= now for value in
               (self.access_expires_at, self.delivery_deadline)):
            raise OriginalExchangeRefused("original_exchange_delivery_expired")
        if type(self.scopes) is not tuple:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        for value in self.scopes:
            text(value)
            if any(char.isspace() for char in value):
                raise OriginalExchangeRefused("original_exchange_binding_invalid")
        if len(set(self.scopes)) != len(self.scopes):
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        return {
            "access_token": self.access_token, "refresh_token": self.refresh_token,
            "token_type": "Bearer", "expires_in": self.access_expires_at - now,
            "scope": " ".join(self.scopes), "access_id": self.access_id, "card_kind": self.card_kind,
        }


@dataclass(frozen=True)
class OriginalCodeExchangeHandler:
    """Server-owned composition capability, never constructed from request data.

    exchange(proof=..., code=...) must validate a first live consumed code or
    read its matching durable mapping, authenticate the complete original Hub
    plan/result, fence the live target and recover both original bearers from
    qualified custody. Pending/uncertain outcomes must raise without reminting.
    The returned pair is data; its type alone grants no issuance authority.
    """

    tenant: str
    project: str
    exchange: Callable[..., Awaitable[OriginalTokenPair]] = field(repr=False)


def _error(error: str, status: int) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": "Original credential exchange unavailable."
                        if status == 503 else "Original credential exchange refused."},
                        status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def original_authorization_code_response(request: Any, form: Mapping[str, Any]) -> JSONResponse | None:
    """Select only a host-bound capability; an invalid configured one is closed.

    Absent bindings retain the existing workflow during source adoption. Once
    bound, neither errors nor missing original results fall back to consume or
    ordinary minting. Request-local bindings take precedence over app scope.
    """
    factory = None
    present = False
    for state in (getattr(request, "state", None), getattr(getattr(request, "app", None), "state", None)):
        if state is not None and hasattr(state, "oauth_original_exchange_factory"):
            factory = getattr(state, "oauth_original_exchange_factory")
            present = True
            break
    if not present:
        return None
    if not callable(factory):
        # The host keeps its binding present and closed when it could not compose it (or it is None).
        return _unavailable("original_exchange_not_bound")
    try:
        bound = factory()
        tenant, project = oauth_tenant_project(request)
        if (type(bound) is not OriginalCodeExchangeHandler or not callable(bound.exchange)
                or bound.tenant != tenant or bound.project != project):
            return _unavailable("original_exchange_handler_invalid")
        proof = CodeExchangeProof.from_request(
            tenant=tenant, project=project, code=form.get("code"), client_id=form.get("client_id"),
            redirect_uri=form.get("redirect_uri"), verifier=form.get("code_verifier"),
        )
        pair = await bound.exchange(proof=proof, code=form.get("code"))
        if type(pair) is not OriginalTokenPair:
            return _unavailable("original_exchange_pair_invalid")
        return JSONResponse(pair.response(proof), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except OriginalExchangeRefused as exc:
        LOGGER.warning("[connection-hub.oauth] original code exchange refused reason=%s", _reason(exc))
        return _error("invalid_grant", 400)
    except Exception as exc:
        # Provider errors can contain secrets. Neither the response nor a
        # traceback/log receives their text; an uncertain result stays closed.
        # The log names only the exception's class and a bare reason code.
        return _unavailable(_reason(exc))
