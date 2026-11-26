# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Publish an application's shared descriptor Controls when it deploys.

Connection Hub holds one descriptor Control per agent, for every user of the
agent. The application's deployment publishes it, as the application and
never on a user's behalf, from the props the deployment re-read from the
authority under its shared lock: the publication that commits last carries
the newest descriptor. A refused or failed publication never fails the
deployment; a user's first sync of a new revision publishes it too.
"""

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    bind_bundle_named_service_caller,
    make_local_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context


def declared_agents(props: Mapping[str, Any] | None) -> list[str]:
    """The agents an application declares under ``surfaces.as_consumer.agents``."""
    consumer = ((dict(props or {}).get("surfaces") or {}).get("as_consumer")) or {}
    agents = consumer.get("agents") if isinstance(consumer, Mapping) else None
    if not isinstance(agents, Mapping):
        return []
    return sorted(str(agent).strip() for agent, block in agents.items()
                  if str(agent).strip() and isinstance(block, Mapping))


def _deployment_context(*, tenant: str, project: str, bundle_id: str) -> ExternalEventPayload:
    """The application's own, identity-free calling context (as a headless bundle job's)."""
    return ExternalEventPayload(
        actor=ExternalEventActor(tenant_id=tenant, project_id=project),
        routing=ExternalEventRouting(bundle_id=bundle_id, session_id=""),
        user=ExternalEventUser(user_type="anonymous"),
    )


async def publish_agent_descriptor_controls(
    *,
    workflow: Any,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Mapping[str, Any],
    redis: Any,
    pg_pool: Any,
    logger: Any = None,
) -> dict[str, str]:
    """Publish every declared agent's shared descriptor Control; return each outcome."""
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import publish_agent_capability_control
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import agent_capabilities_catalog

    agents = declared_agents(props)
    if not agents:
        return {}
    context = _deployment_context(tenant=tenant, project=project, bundle_id=bundle_id)
    caller = make_local_bundle_named_service_caller(redis=redis, pg_pool=pg_pool, comm_context=context)
    root_of = getattr(workflow, "_bundle_root", None)
    bundle_root = root_of() if callable(root_of) else None
    identity = {"tenant": tenant, "project": project, "bundle_id": bundle_id}
    outcomes: dict[str, str] = {}
    with bind_current_request_context(context, comm=None), bind_bundle_named_service_caller(caller):
        for agent_id in agents:
            try:
                catalog = agent_capabilities_catalog(dict(props), agent_id, bundle_root=bundle_root)
                answer = await publish_agent_capability_control(
                    workflow, catalog=catalog, agent_id=agent_id, bundle_props=props, identity=identity)
            except Exception as exc:  # never fail the deployment
                outcomes[agent_id] = f"failed:{type(exc).__name__}"
                continue
            if answer.get("ok") is True:
                outcomes[agent_id] = "published" if answer.get("control_changed") else "unchanged"
            else:
                outcomes[agent_id] = f"refused:{answer.get('error') or 'unknown'}"
    if logger is not None:
        try:
            logger.log(f"[bundle.deploy.descriptor-controls] bundle={bundle_id} outcomes={outcomes}", "INFO")
        except Exception:
            pass
    return outcomes


__all__ = ["declared_agents", "publish_agent_descriptor_controls"]
