# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Publish an application's shared descriptor Controls.

Connection Hub holds one descriptor Control per agent, for every user of the
agent. The application's deployment publishes it, as the application and
never on a user's behalf, from the props the deployment re-read from the
authority: the publication that commits last carries the newest descriptor.

Every publication runs under one short lock per application storage root
(``descriptor-control-publish``), so a deployment and an on-demand
publication never interleave. The deployment takes it inside its own
resource-phase lock; an on-demand publication takes only this one.

- A deployment that cannot reach Connection Hub (not loaded yet, or failing)
  retries in the background and does not hold the application back: a user's
  first sync of a new revision publishes it too.
- A refusal by Connection Hub (a final answer, not a busy or failing one)
  fails the deployment's resource phase, so no generation is recorded as done
  with an unpublished descriptor. The application is not ready until its
  descriptor is fixed; its lifecycle state names each refused agent with the
  Hub's code, reason and status.
- Connection Hub may ask for an on-demand publication of one agent (when an
  administrator opens that agent's Control) through
  ``sdk/infra/descriptor_control_publisher.py``; the platform answers it with
  :func:`make_descriptor_control_publisher` for an application that is ready in
  this process, and never loads one for it.
"""

from __future__ import annotations

import asyncio
import pathlib
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping, Sequence

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


PUBLISH_LOCK_OPERATION = "descriptor-control-publish"
DEPLOY_LOCK_WAIT_SECONDS = 60.0
ON_DEMAND_LOCK_WAIT_SECONDS = 3.0
OUTCOME_BUSY = "busy"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_NOT_DECLARED = "refused:agent_not_declared"
RETRY_DELAYS_SECONDS: Sequence[float] = (10.0, 30.0, 60.0, 120.0, 300.0)
_RETRIES: set[asyncio.Task] = set()


class DescriptorControlRefused(RuntimeError):
    """Connection Hub refused a deployment's descriptor Control publication.

    Its text is what the application's lifecycle state shows: the application,
    each refused agent with the Hub's code, reason and status, and what to do.
    """

    def __init__(self, outcomes: Mapping[str, str], *, bundle_id: str = "",
                 failures: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        failures = dict(failures or {})
        rows = []
        for agent, outcome in sorted(outcomes.items()):
            failure = failures.get(agent) or {}
            reason, status = failure.get("reason"), failure.get("status")
            row = f"agent={agent} error={outcome.removeprefix('refused:')}"
            if reason and reason != outcome.removeprefix("refused:"):
                row += f" reason={reason}"
            if status:
                row += f" status={status}"
            rows.append(row)
        scope = f" application={bundle_id}" if bundle_id else ""
        super().__init__(f"descriptor_control_refused:{scope} Connection Hub refused the descriptor Control of "
                         + "; ".join(rows) + ". Fix the agent's descriptor, then reload the application.")
        self.outcomes = dict(outcomes)
        self.failures = failures


def _current(outcome: str) -> bool:
    return outcome in ("published", "unchanged")


def publish_lock_path(storage_root: Any) -> pathlib.Path:
    return pathlib.Path(storage_root) / ".kdcube.once" / f"{PUBLISH_LOCK_OPERATION}.lock"


@asynccontextmanager
async def descriptor_control_publish_lock(storage_root: Any, *, bundle_id: str,
                                          wait_seconds: float) -> AsyncIterator[None]:
    """The application's publication lock; raises ``ObservedFileLockTimeout`` past ``wait_seconds``."""
    from kdcube_ai_app.storage.observed_file_locks import observed_file_lock_async

    async with observed_file_lock_async(lock_path=publish_lock_path(storage_root),
                                        resource_id=f"descriptor-control:{bundle_id}",
                                        operation=PUBLISH_LOCK_OPERATION, wait_seconds=wait_seconds):
        yield


async def _publish_agents(*, workflow: Any, tenant: str, project: str, bundle_id: str, agents: Sequence[str],
                          props: Mapping[str, Any], redis: Any, pg_pool: Any,
                          failures: dict[str, Mapping[str, Any]] | None = None) -> dict[str, str]:
    """Publish ``agents`` as the application: its own context and a caller built for it, never a request's."""
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import publish_agent_capability_control
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import agent_capabilities_catalog

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
            except Exception as exc:  # the Hub is not reachable now
                outcomes[agent_id] = f"failed:{type(exc).__name__}"
                continue
            if answer.get("ok") is True:
                outcomes[agent_id] = "published" if answer.get("control_changed") else "unchanged"
                continue
            code = answer.get("error") or "unknown"
            # A busy or failing Hub answers too: only a final answer is a refusal.
            outcomes[agent_id] = f"failed:{code}" if answer.get("retryable") is True else f"refused:{code}"
            if failures is not None:
                failures[agent_id] = answer
    return outcomes


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
    storage_root: Any = None,
    reread: Any = None,
) -> dict[str, str]:
    """Publish every declared agent's shared descriptor Control; return each outcome.

    With ``storage_root`` it runs under the application's publication lock.
    ``reread`` (the deployment's props re-read) is used by the background
    retries, so a retry never publishes an older descriptor than the authority
    holds by then.
    """
    agents = declared_agents(props)
    if not agents:
        return {}

    async def _once(current: Mapping[str, Any], names: Sequence[str],
                    failures: dict[str, Mapping[str, Any]] | None = None) -> dict[str, str]:
        if storage_root is None:
            return await _publish_agents(workflow=workflow, tenant=tenant, project=project, bundle_id=bundle_id,
                                         agents=names, props=current, redis=redis, pg_pool=pg_pool,
                                         failures=failures)
        async with descriptor_control_publish_lock(storage_root, bundle_id=bundle_id,
                                                   wait_seconds=DEPLOY_LOCK_WAIT_SECONDS):
            return await _publish_agents(workflow=workflow, tenant=tenant, project=project, bundle_id=bundle_id,
                                         agents=names, props=current, redis=redis, pg_pool=pg_pool,
                                         failures=failures)

    failures: dict[str, Mapping[str, Any]] = {}
    outcomes = await _once(props, agents, failures)
    _log(logger, f"[bundle.deploy.descriptor-controls] bundle={bundle_id} outcomes={outcomes}", "INFO")
    refused = {agent: outcome for agent, outcome in outcomes.items() if outcome.startswith("refused:")}
    if refused:
        raise DescriptorControlRefused(refused, bundle_id=bundle_id, failures=failures)
    unreached = [agent for agent, outcome in outcomes.items() if not _current(outcome)]
    if unreached:
        task = asyncio.ensure_future(_retry(_once, reread=reread, props=props, agents=unreached,
                                            bundle_id=bundle_id, logger=logger))
        _RETRIES.add(task)
        task.add_done_callback(_RETRIES.discard)
    return outcomes


