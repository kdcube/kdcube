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
# Key base class: BaseEntrypointWithEconomicsAndMemory
#   Your bundle overrides:
#     - configuration       → role_models + knowledge repo/root config
#     - on_bundle_load()     → build knowledge space for this tenant/project bundle instance
#     - pre_run_hook()       → reconcile knowledge space only if runtime props changed
#     - execute_core()       → the async method that runs the compiled LangGraph
#
# Knowledge space:
#   The bundle can pull an ai-app root from a git repo (configured via bundle_props)
#   or auto-discover a local ai-app root relative to the bundle.
#   The entrypoint keeps a dedicated resolver module instance for long-lived
#   knowledge-space preparation state; tool modules import the same-bundle
#   resolver normally and seed their own root from bound runtime context.

from __future__ import annotations

import asyncio
import inspect
import pathlib
import os
import shutil
import traceback
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.config import (
    delete_user_secret,
    get_secret,
    get_service_secret,
    get_user_secret,
    set_user_secret,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp as telegram_webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (
    ClaudeCodeAgent,
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
    ClaudeCodeSessionStoreConfig,
    run_claude_code_turn,
)
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, mcp, on_job, ui_widget
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)
from kdcube_ai_app.storage.observed_file_locks import observed_file_lock, lock_owner_summary

