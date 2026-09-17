"""conv named-service provider wiring for this bundle.

Thin: constructs the SDK-owned conversation named-service provider so it is
discoverable and callable through this bundle's `named_services` MCP surface.
list/get/export use the SDK read facade, and search uses the SDK search backend —
both bound per request to the caller's tenant/project. All resources (pg_pool,
model service, conversation store) are passed in from above (the bundle worker's
pooled resources); nothing is inferred from router/app state. Identity, tenant and
project come per request from the named-service context.
"""

from typing import Any, Awaitable, Callable

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_caller import (
    named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import ConversationSearchContext
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


def conversation_search_context(ns_ctx: Any) -> ConversationSearchContext:
    """Scope a search to the application that called, never to this bundle.

    Every `conv` call is served here, so this bundle's own id would confine
    every caller to conversations held by kdcube-services. A hosted agent's
    search is scoped to its own application; an external client's is not.
    """
    caller = named_service_caller(ns_ctx)
    return conversation_search_context_from_ns(
        ns_ctx,
        bundle_id=caller.bundle_id,
        agent_id=caller.agent_id,
    )


def build_conversation_named_service_provider(
    *,
    pool_factory: Callable[[], Any],
    model_service_factory: Callable[[], Any],
    storage_path: str,
    bundle_id: str,
    file_url_factory: Callable[[Any, Any], Any] | None = None,
    bundle_validator: Callable[[Any, str], Awaitable[bool]] | None = None,
):
    """Build the conv provider with search + read/export bound per request to the
    caller's tenant/project.

    Every resource is supplied from above: ``pool_factory`` yields the worker's
    pooled pg pool, ``model_service_factory`` the shared model service, and
    ``storage_path`` the conversation store root. The provider constructs no
    resources from router/app state. ``file_url_factory`` (optional) mints an
    out-of-band download URL for binary ``conv:fi:`` artifacts so their bytes never
    enter the model's context.
    """
    def _store() -> ConversationStore:
        return ConversationStore(storage_path)

    return make_conversation_search_named_service_provider(
        context_factory=conversation_search_context,
        file_url_factory=file_url_factory,
        bundle_validator=bundle_validator,
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
