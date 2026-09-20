"""Resolve a hosted agent's descriptor-owned conversation ceiling."""

from typing import Any

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_caller import (
    named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_policy import (
    ConversationTargetPolicy,
    configured_conversation_targets,
)
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props


async def hosted_conversation_target_policy(
    ns_ctx: Any, *, redis: Any, pg_pool: Any
) -> ConversationTargetPolicy:
    del pg_pool
    caller = named_service_caller(ns_ctx)
    if not caller.bundle_id or not caller.agent_id:
        return ConversationTargetPolicy()
    tenant = str(getattr(ns_ctx, "tenant", "") or "").strip()
    project = str(getattr(ns_ctx, "project", "") or "").strip()
    if not tenant or not project:
        raise ValueError("Hosted conversation target identity is incomplete")
    props = await get_bundle_props(
        redis, tenant=tenant, project=project, bundle_id=caller.bundle_id
    )
    configured = configured_conversation_targets(props, caller.agent_id)
    return ConversationTargetPolicy(configured=configured)
