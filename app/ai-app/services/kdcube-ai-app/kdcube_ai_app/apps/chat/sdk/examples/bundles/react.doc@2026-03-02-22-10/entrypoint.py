# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

"""React.doc bundle entrypoint: prepares knowledge space and wires React hooks."""

import traceback
import pathlib
import os
import shutil
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

BUNDLE_ID = "react.doc"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ReactWorkflow(BaseEntrypoint):
    """Minimal bundle with context search + react + simple answer."""

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
                # Expose bundle-specific knowledge resolvers to React runtime.
                orch.runtime_ctx.knowledge_search_fn = knowledge_resolver.search_knowledge
                orch.runtime_ctx.knowledge_read_fn = knowledge_resolver.read_knowledge
            except Exception:
                pass

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

        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    @property
    def bundle_props_defaults(self) -> Dict[str, Any]:
        defaults = dict(super().bundle_props_defaults or {})
        defaults.update({
            # Knowledge repository (docs + sources) pulled on startup.
            # If set, docs/src roots are resolved relative to the repo root unless absolute.
            "knowledge": {
                "repo": "",
                "ref": "",
                "docs_root": "",
                "src_root": "",
                "deploy_root": "",
                "validate_refs": True,
            }
        })
        return defaults

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
        await super().pre_run_hook(state=state)
        self._ensure_knowledge_space()
        return None

    def _resolve_knowledge_paths(
        self,
        *,
        bundle_root: pathlib.Path,
        storage_root: pathlib.Path,
    ) -> tuple[pathlib.Path | None, pathlib.Path | None, pathlib.Path | None, bool, str | None, str | None]:
        # Resolve docs/src/deploy roots relative to repo or bundle root.
        props = dict(self.bundle_props or {})
        knowledge_def = props.get("knowledge") or {}
        repo = (knowledge_def.get("repo") or "").strip()
        ref = (knowledge_def.get("ref") or "").strip()
        docs_root_raw = (knowledge_def.get("docs_root") or "").strip()
        src_root_raw = (knowledge_def.get("src_root") or "").strip()
        deploy_root_raw = (knowledge_def.get("deploy_root") or "").strip()
        validate = knowledge_def.get("validate_refs")
        validate_refs = True if validate is None else bool(validate)

        # If nothing is configured, try to use local repo (host dev) or fall back
        # to the public platform repo (container/compose).
        if not repo and not docs_root_raw and not src_root_raw and not deploy_root_raw:
            for parent in bundle_root.resolve().parents:
                if (parent / "docs").is_dir() and (parent / "services").is_dir():
                    docs_root_raw = str((parent / "docs").resolve())
                    src_root_raw = str((parent / "services" / "kdcube-ai-app" / "kdcube_ai_app").resolve())
                    deploy_root_raw = str((parent / "deployment").resolve())
                    break
            if not docs_root_raw:
                repo = (os.getenv("KDCUBE_KNOWLEDGE_REPO") or "https://github.com/kdcube/kdcube-ai-app.git").strip()
                if not ref:
                    ref = (os.getenv("KDCUBE_KNOWLEDGE_REF") or "").strip()
                docs_root_raw = "app/ai-app/docs"
                src_root_raw = "app/ai-app/services/kdcube-ai-app/kdcube_ai_app"
                deploy_root_raw = "app/ai-app/deployment"

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
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.update({
            "gate.simple": {"provider": "anthropic", "model": haiku_4},
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},

        })
        config["role_models"] = role_models
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
def _load_knowledge_resolver():
    """
    Load knowledge resolver by file path so it shares the same module instance
    with tools/react_tools.py (single KNOWLEDGE_ROOT state).
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
