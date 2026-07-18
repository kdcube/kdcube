# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── identity.py ── the shared multi-user + multi-agent isolation gate ──
#
# Each preserved agent originally ran on one machine for one person: its CLI passed
# `--user alice` and that raw string keyed both the per-user store and the
# checkpointer thread. One KDCube deployment is bound to one tenant/project,
# while the SAME process serves many users concurrently and this one app hosts
# TWO agents (`lg-solution`, `lg-react`) dispatched by `agent_id`. The keys still
# include tenant/project because the app can run in another deployment against
# shared backing infrastructure. If the platform
# layer forwarded a raw or constant id, two platform users would share state, and
# worse, the two agents' memories could mix.
#
# So this gate maps the PLATFORM identity onto each agent's per-user + per-
# conversation keys AND folds the ACTIVE agent_id into them, separating the two
# agents even though they share a store. (Storage rows are
# also tagged with the scope columns tenant/project/bundle_id/agent_id — see
# pg_target.py — so the store filters `WHERE agent_id = …`; this fold is the
# belt-and-suspenders key-level guarantee.) Kept separate so the isolation rule is
# explicit and testable rather than buried in execute_core.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


def normalize_agent_id(agent_id: Any, *, default: str = "default") -> str:
    """The single place the active agent id is normalized. A blank/None id folds
    to ``default`` so a turn without a declared agent still keys deterministically."""
    return str(agent_id or "").strip() or default


@dataclass(frozen=True)
class TurnIdentity:
    """The identity a hosted agent is driven with for one turn.

    - ``user_id``  keys an agent's per-user store (e.g. lg-solution's pgvector
      ``ported_langgraph_agents_memories`` rows). It folds the full platform
      identity AND the active ``agent_id``, so the same raw user id in two
      tenants/projects — or the same user across the two agents — never collides.
      (Storage rows are additionally tagged with an ``agent_id`` column, so the two
      agents stay apart at the row level too.)
    - ``thread_id`` keys an agent's LangGraph checkpointer, i.e. the conversation.
      It is scoped by ``user_id`` (which already carries the agent), so a shared or
      anonymous conversation id can never let one user's — or one agent's — graph
      state resume into another's.
    """

    user_id: str
    thread_id: str
    agent_id: str


def turn_identity(
    state: Dict[str, Any],
    *,
    agent_id: str,
    fallback_thread_id: str = "default",
) -> TurnIdentity:
    """Derive an agent's per-user + per-conversation keys from the platform turn
    ``state`` (the dict ``execute_core`` receives) and the ACTIVE ``agent_id``.

    ``state`` carries the resolved platform identity: ``tenant``, ``project``,
    ``user`` (or ``fingerprint`` for anonymous), and ``conversation_id`` (or
    ``session_id``). We fold tenant/project/agent/user into one opaque key so each
    agent's own store stays correctly partitioned at scale without the agent
    knowing anything about tenants — or about the sibling agent hosted alongside it.

    The Telegram webhook (the 2nd ingress) needs no special case here: the SDK
    resolves a Telegram sender to the platform ``user`` ``telegram_<id>`` and
    drives the default agent's turn, so ``state["user"]`` is already that scoped id
    and folds identically.
    """
    agent = normalize_agent_id(agent_id)
    tenant = str(state.get("tenant") or "t").strip() or "t"
    project = str(state.get("project") or "p").strip() or "p"
    user = str(state.get("user") or state.get("fingerprint") or "anonymous").strip() or "anonymous"
    conversation = str(
        state.get("conversation_id") or state.get("session_id") or fallback_thread_id
    ).strip() or fallback_thread_id

    # Fold the agent id into the per-user key so the two hosted agents' stores
    # never mix, even under a shared schema.
    user_id = f"{tenant}:{project}:{agent}:{user}"
    # The checkpointer thread is scoped by user (and therefore agent), so a shared
    # conversation id across users/agents can never collide.
    thread_id = f"{user_id}:{conversation}"
    return TurnIdentity(user_id=user_id, thread_id=thread_id, agent_id=agent)
