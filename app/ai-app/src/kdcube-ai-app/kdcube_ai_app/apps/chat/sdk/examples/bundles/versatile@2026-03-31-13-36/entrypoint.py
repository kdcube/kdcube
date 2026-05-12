from __future__ import annotations

import base64
import json
import os
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp as telegram_webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import normalize_exec_runtime_config
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)
from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    build_exec_output_contract,
    run_exec_tool,
)
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, mcp, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from . import tools_descriptor
from .tools import preference_tools as preference_tools_mod
from .event_filter import BundleEventFilter
from .orchestrator.workflow import VersatileWorkflow
from .preferences_store import (
    build_preferences_storage,
    build_preferences_canvas_document,
    export_preferences_canvas_xlsx,
    get_preferences_snapshot,
    import_preferences_canvas_xlsx,
    build_widget_payload,
    save_preferences_canvas_entries,
    save_preferences_canvas_document,
)

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
OPERATION_API_VISIBILITY_ALIASES = (
    "versatile_webapp_widget",
    "versatile_webapp_data",
    "conversations_list",
    "conversations_create",
    "conversations_switch",
    "conversations_delete",
    "preferences_canvas_data",
    "preferences_canvas_save",
    "preferences_canvas_export_excel",
    "preferences_canvas_import_excel",
)
TELEGRAM_OPERATION_API_ALIASES = (
    "telegram_user_admin_data",
    "telegram_user_admin_upsert",
    "telegram_user_admin_delete",
)
WIDGET_VISIBILITY_ALIASES = (
    "versatile_webapp",
)


