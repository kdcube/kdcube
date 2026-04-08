# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── entrypoint.py ──
# Bundle entry point for the kdcube.copilot knowledge-space copilot.
# Registers the bundle in the plugin system via @agentic_workflow and
# manages the knowledge space lifecycle rooted at a single ai-app tree.
#
# What it does:
#   1. Registers the bundle under the name "kdcube.copilot" (@agentic_workflow)
#   2. Builds a LangGraph StateGraph with a single "orchestrate" node
#   3. The "orchestrate" node initializes all dependencies (DB, indexes, RAG)
#      and delegates execution to WithReactWorkflow.process()
#   4. Manages knowledge space — builds it on bundle load and reconciles it only
#      when runtime bundle props change or load-time preparation did not happen
#   5. Defines role_models mapping logical roles → concrete LLM models
#
# Key base class: BaseEntrypoint
#   Your bundle overrides:
#     - configuration       → role_models + knowledge repo/root config
#     - on_bundle_load()     → build knowledge space for this tenant/project bundle instance
#     - pre_run_hook()       → reconcile knowledge space only if runtime props changed
#     - execute_core()       → the async method that runs the compiled LangGraph
#
# Knowledge space:
#   The bundle can pull an ai-app root from a git repo (configured via bundle_props)
#   or auto-discover a local ai-app root relative to the bundle.
#   The knowledge resolver (knowledge/resolver.py) is loaded via importlib
#   with a shared module name so that entrypoint.py and tools/react_tools.py
#   both access the same KNOWLEDGE_ROOT state.

from __future__ import annotations

import pathlib
import os
import shutil
import traceback
import fcntl
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.config import (
    delete_user_secret,
    get_secret,
    get_user_secret,
    set_user_secret,
)
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (
    ClaudeCodeAgent,
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
)
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, ui_widget
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint

from .orchestrator.workflow import WithReactWorkflow
from .event_filter import BundleEventFilter
from .knowledge_base_admin import (
    AGENT_NAME as KB_ADMIN_AGENT_NAME,
    append_conversation_message,
    build_kb_admin_storage,
    build_widget_payload as build_kb_admin_widget_payload,
    build_workspace_prompt_context,
    create_or_load_conversation,
    ensure_workspace as kb_admin_ensure_workspace,
    load_conversation as kb_admin_load_conversation,
    load_user_config as kb_admin_load_user_config,
    push_output_repo as kb_admin_push_output_repo,
    reset_output_repo as kb_admin_reset_output_repo,
    save_user_config as kb_admin_save_user_config,
    update_last_sync as kb_admin_update_last_sync,
    validate_workspace_config as kb_admin_validate_workspace_config,
    workspace_root as kb_admin_workspace_root,
)
import importlib.util
import sys

# Unique bundle ID — used by the plugin system to discover and load this bundle
BUNDLE_ID = "kdcube.copilot"
KB_ADMIN_TURN_KINDS = ("regular", "followup", "steer")


def _knowledge_lock_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.lock"


def _knowledge_signature_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.signature"


def _find_ai_app_root(candidate: pathlib.Path | None) -> pathlib.Path | None:
    if not candidate:
        return None
    try:
        resolved = candidate.resolve()
    except Exception:
        return None
    for path in [resolved, *resolved.parents]:
        if (path / "docs").is_dir() and (path / "src").is_dir():
            return path
    return None


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
    source_root: pathlib.Path | None,
) -> bool:
    if not (storage_root / "index.json").exists():
        return False
    if not (storage_root / "index.md").exists():
        return False
    if source_root:
        for name in ("docs", "deployment", "src", "ui"):
            if (source_root / name).exists() and not (storage_root / name).exists():
                return False
    return True