async def _retry(once: Any, *, reread: Any, props: Mapping[str, Any], agents: Sequence[str], bundle_id: str,
                 logger: Any) -> None:
    """Publish what the Hub did not take yet, with growing pauses; the application keeps serving meanwhile."""
    pending = list(agents)
    for delay in RETRY_DELAYS_SECONDS:
        await asyncio.sleep(delay)
        try:
            current = await reread() if callable(reread) else props
            pending = [agent for agent in pending if agent in declared_agents(current)]
            if not pending:
                return
            outcomes = await once(current, pending)
        except Exception as exc:  # noqa: BLE001 - a busy lock or an unreachable Hub: try again later
            _log(logger, f"[bundle.deploy.descriptor-controls] bundle={bundle_id} retry failed: "
                         f"{type(exc).__name__}", "WARNING")
            continue
        _log(logger, f"[bundle.deploy.descriptor-controls] bundle={bundle_id} retry outcomes={outcomes}", "INFO")
        pending = [agent for agent, outcome in outcomes.items() if not _current(outcome)
                   and not outcome.startswith("refused:")]
        if not pending:
            return
    _log(logger, f"[bundle.deploy.descriptor-controls] bundle={bundle_id} not published after retries: "
                 f"{pending}; the next deployment or a user's sync publishes them", "ERROR")


