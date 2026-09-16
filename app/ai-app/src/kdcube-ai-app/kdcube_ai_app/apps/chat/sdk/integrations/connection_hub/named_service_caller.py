# SPDX-License-Identifier: MIT

"""The application a named-service call comes from.

A provider bundle serves calls for other applications, so the bundle id on its
own request context names the provider, never the caller. The caller is read
from what admission established for this invocation instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from connection_hub.agent_account_scope import agent_identity
from connection_hub.delegated_credentials.cards.identity import (
    RESIDENT_CLIENT_PREFIX,
)


@dataclass(frozen=True)
class NamedServiceCaller:
    bundle_id: str = ""
    agent_id: str = ""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def named_service_caller(ns_ctx: Any) -> NamedServiceCaller:
    """The hosted application behind this call, or an empty caller.

    A hosted agent's admitted client id is ``kdcube-agent:<bundle>:<agent>``;
    admission binds it for MCP, native-door and relayed calls alike. A relayed
    application call carries its validated source bundle on the actor. External
    clients and plain user calls have neither.
    """

    client_id = _clean(agent_identity().get("client_id"))
    if client_id.startswith(RESIDENT_CLIENT_PREFIX):
        bundle_id, sep, agent_id = client_id[len(RESIDENT_CLIENT_PREFIX):].partition(":")
        if sep and _clean(bundle_id) and _clean(agent_id):
            return NamedServiceCaller(bundle_id=_clean(bundle_id), agent_id=_clean(agent_id))
    actor = getattr(ns_ctx, "actor", None)
    actor = actor if isinstance(actor, Mapping) else {}
    return NamedServiceCaller(
        bundle_id=_clean(actor.get("source_bundle_id")),
        agent_id=_clean(actor.get("source_agent_id")),
    )


__all__ = ["NamedServiceCaller", "named_service_caller"]
