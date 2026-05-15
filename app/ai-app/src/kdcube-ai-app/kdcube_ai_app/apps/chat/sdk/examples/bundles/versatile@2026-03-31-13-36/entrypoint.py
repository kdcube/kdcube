from __future__ import annotations

import inspect
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp as telegram_webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, on_job, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from .event_filter import BundleEventFilter
from .orchestrator.workflow import VersatileWorkflow

BUNDLE_ID = "versatile@2026-03-31-13-36"
WORKFLOW_NAME = "versatile"
TELEGRAM_ADMIN_ROLE = "kdcube:role:super-admin"
TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEGRAM_WEBHOOK_PUBLIC_AUTH = {
    "mode": "header_secret",
    "header": TELEGRAM_WEBHOOK_SECRET_HEADER,
    "secret_key": "integrations.telegram.webhook_secret",
}
TELEGRAM_WEBAPP_PUBLIC_AUTH = "none"
TELEGRAM_MEMORY_WIDGET_ALIAS = "telegram_memories"


def _api_visibility(
    alias: str,
    *,
    user_types: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
) -> Dict[str, Any]:
    return {
        "user_types": user_types,
        "user_types_config": f"visibility.api.{alias}.user_types",
        "roles": roles,
        "roles_config": f"visibility.api.{alias}.roles",
    }


def _widget_visibility(
    alias: str,
    *,
    user_types: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
) -> Dict[str, Any]:
    return {
        "user_types": user_types,
        "user_types_config": f"visibility.widget.{alias}.user_types",
        "roles": roles,
        "roles_config": f"visibility.widget.{alias}.roles",
    }


def _storage_root_or_error(entrypoint: Any) -> Path:
    storage_root = entrypoint.bundle_storage_root()
    if not storage_root:
        raise RuntimeError("Bundle storage backend is not configured for this bundle.")
    return storage_root


def _telegram_user_admin_storage(entrypoint: Any) -> TelegramUserAdminStorage:
    return TelegramUserAdminStorage(_storage_root_or_error(entrypoint))


class _VersatileTaskWidgets:
    @staticmethod
    async def payload(entrypoint: Any, **kwargs) -> Dict[str, Any]:
        del entrypoint, kwargs
        return {
            "user_id": "",
            "tasks": [],
            "count": 0,
            "supported_now": {
                "tasks": False,
                "reason": "versatile reference webapp demonstrates memory, conversations, and Telegram admin.",
            },
        }


class _VersatileMemoryWidgets:
    @staticmethod
    async def payload(
        entrypoint: Any,
        *,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        mark_seen: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del user_id, fingerprint, mark_seen, kwargs
        return await entrypoint.memories_widget_data(scope_filter="current_bundle", limit=30)


class _VersatileSettingsWidgets:
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


class _VersatileTaskOperations:
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
    memory_widgets_module=_VersatileMemoryWidgets,
    settings_widgets_module=_VersatileSettingsWidgets,
    task_widgets_module=_VersatileTaskWidgets,
    telegram_user_admin_module=telegram_user_admin,
    bundle_id=BUNDLE_ID,
)
telegram_widget_ops.configure_telegram_widget_ops(
    task_operations_module=_VersatileTaskOperations,
    telegram_user_admin_module=telegram_user_admin,
    telegram_widget_auth_module=telegram_widget_auth,
    webapp_module=telegram_webapp,
    bundle_id=BUNDLE_ID,
)