# @agentic_workflow — registration decorator: on application startup the system
# scans all bundles and auto-loads classes decorated with this.
# priority=100 — selection order when multiple bundles match (higher = preferred)
@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class ReactWorkflow(BaseEntrypoint):
    """KDCube Copilot bundle — gate + ReAct solver with a single-root ks: knowledge space."""

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
    ) -> tuple[pathlib.Path | None, bool, str | None, str | None]:
        """
        Resolve a single ai-app knowledge root from bundle props.
        If a git repo is configured, clone it first via ensure_git_bundle.
        Paths can be absolute or relative to the repo/bundle root.
        """
        props = dict(self.bundle_props or {})
        knowledge_def = props.get("knowledge") or {}
        repo = (knowledge_def.get("repo") or "").strip()
        ref = (knowledge_def.get("ref") or "").strip()
        root_raw = (knowledge_def.get("root") or "").strip()
        legacy_roots_raw = [
            (knowledge_def.get("docs_root") or "").strip(),
            (knowledge_def.get("src_root") or "").strip(),
            (knowledge_def.get("deploy_root") or "").strip(),
            (knowledge_def.get("tests_root") or "").strip(),
        ]
        validate = knowledge_def.get("validate_refs")
        validate_refs = True if validate is None else bool(validate)

        # If nothing is configured, try to use local repo (host dev) or fall back
        # to the public platform repo (container/compose).
        if not repo and not root_raw and not any(legacy_roots_raw):
            for parent in bundle_root.resolve().parents:
                if (parent / "docs").is_dir() and (parent / "src").is_dir():
                    root_raw = str(parent.resolve())
                    break
            if not root_raw:
                repo = (os.getenv("KDCUBE_KNOWLEDGE_REPO") or "https://github.com/kdcube/kdcube-ai-app.git").strip()
                if not ref:
                    ref = (os.getenv("KDCUBE_KNOWLEDGE_REF") or "").strip()
                root_raw = "app/ai-app"

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
        source_root = None
        if root_raw:
            source_root = pathlib.Path(root_raw)
            if not source_root.is_absolute():
                source_root = (base_root / source_root).resolve()
        else:
            derived_roots = []
            for raw in legacy_roots_raw:
                if not raw:
                    continue
                candidate = pathlib.Path(raw)
                if not candidate.is_absolute():
                    candidate = (base_root / candidate).resolve()
                derived = _find_ai_app_root(candidate)
                if derived:
                    derived_roots.append(derived)
            if derived_roots:
                first = derived_roots[0]
                if all(path.resolve() == first.resolve() for path in derived_roots[1:]):
                    source_root = first
                else:
                    self.logger.log(
                        "[kdcube.copilot] legacy knowledge.*_root values do not resolve to one common ai-app root.",
                        "WARNING",
                    )
        if repo_root and not source_root:
            self.logger.log(
                "[kdcube.copilot] knowledge.root should point at the common ai-app root when knowledge.repo is set.",
                "WARNING",
            )
        return source_root, validate_refs, repo, ref or None

    def _resolve_knowledge_setup(
        self,
    ) -> tuple[
        pathlib.Path | None,
        pathlib.Path,
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
                True,
                None,
                None,
                None,
            )

        source_root, validate_refs, repo, ref = self._resolve_knowledge_paths(
            bundle_root=bundle_root,
            storage_root=ws_root,
        )
        signature = f"{repo}|{ref}|{source_root}|{validate_refs}"
        return (
            ws_root,
            bundle_root,
            source_root,
            validate_refs,
            repo,
            ref,
            signature,
        )

    def _ensure_knowledge_space(self, *, reason: str) -> None:
        """
        Build or refresh the knowledge index under bundle storage.
        Uses a signature (repo|ref|root) to skip rebuilding when nothing changed.
        """
        try:
            (
                ws_root,
                bundle_root,
                source_root,
                validate_refs,
                repo,
                ref,
                signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                self.logger.log(
                    f"[kdcube.copilot] knowledge build skipped ({reason}): bundle storage root is unavailable.",
                    "WARNING",
                )
                return None
            self.logger.log(
                f"[kdcube.copilot] knowledge build start ({reason}): storage={ws_root}",
                "INFO",
            )
            self.logger.log(
                (
                    f"[kdcube.copilot] resolved knowledge paths ({reason}): "
                    f"repo={repo or '<local>'} ref={ref or '<default>'} "
                    f"root={source_root or '<missing>'} "
                    f"validate_refs={validate_refs}"
                ),
                "INFO",
            )
            with _knowledge_build_lock(ws_root):
                # Always register the root so search_knowledge() works even on cache hits.
                knowledge_resolver.KNOWLEDGE_ROOT = ws_root
                outputs_ready = _knowledge_outputs_ready(
                    storage_root=ws_root,
                    source_root=source_root,
                )
                shared_signature = _read_shared_knowledge_signature(ws_root)
                if shared_signature == signature and outputs_ready:
                    self._knowledge_signature = signature
                    self.logger.log(
                        f"[kdcube.copilot] knowledge build skipped ({reason}): shared signature cache hit for storage={ws_root}",
                        "INFO",
                    )
                    return None
                if self._knowledge_signature == signature and outputs_ready:
                    if shared_signature != signature:
                        _write_shared_knowledge_signature(ws_root, signature)
                    self.logger.log(
                        f"[kdcube.copilot] knowledge build skipped ({reason}): local signature cache hit for storage={ws_root}",
                        "INFO",
                    )
                    return None
                # Build or refresh the knowledge index under bundle storage.
                knowledge_resolver.prepare_knowledge_space(
                    bundle_root=bundle_root,
                    knowledge_root=ws_root,
                    source_root=source_root,
                    validate_refs=validate_refs,
                    logger=self.logger,
                )
                _write_shared_knowledge_signature(ws_root, signature)
                self._knowledge_signature = signature
            self.logger.log(
                (
                    f"[kdcube.copilot] knowledge build done ({reason}): "
                    f"storage={ws_root} "
                    f"docs={(ws_root / 'docs').exists()} "
                    f"deployment={(ws_root / 'deployment').exists()} "
                    f"src={(ws_root / 'src').exists()} "
                    f"ui={(ws_root / 'ui').exists()} "
                    f"index_json={(ws_root / 'index.json').exists()} "
                    f"index_md={(ws_root / 'index.md').exists()}"
                ),
                "INFO",
            )
        except Exception:
            self.logger.log(f"[kdcube.copilot] knowledge build failed ({reason})", "WARNING")
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
                source_root,
                validate_refs,
                repo,
                ref,
                signature,
            ) = self._resolve_knowledge_setup()
            if not ws_root:
                self.logger.log(
                    f"[kdcube.copilot] knowledge reconcile skipped ({reason}): bundle storage root is unavailable.",
                    "WARNING",
                )
                return None
            if self._knowledge_signature is None:
                self.logger.log(
                    f"[kdcube.copilot] knowledge reconcile ({reason}): load-time signature missing, building now.",
                    "INFO",
                )
                return self._ensure_knowledge_space(reason=reason)
            if self._knowledge_signature != signature:
                self.logger.log(
                    (
                        f"[kdcube.copilot] knowledge reconcile ({reason}): signature changed. "
                        f"repo={repo or '<local>'} ref={ref or '<default>'} "
                        f"root={source_root or '<missing>'} "
                        f"validate_refs={validate_refs}"
                    ),
                    "INFO",
                )
                return self._ensure_knowledge_space(reason=reason)
        except Exception:
            self.logger.log(f"[kdcube.copilot] knowledge reconcile failed ({reason})", "WARNING")
            self.logger.log(traceback.format_exc(), "WARNING")
        return None

    def _kb_admin_storage(self) -> Optional[AIBundleStorage]:
        actor = getattr(self.comm_context, "actor", None)
        tenant = getattr(actor, "tenant_id", None) or self.settings.TENANT
        project = getattr(actor, "project_id", None) or self.settings.PROJECT
        bundle_spec = getattr(self.config, "ai_bundle_spec", None)
        bundle_id = getattr(bundle_spec, "id", None) or BUNDLE_ID
        try:
            return build_kb_admin_storage(
                tenant=str(tenant or "unknown"),
                project=str(project or "unknown"),
                bundle_id=str(bundle_id or BUNDLE_ID),
                storage_uri=self.settings.STORAGE_PATH,
            )
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
            return None

    def _kb_admin_local_root(self) -> Optional[pathlib.Path]:
        try:
            from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir

            actor = getattr(self.comm_context, "actor", None)
            bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
            tenant = getattr(actor, "tenant_id", None) or getattr(self.comm, "tenant", None) or self.settings.TENANT
            project = getattr(actor, "project_id", None) or getattr(self.comm, "project", None) or self.settings.PROJECT
            bundle_id = getattr(bundle_spec, "id", None) or BUNDLE_ID
            unversioned_root = bundle_storage_dir(
                bundle_id=str(bundle_id or BUNDLE_ID),
                version=None,
                tenant=str(tenant or "unknown"),
                project=str(project or "unknown"),
                ensure=True,
            ) / "_knowledge_base_admin"
            if not unversioned_root.exists():
                legacy_root = self.bundle_storage_root()
                if legacy_root:
                    legacy_root = legacy_root / "_knowledge_base_admin"
                    if legacy_root.exists() and legacy_root.resolve() != unversioned_root.resolve():
                        shutil.copytree(legacy_root, unversioned_root, dirs_exist_ok=True)
                        self.logger.log(
                            f"[knowledge_base_admin.workspace] migrated local workspace root from {legacy_root} to {unversioned_root}",
                            "INFO",
                        )
            unversioned_root.mkdir(parents=True, exist_ok=True)
            return unversioned_root
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
            return None

    def _kb_admin_user_id(
        self,
        *,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> str:
        return (
            (user_id or "").strip()
            or (fingerprint or "").strip()
            or str(getattr(self.comm, "user_id", None) or "").strip()
            or str(getattr(getattr(self.comm_context, "user", None), "user_id", None) or "").strip()
            or str(getattr(getattr(self.comm_context, "user", None), "fingerprint", None) or "").strip()
            or "anonymous"
        )

    def _kb_admin_secret_flags(self, *, user_id: str) -> dict[str, bool]:
        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID
        return {
            "has_git_pat": bool(
                get_user_secret("git.http_token", user_id=user_id, bundle_id=bundle_id)
                or get_secret("services.git.http_token")
            ),
            "has_anthropic_api_key": bool(
                get_user_secret("anthropic.api_key", user_id=user_id, bundle_id=bundle_id)
                or get_secret("services.anthropic.api_key")
            ),
            "has_claude_code_key": bool(
                get_user_secret("anthropic.claude_code_key", user_id=user_id, bundle_id=bundle_id)
                or get_secret("services.anthropic.claude_code_key")
            ),
        }

    @staticmethod
    def _kb_admin_repo_source_kind(source: Optional[str]) -> str:
        text = str(source or "").strip()
        if not text:
            return "missing"
        if text.startswith("git@"):
            return "ssh"
        if text.startswith("http://") or text.startswith("https://"):
            return "https"
        if text.startswith("/") or text.startswith("./") or text.startswith("../"):
            return "local"
        return "other"

    def _kb_admin_config_summary(
        self,
        *,
        content_repos: Optional[list[dict[str, Any]]] = None,
        output_repo: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        normalized_content: list[dict[str, Any]] = []
        for idx, item in enumerate(list(content_repos or [])[:3], start=1):
            repo = dict(item or {})
            normalized_content.append(
                {
                    "slot": str(repo.get("slot") or f"content-{idx}"),
                    "id": str(repo.get("id") or "").strip() or None,
                    "label": str(repo.get("label") or "").strip() or None,
                    "branch": str(repo.get("branch") or "").strip() or None,
                    "has_source": bool(str(repo.get("source") or "").strip()),
                    "source_kind": self._kb_admin_repo_source_kind(repo.get("source")),
                }
            )
        output = dict(output_repo or {})
        return {
            "content_repo_count": len(normalized_content),
            "content_repos": normalized_content,
            "output_repo": {
                "id": str(output.get("id") or "").strip() or None,
                "label": str(output.get("label") or "").strip() or None,
                "branch": str(output.get("branch") or "").strip() or None,
                "has_source": bool(str(output.get("source") or "").strip()),
                "source_kind": self._kb_admin_repo_source_kind(output.get("source")),
            },
        }

    def _kb_admin_claude_env(self, *, user_id: str) -> dict[str, str]:
        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID
        env: dict[str, str] = {}
        api_key = (
            get_user_secret("anthropic.api_key", user_id=user_id, bundle_id=bundle_id)
            or get_secret("services.anthropic.api_key")
        )
        auth_token = (
            get_user_secret("anthropic.auth_token", user_id=user_id, bundle_id=bundle_id)
            or get_secret("services.anthropic.auth_token")
        )
        claude_code_key = (
            get_user_secret("anthropic.claude_code_key", user_id=user_id, bundle_id=bundle_id)
            or get_secret("services.anthropic.claude_code_key")
        )
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        if claude_code_key:
            env["CLAUDE_CODE_KEY"] = claude_code_key
        return env

    def _kb_admin_git_credentials(self, *, user_id: str) -> tuple[str | None, str | None]:
        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID
        token = (
            get_user_secret("git.http_token", user_id=user_id, bundle_id=bundle_id)
            or get_secret("services.git.http_token")
        )
        http_user = (
            get_user_secret("git.http_user", user_id=user_id, bundle_id=bundle_id)
            or get_secret("services.git.http_user")
            or "x-access-token"
        )
        return token, http_user

    def _kb_admin_bound_comm(self, *, conversation_id: str, turn_id: str) -> ChatCommunicator:
        base = self.comm
        if not isinstance(base, ChatCommunicator):
            raise RuntimeError("Knowledge Base Admin requires a bound ChatCommunicator")
        conversation = dict(base.conversation or {})
        conversation["conversation_id"] = conversation_id
        conversation["turn_id"] = turn_id
        return ChatCommunicator(
            emitter=base.emitter,
            tenant=base.tenant,
            project=base.project,
            user_id=base.user_id,
            user_type=base.user_type,
            service=dict(base.service or {}),
            conversation=conversation,
            room=base.room,
            target_sid=base.target_sid,
            event_filter=getattr(self, "_event_filter", None),
        )

    def _kb_admin_binding(self, *, user_id: str, conversation_id: str) -> ClaudeCodeBinding:
        session_id = (
            str(getattr(getattr(self.comm_context, "routing", None), "session_id", None) or "").strip()
            or conversation_id
        )
        claude_session_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"kdcube/claude-code/{user_id}/{conversation_id}/{KB_ADMIN_AGENT_NAME}",
            )
        )
        return ClaudeCodeBinding(
            user_id=user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            claude_session_id=claude_session_id,
        )

    def _kb_admin_render_widget(self, payload: dict[str, Any]) -> str:
        bundle_root = pathlib.Path(__file__).resolve().parent
        tsx_path = bundle_root / "ui" / "KnowledgeBaseAdmin.tsx"
        content = tsx_path.read_text(encoding="utf-8").replace(
            "__KNOWLEDGE_BASE_ADMIN_JSON__",
            json.dumps(payload, ensure_ascii=False),
        )
        actor = getattr(self.comm_context, "actor", None)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        rendered = patch_dashboard(
            input_content=content,
            base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
            default_tenant=getattr(actor, "tenant_id", None) or self.settings.TENANT,
            default_project=getattr(actor, "project_id", None) or self.settings.PROJECT,
            default_app_bundle_id=getattr(bundle_spec, "id", None) or BUNDLE_ID,
            access_token=None,
            id_token=None,
            id_token_header="X-ID-Token",
        )
        return self._render_dashboard_html(content=rendered, title="Knowledge Base Admin")

    def _kb_admin_normalize_turn_kind(self, raw: Optional[str]) -> str:
        turn_kind = str(raw or "regular").strip().lower() or "regular"
        if turn_kind not in KB_ADMIN_TURN_KINDS:
            raise ValueError(
                f"Unsupported turn_kind '{raw}'. Expected one of: {', '.join(KB_ADMIN_TURN_KINDS)}."
            )
        return turn_kind

    @api(
        alias="knowledge_base_admin_widget",
        route="operations",
        roles=("super-admin",),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:circle-stack",
            "lucide": "DatabaseZap",
        },
        alias="knowledge_base_admin",
        roles=("super-admin",),
    )
    def knowledge_base_admin_widget(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        selected_conversation_id: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        if not storage:
            return ["<p>Bundle storage backend is not configured for this bundle.</p>"]
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        flags = self._kb_admin_secret_flags(user_id=target_user)
        payload = build_kb_admin_widget_payload(
            storage,
            target_user,
            has_git_pat=flags["has_git_pat"],
            has_anthropic_api_key=flags["has_anthropic_api_key"],
            has_claude_code_key=flags["has_claude_code_key"],
            selected_conversation_id=selected_conversation_id,
        )
        try:
            return [self._kb_admin_render_widget(payload)]
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
            return ["<p>Unable to render the Knowledge Base Admin widget right now.</p>"]

    @api(alias="knowledge_base_admin_widget_data", roles=("super-admin",))
    def knowledge_base_admin_widget_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        selected_conversation_id: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        if not storage:
            return {"ok": False, "error": "Bundle storage backend is not configured for this bundle."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        flags = self._kb_admin_secret_flags(user_id=target_user)
        payload = build_kb_admin_widget_payload(
            storage,
            target_user,
            has_git_pat=flags["has_git_pat"],
            has_anthropic_api_key=flags["has_anthropic_api_key"],
            has_claude_code_key=flags["has_claude_code_key"],
            selected_conversation_id=selected_conversation_id,
        )
        payload["ok"] = True
        local_root = self._kb_admin_local_root()
        payload["workspace_root"] = str(kb_admin_workspace_root(local_root, target_user)) if local_root else None
        return payload

    @api(alias="knowledge_base_admin_conversation_data", roles=("super-admin",))
    def knowledge_base_admin_conversation_data(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        if not storage:
            return {"ok": False, "error": "Bundle storage backend is not configured for this bundle."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        conversation = kb_admin_load_conversation(storage, target_user, conversation_id)
        return {
            "ok": bool(conversation),
            "conversation": conversation,
            "conversation_id": conversation_id,
        }

    @api(alias="knowledge_base_admin_save_settings", roles=("super-admin",))
    def knowledge_base_admin_save_settings(
        self,
        content_repos: Optional[list[dict[str, Any]]] = None,
        output_repo: Optional[dict[str, Any]] = None,
        git_http_token: Optional[str] = None,
        git_http_user: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        claude_code_key: Optional[str] = None,
        clear_git_http_token: bool = False,
        clear_anthropic_api_key: bool = False,
        clear_claude_code_key: bool = False,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        if not storage:
            self.logger.log("[knowledge_base_admin.save_settings] storage backend is unavailable", "WARNING")
            return {"ok": False, "error": "Bundle storage backend is not configured for this bundle."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID
        requested_summary = self._kb_admin_config_summary(
            content_repos=content_repos,
            output_repo=output_repo,
        )
        self.logger.log(
            "[knowledge_base_admin.save_settings] request "
            + json.dumps(
                {
                    "bundle_id": bundle_id,
                    "user_id": target_user,
                    "storage_root": getattr(storage, "root_uri", None),
                    "requested": requested_summary,
                    "secret_updates": {
                        "set_git_http_token": bool(str(git_http_token or "").strip()),
                        "set_git_http_user": bool(str(git_http_user or "").strip()),
                        "set_anthropic_api_key": bool(str(anthropic_api_key or "").strip()),
                        "set_claude_code_key": bool(str(claude_code_key or "").strip()),
                        "clear_git_http_token": bool(clear_git_http_token),
                        "clear_anthropic_api_key": bool(clear_anthropic_api_key),
                        "clear_claude_code_key": bool(clear_claude_code_key),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "INFO",
        )
        try:
            if clear_git_http_token:
                delete_user_secret("git.http_token", user_id=target_user, bundle_id=bundle_id)
                delete_user_secret("git.http_user", user_id=target_user, bundle_id=bundle_id)
            else:
                if str(git_http_token or "").strip():
                    set_user_secret("git.http_token", str(git_http_token).strip(), user_id=target_user, bundle_id=bundle_id)
                if str(git_http_user or "").strip():
                    set_user_secret("git.http_user", str(git_http_user).strip(), user_id=target_user, bundle_id=bundle_id)

            if clear_anthropic_api_key:
                delete_user_secret("anthropic.api_key", user_id=target_user, bundle_id=bundle_id)
            elif str(anthropic_api_key or "").strip():
                set_user_secret("anthropic.api_key", str(anthropic_api_key).strip(), user_id=target_user, bundle_id=bundle_id)

            if clear_claude_code_key:
                delete_user_secret("anthropic.claude_code_key", user_id=target_user, bundle_id=bundle_id)
            elif str(claude_code_key or "").strip():
                set_user_secret("anthropic.claude_code_key", str(claude_code_key).strip(), user_id=target_user, bundle_id=bundle_id)

            config = kb_admin_save_user_config(
                storage,
                target_user,
                content_repos=content_repos or [],
                output_repo=output_repo or {},
                last_sync=kb_admin_load_user_config(storage, target_user).get("last_sync"),
            )
            flags = self._kb_admin_secret_flags(user_id=target_user)
            self.logger.log(
                "[knowledge_base_admin.save_settings] persisted "
                + json.dumps(
                    {
                        "bundle_id": bundle_id,
                        "user_id": target_user,
                        "persisted": self._kb_admin_config_summary(
                            content_repos=list(config.get("content_repos") or []),
                            output_repo=dict(config.get("output_repo") or {}),
                        ),
                        "secret_flags": flags,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "INFO",
            )
            payload = build_kb_admin_widget_payload(
                storage,
                target_user,
                has_git_pat=flags["has_git_pat"],
                has_anthropic_api_key=flags["has_anthropic_api_key"],
                has_claude_code_key=flags["has_claude_code_key"],
            )
            payload["ok"] = True
            payload["config"] = config
            return payload
        except Exception:
            self.logger.log(
                "[knowledge_base_admin.save_settings] failed "
                + json.dumps(
                    {
                        "bundle_id": bundle_id,
                        "user_id": target_user,
                        "requested": requested_summary,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "ERROR",
            )
            self.logger.log(traceback.format_exc(), "ERROR")
            raise

    @api(alias="knowledge_base_admin_sync_workspace", roles=("super-admin",))
    def knowledge_base_admin_sync_workspace(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        local_root = self._kb_admin_local_root()
        if not storage or not local_root:
            return {"ok": False, "error": "Bundle storage root is unavailable for Knowledge Base Admin."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        config = kb_admin_load_user_config(storage, target_user)
        token, http_user = self._kb_admin_git_credentials(user_id=target_user)
        try:
            workspace = kb_admin_ensure_workspace(
                local_root=local_root,
                user_id=target_user,
                config=config,
                git_http_token=token,
                git_http_user=http_user,
                sync_existing=True,
            )
        except Exception as exc:
            self.logger.log(traceback.format_exc(), "ERROR")
            return {"ok": False, "error": str(exc)}
        sync_payload = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "repo_statuses": workspace["repo_statuses"],
            "workspace_root": workspace["workspace_root"],
        }
        kb_admin_update_last_sync(storage, target_user, sync_payload)
        return {
            "ok": True,
            **sync_payload,
        }

    @api(alias="knowledge_base_admin_push_output_repo", roles=("super-admin",))
    def knowledge_base_admin_push_output_repo(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        local_root = self._kb_admin_local_root()
        if not storage or not local_root:
            return {"ok": False, "error": "Bundle storage root is unavailable for Knowledge Base Admin."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        config = kb_admin_load_user_config(storage, target_user)
        token, http_user = self._kb_admin_git_credentials(user_id=target_user)
        try:
            output_status = kb_admin_push_output_repo(
                local_root=local_root,
                user_id=target_user,
                config=config,
                git_http_token=token,
                git_http_user=http_user,
            )
            workspace = kb_admin_ensure_workspace(
                local_root=local_root,
                user_id=target_user,
                config=config,
                git_http_token=token,
                git_http_user=http_user,
                sync_existing=False,
            )
        except Exception as exc:
            self.logger.log(traceback.format_exc(), "ERROR")
            return {"ok": False, "error": str(exc)}

        repo_statuses = [
            output_status if item.get("repo_type") == "output" else item
            for item in workspace["repo_statuses"]
        ]
        sync_payload = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "repo_statuses": repo_statuses,
            "workspace_root": workspace["workspace_root"],
        }
        kb_admin_update_last_sync(storage, target_user, sync_payload)
        return {
            "ok": True,
            "repo_status": output_status,
            **sync_payload,
        }

    @api(alias="knowledge_base_admin_reset_output_repo", roles=("super-admin",))
    def knowledge_base_admin_reset_output_repo(
        self,
        commit: str,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        local_root = self._kb_admin_local_root()
        if not storage or not local_root:
            return {"ok": False, "error": "Bundle storage root is unavailable for Knowledge Base Admin."}
        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        config = kb_admin_load_user_config(storage, target_user)
        token, http_user = self._kb_admin_git_credentials(user_id=target_user)
        try:
            output_status = kb_admin_reset_output_repo(
                local_root=local_root,
                user_id=target_user,
                config=config,
                commit=commit,
                git_http_token=token,
                git_http_user=http_user,
            )
            workspace = kb_admin_ensure_workspace(
                local_root=local_root,
                user_id=target_user,
                config=config,
                git_http_token=token,
                git_http_user=http_user,
                sync_existing=False,
            )
        except Exception as exc:
            self.logger.log(traceback.format_exc(), "ERROR")
            return {"ok": False, "error": str(exc)}

        repo_statuses = [
            output_status if item.get("repo_type") == "output" else item
            for item in workspace["repo_statuses"]
        ]
        sync_payload = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "repo_statuses": repo_statuses,
            "workspace_root": workspace["workspace_root"],
        }
        kb_admin_update_last_sync(storage, target_user, sync_payload)
        return {
            "ok": True,
            "repo_status": output_status,
            **sync_payload,
        }

    @api(alias="knowledge_base_admin_chat", roles=("super-admin",))
    async def knowledge_base_admin_chat(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        turn_kind: str = "regular",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        del kwargs
        storage = self._kb_admin_storage()
        local_root = self._kb_admin_local_root()
        if not storage or not local_root:
            return {"ok": False, "error": "Bundle storage root is unavailable for Knowledge Base Admin."}

        text = str(message or "").strip()
        if not text:
            return {"ok": False, "error": "message is required"}

        try:
            normalized_turn_kind = self._kb_admin_normalize_turn_kind(turn_kind)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        target_user = self._kb_admin_user_id(user_id=user_id, fingerprint=fingerprint)
        config = kb_admin_load_user_config(storage, target_user)
        try:
            config = kb_admin_validate_workspace_config(config)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        token, http_user = self._kb_admin_git_credentials(user_id=target_user)
        try:
            workspace = kb_admin_ensure_workspace(
                local_root=local_root,
                user_id=target_user,
                config=config,
                git_http_token=token,
                git_http_user=http_user,
                sync_existing=False,
            )
        except Exception as exc:
            self.logger.log(traceback.format_exc(), "ERROR")
            return {"ok": False, "error": str(exc)}

        conversation = create_or_load_conversation(
            storage,
            target_user,
            conversation_id=conversation_id,
            title_hint=text[:80],
        )
        cid = str(conversation["conversation_id"])
        turn_id_value = f"kb_admin_turn_{uuid.uuid4().hex[:12]}"
        bound_comm = self._kb_admin_bound_comm(conversation_id=cid, turn_id=turn_id_value)

        history_lines: list[str] = []
        for item in list(conversation.get("messages") or [])[-12:]:
            role = str(item.get("role") or "assistant").capitalize()
            body = str(item.get("text") or "").strip()
            if body:
                history_lines.append(f"{role}: {body}")

        workspace_prompt = build_workspace_prompt_context(workspace["workspace_payload"])
        full_prompt = workspace_prompt
        if history_lines:
            full_prompt += "\n\nPrevious conversation:\n" + "\n\n".join(history_lines)
        full_prompt += f"\n\nCurrent turn kind: {normalized_turn_kind}\n\n{text}"

        append_conversation_message(
            storage,
            target_user,
            conversation_id=cid,
            role="user",
            text=text,
            metadata={"turn_kind": normalized_turn_kind},
        )

        agent = ClaudeCodeAgent(
            config=ClaudeCodeAgentConfig(
                agent_name=KB_ADMIN_AGENT_NAME,
                workspace_path=pathlib.Path(workspace["workspace_root"]),
                allowed_tools=("Read", "Grep", "Bash", "WebFetch", "WebSearch"),
                additional_directories=tuple(
                    pathlib.Path(str(item.get("local_path")))
                    for item in (workspace.get("repo_statuses") or [])
                    if str(item.get("local_path") or "").strip()
                ),
                env=self._kb_admin_claude_env(user_id=target_user),
                step_name="knowledge_base_admin.agent",
                permission_mode="acceptEdits",
            ),
            binding=self._kb_admin_binding(user_id=target_user, conversation_id=cid),
            comm=bound_comm,
            logger=self.logger._logger if hasattr(self.logger, "_logger") else None,
        )

        try:
            result = await agent.run_turn(full_prompt, kind=normalized_turn_kind)
        except Exception as exc:
            conversation_doc = append_conversation_message(
                storage,
                target_user,
                conversation_id=cid,
                role="assistant",
                text=f"Error: {exc}",
                metadata={"error": True, "turn_kind": normalized_turn_kind},
            )
            return {
                "ok": False,
                "error": str(exc),
                "conversation_id": cid,
                "conversation": conversation_doc,
            }

        append_conversation_message(
            storage,
            target_user,
            conversation_id=cid,
            role="assistant",
            text=result.final_text,
            metadata={
                "turn_kind": normalized_turn_kind,
                "status": result.status,
                "delta_count": result.delta_count,
                "exit_code": result.exit_code,
                "claude_session_id": result.session_id,
            },
        )
        conversation_doc = kb_admin_load_conversation(storage, target_user, cid)
        return {
            "ok": True,
            "conversation_id": cid,
            "conversation": conversation_doc,
            "result": {
                "status": result.status,
                "final_text": result.final_text,
                "delta_count": result.delta_count,
                "exit_code": result.exit_code,
                "session_id": result.session_id,
            },
        }

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
        # Knowledge repository — a single ai-app root pulled on startup.
        # If repo is set, knowledge.root is resolved relative to the repo root.
        # If repo is empty, knowledge.root is resolved relative to the bundle directory.
        knowledge = dict(config.get("knowledge") or {})
        knowledge.setdefault("repo", "")            # Git URL (e.g. https://github.com/org/repo); default: empty, then auto-detect local ai-app repo or fall back to KDCUBE_KNOWLEDGE_REPO / public repo
        knowledge.setdefault("ref", "")             # Git ref (branch/tag/commit); empty = default branch; default: empty, then use KDCUBE_KNOWLEDGE_REF or the repo default branch
        knowledge.setdefault("root", "")            # Path to the common ai-app root; default: empty, then auto-detect local app/ai-app or use app/ai-app under the repo
        knowledge.setdefault("validate_refs", True) # Check that backticked common-root-relative refs in docs point to existing files; default: True
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
    KNOWLEDGE_ROOT global. Using a shared module name (_kdcube_copilot_knowledge_resolver)
    ensures they get the same module object regardless of how they are loaded.
    """
    module_name = "_kdcube_copilot_knowledge_resolver"
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