def _visibility_defaults(
    aliases: tuple[str, ...],
    *,
    user_types: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
) -> Dict[str, Dict[str, list[str]]]:
    return {
        alias: {
            "user_types": list(user_types),
            "roles": list(roles),
        }
        for alias in aliases
    }


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
    def payload(
        entrypoint: Any,
        *,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        mark_seen: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        del mark_seen, kwargs
        storage = entrypoint._preferences_storage()
        target_user = user_id or fingerprint or getattr(entrypoint.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
                "items": [],
                "entries": [],
                "count": 0,
                "document_text": "{}\n",
            }
        payload = build_preferences_canvas_document(storage=storage, user_id=target_user)
        entries = payload.get("entries") or []
        payload.update(
            {
                "ok": True,
                "user_id": target_user,
                "items": entries,
                "memories": entries,
                "memos": entries,
                "count": len(entries),
            }
        )
        return payload


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
    bot_token=lambda: telegram_user_admin.bot_token(),
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
class VersatileEntrypoint(BaseEntrypointWithEconomics):
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
        self._preferences_mcp_app: Any = None
        self.graph = self._build_graph()

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

    def _preferences_storage(self) -> Optional[AIBundleStorage]:
        actor = getattr(self.comm_context, "actor", None)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        tenant = getattr(actor, "tenant_id", None) or getattr(self.comm, "tenant", None) or self.settings.TENANT
        project = getattr(actor, "project_id", None) or getattr(self.comm, "project", None) or self.settings.PROJECT
        bundle_id = getattr(bundle_spec, "id", None) or BUNDLE_ID
        if not tenant or not project or not bundle_id:
            return None
        return build_preferences_storage(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )

    def _preferences_ops_root(self) -> Path:
        storage_root = self.bundle_storage_root()
        if storage_root:
            root = storage_root / "_ops"
            root.mkdir(parents=True, exist_ok=True)
            return root
        root = Path(tempfile.gettempdir()) / "kdcube-versatile-ops"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _preferences_mcp_scope(self, *, user_id: str | None = None) -> Dict[str, Any]:
        storage = self._preferences_storage()
        if not storage:
            raise RuntimeError("Bundle storage backend is not configured for this bundle.")

        actor = getattr(self.comm_context, "actor", None)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        tenant = getattr(actor, "tenant_id", None) or getattr(self.comm, "tenant", None) or self.settings.TENANT
        project = getattr(actor, "project_id", None) or getattr(self.comm, "project", None) or self.settings.PROJECT
        bundle_id = getattr(bundle_spec, "id", None) or BUNDLE_ID
        effective_user_id = user_id or getattr(self.comm, "user_id", None) or "anonymous"
        return {
            "tenant": tenant,
            "project": project,
            "bundle_id": bundle_id,
            "user_id": effective_user_id,
            "storage": storage,
        }

    def _require_preferences_mcp_auth(self, request: Request) -> None:
        header_name = self.bundle_prop(
            "mcp.preferences.auth.header_name",
            "X-Versatile-Preferences-MCP-Token",
        )
        expected_token = get_secret("b:mcp.preferences.auth.shared_token")
        provided_token = request.headers.get(header_name)

        if not expected_token:
            raise RuntimeError(
                "Bundle secret b:mcp.preferences.auth.shared_token is not configured."
            )
        if provided_token != expected_token:
            raise HTTPException(
                status_code=401,
                detail=f"Missing or invalid {header_name}",
            )

    @mcp(alias="preferences_tools", route="operations", transport="streamable-http")
    def preferences_tools_mcp(self, request: Request, **kwargs):
        # Bundle-owned MCP auth contract for this endpoint:
        #
        # bundles.yaml
        #   items:
        #     - id: "versatile@2026-03-31-13-36"
        #       config:
        #         mcp:
        #           preferences:
        #             auth:
        #               header_name: "X-Versatile-Preferences-MCP-Token"
        #
        # bundles.secrets.yaml
        #   items:
        #     - id: "versatile@2026-03-31-13-36"
        #       secrets:
        #         mcp:
        #           preferences:
        #             auth:
        #               shared_token: "<rotate-me>"
        #
        # Client call shape:
        # curl -X POST \
        #   "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/mcp/preferences_tools" \
        #   -H "X-Versatile-Preferences-MCP-Token: <shared-token>" \
        #   -H "Content-Type: application/json" \
        #   -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
        #
        # Share with clients:
        # - the operations MCP route for alias "preferences_tools"
        # - the header name from bundle props
        # - the token provisioned in bundle secrets
        self._require_preferences_mcp_auth(request)
        if self._preferences_mcp_app is None:
            self._preferences_mcp_app = preference_tools_mod.build_preferences_mcp_app(
                name=f"{WORKFLOW_NAME}.preferences_tools",
                scope_provider=lambda user_id=None: self._preferences_mcp_scope(user_id=user_id),
            )
        return self._preferences_mcp_app

    @api(
        alias="preferences_widget",
        route="operations",
        user_types=("registered", "paid", "privileged"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="preferences",
        user_types=("registered", "paid", "privileged"),
    )
    def preferences_widget(self, user_id: Optional[str] = None, **kwargs):
        storage = self._preferences_storage()
        if not storage:
            return ["<p>Bundle storage backend is not configured for this bundle.</p>"]

        bundle_root = self._bundle_root()
        if not bundle_root:
            return ["<p>Bundle root is unavailable.</p>"]

        try:
            target_user = user_id or getattr(self.comm, "user_id", None) or "anonymous"
            payload = build_widget_payload(storage=storage, user_id=target_user)
            tsx_path = Path(bundle_root) / "ui" / "PreferencesBrowser.tsx"
            content = tsx_path.read_text(encoding="utf-8")
            rendered = content.replace("__PREFERENCES_JSON__", json.dumps(payload))
            actor = getattr(self.comm_context, "actor", None)
            bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
            rendered = patch_dashboard(
                input_content=rendered,
                base_url=f"http://localhost:{self.settings.CHAT_APP_PORT}",
                default_tenant=getattr(actor, "tenant_id", None) or self.settings.TENANT,
                default_project=getattr(actor, "project_id", None) or self.settings.PROJECT,
                default_app_bundle_id=getattr(bundle_spec, "id", None) or BUNDLE_ID,
                access_token=None,
                id_token=None,
                id_token_header="X-ID-Token",
            )
            return [self._render_dashboard_html(content=rendered, title="Preferences Browser")]
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
            return ["<p>Unable to render the preferences widget right now.</p>"]

    @api(
        method="GET",
        alias="preferences_summary",
        route="operations",
        user_types=("registered", "paid", "privileged"),
    )
    def preferences_summary(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
                "current_count": 0,
                "event_count": 0,
            }

        snapshot = get_preferences_snapshot(storage=storage, user_id=target_user)
        return {
            "ok": True,
            "user_id": target_user,
            "current_count": len(snapshot.get("current") or {}),
            "event_count": len(snapshot.get("items") or []),
        }

    @api(
        method="GET",
        alias="preferences_public_info",
        route="public",
        public_auth="none",
    )
    def preferences_public_info(self, **kwargs):
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        return {
            "ok": True,
            "bundle_id": getattr(bundle_spec, "id", None) or BUNDLE_ID,
            "public": True,
            "note": "Public bundle endpoint example for the versatile reference bundle.",
            "available_routes": {
                "operations_get": "preferences_summary",
                "operations_post": "preferences_widget_data",
                "public_get": "preferences_public_info",
                "telegram_webhook": "telegram_webhook",
                "telegram_admin": "telegram_user_admin_*",
            },
        }

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

    @api(method="GET", alias="telegram_conversations_list", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_conversations_list(
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

    @api(method="POST", alias="telegram_memory_canvas_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memory_canvas_data(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        identity = telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": identity.user_id,
                "entries": [],
                "document_text": "{}\n",
            }
        payload = build_preferences_canvas_document(storage=storage, user_id=identity.user_id)
        payload["ok"] = True
        payload["auth_surface"] = "telegram_webapp"
        payload["telegram_user_id"] = identity.telegram_user_id
        return payload

    @api(method="POST", alias="telegram_memory_canvas_save", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memory_canvas_save(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        document_text: Optional[str] = None,
        entries: Optional[list[dict[str, Any]]] = None,
        content: Optional[str] = None,
        text: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        identity = telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": identity.user_id,
            }
        try:
            if entries is not None:
                payload = save_preferences_canvas_entries(
                    storage=storage,
                    user_id=identity.user_id,
                    entries=entries,
                )
            else:
                raw_document = document_text or content or text or kwargs.get("document")
                if raw_document is None:
                    return {
                        "ok": False,
                        "error": "entries or document_text is required.",
                        "user_id": identity.user_id,
                    }
                payload = save_preferences_canvas_document(
                    storage=storage,
                    user_id=identity.user_id,
                    document_text=str(raw_document),
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "user_id": identity.user_id}
        payload["ok"] = True
        payload["auth_surface"] = "telegram_webapp"
        payload["telegram_user_id"] = identity.telegram_user_id
        return payload

    @api(method="POST", alias="telegram_memory_canvas_export_excel", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memory_canvas_export_excel(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        filename: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        identity = telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": identity.user_id,
            }
        try:
            raw = export_preferences_canvas_xlsx(storage=storage, user_id=identity.user_id)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "user_id": identity.user_id}
        safe_name = Path(filename or f"{identity.user_id}-preferences.xlsx").name or "preferences.xlsx"
        return {
            "ok": True,
            "auth_surface": "telegram_webapp",
            "telegram_user_id": identity.telegram_user_id,
            "user_id": identity.user_id,
            "filename": safe_name,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }

    @api(method="POST", alias="telegram_memory_canvas_import_excel", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_memory_canvas_import_excel(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        content_b64: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        identity = telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": identity.user_id,
            }
        raw_content = content_b64 or kwargs.get("file_b64") or kwargs.get("content")
        if not raw_content:
            return {"ok": False, "error": "content_b64 is required.", "user_id": identity.user_id}
        try:
            binary = base64.b64decode(str(raw_content), validate=True)
            entries = import_preferences_canvas_xlsx(binary)
            payload = save_preferences_canvas_entries(
                storage=storage,
                user_id=identity.user_id,
                entries=entries,
            )
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc), "user_id": identity.user_id}
        payload["ok"] = True
        payload["auth_surface"] = "telegram_webapp"
        payload["telegram_user_id"] = identity.telegram_user_id
        return payload

    @api(alias="preferences_widget_data")
    def preferences_widget_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": user_id or fingerprint or "anonymous",
                "current": {},
                "recent": [],
                "matched_count": 0,
            }

        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        payload = build_widget_payload(storage=storage, user_id=target_user)
        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_data")
    def preferences_canvas_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
                "path": None,
                "document_format": "entries",
                "entries": [],
                "document_text": "{}\n",
                "changed_keys": [],
                "removed_keys": [],
            }

        payload = build_preferences_canvas_document(storage=storage, user_id=target_user)
        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_save")
    def preferences_canvas_save(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        document_text: Optional[str] = None,
        entries: Optional[list[dict[str, Any]]] = None,
        content: Optional[str] = None,
        text: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        try:
            if entries is not None:
                payload = save_preferences_canvas_entries(
                    storage=storage,
                    user_id=target_user,
                    entries=entries,
                )
            else:
                raw_document = document_text
                if raw_document is None:
                    raw_document = content
                if raw_document is None:
                    raw_document = text
                if raw_document is None and "document" in kwargs:
                    raw_document = kwargs.get("document")
                if raw_document is None:
                    return {
                        "ok": False,
                        "error": "entries or document_text is required.",
                        "user_id": target_user,
                    }

                payload = save_preferences_canvas_document(
                    storage=storage,
                    user_id=target_user,
                    document_text=str(raw_document),
                )
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_export_excel")
    def preferences_canvas_export_excel(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        filename: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        try:
            raw = export_preferences_canvas_xlsx(storage=storage, user_id=target_user)
        except RuntimeError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        safe_name = Path(filename or f"{target_user}-preferences.xlsx").name or "preferences.xlsx"
        return {
            "ok": True,
            "user_id": target_user,
            "filename": safe_name,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }

    @api(alias="preferences_canvas_import_excel")
    def preferences_canvas_import_excel(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        content_b64: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        raw_content = content_b64 or kwargs.get("file_b64") or kwargs.get("content")
        if not raw_content:
            return {
                "ok": False,
                "error": "content_b64 is required.",
                "user_id": target_user,
            }

        try:
            binary = base64.b64decode(str(raw_content), validate=True)
            entries = import_preferences_canvas_xlsx(binary)
            payload = save_preferences_canvas_entries(
                storage=storage,
                user_id=target_user,
                entries=entries,
            )
        except (ValueError, RuntimeError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        payload["ok"] = True
        return payload

    @api(alias="preferences_exec_report")
    async def preferences_exec_report(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        recency: int = 10,
        kwords: str = "",
        **kwargs,
    ):
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
            }

        tool_subsystem, _ = create_tool_subsystem_with_mcp(
            service=self.models_service,
            comm=self.comm,
            logger=self.logger,
            bundle_spec=self.config.ai_bundle_spec,
            context_rag_client=self.ctx_client,
            registry={},
            raw_tool_specs=tools_descriptor.TOOLS_SPECS,
            tool_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
            mcp_tool_specs=getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or [],
            mcp_services_config=self.bundle_prop("mcp.services"),
            mcp_env_json=os.environ.get("MCP_SERVICES") or "",
        )

        op_root = self._preferences_ops_root() / "preferences_exec_report"
        workdir = op_root / "work"
        outdir = op_root / "out"
        exec_id = f"preferences-{uuid.uuid4().hex[:10]}"
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        try:
            report_recency = max(1, min(100, int(recency)))
        except Exception:
            report_recency = 10
        report_keywords = str(kwords or "").strip()
        snapshot = get_preferences_snapshot(storage=storage, user_id=target_user)
        current = snapshot.get("current") or {}
        events = snapshot.get("items") or []
        contract_items = [
            {
                "filename": "turn_preferences_exec/files/preferences_exec_report.md",
                "description": "Markdown report generated from shared bundle preference history.",
            }
        ]
        output_contract, contract, err = build_exec_output_contract(contract_items)
        if err or not output_contract or not contract:
            return {
                "ok": False,
                "error": err or {"message": "Failed to build exec output contract."},
            }

        code = f"""
from pathlib import Path
import json

current = json.loads({json.dumps(json.dumps(current, ensure_ascii=False))})
events = json.loads({json.dumps(json.dumps(events, ensure_ascii=False))})

report_keywords = {json.dumps(report_keywords)}
keyword_tokens = [token.lower() for token in report_keywords.split() if token.strip()]
if keyword_tokens:
    filtered_current = {{}}
    for key, item in current.items():
        haystack = f"{{key}} {{item.get('value', '')}} {{item.get('origin', '')}}".lower()
        if all(token in haystack for token in keyword_tokens):
            filtered_current[key] = item
    current = filtered_current

    filtered_events = []
    for event in events:
        haystack = (
            f"{{event.get('key', '')}} {{event.get('value', '')}} "
            f"{{event.get('origin', '')}} {{event.get('evidence', '')}}"
        ).lower()
        if all(token in haystack for token in keyword_tokens):
            filtered_events.append(event)
    events = filtered_events

lines = [
    "# Preferences Exec Report",
    "",
    f"User: {target_user}",
    f"Recency: {report_recency}",
    f"Keywords: {{report_keywords or '(none)'}}",
    "",
    "## Current preferences",
]

if current:
    for key, item in sorted(current.items()):
        if keyword_tokens:
            haystack = f"{{key}} {{item.get('value', '')}}".lower()
            if not all(keyword in haystack for keyword in keyword_tokens):
                continue
        lines.append(f"- {{key}}: {{item.get('value')}}")
else:
    lines.append("- No stored preferences yet.")

lines.extend(["", "## Recent observations"])
if events:
    for event in events[-{report_recency}:]:
        lines.append(
            f"- {{event.get('captured_at')}} | {{event.get('key')}} = {{event.get('value')}} "
            f"(origin={{event.get('origin')}})"
        )
else:
    lines.append("- No captured observations yet.")

report_path = Path(OUTPUT_DIR) / "turn_preferences_exec/files/preferences_exec_report.md"
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
print(f"wrote {{report_path}}")
"""

        envelope = await run_exec_tool(
            tool_manager=tool_subsystem,
            output_contract=output_contract,
            code=code,
            contract=contract,
            timeout_s=60,
            workdir=workdir,
            outdir=outdir,
            logger=self.logger,
            exec_id=exec_id,
            exec_runtime=normalize_exec_runtime_config(self.bundle_prop("execution.runtime")),
            bundle_storage_dir=str(self.bundle_storage_root()) if self.bundle_storage_root() else None,
        )
        report_relpath = "turn_preferences_exec/files/preferences_exec_report.md"
        report_abspath = outdir / report_relpath
        report_content_b64 = None
        if report_abspath.exists():
            try:
                report_content_b64 = base64.b64encode(report_abspath.read_bytes()).decode("ascii")
            except Exception:
                report_content_b64 = None
        return {
            "ok": bool(envelope.get("ok")),
            "report_text": envelope.get("report_text"),
            "items": envelope.get("items") or [],
            "out_dyn": envelope.get("out_dyn") or {},
            "error": envelope.get("error"),
            "recency": report_recency,
            "keywords": report_keywords,
            "report_filename": "preferences_exec_report.md",
            "report_mime": "text/markdown",
            "report_content_b64": report_content_b64,
        }

    def configuration_defaults(self) -> Dict[str, Any]:
        versatile_defaults = {
            "visibility": {
                "bundle": {
                    "allowed_roles": [],
                },
                "api": {
                    **_visibility_defaults(OPERATION_API_VISIBILITY_ALIASES),
                    **_visibility_defaults(
                        TELEGRAM_OPERATION_API_ALIASES,
                        roles=(TELEGRAM_ADMIN_ROLE,),
                    ),
                },
                "widget": _visibility_defaults(WIDGET_VISIBILITY_ALIASES),
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

        preferences_cfg = dict(config.get("preferences") or {})
        preferences_cfg.setdefault("auto_capture", True)
        preferences_cfg.setdefault("max_recent_events", 25)
        preferences_cfg.setdefault("widget_max_events", 15)
        config["preferences"] = preferences_cfg

        execution_cfg = dict(config.get("execution") or {})
        execution_cfg.setdefault("runtime", {"mode": "docker"})
        config["execution"] = execution_cfg

        subsystems = dict(config.get("subsystems") or {})
        subsystems.setdefault("preferences_browser", {"dashboard": "ui/PreferencesBrowser.tsx"})
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
