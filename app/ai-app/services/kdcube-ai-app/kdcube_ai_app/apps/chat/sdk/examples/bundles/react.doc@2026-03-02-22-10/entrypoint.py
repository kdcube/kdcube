# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point for the react.doc documentation-reader assistant.
# Registers the bundle in the plugin system via @agentic_workflow and
# manages the knowledge space lifecycle (docs, sources, deployment artifacts).
#
# What it does:
#   1. Registers the bundle under the name "react.doc" (@agentic_workflow)
#   2. Builds a LangGraph StateGraph with a single "orchestrate" node
#   3. The "orchestrate" node initializes all dependencies (DB, indexes, RAG)
#      and delegates execution to WithReactWorkflow.process()
#   4. Manages knowledge space — on each turn, ensures the docs index is built
#      and up-to-date (signature-based caching avoids redundant rebuilds)
#   5. Defines role_models mapping logical roles → concrete LLM models
#
# Key base class: BaseEntrypoint
#   Your bundle overrides:
#     - configuration       → role_models (which LLM for which agent)
#     - bundle_props_defaults → knowledge repo/docs/src/deploy config
#     - pre_run_hook()       → ensure knowledge space is ready before each turn
#     - execute_core()       → the async method that runs the compiled LangGraph
#
# Knowledge space:
#   The bundle can pull docs from a git repo (configured via bundle_props)
#   or auto-discover docs/src/deploy roots relative to the ai-app root.
#   The knowledge resolver (knowledge/resolver.py) is loaded via importlib
#   with a shared module name so that entrypoint.py and tools/react_tools.py
#   both access the same KNOWLEDGE_ROOT state.

from __future__ import annotations

import traceback
import pathlib
from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .orchestrator.workflow import WithReactWorkflow
from .event_filter import BundleEventFilter
import importlib.util
import sys

# Unique bundle ID — used by the plugin system to discover and load this bundle
BUNDLE_ID = "react.doc"


