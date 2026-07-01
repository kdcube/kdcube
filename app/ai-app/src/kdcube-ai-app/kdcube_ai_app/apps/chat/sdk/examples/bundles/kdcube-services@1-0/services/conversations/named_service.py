"""conv named-service provider wiring for this bundle.

Thin: constructs the SDK-owned conversation named-service provider so it is
discoverable and callable through this bundle's `named_services` MCP surface.
list/get/export use the SDK read facade, and search uses the SDK search backend —
both bound per request to the caller's tenant/project. All resources (pg_pool,
model service, conversation store) are passed in from above (the bundle worker's
pooled resources); nothing is inferred from router/app state. Identity, tenant and
project come per request from the named-service context.
"""

from typing import Any, Callable

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.named_service import (
    make_conversation_search_named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    make_conversation_read_service,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_ns,
    make_conversation_search_backend,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore


def build_conversation_named_service_provider(
    *,
    pool_factory: Callable[[], Any],
    model_service_factory: Callable[[], Any],
    storage_path: str,
    bundle_id: str,
):
    """Build the conv provider with search + read/export bound per request to the
    caller's tenant/project.

    Every resource is supplied from above: ``pool_factory`` yields the worker's
    pooled pg pool, ``model_service_factory`` the shared model service, and
    ``storage_path`` the conversation store root. The provider constructs no
    resources from router/app state.
    """
    def _store() -> ConversationStore:
        return ConversationStore(storage_path)

    return make_conversation_search_named_service_provider(
        context_factory=conversation_search_context_from_ns,
        search_backend_factory=lambda ns_ctx: make_conversation_search_backend(
            pg_pool=pool_factory(),
            tenant=ns_ctx.tenant or "",
            project=ns_ctx.project or "",
            model_service=model_service_factory(),
            store=_store(),
            user_id=str(getattr(ns_ctx, "user_id", "") or ""),
            conversation_id=str(getattr(ns_ctx, "conversation_id", "") or ""),
        ),
        read_service_factory=lambda ns_ctx: make_conversation_read_service(
            pg_pool=pool_factory(),
            tenant=ns_ctx.tenant or "",
            project=ns_ctx.project or "",
            model_service=model_service_factory(),
            store=_store(),
        ),
        bundle_id=bundle_id,
    )
