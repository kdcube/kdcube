# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ── composition root ── ONE app, MANY agents ──
#
# This app hosts TWO ported LangGraph agents behind a SINGLE `execute_core`,
# dispatched by `agent_id`:
#
#   - `lg-solution` — the rich research graph vendored under
#     `solution/lg_solution/` (KB retrieval + per-user pgvector memory + a nested
#     subagent). Linear shape with a DEDICATED answer node, so its stream adapter
#     (platform/stream_solution.py) streams that node's tokens.
#   - `lg-react` — the standard `langgraph.prebuilt.create_react_agent` vendored
#     under `solution/lg_prebuilt/` (plain + MCP tools). Its `agent` node LOOPS
#     (once per tool-decision cycle) with no dedicated answer node, so its stream
#     adapter (platform/stream_prebuilt.py) streams ONLY the final agent turn.
#
# Both agents' graph structure and logic are UNCHANGED from their standalone
# "before" (poc/lg-solution, poc/lg-prebuilt-agent). The platform integration is:
#
#   1. a dispatcher      — execute_core resolves agent_id and runs the right graph
#                          through its own stream adapter (the teaching point:
#                          different agent shapes -> different stream adapters,
#                          selected by agent_id)
#   2. scaled serving    — the agent's graph is REBUILT every turn, never cached
#                          in-process. KDCube is distributed (turns hop workers/
#                          machines), so a process-cached graph would neither survive
#                          the hop nor reflect this conversation's saved selection. Durable
#                          state lives in shared Postgres (checkpointer, keyed by
#                          thread_id); only connections are long-lived on self.
#   3. isolation         — platform identity + agent_id -> per-agent per-user keys
#                          (platform/identity.py), plus one tenant/project schema
#                          with app-prefixed tables and agent_id row scope
#   4. streaming         — astream_events -> comm_ctx (the two stream adapters)
#   5. a shared ingress  — a Telegram Bot API webhook (platform/telegram.py) that
#                          drives the SAME execute_core for the DEFAULT agent
#
# Persistence is mapped by DATA KIND (see docs/storage/README.md): each agent's OWN
# working store (memory/KB + the LangGraph checkpointer) is routed onto KDCube's
# SHARED Postgres (self.pg_pool) in the ONE tenant/project schema when hosted,
# with app-prefixed tables and agent_id row scope; it uses its own
# DATABASE_URL standalone, in-memory offline. KDCube owns the conversation record.

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, external_events_text
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.sdk.util import _now_ms
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context_patch
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.conversation_title import (
    generate_conversation_title,
    emit_conversation_title_event,
)
from kdcube_ai_app.infra.service_hub.inventory import Config
from kdcube_ai_app.infra.plugin.bundle_loader import api, bundle_entrypoint, bundle_id, ui_widget
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)
# Reused SDK adapter (unchanged): route a create_react agent's model calls through
# KDCube's accounted, still-streaming model service.
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeChatModel

# The vendored, UNCHANGED agents, imported package-relative as subpackages.
#   lg-solution (the research graph)
from .solution.lg_solution.deps import build_deps as build_solution_deps
from .solution.lg_solution.graph import build_graph as build_solution_graph
from .solution.lg_solution.config import get_config as get_solution_config
from .solution.lg_solution._pg import StorageScope
from .solution.lg_solution.memory import ensure_memory_tables
from .solution.lg_solution.knowledge import ensure_kb_tables
#   lg-react (the create_react agent)
from .solution.lg_prebuilt.agent import build_agent as build_prebuilt_agent, AGENT_NODE as PREBUILT_AGENT_NODE
from .solution.lg_prebuilt.tools import plain_tool_registry
from .solution.lg_prebuilt.config import get_config as get_prebuilt_config
from .solution.lg_prebuilt.llm import StubChatModel

# The platform seams.
from .platform.pg_target import resolve_solution_pg, resolve_solution_memory, schema_for_scope
from .platform.turn_batch import fold_turn_external_events
from .platform.identity import turn_identity, normalize_agent_id
from .platform.stream_solution import stream_graph_turn
from .platform.stream_prebuilt import stream_react_turn
from .platform.tools_mcp import load_mcp_tools, mcp_cfg_from_connections
from .platform.tool_pick import agent_tool_connections, run_python_bound, select_bound_tools
from .platform.turn_workspace import (
    build_pull_files_tool,
    build_read_file_tool,
    frame_turn_input,
    prepare_turn_workspace,
)
from .platform.capabilities import resolve_turn_role_models, resolve_turn_disabled_tools
from .platform.code_exec import build_code_exec_context, code_exec_scope
from .platform import telegram as telegram_ingress

LOGGER = logging.getLogger("kdcube.ported_langgraph_agents")

BUNDLE_ID = "ported-langgraph-agents@2026-07-13"

# The agent the app dispatches to when a turn declares no agent_id (both ingresses,
# including the Telegram webhook, land here by default).
DEFAULT_AGENT_ID = "lg-solution"

# ── lg-solution constants ────────────────────────────────────────────────────
# Graph nodes surfaced to the chat timeline as progress steps; the dedicated
# answer node whose model tokens are the user-visible answer. `compact` runs the
# accounted conversation-summary call — surfaced as a step, NEVER as the answer.
SOLUTION_STEP_NODES = {"compact", "retrieve", "plan", "delegate", "answer"}
SOLUTION_ANSWER_NODE = "answer"
# The model role lg-solution's chat + embedding calls bill under, and the economics
# flow label for its retrieval/memory embeddings.
SOLUTION_ANSWER_ROLE = "lg-solution.answer"
SOLUTION_RETRIEVAL_FLOW = "lg-solution.retrieval"
# A DISTINCT accounted role for lg-solution's conversation-compaction summary call,
# so it bills apart from the answer and (running in the non-answer `compact` node)
# is never streamed to the user as the answer.
SOLUTION_SUMMARY_ROLE = "lg-solution.summary"

# ── lg-react constants ────────────────────────────────────────────────────
# The agent id lg-react is dispatched + configured under. Its per-agent config
# (capabilities, tools) lives at `surfaces.as_consumer.agents.lg-react`.
PREBUILT_AGENT_ID = "lg-react"
# The model role the create_react agent's chat calls bill under.
PREBUILT_ANSWER_ROLE = "lg-react.answer"
# A DISTINCT accounted role for lg-react's compaction (LangMem SummarizationNode)
# summary call, so it bills apart from the answer and (running in the
# `summarization` pre-model node, not the `agent` node) is never streamed.
PREBUILT_SUMMARY_ROLE = "lg-react.summary"


