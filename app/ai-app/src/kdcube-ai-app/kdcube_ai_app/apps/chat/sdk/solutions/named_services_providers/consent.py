# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Demand-driven consent raise for named-service consumer paths.

One contract, every path: a provider consent error
(``needs_connected_account_consent``, gate 2 — connected-account claims inside
the realm) raises the identical scoped demand whether the attempt was a
model-callable ``named_services.*`` tool or a ``react.pull`` materialization
through the namespace artifact rehoster.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

LOGGER = logging.getLogger("kdcube.sdk.named_services.consent")


async def raise_named_service_consent_demand(
    payload: Mapping[str, Any],
    *,
    namespace: str,
    tool_name: str,
) -> None:
    """Demand-driven consent for in-chat named-service consumer paths.

    A provider consent error (``needs_connected_account_consent``, gate 2 —
    connected-account claims inside the realm) speaks the identical contract
    as a direct tool attempt: record the pending demand + emit ONE scoped
    chat consent event (``consent_demand.announce_consent_demand``). The
    banner lists the underlying provider claims (e.g. mail get → the gmail
    read claim) while the turn-off spotlight targets the NAMESPACE entry the
    user sees in the composer menu. The external MCP surface stays as is —
    its clients render consent from the response itself. Best-effort.
    """
    try:
        if not isinstance(payload, Mapping):
            return
        error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
        consent: Mapping[str, Any] = {}
        if payload.get("ok") is False:
            if str(error.get("code") or "") != "needs_connected_account_consent":
                return
            consent = details.get("consent") if isinstance(details.get("consent"), Mapping) else {}
            if not consent:
                consent = payload.get("consent") if isinstance(payload.get("consent"), Mapping) else {}
        else:
            # A successful listing with ZERO connected accounts ships a
            # connect hint instead of an error (an empty list is not a
            # failure). The user still needs the actionable card — without it
            # the model can only hand-write a link. Same demand, same banner.
            ret = payload.get("ret") if isinstance(payload.get("ret"), Mapping) else {}
            extra = ret.get("extra") if isinstance(ret.get("extra"), Mapping) else {}
            hint = extra.get("consent") if isinstance(extra.get("consent"), Mapping) else {}
            if str(hint.get("reason") or "") != "connect_required":
                return
            consent = hint
        provider_id = str(consent.get("provider_id") or details.get("provider_id") or "").strip()
        if not provider_id:
            return
        ns_token = str(namespace or "").split(":", 1)[0].strip()
        claims = [
            str(c).strip()
            for c in (consent.get("claims") or details.get("claims") or [])
            if str(c or "").strip()
        ]
        banner_payload = {
            "ok": False,
            "error": {
                "code": "needs_connected_account_consent",
                "message": str(
                    error.get("message")
                    or consent.get("message")
                    or f"Connect a {provider_id} account in Connection Hub to continue."
                ),
            },
            "consent": {
                **dict(consent),
                # The menu entry the user can turn off is the namespace row.
                "tools": [ns_token] if ns_token else list(consent.get("tools") or []),
            },
            "tools": [tool_name] if tool_name else [],
        }
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
            announce_consent_demand,
        )

        await announce_consent_demand(
            payload=banner_payload,
            provider_id=provider_id,
            connector_app_id=str(consent.get("connector_app_id") or "").strip(),
            claims=claims,
            tool_name=ns_token or tool_name,
        )
    except Exception:
        LOGGER.debug("named-service consent demand announce unavailable", exc_info=True)


__all__ = ["raise_named_service_consent_demand"]
