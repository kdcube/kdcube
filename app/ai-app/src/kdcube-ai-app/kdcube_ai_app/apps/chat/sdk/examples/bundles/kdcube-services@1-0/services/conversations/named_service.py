"""conv named-service provider wiring for this bundle.

Thin: constructs the SDK-owned conversation named-service provider so it is
discoverable and callable through this bundle's `named_services` MCP surface.
list/get/export use the SDK read facade, and search uses the SDK search backend —
both bound per request to the caller's tenant/project over the control-plane
materialization + search plumbing. Identity/tenant/project come per request from
the named-service context.
"""

from typing import Any, Callable

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.named_service import (
    make_conversation_search_named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    make_control_plane_read_service,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_ns,
    make_control_plane_search_backend,
)


def build_conversation_named_service_provider(
    *,
    pool_factory: Callable[[], Any],
    bundle_id: str,
):
    """Build the conv provider with search + read/export bound per request to the
    caller's tenant/project (pool read lazily via ``pool_factory``)."""
    return make_conversation_search_named_service_provider(
        context_factory=conversation_search_context_from_ns,
        search_backend_factory=lambda ns_ctx: make_control_plane_search_backend(
            pool_factory=pool_factory,
            tenant=ns_ctx.tenant or "",
            project=ns_ctx.project or "",
        ),
        read_service_factory=lambda ns_ctx: make_control_plane_read_service(
            pg_pool=pool_factory(),
            tenant=ns_ctx.tenant or "",
            project=ns_ctx.project or "",
        ),
        bundle_id=bundle_id,
    )