@agentic_workflow(
    name=WORKFLOW_NAME,
    version="1.0.0",
    priority=100,
    allowed_roles_config="visibility.bundle.allowed_roles",
)
class VersatileEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    """All-features reference bundle for bundle builders."""

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
        self.graph = self._build_graph()

    @on_job
    async def on_job(self, **kwargs) -> Dict[str, Any]:
        handled = await super().handle_job(**kwargs)
        if handled.get("handled"):
            return handled
        return handled

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

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

            try:
                orch = VersatileWorkflow(
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

                async def _run_versatile_turn() -> Dict[str, Any]:
                    return await orch.process(payload)

                res = await telegram_user_admin.run_with_queued_telegram_delivery(
                    self,
                    runner=_run_versatile_turn,
                )
                if not isinstance(res, dict):
                    res = {}
                state["final_answer"] = res.get("answer") or ""
                state["followups"] = res.get("followups") or []
                if isinstance(res.get("turn_log"), dict):
                    state["turn_log"] = res["turn_log"]
                if isinstance(res.get("timeline"), dict):
                    state["timeline"] = res["timeline"]
            except Exception as exc:
                await self.report_turn_error(state=state, exc=exc, title="Turn Error")

            return state

        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    async def on_bundle_load(self, **kwargs) -> None:
        await super().on_bundle_load(**kwargs)
        storage_root = self.bundle_storage_root()
        if storage_root:
            (storage_root / "_ops").mkdir(parents=True, exist_ok=True)

        return None

    @api(
        alias="versatile_webapp_widget",
        route="operations",
        **_api_visibility("versatile_webapp_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:rectangle-group",
            "lucide": "PanelTop",
        },
        alias="versatile_webapp",
        **_widget_visibility("versatile_webapp"),
    )
    def versatile_webapp_widget(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        widget_path: str = "",
        path: str = "",
        **kwargs,
    ):
        del user_id, fingerprint, widget_path, path, kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Versatile webapp is served from the built widget source folder."
            "</div>"
        ]

    @api(alias="telegram_memories_widget", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:book-open",
            "lucide": "NotebookTabs",
        },
        alias=TELEGRAM_MEMORY_WIDGET_ALIAS,
        user_types=(),
        roles=(),
    )
    def telegram_memories_widget(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ):
        del request, telegram_init_data, kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Telegram user memories are served from the built memories widget."
            "</div>"
        ]

    @api(
        method="POST",
        alias="versatile_webapp_data",
        route="operations",
        **_api_visibility("versatile_webapp_data"),
    )
    async def versatile_webapp_data(
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
        )

    @api(method="GET", alias="conversations_list", route="operations", **_api_visibility("conversations_list"))
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

    @api(method="POST", alias="conversations_create", route="operations", **_api_visibility("conversations_create"))
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

    @api(method="POST", alias="conversations_switch", route="operations", **_api_visibility("conversations_switch"))
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

    @api(method="POST", alias="conversations_delete", route="operations", **_api_visibility("conversations_delete"))
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

    @api(
        method="POST",
        alias="telegram_user_admin_data",
        route="operations",
        **_api_visibility("telegram_user_admin_data", roles=(TELEGRAM_ADMIN_ROLE,)),
    )
    async def telegram_user_admin_data(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        return telegram_user_admin.payload(self)

    @api(
        method="POST",
        alias="telegram_user_admin_upsert",
        route="operations",
        **_api_visibility("telegram_user_admin_upsert", roles=(TELEGRAM_ADMIN_ROLE,)),
    )
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
        return telegram_user_admin.upsert(
            self,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
            kdcube_user_id=kdcube_user_id,
            role=role,
            conversation_id=conversation_id,
            notes=notes,
        )

    @api(
        method="POST",
        alias="telegram_user_admin_delete",
        route="operations",
        **_api_visibility("telegram_user_admin_delete", roles=(TELEGRAM_ADMIN_ROLE,)),
    )
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

    @api(method="POST", alias="telegram_versatile_webapp_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_versatile_webapp_data(
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

    def configuration_defaults(self) -> Dict[str, Any]:
        versatile_defaults = {
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
                "web_app_widgets": {
                    "versatile_webapp": {
                        "src_folder": "ui/widgets/versatile_webapp",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
                            "memory_widget": {
                                "src_folder": "sdk://context/memory/ui/widget/memories",
                                "target": "_shared/memory-widget",
                            },
                        },
                    },
                    TELEGRAM_MEMORY_WIDGET_ALIAS: {
                        "enabled": False,
                        "src_folder": "sdk://context/memory/ui/widget/memories",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), versatile_defaults)

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

        execution_cfg = dict(config.get("execution") or {})
        execution_cfg.setdefault("runtime", {"mode": "docker"})
        config["execution"] = execution_cfg

        subsystems = dict(config.get("subsystems") or {})
        config["subsystems"] = subsystems

        ui_cfg = dict(config.get("ui") or {})
        main_view_cfg = dict(ui_cfg.get("main_view") or {})
        main_view_cfg.setdefault("src_folder", "ui/main")
        main_view_cfg.setdefault(
            "build_command",
            "npm install && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
        )
        ui_cfg["main_view"] = main_view_cfg
        config["ui"] = ui_cfg

        mcp = dict(config.get("mcp") or {})
        mcp.setdefault("services", {})
        config["mcp"] = mcp
        return config

    @property
    def app_quota_policies(self):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy

        return {
            "anonymous": QuotaPolicy(
                max_concurrent=1,
                requests_per_day=5,
                requests_per_month=120,
                total_requests=None,
                tokens_per_hour=200_000,
                tokens_per_day=1_500_000,
                tokens_per_month=20_000_000,
            ),
            "free": QuotaPolicy(
                max_concurrent=2,
                requests_per_day=100,
                requests_per_month=30_000,
                total_requests=None,
                tokens_per_hour=500_000,
                tokens_per_day=2_000_000,
                tokens_per_month=30_000_000,
            ),
            "payasyougo": QuotaPolicy(
                max_concurrent=3,
                requests_per_day=500,
                requests_per_month=100_000,
                total_requests=None,
                tokens_per_hour=2_000_000,
                tokens_per_day=6_000_000,
                tokens_per_month=80_000_000,
            ),
            "admin": QuotaPolicy(max_concurrent=10),
        }

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
