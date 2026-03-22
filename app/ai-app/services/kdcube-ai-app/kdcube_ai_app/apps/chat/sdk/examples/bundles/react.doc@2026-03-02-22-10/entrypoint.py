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
#   4. Manages knowledge space — builds it on bundle load and reconciles it only
#      when runtime bundle props change or load-time preparation did not happen
#   5. Defines role_models mapping logical roles → concrete LLM models
#
# Key base class: BaseEntrypoint
#   Your bundle overrides:
#     - configuration       → role_models + knowledge repo/docs/src/deploy config
#     - on_bundle_load()     → build knowledge space for this tenant/project bundle instance
#     - pre_run_hook()       → reconcile knowledge space only if runtime props changed
#     - execute_core()       → the async method that runs the compiled LangGraph
#
# Knowledge space:
#   The bundle can pull docs from a git repo (configured via bundle_props)
#   or auto-discover docs/src/deploy roots relative to the ai-app root.
#   The knowledge resolver (knowledge/resolver.py) is loaded via importlib
#   with a shared module name so that entrypoint.py and tools/react_tools.py
#   both access the same KNOWLEDGE_ROOT state.

from __future__ import annotations

import pathlib
import os
import shutil
import traceback
import fcntl
from contextlib import contextmanager
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


def _knowledge_lock_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.lock"


def _knowledge_signature_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.signature"


@contextmanager
def _knowledge_build_lock(storage_root: pathlib.Path):
    storage_root.mkdir(parents=True, exist_ok=True)
    lock_path = _knowledge_lock_path(storage_root)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_shared_knowledge_signature(storage_root: pathlib.Path) -> str | None:
    path = _knowledge_signature_path(storage_root)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return raw or None


def _write_shared_knowledge_signature(storage_root: pathlib.Path, signature: str) -> None:
    path = _knowledge_signature_path(storage_root)
    path.write_text(f"{signature}\n", encoding="utf-8")


