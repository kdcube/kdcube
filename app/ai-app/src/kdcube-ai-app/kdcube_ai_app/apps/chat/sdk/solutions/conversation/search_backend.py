# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK-owned conversation search backend for the `conv` named-service provider.

The provider searches through `run_conversation_search`, which needs a
`ConversationSearchBackend` (search / search_turn_catalog / get_turn_log) and an
explicit `ConversationSearchContext`. This module builds such a backend from
pooled resources passed in from above (pg_pool + model service + store) — never
from router/app state — bound per request to the caller's tenant/project.

Identity is explicit: the backend carries no user identity; it is passed per call
via `ConversationSearchContext` (mapped from the named-service context).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.event_identity import (
    index_agent_id,
    resolve_request_identity,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchBackend,
    ConversationSearchContext,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import build_conversation_ctx_client
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


def conversation_search_context_from_tool_subsystem(
    tool_subsystem: Any,
) -> ConversationSearchContext:
    """Build search identity from a trusted, bound tool invocation.

    The model-facing tool parameters intentionally contain no user, tenant,
    project, bundle, conversation, turn, or agent identity. Those values come
    from the communicator and bundle spec already bound by the host.
    """
    if tool_subsystem is None:
        raise RuntimeError("conversation search requires a bound tool subsystem")
    comm = getattr(tool_subsystem, "comm", None)
    if comm is None:
        raise RuntimeError("conversation search requires a bound communicator")

    identity = resolve_request_identity(comm)
    user_id = str(identity.get("owner") or "").strip()
    if not user_id:
        raise RuntimeError("conversation search requires a resolved caller user")

    service = getattr(comm, "service", None)
    service = service if isinstance(service, Mapping) else {}
    conversation = getattr(comm, "conversation", None)
    conversation = conversation if isinstance(conversation, Mapping) else {}
    registry = getattr(tool_subsystem, "registry", None)
    registry = registry if isinstance(registry, Mapping) else {}
    bundle_spec = getattr(tool_subsystem, "bundle_spec", None)

    return ConversationSearchContext(
        user_id=user_id,
        conversation_id=str(
            identity.get("conversation_id")
            or conversation.get("conversation_id")
            or ""
        ).strip(),
        turn_id=str(service.get("turn_id") or conversation.get("turn_id") or "").strip(),
        bundle_id=str(
            service.get("bundle_id")
            or getattr(bundle_spec, "id", "")
            or ""
        ).strip()
        or None,
        agent_id=index_agent_id(
            service.get("agent_id") or registry.get("client_id")
        ),
        tenant=str(identity.get("tenant") or "").strip() or None,
        project=str(identity.get("project") or "").strip() or None,
    )


class _PooledSearchBackend:
    """A ConversationSearchBackend over a ContextBrowser built from pooled
    resources (pg_pool + model service + store, all passed in from above).

    The ContextBrowser search path uses only the ctx_client + model service and
    the explicit per-call context; it carries no app/router state. Built lazily on
    first use.
    """

    def __init__(
        self, *, pg_pool: Any, tenant: str, project: str, model_service: Any, store: Any,
        user_id: str = "", conversation_id: str = "",
    ):
        self._pg_pool = pg_pool
        self._tenant = tenant
        self._project = project
        self._model_service = model_service
        self._store = store
        # Identity for the browser's runtime_ctx. get_turn_log() materializes turn
        # payloads using user_id read off runtime_ctx (not the call), so snippet text
        # only resolves when the browser is user-scoped. conversation_id is a fallback
        # anchor; per-hit conversation ids are passed explicitly by the search API.
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._browser: Any = None

    def _ensure_browser(self) -> Any:
        if self._browser is None:
            from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
            from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

            ctx_client = build_conversation_ctx_client(
                pg_pool=self._pg_pool, tenant=self._tenant, project=self._project,
                model_service=self._model_service, store=self._store,
            )
            runtime_ctx = RuntimeCtx(
                tenant=self._tenant or None,
                project=self._project or None,
                user_id=self._user_id or None,
                conversation_id=self._conversation_id or None,
            )
            self._browser = ContextBrowser(
                ctx_client=ctx_client, model_service=self._model_service, runtime_ctx=runtime_ctx,
            )
        return self._browser

    async def search(self, **kwargs: Any) -> Any:
        return await self._ensure_browser().search(**kwargs)

    async def search_turn_catalog(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await self._ensure_browser().search_turn_catalog(**kwargs)

    async def get_turn_log(self, *, turn_id: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._ensure_browser().get_turn_log(turn_id=turn_id, conversation_id=conversation_id)

    async def materialize_file(self, *, fi_ref: str, conversation_id: str = "") -> Dict[str, Any]:
        """Materialize a `conv:fi:` artifact to bytes via the browser's runtime (user-scoped)."""
        from kdcube_ai_app.apps.chat.sdk.solutions.conversation.files import materialize_fi_artifact

        return await materialize_fi_artifact(
            browser=self._ensure_browser(), fi_ref=fi_ref, conversation_id=conversation_id,
        )


def make_conversation_search_backend(
    *,
    pg_pool: Any,
    tenant: str,
    project: str,
    model_service: Any,
    store: Any,
    user_id: str = "",
    conversation_id: str = "",
) -> ConversationSearchBackend:
    return _PooledSearchBackend(
        pg_pool=pg_pool, tenant=tenant or "", project=project or "",
        model_service=model_service, store=store,
        user_id=user_id or "", conversation_id=conversation_id or "",
    )


def make_conversation_search_backend_from_client(
    *,
    context_rag_client: Any,
    context: ConversationSearchContext,
) -> ConversationSearchBackend:
    """Adapt an existing conversation client to the common search backend.

    Direct and managed harnesses already own this client. Reusing it keeps one
    Postgres pool and one storage binding for the host lifetime.
    """
    if context_rag_client is None:
        raise RuntimeError("conversation search requires a conversation client")

    from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
    from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

    runtime_ctx = RuntimeCtx(
        tenant=context.tenant,
        project=context.project,
        user_id=context.user_id,
        conversation_id=context.conversation_id or None,
        turn_id=context.turn_id or None,
        bundle_id=context.bundle_id,
        agent_id=context.agent_id or "",
    )
    return ContextBrowser(
        ctx_client=context_rag_client,
        model_service=getattr(context_rag_client, "model_service", None),
        runtime_ctx=runtime_ctx,
    )


__all__ = [
    "conversation_search_context_from_ns",
    "conversation_search_context_from_tool_subsystem",
    "make_conversation_search_backend",
    "make_conversation_search_backend_from_client",
]
