"""Resolve a hosted agent's configured conversation ceiling and user choice."""

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_caller import (
    named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_policy import (
    ConversationTargetPolicy,
    configured_conversation_targets,
)
from kdcube_ai_app.apps.chat.sdk.solutions.user_settings import UserAgentSelectionStore
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props


async def hosted_conversation_target_policy(
    ns_ctx: Any, *, redis: Any, pg_pool: Any
) -> ConversationTargetPolicy:
    caller = named_service_caller(ns_ctx)
    if not caller.bundle_id or not caller.agent_id:
        return ConversationTargetPolicy()
    tenant = str(getattr(ns_ctx, "tenant", "") or "").strip()
    project = str(getattr(ns_ctx, "project", "") or "").strip()
    user_id = str(getattr(ns_ctx, "user_id", "") or "").strip()
    if not tenant or not project or not user_id:
        raise ValueError("Hosted conversation target identity is incomplete")
    props = await get_bundle_props(
        redis, tenant=tenant, project=project, bundle_id=caller.bundle_id
    )
    configured = configured_conversation_targets(props, caller.agent_id)
    selection = await UserAgentSelectionStore(
        pg_pool=pg_pool, tenant=tenant, project=project
    ).get_selection(
        user_id=user_id,
        bundle_id=caller.bundle_id,
        agent_id=caller.agent_id,
        conversation_id=str(getattr(ns_ctx, "conversation_id", "") or ""),
    )
    disabled = selection.get("disabled") if isinstance(selection, Mapping) else None
    targets = disabled.get("conversation_targets") if isinstance(disabled, Mapping) else None
    return ConversationTargetPolicy(
        configured=configured,
        disabled=tuple(sorted(
            str(target) for target, denied in targets.items() if denied is True
        )) if isinstance(targets, Mapping) else (),
    )