def _knowledge_outputs_ready(
    *,
    storage_root: pathlib.Path,
    docs_root: pathlib.Path | None,
    src_root: pathlib.Path | None,
    deploy_root: pathlib.Path | None,
    tests_root: pathlib.Path | None,
) -> bool:
    if not (storage_root / "index.json").exists():
        return False
    if not (storage_root / "index.md").exists():
        return False
    if docs_root and not (storage_root / "docs").exists():
        return False
    if src_root and not (storage_root / "src").exists():
        return False
    if deploy_root and not (storage_root / "deploy").exists():
        return False
    if tests_root and not (storage_root / "tests").exists():
        return False
    return True


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

            try:
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
                    bundle_props=self.bundle_props,
                )
                try:
                    # Expose bundle-specific knowledge resolvers to the React runtime
                    # so the solver can search/read the knowledge space via ks: paths
                    orch.runtime_ctx.knowledge_search_fn = knowledge_resolver.search_knowledge
                    orch.runtime_ctx.knowledge_read_fn = knowledge_resolver.read_knowledge
                except Exception:
                    pass

                # Execute the workflow, passing the full turn state
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
                await self.report_turn_error(state=state, exc=e, title="Turn Error")

            return state

        # Wire the graph: START → orchestrate → END (single-node linear graph)
        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    def on_bundle_load(self, **kwargs) -> None:
        """Build bundle knowledge space once when this tenant/project bundle instance is loaded."""
        self._ensure_knowledge_space(reason="on_bundle_load")
        return None

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
        """Reconcile knowledge space only if load-time prep did not happen or config changed."""
        await super().pre_run_hook(state=state)
        self._reconcile_knowledge_space(reason="pre_run_hook")
        return None

    def _resolve_knowledge_paths(
        self,
        *,
        bundle_root: pathlib.Path,
        storage_root: pathlib.Path,
    ) -> tuple[
        pathlib.Path | None,
        pathlib.Path | None,
        pathlib.Path | None,
        pathlib.Path | None,
        bool,
        str | None,
        str | None,
    ]:
        """
        Resolve docs/src/deploy/tests roots from bundle props.
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
        tests_root_raw = (knowledge_def.get("tests_root") or "").strip()
        validate = knowledge_def.get("validate_refs")
        validate_refs = True if validate is None else bool(validate)

        default_repo_tests_rel = (
            "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests"
        )

        # If nothing is configured, try to use local repo (host dev) or fall back
        # to the public platform repo (container/compose).
        if not repo and not docs_root_raw and not src_root_raw and not deploy_root_raw and not tests_root_raw:
            for parent in bundle_root.resolve().parents:
                if (parent / "docs").is_dir() and (parent / "services").is_dir():
                    docs_root_raw = str((parent / "docs").resolve())
                    src_root_raw = str((parent / "services" / "kdcube-ai-app" / "kdcube_ai_app").resolve())
                    deploy_root_raw = str((parent / "deployment").resolve())
                    local_tests_root = (
                        parent
                        / "services"
                        / "kdcube-ai-app"
                        / "kdcube_ai_app"
                        / "apps"
                        / "chat"
                        / "sdk"
                        / "examples"
                        / "tests"
                    )
                    if local_tests_root.is_dir():
                        tests_root_raw = str(local_tests_root.resolve())
                    break
            if not docs_root_raw:
                repo = (os.getenv("KDCUBE_KNOWLEDGE_REPO") or "https://github.com/kdcube/kdcube-ai-app.git").strip()
                if not ref:
                    ref = (os.getenv("KDCUBE_KNOWLEDGE_REF") or "").strip()
                docs_root_raw = "app/ai-app/docs"
                src_root_raw = "app/ai-app/services/kdcube-ai-app/kdcube_ai_app"
                deploy_root_raw = "app/ai-app/deployment"
                tests_root_raw = default_repo_tests_rel

        repo_root = None
        if repo:
            try:
                from kdcube_ai_app.infra.plugin.git_bundle import ensure_git_bundle, bundle_dir_for_git
                repos_root = (storage_root / "repos").resolve()
                # Git auth is handled by git_bundle (SSH or HTTPS token via GIT_HTTP_TOKEN).
                paths = ensure_git_bundle(
                    bundle_id=f"{BUNDLE_ID}.knowledge",
                    git_url=repo,
                    git_ref=ref or None,
                    git_subdir=None,
                    bundles_root=repos_root,
                    logger=self.logger,
                )
                repo_root = paths.repo_root
                # Cleanup older clones for this knowledge repo (keep current).
                try:
                    base_prefix = bundle_dir_for_git(
                        bundle_id=f"{BUNDLE_ID}.knowledge",
                        git_url=repo,
                        git_ref=None,
                    )
                    for child in repos_root.iterdir():
                        if not child.is_dir():
                            continue
                        if child.resolve() == repo_root.resolve():
                            continue
                        if child.name == base_prefix or child.name.startswith(f"{base_prefix}__"):
                            shutil.rmtree(child, ignore_errors=True)
                except Exception:
                    self.logger.log(traceback.format_exc(), "WARNING")
            except Exception:
                self.logger.log(traceback.format_exc(), "WARNING")

        base_root = repo_root or bundle_root
        if not tests_root_raw:
            candidate = (base_root / default_repo_tests_rel).resolve()
            if candidate.is_dir():
                tests_root_raw = str(candidate)
        docs_root = None
        src_root = None
        deploy_root = None
        tests_root = None
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
        if tests_root_raw:
            tests_root = pathlib.Path(tests_root_raw)
            if not tests_root.is_absolute():
                tests_root = (base_root / tests_root).resolve()
        return docs_root, src_root, deploy_root, tests_root, validate_refs, repo, ref or None

    def _resolve_knowledge_setup(
        self,
    ) -> tuple[
        pathlib.Path | None,
        pathlib.Path,
        pathlib.Path | None,
        pathlib.Path | None,
        pathlib.Path | None,
        pathlib.Path | None,
        bool,
        str | None,
        str | None,
        str | None,
    ]:
        ws_root = self.bundle_storage_root()
        bundle_root = None
        try:
            spec = getattr(self.config, "ai_bundle_spec", None)
            if spec and getattr(spec, "path", None):
                bundle_root = pathlib.Path(spec.path).resolve()
        except Exception:
            bundle_root = None
        if not bundle_root:
            bundle_root = pathlib.Path(__file__).resolve().parent

        if not ws_root:
            return (
                None,
                bundle_root,
                None,
                None,
                None,
                None,
                True,
                None,
                None,
                None,
            )

        docs_root, src_root, deploy_root, tests_root, validate_refs, repo, ref = self._resolve_knowledge_paths(
            bundle_root=bundle_root,
            storage_root=ws_root,
        )
        signature = f"{repo}|{ref}|{docs_root}|{src_root}|{deploy_root}|{tests_root}|{validate_refs}"
        return (
            ws_root,
            bundle_root,
            docs_root,
            src_root,
            deploy_root,
            tests_root,
            validate_refs,
            repo,
            ref,
            signature,
        )

    def _ensure_knowledge_space(self, *, reason: str) -> None:
        """
        Build or refresh the knowledge index under bundle storage.
        Uses a signature (repo|ref|roots) to skip rebuilding when nothing changed.
        """
        try:
            (
                ws_root,
                bundle_root,
                docs_root,
                src_root,
                deploy_root,
                tests_root,
                validate_refs,
                repo,
                ref,
                signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                self.logger.log(
                    f"[react.doc] knowledge build skipped ({reason}): bundle storage root is unavailable.",
                    "WARNING",
                )
                return None
            self.logger.log(
                f"[react.doc] knowledge build start ({reason}): storage={ws_root}",
                "INFO",
            )
            self.logger.log(
                (
                    f"[react.doc] resolved knowledge paths ({reason}): "
                    f"repo={repo or '<local>'} ref={ref or '<default>'} "
                    f"docs={docs_root or '<missing>'} src={src_root or '<missing>'} "
                    f"deploy={deploy_root or '<missing>'} tests={tests_root or '<missing>'} "
                    f"validate_refs={validate_refs}"
                ),
                "INFO",
            )
            with _knowledge_build_lock(ws_root):
                outputs_ready = _knowledge_outputs_ready(
                    storage_root=ws_root,
                    docs_root=docs_root,
                    src_root=src_root,
                    deploy_root=deploy_root,
                    tests_root=tests_root,
                )
                shared_signature = _read_shared_knowledge_signature(ws_root)
                if shared_signature == signature and outputs_ready:
                    self._knowledge_signature = signature
                    self.logger.log(
                        f"[react.doc] knowledge build skipped ({reason}): shared signature cache hit for storage={ws_root}",
                        "INFO",
                    )
                    return None
                if self._knowledge_signature == signature and outputs_ready:
                    if shared_signature != signature:
                        _write_shared_knowledge_signature(ws_root, signature)
                    self.logger.log(
                        f"[react.doc] knowledge build skipped ({reason}): local signature cache hit for storage={ws_root}",
                        "INFO",
                    )
                    return None
                # Build or refresh the knowledge index under bundle storage.
                knowledge_resolver.prepare_knowledge_space(
                    bundle_root=bundle_root,
                    knowledge_root=ws_root,
                    docs_root=docs_root,
                    src_root=src_root,
                    deploy_root=deploy_root,
                    tests_root=tests_root,
                    validate_refs=validate_refs,
                    logger=self.logger,
                )
                _write_shared_knowledge_signature(ws_root, signature)
                self._knowledge_signature = signature
            self.logger.log(
                (
                    f"[react.doc] knowledge build done ({reason}): "
                    f"storage={ws_root} "
                    f"docs={(ws_root / 'docs').exists()} "
                    f"src={(ws_root / 'src').exists()} "
                    f"deploy={(ws_root / 'deploy').exists()} "
                    f"tests={(ws_root / 'tests').exists()} "
                    f"index_json={(ws_root / 'index.json').exists()} "
                    f"index_md={(ws_root / 'index.md').exists()}"
                ),
                "INFO",
            )
        except Exception:
            self.logger.log(f"[react.doc] knowledge build failed ({reason})", "WARNING")
            self.logger.log(traceback.format_exc(), "WARNING")
        return None

    def _reconcile_knowledge_space(self, *, reason: str) -> None:
        """
        Re-check current bundle props at run time and rebuild only if load-time prep
        never happened or the effective knowledge signature changed.
        """
        try:
            (
                ws_root,
                _bundle_root,
                docs_root,
                src_root,
                deploy_root,
                tests_root,
                validate_refs,
                repo,
                ref,
                signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                self.logger.log(
                    f"[react.doc] knowledge reconcile skipped ({reason}): bundle storage root is unavailable.",
                    "WARNING",
                )
                return None
            if self._knowledge_signature is None:
                self.logger.log(
                    f"[react.doc] knowledge reconcile ({reason}): load-time signature missing, building now.",
                    "INFO",
                )
                return self._ensure_knowledge_space(reason=reason)
            if self._knowledge_signature != signature:
                self.logger.log(
                    (
                        f"[react.doc] knowledge reconcile ({reason}): signature changed. "
                        f"repo={repo or '<local>'} ref={ref or '<default>'} "
                        f"docs={docs_root or '<missing>'} src={src_root or '<missing>'} "
                        f"deploy={deploy_root or '<missing>'} tests={tests_root or '<missing>'} "
                        f"validate_refs={validate_refs}"
                    ),
                    "INFO",
                )
                return self._ensure_knowledge_space(reason=reason)
        except Exception:
            self.logger.log(f"[react.doc] knowledge reconcile failed ({reason})", "WARNING")
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
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},                         # Gate — fast, lightweight
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},            # Answer — strong generation
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},              # Solver coordinator
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45}, # Solver — hard reasoning
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},  # Solver — routine steps
        }.items():
            role_models.setdefault(key, value)
        config["role_models"] = role_models
        # Knowledge repository — docs + sources pulled on startup.
        # If repo is set, docs/src/deploy roots are resolved relative to the repo root.
        # If repo is empty, roots are resolved relative to the bundle directory.
        knowledge = dict(config.get("knowledge") or {})
        knowledge.setdefault("repo", "")            # Git URL (e.g. https://github.com/org/repo); default: empty, then auto-detect local ai-app repo or fall back to KDCUBE_KNOWLEDGE_REPO / public repo
        knowledge.setdefault("ref", "")             # Git ref (branch/tag/commit); empty = default branch; default: empty, then use KDCUBE_KNOWLEDGE_REF or the repo default branch
        knowledge.setdefault("docs_root", "")       # Path to docs/ directory; default: empty, then auto-detect local docs/ or use app/ai-app/docs under the repo
        knowledge.setdefault("src_root", "")        # Path to source code root; default: empty, then auto-detect local services/kdcube-ai-app/kdcube_ai_app or use app/ai-app/services/kdcube-ai-app/kdcube_ai_app under the repo
        knowledge.setdefault("deploy_root", "")     # Path to deployment configs (compose, env, dockerfiles); default: empty, then auto-detect local deployment/ or use app/ai-app/deployment under the repo
        knowledge.setdefault("validate_refs", True) # Check that code refs in docs point to existing files; default: True
        knowledge.setdefault("tests_root", "")      # Path to bundle test fixtures/docs exposed as ks:tests; default: empty, then auto-detect local sdk/examples/tests or use app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests under the repo
        config["knowledge"] = knowledge
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