from .orchestrator.workflow import WithReactWorkflow
from .event_filter import BundleEventFilter
from .tools import react_tools as doc_reader_tools
from .knowledge_base_admin import (
    AGENT_NAME as KB_ADMIN_AGENT_NAME,
    DEFAULT_CLAUDE_CODE_MODEL,
    append_conversation_message,
    build_kb_admin_storage,
    build_widget_payload as build_kb_admin_widget_payload,
    build_workspace_prompt_context,
    create_or_load_conversation,
    ensure_workspace as kb_admin_ensure_workspace,
    load_conversation as kb_admin_load_conversation,
    load_user_config as kb_admin_load_user_config,
    push_output_repo as kb_admin_push_output_repo,
    refresh_workspace_support_files as kb_admin_refresh_workspace_support_files,
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
TELEGRAM_ADMIN_ROLE = "kdcube:role:super-admin"
TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEGRAM_WEBHOOK_PUBLIC_AUTH = {
    "mode": "header_secret",
    "header": TELEGRAM_WEBHOOK_SECRET_HEADER,
    "secret_key": "integrations.telegram.webhook_secret",
}
TELEGRAM_WEBAPP_PUBLIC_AUTH = "none"
COPILOT_WEBAPP_ALIAS = "copilot_webapp"


def _storage_root_or_error(entrypoint: Any) -> pathlib.Path:
    storage_root = entrypoint.bundle_storage_root()
    if not storage_root:
        raise RuntimeError("Bundle storage backend is not configured for this bundle.")
    return storage_root


def _telegram_user_admin_storage(entrypoint: Any) -> TelegramUserAdminStorage:
    return TelegramUserAdminStorage(_storage_root_or_error(entrypoint))


class _CopilotTaskWidgets:
    @staticmethod
    async def payload(entrypoint: Any, **kwargs) -> Dict[str, Any]:
        del entrypoint, kwargs
        return {
            "user_id": "",
            "tasks": [],
            "count": 0,
            "supported_now": {
                "tasks": False,
                "reason": "kdcube.copilot exposes Telegram admin and user memory, not task management.",
            },
        }


class _CopilotMemoryWidgets:
    @staticmethod
    async def payload(
        entrypoint: Any,
        *,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        mark_seen: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del mark_seen, kwargs
        with entrypoint._memory_user_identity(
            user_id=user_id or "",
            fingerprint=fingerprint or "",
            user_type="registered",
        ):
            return await entrypoint.memories_widget_data(scope_filter="current_bundle", limit=30)


class _CopilotSettingsWidgets:
    @staticmethod
    def payload(
        entrypoint: Any,
        *,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        telegram_identity: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return {
            "user_id": user_id or fingerprint or getattr(entrypoint.comm, "user_id", None) or "anonymous",
            "telegram_identity": telegram_identity,
        }


class _CopilotTaskOperations:
    @staticmethod
    async def list_tasks(*args, **kwargs) -> Dict[str, Any]:
        del args, kwargs
        return {"ok": True, "tasks": [], "count": 0}


telegram_user_admin.configure_telegram_user_admin(
    storage_factory=_telegram_user_admin_storage,
    storage_root_or_error=_storage_root_or_error,
    bundle_id=BUNDLE_ID,
)
telegram_widget_auth.configure_telegram_widget_auth(
    storage_for=telegram_user_admin.storage,
    bot_token=telegram_user_admin.bot_token,
    bundle_id=BUNDLE_ID,
)
telegram_webapp.configure_telegram_webapp(
    memory_widgets_module=_CopilotMemoryWidgets,
    settings_widgets_module=_CopilotSettingsWidgets,
    task_widgets_module=_CopilotTaskWidgets,
    telegram_user_admin_module=telegram_user_admin,
    bundle_id=BUNDLE_ID,
)
telegram_widget_ops.configure_telegram_widget_ops(
    task_operations_module=_CopilotTaskOperations,
    telegram_user_admin_module=telegram_user_admin,
    telegram_widget_auth_module=telegram_widget_auth,
    webapp_module=telegram_webapp,
    bundle_id=BUNDLE_ID,
)


def _knowledge_lock_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.lock"


def _knowledge_signature_path(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / ".knowledge.signature"


def _knowledge_root_for_storage(storage_root: pathlib.Path) -> pathlib.Path:
    return storage_root / "knowledge"


def _cleanup_legacy_top_level_knowledge_symlinks(storage_root: pathlib.Path) -> None:
    """
    Older copilot builds materialized ks: directly under bundle storage, including
    a top-level ui symlink. Platform UI builds reserve <bundle_storage>/ui, so only
    remove legacy symlinks and leave real built UI directories untouched.
    """
    for name in ("docs", "deployment", "src", "ui"):
        path = storage_root / name
        try:
            if path.is_symlink():
                path.unlink()
        except Exception:
            pass


def _knowledge_lock_wait_seconds() -> int:
    try:
        return max(1, int(os.environ.get("KDCUBE_COPILOT_KNOWLEDGE_LOCK_WAIT_SECONDS", "300") or "300"))
    except Exception:
        return 300


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
def _knowledge_build_lock(storage_root: pathlib.Path, *, logger: Any = None):
    storage_root.mkdir(parents=True, exist_ok=True)
    lock_path = _knowledge_lock_path(storage_root)

    def _on_wait(path: pathlib.Path, metadata: dict[str, Any] | None, age: float | None) -> None:
        age_text = f"{age:.1f}s" if age is not None else "unknown"
        msg = (
            f"[kdcube.copilot] waiting for knowledge build lock "
            f"storage={storage_root} age={age_text} lock={path}{lock_owner_summary(metadata)}"
        )
        log_fn = getattr(logger, "log", None)
        if callable(log_fn):
            log_fn(msg, "WARNING")
        else:
            print(msg)

    with observed_file_lock(
        lock_path=lock_path,
        resource_id=f"knowledge-space:{storage_root}",
        operation="kdcube.copilot.knowledge.build",
        on_wait=_on_wait,
        wait_seconds=_knowledge_lock_wait_seconds(),
    ):
        yield


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
    if not (storage_root / "docs").exists():
        return False
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
@agentic_workflow(
    name=BUNDLE_ID,
    version="1.0.0",
    priority=100,
    allowed_roles_config="visibility.bundle.allowed_roles",
)
class ReactWorkflow(BaseEntrypointWithEconomicsAndMemory):
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

    @on_job
    async def on_job(self, **kwargs) -> Dict[str, Any]:
        handled = await super().handle_job(**kwargs)
        if handled.get("handled"):
            return handled
        return handled

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
                    pg_pool=self.pg_pool,
                    redis=self.redis,
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
                payload = {
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
                }

                async def _run_copilot_turn() -> Dict[str, Any]:
                    return await orch.process(payload)

                res = await telegram_user_admin.run_with_queued_telegram_delivery(
                    self,
                    runner=_run_copilot_turn,
                )
                if not isinstance(res, dict):
                    res = {}
                state["final_answer"] = res.get("answer") or ""
                state["followups"] = res.get("followups") or []
                if isinstance(res.get("turn_log"), dict):
                    state["turn_log"] = res["turn_log"]
                if isinstance(res.get("timeline"), dict):
                    state["timeline"] = res["timeline"]
            except Exception as e:
                await self.report_turn_error(state=state, exc=e, title="Turn Error")

            return state

        # Wire the graph: START → orchestrate → END (single-node linear graph)
        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    async def _ensure_ui_build(self) -> None:
        storage_root = self.bundle_storage_root()
        if storage_root is not None:
            _cleanup_legacy_top_level_knowledge_symlinks(storage_root)
        await super()._ensure_ui_build()

    async def on_bundle_load(self, **kwargs) -> None:
        """Build bundle UI assets and the dedicated ks: knowledge root for this tenant/project instance."""
        state = {
            "tenant": getattr(getattr(self.comm_context, "actor", None), "tenant_id", None),
            "project": getattr(getattr(self.comm_context, "actor", None), "project_id", None),
        }
        if state["tenant"] and state["project"]:
            await self.refresh_bundle_props(
                state=state,
                notify=False,
                reason="bundle.on_load",
            )
        await asyncio.to_thread(self._ensure_knowledge_space, reason="on_bundle_load")
        await self._ensure_ui_build()
        return None

    async def pre_run_hook(self, *, state: Dict[str, Any], econ_ctx: Optional[Dict[str, Any]] = None) -> None:
        """Reconcile knowledge space only if load-time prep did not happen or config changed."""
        await super().pre_run_hook(state=state, econ_ctx=econ_ctx or {})
        await asyncio.to_thread(self._reconcile_knowledge_space, reason="pre_run_hook")
        return None

    def _doc_reader_storage_root(self) -> pathlib.Path | None:
        storage_root = self.bundle_storage_root()
        knowledge_root = _knowledge_root_for_storage(storage_root) if storage_root is not None else None
        if knowledge_root is not None:
            doc_reader_tools.ensure_knowledge_root(storage_root=knowledge_root)
        return knowledge_root

    def _build_doc_reader_mcp_app(self, *, name_suffix: str) -> Any:
        return doc_reader_tools.build_doc_reader_mcp_app(
            name=f"{BUNDLE_ID}.{name_suffix}",
            storage_root_provider=self._doc_reader_storage_root,
            refresh_knowledge_space=lambda: self._reconcile_knowledge_space(reason=name_suffix),
        )

    def _require_doc_reader_mcp_auth(self, request: Request) -> None:
        header_name = self.bundle_prop(
            "mcp.doc_reader.auth.header_name",
            "X-KDCube-Copilot-MCP-Token",
        )
        expected_token = get_secret("b:mcp.doc_reader.auth.shared_token")
        provided_token = request.headers.get(header_name)

        if not expected_token:
            raise RuntimeError(
                "Bundle secret b:mcp.doc_reader.auth.shared_token is not configured."
            )
        if provided_token != expected_token:
            raise HTTPException(
                status_code=401,
                detail=f"Missing or invalid {header_name}",
            )

    @mcp(alias="kdcube-doc", route="public")
    def kdcube_doc_mcp(self, **kwargs):
        return self._build_doc_reader_mcp_app(name_suffix="kdcube-doc")

    @mcp(alias="doc_reader", route="operations")
    def doc_reader_mcp(self, request: Request, **kwargs):
        # Bundle-owned MCP auth contract for this endpoint:
        #
        # bundles.yaml
        #   items:
        #     - id: "kdcube.copilot"
        #       config:
        #         mcp:
        #           doc_reader:
        #             auth:
        #               header_name: "X-KDCube-Copilot-MCP-Token"
        #
        # bundles.secrets.yaml
        #   items:
        #     - id: "kdcube.copilot"
        #       secrets:
        #         mcp:
        #           doc_reader:
        #             auth:
        #               shared_token: "<rotate-me>"
        #
        # Share with MCP clients:
        # - the operations route URL for alias "doc_reader"
        # - the header name from bundle props
        # - the token provisioned in bundle secrets
        self._require_doc_reader_mcp_auth(request)
        return self._build_doc_reader_mcp_app(name_suffix="doc_reader")

    @api(alias="copilot_webapp_widget", route="operations", user_types=("registered", "paid", "privileged"))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:squares-2x2",
            "lucide": "PanelsTopLeft",
        },
        alias=COPILOT_WEBAPP_ALIAS,
        user_types=(),
        roles=(),
    )
    def copilot_webapp_widget(
        self,
        **kwargs,
    ):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "KDCube Copilot WebApp is served from the built widget source folder."
            "</div>"
        ]

    @api(method="POST", alias="copilot_webapp_data", route="operations", user_types=("registered", "paid", "privileged"))
    async def copilot_webapp_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        mark_memory_seen: bool = False,
        widget_path: str = "",
        path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_webapp.payload(
            self,
            user_id=user_id,
            fingerprint=fingerprint,
            mark_memory_seen=mark_memory_seen,
            widget_path=widget_path or path,
            include_admin=telegram_webapp.user_has_role(self, TELEGRAM_ADMIN_ROLE),
        )

    @api(method="GET", alias="conversations_list", route="operations", user_types=("registered", "paid", "privileged"))
    async def conversations_list(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_webapp.list_conversations(
            self,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    @api(method="POST", alias="conversations_create", route="operations", user_types=("registered", "paid", "privileged"))
    async def conversations_create(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        title: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_webapp.create_conversation(
            self,
            user_id=user_id,
            fingerprint=fingerprint,
            title=title,
        )

    @api(method="POST", alias="conversations_switch", route="operations", user_types=("registered", "paid", "privileged"))
    async def conversations_switch(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_webapp.switch_conversation(
            self,
            conversation_id=conversation_id,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    @api(method="POST", alias="conversations_delete", route="operations", user_types=("registered", "paid", "privileged"))
    async def conversations_delete(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        delete_history: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_webapp.delete_conversation(
            self,
            conversation_id=conversation_id,
            user_id=user_id,
            fingerprint=fingerprint,
            delete_history=delete_history,
        )

    @api(method="POST", alias="telegram_user_admin_data", route="operations", roles=(TELEGRAM_ADMIN_ROLE,))
    async def telegram_user_admin_data(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        return telegram_user_admin.payload(self)

    @api(method="POST", alias="telegram_user_admin_upsert", route="operations", roles=(TELEGRAM_ADMIN_ROLE,))
    async def telegram_user_admin_upsert(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        telegram_username: str = "",
        kdcube_user_id: str = "",
        role: str = "anonymous",
        conversation_id: str = "",
        notes: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        result = telegram_user_admin.upsert(
            self,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
            kdcube_user_id=kdcube_user_id,
            role=role,
            conversation_id=conversation_id,
            notes=notes,
        )
        result["notification"] = await telegram_user_admin.notify_access_change(self, result=result)
        return result

    @api(method="POST", alias="telegram_user_admin_delete", route="operations", roles=(TELEGRAM_ADMIN_ROLE,))
    async def telegram_user_admin_delete(
        self,
        *,
        telegram_user_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return telegram_user_admin.delete(self, telegram_user_id=telegram_user_id)

    @api(
        method="POST",
        alias="telegram_webhook",
        route="public",
        public_auth=TELEGRAM_WEBHOOK_PUBLIC_AUTH,
    )
    async def telegram_webhook(self, **update) -> Dict[str, Any]:
        return await telegram_user_admin.handle_webhook(self, **update)

    @api(method="GET", alias="telegram_profile", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_profile(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.profile(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
        )

    @api(method="GET", alias="conversations_list", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_public_conversations_list(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.list_conversations(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
        )

    @api(method="POST", alias="telegram_conversations_create", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_conversations_create(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        title: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.create_conversation(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            title=title,
        )

    @api(method="POST", alias="telegram_conversations_switch", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_conversations_switch(
        self,
        conversation_id: str,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.switch_conversation(
            self,
            conversation_id=conversation_id,
            request=request,
            telegram_init_data=telegram_init_data,
        )

    @api(method="POST", alias="telegram_conversations_delete", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_conversations_delete(
        self,
        conversation_id: str,
        request: Any = None,
        telegram_init_data: str = "",
        delete_history: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.delete_conversation(
            self,
            conversation_id=conversation_id,
            request=request,
            telegram_init_data=telegram_init_data,
            delete_history=delete_history,
        )

    @api(method="POST", alias="telegram_copilot_webapp_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_copilot_webapp_data(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        mark_memory_seen: bool = False,
        widget_path: str = "",
        path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.webapp_data(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            mark_memory_seen=mark_memory_seen,
            widget_path=widget_path,
            path=path,
        )

    async def _telegram_memory_widget_call(
        self,
        operation: str,
        *,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        identity = telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        method = getattr(self, operation)
        with self._memory_user_identity(
            user_id=identity.user_id,
            fingerprint=identity.fingerprint,
            user_type="privileged" if identity.role == "admin" else "registered",
        ):
            result = method(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        if isinstance(result, dict):
            result.setdefault("auth_surface", "telegram_webapp")
            result.setdefault("telegram_user_id", identity.telegram_user_id)
            result.setdefault("user_id", identity.user_id)
        return result if isinstance(result, dict) else {"ok": True, "result": result}

    # Public Telegram bridge APIs for the shared memory component embedded in
    # `copilot_webapp`. These are operation endpoints, not separate widget
    # surfaces; the only Copilot web-app widget alias is `copilot_webapp`.
    @api(method="POST", alias="telegram_memories_widget_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_data(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_data", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_get", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_get(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_get", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_events", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_events(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_events", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_create", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_create(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_create", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_update", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_update(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_update", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_pin", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_pin(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_pin", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_confirm", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_confirm(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_confirm", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_retire", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_retire(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_retire", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_delete", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_delete(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_delete", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_snapshot_create", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_snapshot_create(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_snapshot_create", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_snapshots", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_snapshots(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_snapshots", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_snapshot_export", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_snapshot_export(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_snapshot_export", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_snapshot_restore_preview", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_snapshot_restore_preview(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_snapshot_restore_preview", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_snapshot_restore_apply", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_snapshot_restore_apply(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_snapshot_restore_apply", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_analyze", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_analyze(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_analyze", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_run", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_run(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_run", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_jobs", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_jobs(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_jobs", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_job", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_job(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_job", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_export", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_export(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_export", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_memories_widget_reconcile_apply", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memories_widget_reconcile_apply(self, request: Any = None, telegram_init_data: str = "", **kwargs) -> Dict[str, Any]:
        return await self._telegram_memory_widget_call("memories_widget_reconcile_apply", request=request, telegram_init_data=telegram_init_data, **kwargs)

    @api(method="POST", alias="telegram_webapp_user_admin_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_user_admin_data_public(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.admin_payload(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
        )

    @api(method="POST", alias="telegram_webapp_user_admin_upsert", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_user_admin_upsert_public(
        self,
        telegram_user_id: str,
        request: Any = None,
        telegram_init_data: str = "",
        telegram_chat_id: str = "",
        telegram_username: str = "",
        kdcube_user_id: str = "",
        role: str = "anonymous",
        conversation_id: str = "",
        notes: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.admin_upsert(
            self,
            telegram_user_id=telegram_user_id,
            request=request,
            telegram_init_data=telegram_init_data,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
            kdcube_user_id=kdcube_user_id,
            role=role,
            conversation_id=conversation_id,
            notes=notes,
        )

    @api(method="POST", alias="telegram_webapp_user_admin_delete", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_user_admin_delete_public(
        self,
        telegram_user_id: str,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        return await telegram_widget_ops.admin_delete(
            self,
            telegram_user_id=telegram_user_id,
            request=request,
            telegram_init_data=telegram_init_data,
        )

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

    def _expected_knowledge_paths(
        self,
        *,
        bundle_root: pathlib.Path,
        storage_root: pathlib.Path,
    ) -> tuple[pathlib.Path | None, bool, str | None, str | None]:
        """
        Resolve the expected knowledge paths without cloning/fetching git.
        Hot MCP reads use this to trust an already-built shared knowledge space
        before touching network-backed git materialization.
        """
        props = dict(self.bundle_props or {})
        knowledge_def = props.get("knowledge") or {}
        repo = (knowledge_def.get("repo") or "").strip()
        ref = (knowledge_def.get("ref") or "").strip()
        root_raw = (knowledge_def.get("root") or "").strip()
        validate = knowledge_def.get("validate_refs")
        validate_refs = True if validate is None else bool(validate)

        if not repo and not root_raw:
            for parent in bundle_root.resolve().parents:
                if (parent / "docs").is_dir() and (parent / "src").is_dir():
                    root_raw = str(parent.resolve())
                    break
            if not root_raw:
                repo = (os.getenv("KDCUBE_KNOWLEDGE_REPO") or "https://github.com/kdcube/kdcube-ai-app.git").strip()
                if not ref:
                    ref = (os.getenv("KDCUBE_KNOWLEDGE_REF") or "").strip()
                root_raw = "app/ai-app"

        base_root = bundle_root
        if repo:
            try:
                from kdcube_ai_app.infra.git.auth import normalize_git_remote_url
                from kdcube_ai_app.infra.plugin.git_bundle import compute_git_bundle_paths

                repo_url = normalize_git_remote_url(repo)
                paths = compute_git_bundle_paths(
                    bundle_id=f"{BUNDLE_ID}.knowledge",
                    git_url=repo_url,
                    git_ref=ref or None,
                    git_subdir=None,
                    bundles_root=(storage_root / "repos").resolve(),
                )
                base_root = paths.repo_root
            except Exception:
                base_root = storage_root / "repos"

        source_root = None
        if root_raw:
            source_root = pathlib.Path(root_raw)
            if not source_root.is_absolute():
                source_root = (base_root / source_root).resolve()

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
        storage_root = self.bundle_storage_root()
        bundle_root = None
        try:
            spec = getattr(self.config, "ai_bundle_spec", None)
            if spec and getattr(spec, "path", None):
                bundle_root = pathlib.Path(spec.path).resolve()
        except Exception:
            bundle_root = None
        if not bundle_root:
            bundle_root = pathlib.Path(__file__).resolve().parent

        if not storage_root:
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
            storage_root=storage_root,
        )
        ws_root = _knowledge_root_for_storage(storage_root)
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

    def _expected_knowledge_setup(
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
        storage_root = self.bundle_storage_root()
        bundle_root = None
        try:
            spec = getattr(self.config, "ai_bundle_spec", None)
            if spec and getattr(spec, "path", None):
                bundle_root = pathlib.Path(spec.path).resolve()
        except Exception:
            bundle_root = None
        if not bundle_root:
            bundle_root = pathlib.Path(__file__).resolve().parent

        if not storage_root:
            return (
                None,
                bundle_root,
                None,
                True,
                None,
                None,
                None,
            )

        source_root, validate_refs, repo, ref = self._expected_knowledge_paths(
            bundle_root=bundle_root,
            storage_root=storage_root,
        )
        ws_root = _knowledge_root_for_storage(storage_root)
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
                expected_ws_root,
                _expected_bundle_root,
                expected_source_root,
                _expected_validate_refs,
                _expected_repo,
                _expected_ref,
                expected_signature,
            ) = self._expected_knowledge_setup()
            if (
                expected_ws_root
                and _read_shared_knowledge_signature(expected_ws_root) == expected_signature
                and _knowledge_outputs_ready(storage_root=expected_ws_root, source_root=expected_source_root)
            ):
                knowledge_resolver.KNOWLEDGE_ROOT = expected_ws_root
                self._knowledge_signature = expected_signature
                self.logger.log(
                    f"[kdcube.copilot] knowledge build skipped ({reason}): shared signature cache hit for storage={expected_ws_root}",
                    "INFO",
                )
                return None
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
            with _knowledge_build_lock(ws_root, logger=self.logger):
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
                if not _knowledge_outputs_ready(storage_root=ws_root, source_root=source_root):
                    raise RuntimeError(f"knowledge build completed but outputs are not ready: storage={ws_root}")
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
            ) = self._expected_knowledge_setup()
            if not ws_root:
                self.logger.log(
                    f"[kdcube.copilot] knowledge reconcile skipped ({reason}): bundle storage root is unavailable.",
                    "WARNING",
                )
                return None
            knowledge_resolver.KNOWLEDGE_ROOT = ws_root
            if _read_shared_knowledge_signature(ws_root) == signature and _knowledge_outputs_ready(
                storage_root=ws_root,
                source_root=source_root,
            ):
                self._knowledge_signature = signature
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
            storage_root = self.bundle_storage_root()
            if not storage_root:
                return None
            local_root = storage_root / "_knowledge_base_admin"
            local_root.mkdir(parents=True, exist_ok=True)
            return local_root
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
                or get_service_secret("git.http_token")
            ),
            "has_anthropic_api_key": bool(
                get_user_secret("anthropic.api_key", user_id=user_id, bundle_id=bundle_id)
                or get_service_secret("anthropic.api_key")
            ),
            "has_claude_code_key": bool(
                get_user_secret("anthropic.claude_code_key", user_id=user_id, bundle_id=bundle_id)
                or get_service_secret("anthropic.claude_code_key")
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
        claude_code_model: Optional[str] = None,
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
            "claude_code_model": str(claude_code_model or DEFAULT_CLAUDE_CODE_MODEL).strip() or DEFAULT_CLAUDE_CODE_MODEL,
        }

    def _kb_admin_claude_env(self, *, user_id: str) -> dict[str, str]:
        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID
        env: dict[str, str] = {}
        api_key = (
            get_user_secret("anthropic.api_key", user_id=user_id, bundle_id=bundle_id)
            or get_service_secret("anthropic.api_key")
        )
        auth_token = (
            get_user_secret("anthropic.auth_token", user_id=user_id, bundle_id=bundle_id)
            or get_service_secret("anthropic.auth_token")
        )
        claude_code_key = (
            get_user_secret("anthropic.claude_code_key", user_id=user_id, bundle_id=bundle_id)
            or get_service_secret("anthropic.claude_code_key")
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
            or get_service_secret("git.http_token")
        )
        http_user = (
            get_user_secret("git.http_user", user_id=user_id, bundle_id=bundle_id)
            or get_service_secret("git.http_user")
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
            base_url=f"http://localhost:{self.settings.CHAT_APP_PORT}",
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
        roles=("kdcube:role:super-admin",),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:circle-stack",
            "lucide": "DatabaseZap",
        },
        alias="knowledge_base_admin",
        roles=("kdcube:role:super-admin",),
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

    @api(alias="knowledge_base_admin_widget_data", roles=("kdcube:role:super-admin",))
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

    @api(alias="knowledge_base_admin_conversation_data", roles=("kdcube:role:super-admin",))
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

    @api(alias="knowledge_base_admin_save_settings", roles=("kdcube:role:super-admin",))
    def knowledge_base_admin_save_settings(
        self,
        content_repos: Optional[list[dict[str, Any]]] = None,
        output_repo: Optional[dict[str, Any]] = None,
        claude_code_model: Optional[str] = None,
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
            claude_code_model=claude_code_model,
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
                claude_code_model=claude_code_model or DEFAULT_CLAUDE_CODE_MODEL,
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
                            claude_code_model=str(config.get("claude_code_model") or DEFAULT_CLAUDE_CODE_MODEL),
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

    @api(alias="knowledge_base_admin_sync_workspace", roles=("kdcube:role:super-admin",))
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

    @api(alias="knowledge_base_admin_push_output_repo", roles=("kdcube:role:super-admin",))
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

    @api(alias="knowledge_base_admin_reset_output_repo", roles=("kdcube:role:super-admin",))
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

    @api(alias="knowledge_base_admin_chat", roles=("kdcube:role:super-admin",))
    async def knowledge_base_admin_chat(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        turn_kind: str = "regular",
        claude_code_model: Optional[str] = None,
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
        selected_model = str(claude_code_model or config.get("claude_code_model") or DEFAULT_CLAUDE_CODE_MODEL).strip() or DEFAULT_CLAUDE_CODE_MODEL
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
        existing_messages = list(conversation.get("messages") or [])
        resume_existing = any(
            str(item.get("role") or "").strip() == "assistant"
            and not bool((item.get("metadata") or {}).get("error"))
            for item in existing_messages
        )
        cid = str(conversation["conversation_id"])
        turn_id_value = f"kb_admin_turn_{uuid.uuid4().hex[:12]}"
        bound_comm = self._kb_admin_bound_comm(conversation_id=cid, turn_id=turn_id_value)
        workspace_root_path = pathlib.Path(workspace["workspace_root"])

        history_lines: list[str] = []
        for item in existing_messages[-12:]:
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
                model=selected_model,
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
        session_store = ClaudeCodeSessionStoreConfig(
            implementation=str(getattr(self.settings, "CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION", "local") or "local"),
            local_root=workspace_root_path / ".claude",
            tenant=str(getattr(self.settings, "TENANT", "home") or "home"),
            project=str(getattr(self.settings, "PROJECT", "default-project") or "default-project"),
            user_id=str(target_user or "anonymous"),
            conversation_id=cid,
            agent_name=KB_ADMIN_AGENT_NAME,
            git_repo=getattr(self.settings, "CLAUDE_CODE_SESSION_GIT_REPO", None),
        )

        def _refresh_claude_support_files() -> None:
            workspace["workspace_payload"] = kb_admin_refresh_workspace_support_files(
                local_root=local_root,
                user_id=target_user,
                config=config,
                repo_statuses=list(workspace.get("repo_statuses") or []),
            )

        try:
            result = await run_claude_code_turn(
                agent=agent,
                prompt=full_prompt,
                kind=normalized_turn_kind,
                resume_existing=resume_existing or normalized_turn_kind in ("followup", "steer"),
                session_store=session_store,
                refresh_support_files=_refresh_claude_support_files,
                logger=self.logger._logger if hasattr(self.logger, "_logger") else None,
            )
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

        assistant_text = result.final_text
        if not assistant_text and result.status != "completed":
            assistant_text = f"Error: {result.error_message or f'Claude exited with code {result.exit_code}'}"

        append_conversation_message(
            storage,
            target_user,
            conversation_id=cid,
            role="assistant",
            text=assistant_text,
            metadata={
                "turn_kind": normalized_turn_kind,
                "status": result.status,
                "delta_count": result.delta_count,
                "exit_code": result.exit_code,
                "claude_session_id": result.session_id,
                "provider": result.provider,
                "requested_model": result.requested_model or selected_model,
                "model": result.model,
                "usage": dict(result.usage or {}),
                "cost_usd": result.cost_usd,
                "duration_ms": result.duration_ms,
                "api_duration_ms": result.api_duration_ms,
                "error": result.status != "completed",
            },
        )
        conversation_doc = kb_admin_load_conversation(storage, target_user, cid)
        return {
            "ok": True,
            "conversation_id": cid,
            "conversation": conversation_doc,
            "result": {
                "status": result.status,
                "final_text": assistant_text,
                "delta_count": result.delta_count,
                "exit_code": result.exit_code,
                "session_id": result.session_id,
                "provider": result.provider,
                "requested_model": result.requested_model or selected_model,
                "model": result.model,
                "usage": dict(result.usage or {}),
                "cost_usd": result.cost_usd,
                "duration_ms": result.duration_ms,
                "api_duration_ms": result.api_duration_ms,
                "error_message": result.error_message,
            },
        }

    def configuration_defaults(self) -> Dict[str, Any]:
        copilot_defaults = {
            "enabled": {
                "widget": {
                    # Copilot exposes user memory inside the unified web app; keep
                    # the inherited standalone memory widget out of the widget bar.
                    "memories": False,
                },
            },
            "visibility": {
                "bundle": {
                    "allowed_roles": [],
                },
            },
            "integrations": {
                "telegram": {
                    "enabled": False,
                    "webhook_url": "",
                    "send_responses": True,
                    "stream_activity": True,
                    "web_app_auth_max_age_seconds": 86400,
                },
            },
            "ui": {
                "widgets": {
                    COPILOT_WEBAPP_ALIAS: {
                        "enabled": False,
                        "src_folder": "ui/widgets/copilot_webapp",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
                            "memory_widget": {
                                "src_folder": "sdk://context/memory/ui/widget/memories",
                                "target": "_shared/memory-widget",
                            },
                            "telegram_widget": {
                                "src_folder": "sdk://integrations/telegram/ui/widget.telegram",
                                "target": "_shared/telegram-widget",
                            },
                        },
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), copilot_defaults)

    @property
    def configuration(self) -> Dict[str, Any]:
        """
        Override model configuration for this bundle.
        role_models maps logical roles → specific LLM providers/models.
        The gate agent uses a cheap/fast model (Haiku), while the solver
        uses a strong model (Sonnet) for hard reasoning and answer generation.
        """
        sonnet_45 = "claude-sonnet-4-5-20250929"
        opus_46 = "claude-opus-4-6"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},                         # Gate — fast, lightweight
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},            # Answer — strong generation
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},              # Solver coordinator
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45}, # Solver — hard reasoning
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},  # Solver — routine steps
            "memory.reconciler": {"provider": "anthropic", "model": sonnet_45},
            "memory.reconciler.lite": {"provider": "anthropic", "model": haiku_4},
            "memory.reconciler.regular": {"provider": "anthropic", "model": sonnet_45},
            "memory.reconciler.strong": {"provider": "anthropic", "model": opus_46},
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