# ── provider-surface visibility helpers ──────────────────────────────────────
# A widget/api is registered in the bundle MANIFEST by its decorator (not by the
# ui.widgets build config); these helpers give the decorators an open default
# with a config override path, so `config.visibility.widget.<alias>` /
# `.api...` can tighten it later without editing code. Empty selectors = open.

def _api_visibility(
    alias: str,
    *,
    route: str = "operations",
    method: str = "POST",
    user_types: Tuple[str, ...] = (),
    roles: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    base = f"surfaces.as_provider.api.{route}.{alias}.{method.upper()}.visibility"
    return {
        "user_types": user_types,
        "user_types_config": f"{base}.user_types",
        "roles": roles,
        "roles_config": f"{base}.roles",
    }


def _widget_visibility(
    alias: str,
    *,
    user_types: Tuple[str, ...] = (),
    roles: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    base = f"surfaces.as_provider.widget.{alias}.visibility"
    return {
        "user_types": user_types,
        "user_types_config": f"{base}.user_types",
        "roles": roles,
        "roles_config": f"{base}.roles",
    }


# ── the per-agent registry ───────────────────────────────────────────────────
# Each vendored agent is described by an AgentSpec: how to BUILD its graph (with
# its own deps/checkpointer/store), how to STREAM it (its own adapter), how to
# shape its INPUTS, its model role, and its `agent_id` (the row-scope discriminator
# that keeps the two agents' rows apart inside the SHARED tenant/project schema).
# The module-level build/stream/input functions take the entrypoint explicitly so
# the spec stays a plain value object and the entrypoint methods stay thin.

@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    build_graph: Callable[["LGPortedAgentsBundle"], Awaitable[Any]]
    stream: Callable[[Any, Dict[str, Any], Dict[str, Any]], Awaitable[str]]
    # (question, ident, attachments) -> (inputs, run_config). `attachments` is the
    # turn's materialized multimodal blocks (image/document); empty for text-only.
    # (framed turn text, identity, multimodal attachment blocks)
    build_inputs: Callable[[str, Any, list], Tuple[Dict[str, Any], Dict[str, Any]]]


def _solution_scope(ep: "LGPortedAgentsBundle", agent_id: str) -> StorageScope:
    """The row-level storage scope for one agent, resolved from the process runtime
    identity: tenant/project from platform settings (mirrors `UserMemoryStore` /
    `conv_index`), bundle_id from the ai_bundle_spec, agent_id the dispatched agent.
    Carried as DATA and written into every memory/KB row's scope columns."""
    return StorageScope(
        tenant=str(getattr(ep.settings, "TENANT", "") or ""),
        project=str(getattr(ep.settings, "PROJECT", "") or ""),
        bundle_id=ep._named_services_bundle_id(),
        agent_id=agent_id,
    )


async def _build_solution_graph(
    ep: "LGPortedAgentsBundle", *, disabled_tools: Optional[Dict[str, Any]] = None
) -> Any:
    """Build lg-solution's research graph. (`disabled_tools` is accepted for a
    uniform build signature but ignored — the linear research graph has no pickable
    tool loop.) Route its pgvector memory + KB onto
    KDCube's SHARED asyncpg pool (in the ONE per-tenant/project schema, rows scoped
    by columns) and its checkpointer onto the same shared Postgres via a psycopg
    DSN, hand the graph an accounted + economics-guarded model/embedding edge, open
    its checkpointer, seed the KB."""
    own = get_solution_config()
    scope = _solution_scope(ep, "lg-solution")
    schema = schema_for_scope(scope.tenant, scope.project)
    database_url = await ep._hosted_database_url(own.database_url, schema)
    # Hosted config comes from the DESCRIPTOR: the answer model's output budget
    # overlays the vendored config's standalone default (see _agent_max_tokens).
    config = replace(
        own,
        database_url=database_url,
        max_tokens=ep._agent_max_tokens("lg-solution", own.max_tokens),
    )
    # memory + KB drive KDCube's asyncpg pool DIRECTLY (no psycopg), in the shared
    # tenant/project schema; the agent's rows stay apart via the agent_id column.
    mem = resolve_solution_memory(getattr(ep, "pg_pool", None), schema)
    deps = build_solution_deps(
        config=config,
        models_service=getattr(ep, "models_service", None),
        model_role=SOLUTION_ANSWER_ROLE,
        embedding_service=ep._solution_embedding_service,
        pg_pool=mem.pool,
        schema=mem.schema,
        scope=scope,
        # The compact node's summary call bills on a DISTINCT accounted role and,
        # running outside the answer node, never streams as the answer.
        summary_model_role=SOLUTION_SUMMARY_ROLE,
    )
    checkpointer = await ep._open_checkpointer("lg-solution", deps.config.database_url)
    try:
        await deps.knowledge.seed()  # best-effort; no-op if the DB is unreachable
    except Exception:
        pass
    return build_solution_graph(deps, checkpointer=checkpointer)


async def _build_prebuilt_graph(
    ep: "LGPortedAgentsBundle", *, disabled_tools: Optional[Dict[str, Any]] = None
) -> Any:
    """Build lg-react's create_agent graph FOR THIS TURN (never cached).

    Tools are DECLARED as a connection list on the agent
    (`surfaces.as_consumer.agents.lg-react.tools`, the standard KDCube shape) — the
    ADMIN CEILING. This binds EXACTLY the declared tools the user has NOT opted out
    of this turn (`disabled_tools`, the saved capabilities-widget deny-map), so the
    per-tool picker takes effect: a tool the admin does not declare is never bound;
    a declared tool is on by default and the user may opt out per conversation. MCP
    tools are declared as `kind: mcp` connections and loaded best-effort. Checkpointer
    reused across per-turn builds. `build_agent` is vendored + injectable — no edit."""
    own = get_prebuilt_config()
    schema = schema_for_scope(
        str(getattr(ep.settings, "TENANT", "") or ""),
        str(getattr(ep.settings, "PROJECT", "") or ""),
    )
    database_url = await ep._hosted_database_url(own.database_url, schema)
    config = replace(own, database_url=database_url)
    model = ep._build_prebuilt_model(config)
    summary_model = ep._build_prebuilt_summary_model(config)

    connections = agent_tool_connections(ep, PREBUILT_AGENT_ID)

    def _run_python_factory() -> Any:
        # Lazy + package-relative: only import the code-exec tool when the exec
        # connection is declared and enabled for this turn.
        from .platform.code_exec_tool import build_run_python_tool
        return build_run_python_tool()

    def _pull_files_factory() -> Any:
        # Workspace companion: materialize conversation files into the sandbox
        # working directory by their conv:fi: links.
        return build_pull_files_tool()

    def _read_file_factory() -> Any:
        # Workspace companion: view one conversation file in visible context
        # (text bounded; images/PDF as visual payloads — react.read semantics).
        return build_read_file_tool()

    def _web_tool_factories() -> Dict[str, Callable[[], Any]]:
        # web_search / web_fetch run PAID backends (search provider + an LLM
        # that filters/segments the retrieved results) — the LLM side binds to
        # this app's ACCOUNTED model service, the search side meters through
        # the ambient turn accounting. Without a model service (standalone
        # offline) the tools stay unbound even if declared.
        svc = getattr(ep, "models_service", None)
        if svc is None:
            return {}

        def _search() -> Any:
            from .platform.web_tools import build_web_search_tool
            return build_web_search_tool(svc)

        def _fetch() -> Any:
            from .platform.web_tools import build_web_fetch_tool
            return build_web_fetch_tool(svc)

        return {"web_search": _search, "web_fetch": _fetch}

    # Plain + code-exec tools narrowed to (admin-declared − user-disabled) this turn.
    tools = select_bound_tools(
        connections,
        disabled_tools or {},
        plain_registry=plain_tool_registry(),
        run_python_factory=_run_python_factory,
        pull_files_factory=_pull_files_factory,
        read_file_factory=_read_file_factory,
        extra_factories=_web_tool_factories(),
    )
    # MCP tools declared as `kind: mcp` connections (optional; degrades to none).
    tools += await load_mcp_tools(mcp_cfg_from_connections(connections))

    checkpointer = await ep._open_checkpointer("lg-react", config.database_url)
    return build_prebuilt_agent(
        config, model=model, tools=tools, checkpointer=checkpointer, summary_model=summary_model,
        system_prompt=_prebuilt_system_prompt(tools),
    )


def _prebuilt_system_prompt(tools: List[Any]) -> Optional[str]:
    """The lg-react system prompt for this turn's tool binding: with the
    workspace tools bound (`run_python` + its `pull_files` companion), the
    SDK's standalone distributed-turn-workspace block is APPENDED to the
    agent's own prompt — a separate block, so the agent's prose stays its own
    and any workspace-connected agent shares the same guidance. Without the
    tools, None keeps the vendored default."""
    names = {str(getattr(tool, "name", "") or "") for tool in tools or []}
    if "run_python" not in names:
        return None
    from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import (
        distributed_turn_workspace_guide,
    )
    from .solution.lg_prebuilt.agent import SYSTEM_PROMPT

    return SYSTEM_PROMPT + "\n" + distributed_turn_workspace_guide(
        exec_tool="run_python",
        pull_tool="pull_files" if "pull_files" in names else "run_python",
        read_tool="read_file" if "read_file" in names else "",
    )


def _solution_inputs(
    question: str, ident: Any, attachments: list
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # `question` arrives ALREADY FRAMED (`frame_turn_input`: turn start + user
    # message + arriving-file links) — the frame is part of the turn, so it
    # rides the checkpointed `messages` history too. `attachments` stays a
    # pass-through slot for callers that add multimodal blocks explicitly.
    inputs = {
        "question": question,
        "user_id": ident.user_id,
        "messages": [("user", question)],
        "attachments": list(attachments or []),
    }
    run_config = {"configurable": {"thread_id": ident.thread_id}}
    return inputs, run_config


def _prebuilt_inputs(
    question: str, ident: Any, attachments: list
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # `question` arrives ALREADY FRAMED (`frame_turn_input`); create_react's
    # `messages` list IS the model input and the checkpointed history, so the
    # frame lands in both. `attachments` stays a pass-through for explicit
    # multimodal blocks; plain turns stay a plain ("user", text) tuple.
    if attachments:
        from langchain_core.messages import HumanMessage

        user_msg: Any = HumanMessage(
            content=[{"type": "text", "text": question}, *attachments]
        )
    else:
        user_msg = ("user", question)
    inputs = {"messages": [user_msg]}
    run_config = {"configurable": {"thread_id": ident.thread_id}}
    return inputs, run_config


async def _stream_solution(graph: Any, inputs: Dict[str, Any], run_config: Dict[str, Any]) -> str:
    return await stream_graph_turn(
        graph, inputs, run_config,
        answer_node=SOLUTION_ANSWER_NODE, step_nodes=SOLUTION_STEP_NODES,
    )


async def _stream_prebuilt(graph: Any, inputs: Dict[str, Any], run_config: Dict[str, Any]) -> str:
    return await stream_react_turn(graph, inputs, run_config, agent_node=PREBUILT_AGENT_NODE)


AGENTS: Dict[str, AgentSpec] = {
    "lg-solution": AgentSpec(
        agent_id="lg-solution",
        role=SOLUTION_ANSWER_ROLE,
        build_graph=_build_solution_graph,
        stream=_stream_solution,
        build_inputs=_solution_inputs,
    ),
    "lg-react": AgentSpec(
        agent_id="lg-react",
        role=PREBUILT_ANSWER_ROLE,
        build_graph=_build_prebuilt_graph,
        stream=_stream_prebuilt,
        build_inputs=_prebuilt_inputs,
    ),
}


# Bind the reusable Telegram SDK subsystem to this bundle's storage once, at module
# load. Safe when Telegram is unconfigured: with no bot token / webhook secret the
# webhook simply rejects every call. The webhook drives the DEFAULT agent.
telegram_ingress.configure(bundle_id=BUNDLE_ID)


@bundle_entrypoint(name="ported-langgraph-agents", version="1.0.0", priority=10)
@bundle_id(id=BUNDLE_ID)
class LGPortedAgentsBundle(BaseEntrypointWithEconomics):
    """Hosts TWO ported LangGraph agents behind one `execute_core`, dispatched by
    `agent_id` — the "one app, many agents" demonstration.

    Both agents are RUN-TO-COMPLETION: each turn runs to completion and does not
    consume in-turn followups/steers (a mid-turn followup is promoted to the next
    turn). Each is streamed to the reusable chat component, isolated per (agent,
    user, conversation), and recorded by the platform-owned conversation log.

    Economic ENFORCEMENT (T2b) closes the backend loop on top of T2a accounting:
      - Turn-level — deriving `BaseEntrypointWithEconomics` wraps every turn in the
        budget preflight + rate limiter, so overspend is blocked before
        `execute_core` runs (covers chat generation for either agent).
      - Per-call — lg-solution's retrieval/memory embeddings route through the
        economics-guarded search facade, budget-checked per embed. With no economics
        runtime the facade degrades to the raw accounted service, so both agents
        still run offline."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ExternalEventPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )
        # SCALED-SERVING INVARIANT: the compiled graph is REBUILT every turn, never
        # cached in-process. KDCube is distributed — turn 1 can land on worker 1,
        # turn 2 on worker 2 on machine B — so a process-cached graph would neither
        # survive the hop nor reflect THIS conversation's saved model/tool
        # selection. The graph instance exists for one turn only. The only
        # long-lived things on `self` are CONNECTIONS (the
        # checkpointer, like `pg_pool`): infra, re-established lazily per worker, not
        # rebuildable state. Every mutable byte is in shared Postgres keyed per
        # (agent, user, conversation). So ANY worker can serve ANY turn.
        self._checkpointer_cms: Dict[str, Any] = {}      # held so async PG savers aren't GC'd
        self._checkpointers: Dict[str, Any] = {}         # opened saver, reused across per-turn builds
        self._checkpointer_locks: Dict[str, asyncio.Lock] = {}  # open-once guard per agent

    # ── one-time provisioning (bundle load) ───────────────────────────────────

    async def on_bundle_load(self, **kwargs: Any) -> None:
        """Provision the memory + KB storage at load, once per process per
        tenant/project — the PRIMARY provisioning path (the stores' lazy `_prepare`
        stays as an idempotent `_ready`-guarded fallback for offline / a missed load).

        Bundle-level, NOT per-agent: both tables carry `agent_id` as a COLUMN, so a
        single ensure of the ONE `kdcube_{tenant}_{project}` schema + the two
        bundle-prefixed tables (`ported_langgraph_agents_memories`,
        `ported_langgraph_agents_kb`) covers both agents. All DDL is
        `IF NOT EXISTS` (safe across racing workers/replicas) and never
        `CREATE EXTENSION` — the platform PostgresSetup job owns the `vector`
        extension. Offline (no pool) skips cleanly; failures are non-fatal (the lazy
        fallback still self-heals on first turn)."""
        await super().on_bundle_load(**kwargs)
        pool = self.pg_pool or kwargs.get("pg_pool")
        if pool is None:
            LOGGER.info(
                "[ported-langgraph] on_bundle_load: no pg_pool available; skipping schema provisioning"
            )
            return
        ident = self.runtime_identity()
        scope = StorageScope(
            tenant=ident.get("tenant") or "default",
            project=ident.get("project") or "default",
            bundle_id=self._named_services_bundle_id(),
            agent_id="",  # bundle-level: the tables are shared, rows scoped by columns
        )
        schema = schema_for_scope(scope.tenant, scope.project)
        embed_dim = get_solution_config().embed_dim
        try:
            await ensure_memory_tables(pool, schema, embed_dim)
            await ensure_kb_tables(pool, schema, embed_dim)
        except Exception:
            LOGGER.exception(
                "[ported-langgraph] on_bundle_load: failed to ensure memory/KB schema "
                "tenant=%s project=%s",
                scope.tenant,
                scope.project,
            )
            return

    # ── storage edge (shared by both agents; one tenant/project schema) ───────

    async def _hosted_database_url(self, own_database_url: str, schema: str) -> str:
        """Resolve the psycopg DSN the checkpointer (+ optional `langgraph_store`)
        connect through. Hosted (self.pg_pool present) -> KDCube's shared Postgres
        with the per-tenant/project schema on the search_path (created idempotently
        first, via the asyncpg pool — no psycopg). Standalone -> the agent's own
        DATABASE_URL. Fail-open: a shared-DB failure degrades to the own/offline
        path (the checkpointer then falls back to MemorySaver)."""
        target = resolve_solution_pg(getattr(self, "pg_pool", None), own_database_url, schema)
        if not target.hosted:
            return own_database_url
        try:
            await self._ensure_schema_via_pool(target.schema)
        except Exception:
            return own_database_url
        return target.database_url

    async def _ensure_schema_via_pool(self, schema: str) -> None:
        """``CREATE SCHEMA IF NOT EXISTS`` on KDCube's SHARED asyncpg pool.

        Run before the checkpointer opens (its `setup()` creates tables in the
        search_path'd schema, which must already exist). Uses the pool — the same
        asyncpg pool memory/KB use — so schema creation needs no psycopg."""
        async with self.pg_pool.acquire() as con:
            await con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    async def _open_checkpointer(self, agent_id: str, database_url: str):
        """Return the LangGraph Postgres checkpointer for one agent, opening it ONCE
        and reusing it across every per-turn graph rebuild.

        The graph is rebuilt each turn (scaled serving), but the checkpointer is a
        CONNECTION — infra, like `pg_pool` — so it is opened lazily per agent and
        held (the cm is retained so the async saver isn't GC'd), not reopened per
        turn (which would leak a psycopg connection every turn). Concurrency note:
        `from_conn_string` opens a single connection shared by concurrent turns of
        this agent — same as the prior cached-graph design; a pooled
        `AsyncConnectionPool` is a clean follow-up if concurrency grows.

        Falls back to an in-memory saver when no DB is reachable (offline
        degradation). The fallback is LOUD: an in-memory saver keeps NO cross-turn
        history — every conversation restarts empty on the next process, and a
        reloaded conversation has no memory. That is a silent, confusing failure if
        it happens by accident (the usual cause: the declared deps
        `langgraph-checkpoint-postgres` + `psycopg[binary]` v3 are not installed in
        the runtime venv, or the DSN is unreachable). We log WHY at WARNING so it is
        visible, not mysterious. The in-memory fallback is NOT cached (each rebuild
        retries Postgres, so a transient outage self-heals on the next turn)."""
        existing = self._checkpointers.get(agent_id)
        if existing is not None:
            return existing

        lock = self._checkpointer_locks.get(agent_id)
        if lock is None:
            # Safe to create lazily under asyncio: no await between get and set.
            lock = asyncio.Lock()
            self._checkpointer_locks[agent_id] = lock
        async with lock:
            existing = self._checkpointers.get(agent_id)
            if existing is not None:
                return existing
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                cm = AsyncPostgresSaver.from_conn_string(database_url)
                checkpointer = await cm.__aenter__()
                await checkpointer.setup()
                self._checkpointer_cms[agent_id] = cm
                self._checkpointers[agent_id] = checkpointer
                LOGGER.info(
                    "[ported-langgraph] %s checkpointer: durable Postgres (cross-turn history persists)",
                    agent_id,
                )
                return checkpointer
            except Exception:
                # NOT cached: the next per-turn rebuild retries Postgres, so a
                # transient outage self-heals on a later turn.
                from langgraph.checkpoint.memory import MemorySaver

                LOGGER.warning(
                    "[ported-langgraph] %s checkpointer FELL BACK to in-memory — cross-turn "
                    "history will NOT persist (lost on restart, absent for reloaded "
                    "conversations). Install `langgraph-checkpoint-postgres` + `psycopg[binary]` "
                    "(v3) in the runtime venv and ensure the DSN is reachable. Cause:",
                    agent_id,
                    exc_info=True,
                )
                return MemorySaver()

    # ── lg-solution model/embedding edge ──────────────────────────────────────

    def _solution_embedding_service(self):
        """Resolve the economics-guarded embedding facade for the CURRENT turn.
        Per-call (not cached): `search_model_service` reads the live comm_context,
        so each embed is guarded for the turn's own identity. Degrades to the raw
        accounted service when economics is off, or None with no model service."""
        return self.search_model_service(flow=SOLUTION_RETRIEVAL_FLOW)

    # ── lg-react model edge ────────────────────────────────────────────────

    def _build_prebuilt_model(self, config: Any):
        """lg-react's create_react `model`: HOSTED (a platform model service is
        present) -> an accounted `KDCubeChatModel` bound to the answer role. The
        platform service provides the model/key, so the vendored config's own
        `offline` flag (which only tracks the STANDALONE OpenAI key) is irrelevant
        here — mirror lg-solution's `llm.chat_model()`, which gates on
        `models_service` alone. Only with NO model service (truly standalone) ->
        the vendored deterministic stub, so the agent degrades like the CLI.

        The output budget (NOT the adapter's small default) bounds the answer
        model: this agent carries whole payloads in tool arguments (`run_python`
        code), and a ceiling below one complete tool call cuts the response
        mid-call — the truncated args fail validation and the model retries
        into the same wall until the recursion limit. Hosted, the budget is a
        DESCRIPTOR property (`_agent_max_tokens`); the vendored config's value
        is only the standalone fallback."""
        if getattr(self, "models_service", None) is not None:
            return KDCubeChatModel(
                models_service=self.models_service, role=PREBUILT_ANSWER_ROLE,
                temperature=0.2,
                max_tokens=self._agent_max_tokens(
                    PREBUILT_AGENT_ID, int(getattr(config, "max_tokens", 0) or 16384)
                ),
            )
        return StubChatModel()

    def _agent_max_tokens(self, agent_id: str, fallback: int) -> int:
        """One agent's answer-model OUTPUT budget, from the app descriptor:
        `surfaces.as_consumer.agents.<agent>.model.max_tokens`. KDCube apps are
        configured through descriptor properties (`bundle_prop`) — process env
        vars are not a configuration surface here; the vendored config's env
        knob is the STANDALONE idiom and serves only as the hosted fallback."""
        try:
            raw = self.bundle_prop(
                f"surfaces.as_consumer.agents.{agent_id}.model.max_tokens", None
            )
            value = int(raw or 0)
        except Exception:
            value = 0
        return value if value > 0 else int(fallback)

    def _build_prebuilt_summary_model(self, config: Any):
        """lg-react's compaction summary model: HOSTED -> an accounted
        `KDCubeChatModel` on the DISTINCT `lg-react.summary` role (so LangMem's
        SummarizationNode folds old turns on a separately-billed, non-streamed
        role). None with no model service (offline) -> the pre_model_hook falls back
        to trim, so a turn still runs without summarization."""
        del config
        if getattr(self, "models_service", None) is not None:
            return KDCubeChatModel(
                models_service=self.models_service, role=PREBUILT_SUMMARY_ROLE, temperature=0.2
            )
        return None

    # ── graph build (per turn) ────────────────────────────────────────────────

    async def _build_graph(
        self, agent_id: str, *, disabled_tools: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Build the agent's graph FOR THIS TURN — never cached in-process.

        KDCube is distributed (turns hop workers/machines), so a process-cached
        graph would neither survive the hop nor reflect this conversation's saved state
        (model pick, tool selection). Each agent builds with its own deps + the
        SHARED checkpointer connection (opened once per agent, reused — see
        `_open_checkpointer`), storing into the shared tenant/project schema scoped
        by its agent_id column. Rebuilding is cheap: the checkpointer is reused, and
        the rest is pure in-memory graph compilation. `disabled_tools` is this turn's
        current conversation's tool opt-outs — the per-turn build is exactly what lets them narrow
        the bound tool set (lg-react); agents with no tool loop ignore it."""
        return await AGENTS[agent_id].build_graph(self, disabled_tools=disabled_tools)

    # ── the turn (the dispatcher) ─────────────────────────────────────────────

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Resolve the ACTIVE agent from the turn state; unknown/blank -> default.
        agent_id = normalize_agent_id(state.get("agent_id"), default=DEFAULT_AGENT_ID)
        if agent_id not in AGENTS:
            agent_id = DEFAULT_AGENT_ID
        spec = AGENTS[agent_id]

        # This (user, conversation)'s tool opt-outs from the capabilities widget —
        # resolved BEFORE the build so they narrow the bound tool set (admin ceiling
        # ∩ user-enabled). Fails open to {} (every admin-allowed tool stays bound).
        disabled_tools = await resolve_turn_disabled_tools(self, state, agent_id)

        # Built fresh every turn — no in-process graph cache (scaled serving).
        graph = await self._build_graph(agent_id, disabled_tools=disabled_tools)

        # (batch fold) the lane-wakeup dispatch hands a run-to-completion turn
        # only its single wakeup event (the prompt) — the attachment events
        # that arrived in the SAME ingress batch ride separate lane events.
        # Fold the batch back in (bundle-local, read-only on the lane; see
        # platform/turn_batch.py). Fail-open: the dispatched events stand.
        state["external_events"] = await fold_turn_external_events(self, state)

        # (state mapping) pull the user's question out of the platform external
        # events; each agent shapes it into its own inputs. Nothing else is
        # read for the model automatically: arriving files reach it as
        # METADATA + LINKS in the turn frame below, and the model chooses the
        # door — read_file to view, pull_files + run_python to process.
        question = external_events_text(state.get("external_events") or [])
        attachments: list = []

        # (code execution) build the per-turn code-exec scope for the ACTIVE agent —
        # BEFORE the inputs, because the exec workspace also receives this turn's
        # attachments. When `tools.code_exec.enabled` is on, this stands up an
        # isolated-runtime + hosting edge so a model-called `run_python` runs code
        # and hosts the files it produces into conversation storage (exactly like a
        # user attachment). Disabled / offline yields an inert context and the turn
        # runs unchanged.
        try:
            code_exec_ctx = build_code_exec_context(self, state, agent_id)
        except Exception:
            LOGGER.warning("[ported-langgraph] code-exec context build failed", exc_info=True)
            code_exec_ctx = None

        # (turn workspace) account for this turn's arriving files — metadata as
        # received + a durable conv:fi: link each — and frame the model's turn
        # text like React frames its timeline: [Turn start <id>] (the boundary
        # + the empty-fresh-workspace rule), [User message], [Files arriving
        # this turn]. A file is never silently dropped: with no workspace
        # tools the frame says exactly why contents are out of reach.
        try:
            workspace = await prepare_turn_workspace(
                code_exec_ctx,
                state.get("external_events") or [],
                exec_tool_bound=run_python_bound(agent_tool_connections(self, agent_id), disabled_tools),
            )
        except Exception:
            LOGGER.warning("[ported-langgraph] turn workspace preparation failed", exc_info=True)
            from .platform.turn_workspace import TurnWorkspace
            workspace = TurnWorkspace(live=False)
        framed_question = frame_turn_input(question, workspace)

        # Whole-turn summary at the boundary: one line tells the story of what this
        # turn was asked to do (cheap, non-fatal).
        LOGGER.info(
            "[ported-langgraph] TURN start agent=%s conversation=%s question_len=%d attachments=%d "
            "workspace_live=%s workspace_files=%d",
            agent_id, thread_id, len(question or ""), len(attachments or []),
            workspace.live, len(workspace.files),
        )

        # (isolation) map platform identity + agent_id onto the agent's per-user +
        # per-conversation keys — the gate that keeps the two agents' state apart.
        ident = turn_identity(state, agent_id=agent_id, fallback_thread_id=thread_id)
        inputs, run_config = spec.build_inputs(framed_question, ident, attachments)

        # First turn only: name the conversation from the USER'S QUESTION and emit it
        # to the client header BEFORE the agent runs, so the title appears even if the
        # agent's turn later errors — the title never depends on a successful answer.
        # Stashed on `state` for the turn recorder to persist. One small accounted
        # title call, first turn only. (The ORIGINAL question — the staged-files
        # note is model plumbing, not the user's ask.)
        await self._finalize_conversation_title(
            state=state, conversation_id=thread_id, question=question,
            title_role=spec.role,
        )

        # (streaming) run the agent's own astream_events loop through ITS stream
        # adapter, redirected at the chat component via comm_ctx. Returns the
        # answer in the shape the Telegram renderer reads. The graph runs INSIDE the
        # code-exec scope so `run_python`'s `host_files` resolves during the run.
        async def _run_turn() -> Dict[str, Any]:
            async with code_exec_scope(code_exec_ctx):
                answer = await spec.stream(graph, inputs, run_config)
            return {"answer": answer, "final_answer": answer}

        # Capabilities model pick for the ACTIVE agent (per user, per conversation).
        # Bind the resolved answer-role override onto the bundle call context so the
        # model router overlays it on this agent's `get_client("<agent>.answer")`
        # for this turn only. Empty overlay => the router's configured default
        # routes. Fail-open by construction. Both ingresses honor the pick.
        role_models = await resolve_turn_role_models(self, state, agent_id)

        # Time the turn so its elapsed_ms lands in the recorded-events artifact the
        # reload reader replays (mirrors BaseWorkflow.report_timings). Captured here,
        # emitted after the run.
        _turn_started_ms = _now_ms()
        _turn_t0 = time.perf_counter()

        # Wrap the run so a Telegram-originated turn is delivered from the processor
        # side; a browser turn passes through unchanged.
        with bind_current_bundle_call_context_patch({"role_models": role_models}):
            result = await telegram_ingress.run_turn_with_delivery(self, runner=_run_turn)

        # The platform's canonical final answer (what the framework-neutral turn
        # recorder persists for reload). The conversation title was already emitted
        # before the agent ran (see above), so it is unaffected by a turn error.
        state["final_answer"] = (result or {}).get("final_answer") or (result or {}).get("answer") or ""

        # Surface the files this turn hosted (run_python outputs) so the framework-
        # neutral turn recorder persists their refs — reload re-surfaces the files and
        # a later turn can pull them by ref. Empty when code exec was off/unused.
        if code_exec_ctx is not None and getattr(code_exec_ctx, "hosted_files", None):
            state["hosted_files"] = list(code_exec_ctx.hosted_files)

        # Turn timing: emit the SAME `chat.turn.summary` event React authors, so the
        # turn's elapsed time is recorded (and, via the events artifact, restored on
        # reload) alongside the economics door's `accounting.usage` $ badge. The cost
        # itself is emitted by the door's post-run accounting; both ride the recorded
        # comm and are persisted together by `_save_events_artifact` (post_run_hook).
        _turn_total_ms = int((time.perf_counter() - _turn_t0) * 1000)
        await self._emit_turn_timing(started_ms=_turn_started_ms, total_ms=_turn_total_ms)

        LOGGER.info(
            "[ported-langgraph] TURN done agent=%s conversation=%s answer_len=%d elapsed_ms=%d",
            agent_id, thread_id, len(state.get("final_answer") or ""), _turn_total_ms,
        )
        return state

    async def _emit_turn_timing(self, *, started_ms: int, total_ms: int) -> None:
        """Emit the turn-timing summary event (mirrors ``BaseWorkflow.report_timings``).

        A run-to-completion turn has no React timeline to author ``chat.turn.summary``,
        so the turn's elapsed time never reaches the recorded-events artifact the
        conversation reload replays — a reloaded turn would lose its duration. Emitting
        the SAME event here, on the recorded comm (the one the economics door's
        ``accounting.usage`` cost badge also rides), threads ``elapsed_ms`` into that
        artifact so reload restores the time exactly like React. Field name/shape match
        BaseWorkflow so the same reload reader surfaces it. Best-effort: a timing-emit
        failure never affects the turn."""
        try:
            comm = self.comm
            if comm is None:
                return
            await comm.event(
                agent="turn_controller",
                type="chat.turn.summary",
                route="chat.step",
                title="Turn Summary (Timings)",
                step="turn.summary",
                data={"elapsed_ms": int(total_ms), "started_ms": int(started_ms), "ended_ms": int(_now_ms())},
                status="completed",
            )
        except Exception:
            LOGGER.warning("[ported-langgraph] turn-timing emit failed", exc_info=True)

    # ── per-turn economics persistence (reload) ───────────────────────────────

    async def post_run_hook(
        self,
        *,
        state: Dict[str, Any],
        result: Dict[str, Any],
        econ_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist this turn's recorded chat events as the ``conv.artifacts.events``
        artifact the conversation reload replays.

        The economics door emits the turn's cost live (``accounting.usage`` — the $
        badge) and this app emits the turn's elapsed time (``chat.turn.summary``);
        both are recorded on the turn's comm (recording started by the base
        ``pre_run_hook``). But a run-to-completion app writes no React timeline, so
        without this the recorded events are never saved — the turn streamed cost +
        time live yet a reloaded turn showed neither. Persisting the SAME events
        artifact the React/workspace path persists (its ``post_run_hook`` does exactly
        this) makes reload restore both, via the shared SDK mechanism (no hand-rolled
        economics format). Best-effort by construction: a persistence failure never
        affects the turn.

        The saved-artifact user is threaded from the economics/authority projection
        when the raw ``user`` state key is empty (this app carries the user on the
        authority projection — mirrors ``_conversation_is_new``'s user resolution), so
        the artifact is scoped to the same (user, conversation) the reload reads."""
        await super().post_run_hook(state=state, result=result, econ_ctx=econ_ctx or {})
        # Scope the artifact to the record user. `_save_events_artifact` reads
        # `state["user"]`; this app's turn carries the user on the authority
        # projection, so fall back to it without mutating the caller's state.
        save_state = state
        if not str(state.get("user") or "").strip():
            record_user = str(
                state.get("economics_user")
                or state.get("authority_user")
                or state.get("actor_user")
                or state.get("fingerprint")
                or ""
            ).strip()
            if record_user:
                save_state = dict(state)
                save_state["user"] = record_user
        await self._save_events_artifact(state=save_state)
        # Subsystem/canvas stream replay on reload (the code-exec panel): persist
        # this turn's delta aggregates as conv.artifacts.stream — the same artifact
        # React saves itself; the fallback is inert on rich-log turns.
        await self._persist_stream_artifacts_fallback(state=save_state)

    # ── first-turn conversation title ─────────────────────────────────────────

    async def _finalize_conversation_title(
        self, *, state: Dict[str, Any], conversation_id: str, question: str,
        answer: Optional[str] = None, title_role: Optional[str] = None,
    ) -> None:
        """Propose a short conversation title on the FIRST turn, stream it to the
        client, and stash it on ``state`` for the turn recorder to persist.

        Generated from the user's QUESTION (an optional ``answer`` adds signal when
        available) so it can run BEFORE the agent — the title then appears even if
        the agent's turn later errors, and never depends on a successful answer. The
        first-turn signal is framework-neutral: a new conversation has no prior
        recorded turn log. Uses the reusable SDK utility directly (no ctx_browser /
        no thinking stream — this is a run-to-completion bundle). Fail-open by
        construction: any failure leaves the turn untouched."""
        try:
            question = (question or "").strip()
            answer = (answer or "").strip()
            conversation_id = str(
                conversation_id or state.get("conversation_id") or state.get("session_id") or ""
            ).strip()
            svc = getattr(self, "models_service", None)
            LOGGER.info(
                "[ported-langgraph] title check conversation=%s question_len=%d svc=%s",
                conversation_id, len(question), "set" if svc is not None else "NONE",
            )
            if not question or not conversation_id or svc is None:
                LOGGER.info("[ported-langgraph] title SKIP: missing question/conversation/model-service")
                return
            if not await self._conversation_is_new(state=state, conversation_id=conversation_id):
                LOGGER.info("[ported-langgraph] title SKIP: conversation not new conversation=%s", conversation_id)
                return
            # Use the AGENT's own answer role for the title (a known-good modern
            # model that follows the two-channel protocol), not the unconfigured
            # `gate.simple` default. Falls back to the utility default when unset.
            title_kwargs = {"role": title_role} if title_role else {}
            title = (await generate_conversation_title(
                svc, user_message=question, answer=answer or None, **title_kwargs,
            ) or "").strip()
            if not title:
                LOGGER.info("[ported-langgraph] title SKIP: model returned an empty title")
                return
            # Persist seam: the framework-neutral recorder reads this off `result`.
            state["conversation_title"] = title
            _comm = comm_ctx.get_comm()
            try:
                LOGGER.info(
                    "[ported-langgraph] conversation-title generated conversation=%s title=%r "
                    "comm=%s turn=%s — emitting",
                    conversation_id, title, ("set" if _comm is not None else "NONE"),
                    str(state.get("turn_id") or ""),
                )
            except Exception:
                pass
            # Emit seam: the SAME chat event the React workflow emits, streamed via
            # this turn's comm (the one the bundle already streams through), so the
            # chat component updates the conversation header live.
            await emit_conversation_title_event(
                _comm,
                conversation_id=conversation_id,
                turn_id=str(state.get("turn_id") or "").strip(),
                title=title,
            )
            try:
                LOGGER.info(
                    "[ported-langgraph] conversation-title emitted conversation=%s", conversation_id
                )
            except Exception:
                pass
        except Exception:
            LOGGER.warning(
                "[ported-langgraph] conversation-title generation/emit FAILED", exc_info=True
            )

    async def _conversation_is_new(self, *, state: Dict[str, Any], conversation_id: str) -> bool:
        """A conversation is new when it has no prior recorded turn (the current
        turn's log is written after ``execute_core``). Read the platform conversation
        record — the same store the conversation list reads — so the signal matches
        what the user sees. Fail-safe to "not new" (skip the title) on any error."""
        try:
            client = await self.get_ctx_client()
            if client is None:
                LOGGER.info("[ported-langgraph] is_new: NO ctx client (pg_pool missing?) -> not new")
                return False
            # Read under the SAME user the door records the turn log under: the
            # economics door writes the minimal turn log under its `user_id`
            # (== state["economics_user"], the projected-authority record user),
            # NOT the raw `actor_user`/`user`/`fingerprint` state keys — those can
            # be empty when the user is carried only on the authority projection
            # (comm user_obj / identity_authority). Preferring `economics_user`
            # keeps record, list, and this probe agreed on (user, conversation);
            # the raw keys stay as fallbacks for a non-economics run().
            user_id = str(
                state.get("economics_user")
                or state.get("authority_user")
                or state.get("actor_user")
                or state.get("user")
                or state.get("fingerprint")
                or ""
            ).strip()
            if not user_id or not conversation_id:
                LOGGER.info(
                    "[ported-langgraph] is_new: empty user_id=%r or conversation_id=%r -> not new",
                    user_id, conversation_id,
                )
                return False
            res = await client.recent(
                kinds=["artifact:turn.log"],
                roles=("artifact",),
                limit=1,
                days=365,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            items = res.get("items") or []
            LOGGER.info(
                "[ported-langgraph] is_new probe user=%s conversation=%s prior_turn_logs=%d -> new=%s",
                user_id, conversation_id, len(items), not items,
            )
            return not items
        except Exception:
            LOGGER.warning("[ported-langgraph] is_new probe FAILED -> not new", exc_info=True)
            return False

    # ── scene chat widgets (one per agent) ────────────────────────────────────
    # The scene mounts two chat tiles as iframes at `widgets/chat_lg_solution`
    # and `widgets/chat_lg_react`. A widget is only reachable once it is declared
    # in the bundle MANIFEST — that declaration is the `@ui_widget` decorator
    # here, NOT the `ui.widgets` build config (which only builds the assets). Each
    # method returns a tiny static fallback; the real UI is served from the built
    # `sdk://solutions/chat/ui/widget` assets (per that alias's `ui.widgets`
    # entry), each build agent-bound to its own agent via VITE_CHAT_AGENT_ID.

    @api(
        alias="chat_lg_solution_widget",
        route="operations",
        **_api_visibility("chat_lg_solution_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-left-right",
            "lucide": "MessagesSquare",
        },
        alias="chat_lg_solution",
        **_widget_visibility("chat_lg_solution"),
    )
    def chat_lg_solution_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "lg-solution chat is served from sdk://solutions/chat/ui/widget after build."
            "</div>"
        ]

    @api(
        alias="chat_lg_react_widget",
        route="operations",
        **_api_visibility("chat_lg_react_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-oval-left-ellipsis",
            "lucide": "MessageCircleMore",
        },
        alias="chat_lg_react",
        **_widget_visibility("chat_lg_react"),
    )
    def chat_lg_react_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "lg-react chat is served from sdk://solutions/chat/ui/widget after build."
            "</div>"
        ]

    @api(
        method="POST",
        alias="telegram_webhook",
        route="public",
    )
    async def telegram_webhook(self, request: Any = None, **update) -> Dict[str, Any]:
        """The shared ingress: the Telegram Bot API webhook.

        `route="public"` exposes the route at the proc layer without platform auth
        — the trust boundary is the Telegram webhook secret, which the SDK verifies
        before any work. The SDK resolves the Telegram user into a `telegram_<id>`
        platform identity and drives the SAME turn (`execute_core`) for the DEFAULT
        agent, then renders the answer back over the Bot API. Thin: routes only."""
        return await telegram_ingress.handle_webhook(self, request=request, **update)

    @api(
        method="POST",
        alias="scene_object_action",
        route="operations",
        **_api_visibility("scene_object_action"),
    )
    async def scene_object_action(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Serve the chat component's generic object-action operation for THIS bundle's
        hosted conversation FILES. The chat file card calls `scene_object_action` for
        any object ref; this bundle has no canvas/scene board — only conversation files
        a turn produced (e.g. code-exec output). For a `conv:fi:` file ref, delegate to
        the platform file resolver: it reads the bytes from conversation storage and
        returns a `download_url` the client fetches. Without this operation the file is
        hosted and shown but the Download action has no endpoint to serve it.

        Identity: the file is stored under the turn owner (svc-first — see
        `resolve_request_identity`), so the download resolves under that same owner;
        tenant/project come from the bundle runtime."""
        from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import resolve_event_ref_action
        from kdcube_ai_app.apps.chat.sdk.event_identity import resolve_request_identity

        payload: Dict[str, Any] = (
            {str(k): v for k, v in data.items()} if isinstance(data, dict)
            else {
                str(k): v for k, v in kwargs.items()
                if k not in {"request", "alias", "route", "endpoint_alias"} and v is not None
            }
        )
        ident = self.runtime_identity()
        owner = ""
        try:
            owner = str(resolve_request_identity(self.comm).get("owner") or "").strip()
        except Exception:
            owner = ""
        user_id = str(
            payload.get("user_id") or owner or getattr(self.comm, "user_id", "") or ""
        ).strip()
        result = await resolve_event_ref_action(
            payload,
            tenant=ident.get("tenant") or "default",
            project=ident.get("project") or "default",
            user_id=user_id,
            storage_path=str(getattr(self.settings, "STORAGE_PATH", "")),
            require_embedded_conversation=True,
        )
        try:
            LOGGER.info(
                "[ported-langgraph] scene_object_action action=%s ref=%s user=%s ok=%s error=%s",
                payload.get("action"),
                payload.get("ref") or payload.get("object_ref") or payload.get("event_ref"),
                user_id, (result or {}).get("ok"), (result or {}).get("error"),
            )
        except Exception:
            pass
        return result
