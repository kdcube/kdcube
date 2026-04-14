# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point for the react.code code-assistant bundle.
# Combines documentation knowledge space (docs/src/deploy) with
# Neo4j code knowledge graph for structural code exploration.
#
# Key differences from react.doc:
#   - Creates and manages a CodeGraphClient alongside the KB client
#   - Stores CodeGraphClient on sys.modules for tool access
#   - Passes code_graph to WithReactCodeWorkflow

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

from .orchestrator.workflow import WithReactCodeWorkflow
from .event_filter import BundleEventFilter
import importlib.util
import sys

BUNDLE_ID = "react.code"


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


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ReactCodeWorkflow(BaseEntrypoint):
    """React.code bundle — gate + ReAct solver with knowledge space + code graph."""

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
            event_filter=BundleEventFilter(),
        )
        self._knowledge_signature: str | None = None
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
            from kdcube_ai_app.apps.chat.sdk.retrieval.code_graph_client import create_code_graph_client

            conv_idx = ConvIndex(pool=self.pg_pool)
            kb = KBClient(pool=self.pg_pool)
            store = ConversationStore(self.settings.STORAGE_PATH)
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool)

            await conv_idx.init()
            await kb.init()
            await conv_ticket_store.init()

            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

            # Initialize code graph client (Neo4j)
            code_graph = create_code_graph_client()
            if code_graph.enabled:
                try:
                    await code_graph.init()
                    self.logger.log("[react.code] CodeGraphClient connected", "INFO")
                except Exception:
                    self.logger.log(f"[react.code] CodeGraphClient init failed: {traceback.format_exc()}", "WARNING")
                    from kdcube_ai_app.apps.chat.sdk.retrieval.code_graph_client import NullCodeGraphClient
                    code_graph = NullCodeGraphClient()

            # Read feature toggles from bundle props
            features = self.bundle_prop("features", default={})
            enable_code_graph = features.get("enable_code_graph", True)
            enable_knowledge_search = features.get("enable_knowledge_search", True)

            # When code graph feature is disabled, swap to null client
            if not enable_code_graph:
                from kdcube_ai_app.apps.chat.sdk.retrieval.code_graph_client import NullCodeGraphClient
                code_graph = NullCodeGraphClient()

            # Store client on shared module for tool access (importlib pattern)
            code_graph_state = _load_code_graph_state()
            code_graph_state.CLIENT = code_graph

            # Store knowledge search flag on resolver module global
            knowledge_resolver.SEARCH_ENABLED = bool(enable_knowledge_search)

            try:
                orch = WithReactCodeWorkflow(
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
                    code_graph=code_graph,
                )
                try:
                    orch.runtime_ctx.knowledge_search_fn = knowledge_resolver.search_knowledge
                    orch.runtime_ctx.knowledge_read_fn = knowledge_resolver.read_knowledge
                except Exception:
                    pass

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
            finally:
                # Clean up code graph client
                if code_graph.enabled:
                    try:
                        await code_graph.close()
                    except Exception:
                        pass

            return state

        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    def on_bundle_load(self, **kwargs) -> None:
        self._ensure_knowledge_space(reason="on_bundle_load")
        return None

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
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

        if not repo and not docs_root_raw and not src_root_raw and not deploy_root_raw and not tests_root_raw:
            for parent in bundle_root.resolve().parents:
                if (parent / "docs").is_dir() and (parent / "services").is_dir():
                    docs_root_raw = str((parent / "docs").resolve())
                    src_root_raw = str((parent / "services" / "kdcube-ai-app" / "kdcube_ai_app").resolve())
                    deploy_root_raw = str((parent / "deployment").resolve())
                    local_tests_root = (
                        parent / "services" / "kdcube-ai-app" / "kdcube_ai_app"
                        / "apps" / "chat" / "sdk" / "examples" / "tests"
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
                paths = ensure_git_bundle(
                    bundle_id=f"{BUNDLE_ID}.knowledge",
                    git_url=repo,
                    git_ref=ref or None,
                    git_subdir=None,
                    bundles_root=repos_root,
                    logger=self.logger,
                )
                repo_root = paths.repo_root
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
            self.logger.log("[react.code] knowledge.docs_root is required when knowledge.repo is set.", "WARNING")
        if src_root_raw:
            src_root = pathlib.Path(src_root_raw)
            if not src_root.is_absolute():
                src_root = (base_root / src_root).resolve()
        elif repo_root:
            self.logger.log("[react.code] knowledge.src_root is required when knowledge.repo is set.", "WARNING")
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
            return (None, bundle_root, None, None, None, None, True, None, None, None)

        docs_root, src_root, deploy_root, tests_root, validate_refs, repo, ref = self._resolve_knowledge_paths(
            bundle_root=bundle_root,
            storage_root=ws_root,
        )
        signature = f"{repo}|{ref}|{docs_root}|{src_root}|{deploy_root}|{tests_root}|{validate_refs}"
        return (ws_root, bundle_root, docs_root, src_root, deploy_root, tests_root, validate_refs, repo, ref, signature)

    def _ensure_knowledge_space(self, *, reason: str) -> None:
        try:
            (
                ws_root, bundle_root, docs_root, src_root, deploy_root, tests_root,
                validate_refs, repo, ref, signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                self.logger.log(f"[react.code] knowledge build skipped ({reason}): storage root unavailable.", "WARNING")
                return None
            self.logger.log(f"[react.code] knowledge build start ({reason}): storage={ws_root}", "INFO")
            with _knowledge_build_lock(ws_root):
                outputs_ready = _knowledge_outputs_ready(
                    storage_root=ws_root, docs_root=docs_root, src_root=src_root,
                    deploy_root=deploy_root, tests_root=tests_root,
                )
                shared_signature = _read_shared_knowledge_signature(ws_root)
                if shared_signature == signature and outputs_ready:
                    self._knowledge_signature = signature
                    self.logger.log(f"[react.code] knowledge build skipped ({reason}): cache hit", "INFO")
                    return None
                if self._knowledge_signature == signature and outputs_ready:
                    if shared_signature != signature:
                        _write_shared_knowledge_signature(ws_root, signature)
                    return None
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
            self.logger.log(f"[react.code] knowledge build done ({reason})", "INFO")
        except Exception:
            self.logger.log(f"[react.code] knowledge build failed ({reason})", "WARNING")
            self.logger.log(traceback.format_exc(), "WARNING")
        return None

    def _reconcile_knowledge_space(self, *, reason: str) -> None:
        try:
            (
                ws_root, _bundle_root, docs_root, src_root, deploy_root, tests_root,
                validate_refs, repo, ref, signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                return None
            if self._knowledge_signature is None:
                return self._ensure_knowledge_space(reason=reason)
            if self._knowledge_signature != signature:
                return self._ensure_knowledge_space(reason=reason)
        except Exception:
            self.logger.log(f"[react.code] knowledge reconcile failed ({reason})", "WARNING")
            self.logger.log(traceback.format_exc(), "WARNING")
        return None

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},
        }.items():
            role_models.setdefault(key, value)
        config["role_models"] = role_models
        knowledge = dict(config.get("knowledge") or {})
        knowledge.setdefault("repo", "")
        knowledge.setdefault("ref", "")
        knowledge.setdefault("docs_root", "")
        knowledge.setdefault("src_root", "")
        knowledge.setdefault("deploy_root", "")
        knowledge.setdefault("validate_refs", True)
        knowledge.setdefault("tests_root", "")
        config["knowledge"] = knowledge
        features = dict(config.get("features") or {})
        features.setdefault("enable_knowledge_search", True)
        features.setdefault("enable_code_graph", True)
        config["features"] = features
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})


def _load_knowledge_resolver():
    module_name = "_kdcube_react_code_knowledge_resolver"
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


def _load_code_graph_state():
    """
    Load shared code graph state module (SDK level).
    Uses a shared module name (_kdcube_code_graph_state) so that
    any entrypoint and sdk/tools/code_graph_tools.py access the same CLIENT global.
    """
    module_name = "_kdcube_code_graph_state"
    if module_name in sys.modules:
        return sys.modules[module_name]
    # Resolve from SDK tools directory
    found = importlib.util.find_spec("kdcube_ai_app.apps.chat.sdk.tools.code_graph_state")
    if not found or not found.origin:
        raise ImportError("Cannot locate SDK code_graph_state module")
    state_path = pathlib.Path(found.origin)
    spec = importlib.util.spec_from_file_location(module_name, str(state_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load code graph state: {state_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod

knowledge_resolver = _load_knowledge_resolver()