async def publish_agent_descriptor_control_now(*, workflow: Any, tenant: str, project: str, bundle_id: str,
                                               agent_id: str, props: Mapping[str, Any], redis: Any, pg_pool: Any,
                                               storage_root: Any,
                                               wait_seconds: float = ON_DEMAND_LOCK_WAIT_SECONDS) -> str:
    """Publish one agent's descriptor Control now, as the application; ``busy`` while a deployment publishes."""
    from kdcube_ai_app.storage.observed_file_locks import ObservedFileLockTimeout

    if str(agent_id or "").strip() not in declared_agents(props):
        return OUTCOME_NOT_DECLARED
    if storage_root is None:
        return OUTCOME_UNAVAILABLE
    try:
        async with descriptor_control_publish_lock(storage_root, bundle_id=bundle_id, wait_seconds=wait_seconds):
            outcomes = await _publish_agents(workflow=workflow, tenant=tenant, project=project, bundle_id=bundle_id,
                                             agents=[agent_id], props=props, redis=redis, pg_pool=pg_pool)
    except ObservedFileLockTimeout:
        return OUTCOME_BUSY
    outcome = outcomes.get(agent_id, OUTCOME_UNAVAILABLE)
    return OUTCOME_UNAVAILABLE if outcome.startswith("failed:") else outcome


def make_descriptor_control_publisher(*, tenant: str, project: str, redis: Any, pg_pool: Any) -> Any:
    """The publisher the platform binds for requests routed to Connection Hub.

    It publishes only for an application that is ready in this process, from
    that application's loaded instance and its effective props; it never
    loads an application. ``pg_pool`` may be a callable resolved only when a
    publication runs, so binding the publisher never requires a pool.
    """

    async def publish(application_id: str, agent_id: str) -> str:
        nonlocal pg_pool
        if callable(pg_pool):
            pg_pool = pg_pool()
        from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import effective_props_now
        from kdcube_ai_app.infra.plugin.app_readiness import application_readiness_registry
        from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec, get_workflow_instance_async
        from kdcube_ai_app.infra.plugin.bundle_store import resolve_bundle_spec_from_store
        from kdcube_ai_app.infra.service_hub.inventory import (
            ConfigRequest, create_workflow_config, resolve_config_request_secrets,
        )

        application_id = str(application_id or "").strip()
        snapshot = application_readiness_registry.snapshot(tenant=tenant, project=project,
                                                           application_id=application_id)
        if not application_id or snapshot is None or not snapshot.ready:
            return OUTCOME_UNAVAILABLE
        spec_resolved = await resolve_bundle_spec_from_store(redis, tenant=tenant, project=project,
                                                             bundle_id=application_id)
        if spec_resolved is None:
            return OUTCOME_UNAVAILABLE
        cfg_req = await resolve_config_request_secrets(
            ConfigRequest(agentic_bundle_id=spec_resolved.id, tenant=tenant, project=project),
            bundle_id=spec_resolved.id)
        wf_config = create_workflow_config(cfg_req)
        wf_config.ai_bundle_spec = spec_resolved
        spec = BundleSpec(id=spec_resolved.id, path=spec_resolved.path, module=spec_resolved.module,
                          singleton=bool(getattr(spec_resolved, "singleton", False)))
        context = _deployment_context(tenant=tenant, project=project, bundle_id=spec_resolved.id)
        workflow, _module = await get_workflow_instance_async(spec, wf_config, comm_context=context,
                                                              redis=redis, pg_pool=pg_pool)
        props = await effective_props_now(workflow, tenant=tenant, project=project, bundle_id=spec_resolved.id)
        storage_of = getattr(workflow, "bundle_storage_root", None)
        return await publish_agent_descriptor_control_now(
            workflow=workflow, tenant=tenant, project=project, bundle_id=spec_resolved.id, agent_id=agent_id,
            props=props, redis=redis, pg_pool=pg_pool,
            storage_root=storage_of() if callable(storage_of) else None)

    return publish


def _log(logger: Any, message: str, level: str) -> None:
    if logger is None:
        return
    try:
        logger.log(message, level)
    except Exception:
        pass

__all__ = ["DescriptorControlRefused", "declared_agents", "descriptor_control_publish_lock",
           "make_descriptor_control_publisher", "publish_agent_descriptor_control_now",
           "publish_agent_descriptor_controls"]
