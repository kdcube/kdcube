# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK-owned conversation search backend for the `conv` named-service provider.

The provider searches through `run_conversation_search`, which needs a
`ConversationSearchBackend` (search / search_turn_catalog / get_turn_log) and an
explicit `ConversationSearchContext`. The ReAct memsearch tool satisfies the same
contract with its `ctx_browser`; this module builds an equivalent backend for a
named-service request (no ReAct runtime), bound per request to the caller's
tenant/project.

Identity is explicit — the backend carries no user identity; it is passed per
call via `ConversationSearchContext` (mapped from the named-service context).

TEMPORARY adapter: the lazy backend reuses the control-plane `_build_ctx`
materialization and ReAct's `ContextBrowser` (the proven search plumbing). Those
imports are the only cross-layer coupling and are expected to move behind an
SDK-owned search store later without changing this module's contract.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchBackend,
    ConversationSearchContext,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext


def conversation_search_context_from_ns(ns_ctx: NamedServiceContext) -> ConversationSearchContext:
    """Map a named-service request context onto the explicit search context."""
    return ConversationSearchContext(
        user_id=str(ns_ctx.user_id or ""),
        conversation_id=str(ns_ctx.conversation_id or ""),
        turn_id=str(ns_ctx.turn_id or ""),
        bundle_id=ns_ctx.bundle_id,
        tenant=ns_ctx.tenant,
        project=ns_ctx.project,
    )


class _LazyControlPlaneSearchBackend:
    """A ConversationSearchBackend that builds its ContextBrowser on first use.

    The named-service `search_backend_factory` is synchronous, but building the
    ctx_client is async, so construction is deferred to the first search call.
    """

    def __init__(self, *, pool_factory: Callable[[], Any], tenant: str, project: str):
        self._pool_factory = pool_factory
        self._tenant = tenant
        self._project = project
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        if self._browser is None:
            # Temporary adapter over the proven materialization + search plumbing.
            from kdcube_ai_app.apps.chat.ingress.control_plane.conversations_browser import _build_ctx
            from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser

            ctx_client = await _build_ctx(self._pool_factory(), self._tenant, self._project)
            self._browser = ContextBrowser(
                ctx_client=ctx_client,
                model_service=getattr(ctx_client, "model_service", None),
            )
        return self._browser

    async def search(self, **kwargs: Any) -> Any:
        return await (await self._ensure_browser()).search(**kwargs)

    async def search_turn_catalog(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await (await self._ensure_browser()).search_turn_catalog(**kwargs)

    async def get_turn_log(self, *, turn_id: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        return await (await self._ensure_browser()).get_turn_log(
            turn_id=turn_id, conversation_id=conversation_id,
        )


def make_control_plane_search_backend(
    *,
    pool_factory: Callable[[], Any],
    tenant: str,
    project: str,
) -> ConversationSearchBackend:
    return _LazyControlPlaneSearchBackend(
        pool_factory=pool_factory, tenant=tenant or "", project=project or "",
    )


__all__ = [
    "conversation_search_context_from_ns",
    "make_control_plane_search_backend",
]