# @agentic_workflow — registration decorator: on application startup the system
# scans all bundles and auto-loads classes decorated with this.
# priority=100 — selection order when multiple bundles match (higher = preferred)
@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ReactWorkflow(BaseEntrypoint):
    """React.doc bundle — gate + ReAct solver with knowledge space (docs/src/deploy)."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
            # Inject bundle-specific event filter (controls which SSE events reach users)
            event_filter=BundleEventFilter(),
        )
        # Signature-based cache key — avoids rebuilding the knowledge index on every turn
        self._knowledge_signature: str | None = None
        # Graph is built once at init and reused across invocations
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build a single-node LangGraph that runs the full workflow."""
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            """
            The only graph node. Initializes all SDK services lazily
            (imports inside the function to keep startup fast) and then
            delegates the actual work to WithReactWorkflow.
            """
            # -- Lazy imports: these services are only needed at execution time --
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

            # -- Initialize SDK services (DB-backed) --
            conv_idx = ConvIndex(pool=self.pg_pool)       # Conversation vector index
            kb = KBClient(pool=self.pg_pool)              # Knowledge base client
            store = ConversationStore(self.settings.STORAGE_PATH)  # Conversation file store
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool) # Ticket storage

            await conv_idx.init()
            await kb.init()
            await conv_ticket_store.init()

            # RAG client — retrieves relevant context from conversation history
            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

            # Create the workflow instance with all dependencies injected
            orch = WithReactWorkflow(
                conv_idx=conv_idx,
                kb=kb,
                store=store,
                comm=self.comm,
                model_service=self.models_service,
                conv_ticket_store=conv_ticket_store,
                config=self.config,
                comm_context=self.comm_context,
                ctx_client=ctx_client,
            )
            try:
                # Expose bundle-specific knowledge resolvers to the React runtime
                # so the solver can search/read the knowledge space via ks: paths
                orch.runtime_ctx.knowledge_search_fn = knowledge_resolver.search_knowledge
                orch.runtime_ctx.knowledge_read_fn = knowledge_resolver.read_knowledge
            except Exception:
                pass

            # Execute the workflow, passing the full turn state
            try:
                res = await orch.process({
                    "request_id": state["request_id"],
                    "tenant": state["tenant"],
                    "project": state["project"],
                    "user": state["user"],
                    "user_type": state["user_type"] or "anonymous",
                    "session_id": state["session_id"],
                    "conversation_id": state["conversation_id"],
                    "turn_id": state["turn_id"],
                    "text": state["text"],
                    "attachments": state.get("attachments") or [],
                })
                if not isinstance(res, dict):
                    res = {}
                state["final_answer"] = res.get("answer") or ""
                state["followups"] = res.get("followups") or []
            except Exception as e:
                self.logger.log(traceback.format_exc(), "ERROR")
                state["error_message"] = str(e)
                await self.comm.step(
                    step="turn",
                    status="error",
                    title="Turn Error",
                    data={"error": str(e)},
                    markdown=f"**Error:** {e}",
                )

            return state

        # Wire the graph: START → orchestrate → END (single-node linear graph)
        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    @property
    def bundle_props_defaults(self) -> Dict[str, Any]:
        """
        Declare configurable knowledge-space properties.
        These can be overridden per-tenant via bundle props in the admin UI.
        """
        defaults = dict(super().bundle_props_defaults or {})
        defaults.update({
            # Knowledge repository — docs + sources pulled on startup.
            # If repo is set, docs/src/deploy roots are resolved relative to the repo root.
            # If repo is empty, roots are resolved relative to the bundle directory.
            "knowledge": {
                "repo": "",           # Git URL (e.g. https://github.com/org/repo)
                "ref": "",            # Git ref (branch/tag/commit); empty = default branch
                "docs_root": "",      # Path to docs/ directory
                "src_root": "",       # Path to source code root
                "deploy_root": "",    # Path to deployment configs (compose, env, dockerfiles)
                "validate_refs": True, # Check that code refs in docs point to existing files
            }
        })
        return defaults

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
        """Called before every turn — ensures knowledge index is built and current."""
        await super().pre_run_hook(state=state)
        self._ensure_knowledge_space()
        return None

    def _resolve_knowledge_paths(
        self,
        *,
        bundle_root: pathlib.Path,
        storage_root: pathlib.Path,
    ) -> tuple[pathlib.Path | None, pathlib.Path | None, pathlib.Path | None, bool, str | None, str | None]:
        """
        Resolve docs/src/deploy roots from bundle props.
        If a git repo is configured, clones it first via ensure_git_bundle.
        Paths can be absolute or relative to the repo/bundle root.
        """
        props = dict(self.bundle_props or {})
        knowledge_def = props.get("knowledge") or {}
        repo = (knowledge_def.get("repo") or "").strip()
        ref = (knowledge_def.get("ref") or "").strip()
        docs_root_raw = (knowledge_def.get("docs_root") or "").strip()
        src_root_raw = (knowledge_def.get("src_root") or "").strip()
        deploy_root_raw = (knowledge_def.get("deploy_root") or "").strip()
        validate = knowledge_def.get("validate_refs")
        validate_refs = True if validate is None else bool(validate)

        repo_root = None
        if repo:
            try:
                from kdcube_ai_app.infra.plugin.git_bundle import ensure_git_bundle
                repos_root = (storage_root / "repos").resolve()
                paths = ensure_git_bundle(
                    bundle_id=f"{BUNDLE_ID}.knowledge",
                    git_url=repo,
                    git_ref=ref or None,
                    git_subdir=None,
                    bundles_root=repos_root,
                    logger=self.logger,
                )
                repo_root = paths.repo_root
            except Exception:
                self.logger.log(traceback.format_exc(), "WARNING")

        base_root = repo_root or bundle_root
        docs_root = None
        src_root = None
        deploy_root = None
        if docs_root_raw:
            docs_root = pathlib.Path(docs_root_raw)
            if not docs_root.is_absolute():
                docs_root = (base_root / docs_root).resolve()
        elif repo_root:
            self.logger.log("[react.doc] knowledge.docs_root is required when knowledge.repo is set.", "WARNING")
        if src_root_raw:
            src_root = pathlib.Path(src_root_raw)
            if not src_root.is_absolute():
                src_root = (base_root / src_root).resolve()
        elif repo_root:
            self.logger.log("[react.doc] knowledge.src_root is required when knowledge.repo is set.", "WARNING")
        if deploy_root_raw:
            deploy_root = pathlib.Path(deploy_root_raw)
            if not deploy_root.is_absolute():
                deploy_root = (base_root / deploy_root).resolve()
        return docs_root, src_root, deploy_root, validate_refs, repo, ref or None

    def _ensure_knowledge_space(self) -> None:
        """
        Build or refresh the knowledge index under bundle storage.
        Uses a signature (repo|ref|roots) to skip rebuilding when nothing changed.
        """
        try:
            ws_root = self.bundle_storage_root()
            if not ws_root:
                return None
            bundle_root = None
            try:
                spec = getattr(self.config, "ai_bundle_spec", None)
                if spec and getattr(spec, "path", None):
                    bundle_root = pathlib.Path(spec.path).resolve()
            except Exception:
                bundle_root = None
            if not bundle_root:
                bundle_root = pathlib.Path(__file__).resolve().parent

            docs_root, src_root, deploy_root, validate_refs, repo, ref = self._resolve_knowledge_paths(
                bundle_root=bundle_root,
                storage_root=ws_root,
            )
            signature = f"{repo}|{ref}|{docs_root}|{src_root}|{deploy_root}|{validate_refs}"
            if self._knowledge_signature == signature:
                return None
            # Build or refresh the knowledge index under bundle storage.
            knowledge_resolver.prepare_knowledge_space(
                bundle_root=bundle_root,
                knowledge_root=ws_root,
                docs_root=docs_root,
                src_root=src_root,
                deploy_root=deploy_root,
                validate_refs=validate_refs,
                logger=self.logger,
            )
            self._knowledge_signature = signature
        except Exception:
            self.logger.log(traceback.format_exc(), "WARNING")
        return None

    @property
    def configuration(self) -> Dict[str, Any]:
        """
        Override model configuration for this bundle.
        role_models maps logical roles → specific LLM providers/models.
        The gate agent uses a cheap/fast model (Haiku), while the solver
        uses a strong model (Sonnet) for hard reasoning and answer generation.
        """
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.update({
            "gate.simple": {"provider": "anthropic", "model": haiku_4},                         # Gate — fast, lightweight
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},            # Answer — strong generation
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},              # Solver coordinator
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45}, # Solver — hard reasoning
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},  # Solver — routine steps

        })
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        """Required by BaseEntrypoint — runs the compiled LangGraph."""
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
def _load_knowledge_resolver():
    """
    Load knowledge resolver by file path so it shares the same module instance
    with tools/react_tools.py (single KNOWLEDGE_ROOT state).

    Why importlib? Both entrypoint.py and react_tools.py need to access the same
    KNOWLEDGE_ROOT global. Using a shared module name (_kdcube_react_doc_knowledge_resolver)
    ensures they get the same module object regardless of how they are loaded.
    """
    module_name = "_kdcube_react_doc_knowledge_resolver"
    if module_name in sys.modules:
        return sys.modules[module_name]
    bundle_root = pathlib.Path(__file__).resolve().parent
    resolver_path = bundle_root / "knowledge" / "resolver.py"
    spec = importlib.util.spec_from_file_location(module_name, str(resolver_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load knowledge resolver: {resolver_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod

knowledge_resolver = _load_knowledge_resolver()
