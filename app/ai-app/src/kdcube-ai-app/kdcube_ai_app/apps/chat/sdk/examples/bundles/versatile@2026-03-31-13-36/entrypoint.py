from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import inspect
import logging
from typing import Any, Dict, Mapping, Optional

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.auth.federated import issue_federated_data_bus_token
from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.comm.sink import StatsTelemetrySink
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp as telegram_webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chat import chat_widget_ui_config
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas import api as canvas_api
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.defaults import default_canvas_event_source_specs
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_agent_event_source_namespaces,
    named_service_agent_pull_namespaces,
    register_configured_named_service_artifact_rehosters,
    register_configured_named_service_event_sources,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import DataBusResult, data_bus_handler
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, api, on_job, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from .event_filter import BundleEventFilter
from .agents.main import VersatileWorkflow
from .services import telemetry as telemetry_service
from .services.canvas import CanvasRuntimeConfig, VersatileCanvasService, payload_from_call

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
TELEMETRY_SINK_TOKEN_SECRET = "b:telemetry_sink.auth.token"
EVENT_RECORD_MAX = 200
DATA_BUS_ECHO_SUBJECT = "versatile.echo"
CANVAS_ARTIFACT_PREFIX = "canvas"
CANVAS_ORIGIN_PREFIX = "canvas"
CANVAS_STATE_EVENT_SOURCE_ID = "canvas.state"
CANVAS_UI_EVENT_TYPE = "canvas.patch.applied"
CANVAS_ARTIFACT_RESOLVER_NAME = "canvas.bundle_artifact_storage"
CANVAS_DATA_BUS_SUBJECT = "canvas.patch"
_log = logging.getLogger("kdcube.bundle.versatile")


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
        del mark_seen, kwargs
        with entrypoint._memory_user_identity(
            user_id=user_id or "",
            fingerprint=fingerprint or "",
            user_type="registered",
        ):
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


@bundle_entrypoint(
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
        comm_context: ExternalEventPayload = None,
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

    @data_bus_handler(
        subject=DATA_BUS_ECHO_SUBJECT,
        idempotency="required",
        user_types=("anonymous",),
    )
    async def data_bus_echo(self, ctx, message) -> Dict[str, Any]:
        payload = {
            "echo": dict(message.payload if message.payload is not None else {}),
            "actor": dict(ctx.actor if ctx.actor is not None else {}),
            "stream_id": ctx.stream_id,
            "handled_at": datetime.now(timezone.utc).isoformat(),
        }
        _log.info(
            "[data_bus.echo] handled bundle=%s subject=%s message_id=%s stream_id=%s",
            BUNDLE_ID,
            message.subject,
            message.message_id,
            ctx.stream_id,
        )
        await ctx.reply.ok(payload)
        return {"status": "ok", "data": payload}

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

    def _resolve_user_id(self, payload: Mapping[str, Any]) -> str:
        value = payload.get("user_id")
        if value is not None and str(value).strip():
            return str(value).strip()
        comm_user_id = getattr(getattr(self, "comm", None), "user_id", None)
        if comm_user_id is not None and str(comm_user_id).strip():
            return str(comm_user_id).strip()
        return "anonymous"

    def _consumer_agent_id(self) -> str:
        return str(self.bundle_prop("surfaces.as_consumer.default_agent", "main") or "main").strip() or "main"

    def _react_event_sources(self):
        from kdcube_ai_app.apps.chat.sdk.context.memory import tools as memory_tools
        from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
        from kdcube_ai_app.apps.chat.sdk.solutions.react.events import core as react_core_events

        subsystem = EventSourceSubsystem(
            modules=[
                {
                    "name": react_core_events.__name__,
                    "mod": react_core_events,
                    "alias": "react",
                    "file": getattr(react_core_events, "__file__", None),
                },
                {
                    "name": memory_tools.__name__,
                    "mod": memory_tools,
                    "alias": "memory",
                    "file": getattr(memory_tools, "__file__", None),
                },
            ],
            event_specs=default_canvas_event_source_specs(),
            bundle_root=Path(__file__).resolve().parent,
            logger=_log,
        )
        ident = self.runtime_identity()
        register_configured_named_service_artifact_rehosters(
            subsystem,
            namespaces=named_service_agent_pull_namespaces(
                self.bundle_props,
                client_id=self._consumer_agent_id(),
            ),
            tenant=str(ident.get("tenant") or ""),
            project=str(ident.get("project") or ""),
            logger=_log,
        )
        register_configured_named_service_event_sources(
            subsystem,
            namespaces=named_service_agent_event_source_namespaces(
                self.bundle_props,
                client_id=self._consumer_agent_id(),
            ),
            logger=_log,
        )
        return subsystem

    def _canvas_service(self) -> VersatileCanvasService:
        service = getattr(self, "_versatile_canvas_service", None)
        if service is None:
            service = VersatileCanvasService(
                self,
                config=CanvasRuntimeConfig(
                    bundle_id=self._bundle_id(),
                    artifact_prefix=CANVAS_ARTIFACT_PREFIX,
                    origin_prefix=CANVAS_ORIGIN_PREFIX,
                    state_event_source_id=CANVAS_STATE_EVENT_SOURCE_ID,
                    ui_event_type=CANVAS_UI_EVENT_TYPE,
                    artifact_resolver_name=CANVAS_ARTIFACT_RESOLVER_NAME,
                ),
                logger=_log,
            )
            self._versatile_canvas_service = service
        return service

    def _canvas_store(self, payload: Mapping[str, Any], *, user_id: str | None = None):
        return self._canvas_service().store(payload, user_id=user_id)

    def _canvas_object_resolvers(self, payload: Mapping[str, Any], *, user_id: str):
        return self._canvas_service().object_resolvers(payload, user_id=user_id)

    def _canvas_target(self) -> Dict[str, str]:
        return self._canvas_service().target()

    def _apply_canvas_patch_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return self._canvas_service().apply_patch_payload(payload)

    async def pre_run_hook(self, *, state: Dict[str, Any], econ_ctx: Optional[Dict[str, Any]] = None) -> None:
        await self._configure_event_recording()
        await super().pre_run_hook(state=state, econ_ctx=econ_ctx or {})

    async def post_run_hook(
        self,
        *,
        state: Dict[str, Any],
        result: Dict[str, Any],
        econ_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        await super().post_run_hook(state=state, result=result, econ_ctx=econ_ctx or {})
        await self._save_events_artifact(state=state)
        await self._send_recorded_events()

    def _bundle_id(self) -> str:
        return str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", None) or BUNDLE_ID)

    async def _make_event_sink(self) -> StatsTelemetrySink | None:
        return await telemetry_service.make_event_sink(
            self,
            bundle_id=self._bundle_id(),
            token_secret=TELEMETRY_SINK_TOKEN_SECRET,
            get_secret_fn=get_secret,
        )

    async def _configure_event_recording(self) -> None:
        await telemetry_service.configure_event_recording(
            self,
            bundle_id=self._bundle_id(),
            event_record_max=EVENT_RECORD_MAX,
        )

    async def _send_recorded_events(self) -> Dict[str, Any]:
        return await telemetry_service.send_recorded_events(self)

    @api(
        alias="versatile_chat_widget",
        route="operations",
        **_api_visibility("versatile_chat_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-left-right",
            "lucide": "MessagesSquare",
        },
        alias="versatile_chat",
        **_widget_visibility("versatile_chat"),
    )
    def versatile_chat_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Versatile chat is served from sdk://solutions/chat/ui/widget after build."
            "</div>"
        ]

    @api(
        alias="usage_card_widget",
        route="operations",
        **_api_visibility("usage_card_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chart-bar",
            "lucide": "Gauge",
        },
        alias="usage_card",
        **_widget_visibility("usage_card"),
    )
    def usage_card_widget(self, **kwargs):
        # Static fallback served when the built widget is not yet on disk.
        # The platform routes the real UI from
        # sdk://infra/economics/ui/widget/usage-card once the bundle build
        # ran. Data comes from /api/economics/me/budget-breakdown, which is
        # itself gated to authenticated users by the platform; the scene
        # additionally hides the toggle button for anonymous visitors.
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Usage card is served from sdk://infra/economics/ui/widget/usage-card after build."
            "</div>"
        ]

    @api(
        method="POST",
        alias="scene_surface_config",
        route="operations",
        **_api_visibility("scene_surface_config"),
    )
    async def scene_surface_config(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        scene_config = self.bundle_prop("surfaces.as_consumer.ui.scene", {}) or {}
        if not isinstance(scene_config, Mapping):
            scene_config = {}
        external_panels = scene_config.get("external_panels")
        if not isinstance(external_panels, list):
            external_panels = []
        return {"ok": True, "external_panels": external_panels}

    @api(
        alias="pinboard_widget",
        route="operations",
        **_api_visibility("pinboard_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:bookmark",
            "lucide": "Pin",
        },
        alias="pinboard",
        **_widget_visibility("pinboard"),
    )
    def pinboard_widget(self, **kwargs):
        # Static fallback served when the built widget is not yet on disk.
        # The platform routes the real UI from
        # sdk://solutions/canvas/ui/widget/pinboard once the bundle build ran.
        # The board talks to the same canvas operations + Data Bus the scene
        # uses, so a pin made here is visible in the scene's board and vice
        # versa (both default to the `<bundle>:main` canvas story).
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Pin Board is served from sdk://solutions/canvas/ui/widget/pinboard after build."
            "</div>"
        ]

    @api(
        alias="telegram_miniapp_widget",
        route="operations",
        **_api_visibility("telegram_miniapp_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:paper-airplane",
            "lucide": "Send",
        },
        alias="telegram_miniapp",
        **_widget_visibility("telegram_miniapp"),
    )
    def telegram_miniapp_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Telegram Mini App is served from ui/widgets/telegram_miniapp after build."
            "</div>"
        ]

    @api(method="POST", alias="canvas_attachment_upload", route="operations", **_api_visibility("canvas_attachment_upload"))
    async def canvas_attachment_upload(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().attachment_upload(
            payload,
            uploaded_files=list(kwargs.get("uploaded_files") or []),
        )

    @api(method="POST", alias="canvas_pin_read", route="operations", **_api_visibility("canvas_pin_read"))
    async def canvas_pin_read(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_pin_read",
            payload,
            lambda *, user_id, story_id: canvas_api.read_pin(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_object_action", route="operations", **_api_visibility("canvas_object_action"))
    async def canvas_object_action(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().object_action(payload)

    @api(method="POST", alias="canvas_search", route="operations", **_api_visibility("canvas_search"))
    async def canvas_search(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_search",
            payload,
            lambda *, user_id, story_id: canvas_api.search(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_list", route="operations", **_api_visibility("canvas_list"))
    async def canvas_list(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_list",
            payload,
            lambda *, user_id, story_id: canvas_api.list_canvases(
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
                info_html=self.bundle_prop("canvas.info_html"),
            ),
        )

    @api(method="POST", alias="canvas_set_active", route="operations", **_api_visibility("canvas_set_active"))
    async def canvas_set_active(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_set_active",
            payload,
            lambda *, user_id, story_id: canvas_api.set_active(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_archive", route="operations", **_api_visibility("canvas_archive"))
    async def canvas_archive(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_archive",
            payload,
            lambda *, user_id, story_id: canvas_api.archive(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_delete", route="operations", **_api_visibility("canvas_delete"))
    async def canvas_delete(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_delete",
            payload,
            lambda *, user_id, story_id: canvas_api.delete(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_read", route="operations", **_api_visibility("canvas_read"))
    async def canvas_read(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_read",
            payload,
            lambda *, user_id, story_id: canvas_api.read(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            ),
        )

    @api(method="POST", alias="canvas_write", route="operations", **_api_visibility("canvas_write"))
    async def canvas_write(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_write",
            payload,
            lambda *, user_id, story_id: canvas_api.write(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
                target=self._canvas_target(),
            ),
        )

    @api(method="POST", alias="canvas_patch", route="operations", **_api_visibility("canvas_patch"))
    async def canvas_patch(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._apply_canvas_patch_payload(payload)

    @data_bus_handler(
        subject=CANVAS_DATA_BUS_SUBJECT,
        partition_by="object_ref",
        ordering="serial_per_partition",
        idempotency="required",
    )
    async def handle_canvas_patch_data_bus(self, ctx, message) -> DataBusResult:
        return self._canvas_service().data_bus_patch_result(ctx, message)

    @api(
        method="POST",
        alias="telegram_miniapp_data",
        route="operations",
        **_api_visibility("telegram_miniapp_data"),
    )
    async def telegram_miniapp_data(
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

    @api(method="POST", alias="telegram_miniapp_data", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_miniapp_data_public(
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

    @api(method="POST", alias="telegram_federated_data_bus_claim", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
    async def telegram_federated_data_bus_claim(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        del kwargs
        identity = await telegram_widget_auth.resolve_identity(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            allowed_roles=("registered", "admin"),
        )
        roles = [TELEGRAM_ADMIN_ROLE] if identity.role == "admin" else []
        user_type = "privileged" if identity.role == "admin" else "registered"
        grant = await issue_federated_data_bus_token(
            request=request,
            tenant=self.settings.TENANT,
            project=self.settings.PROJECT,
            bundle_id=BUNDLE_ID,
            provider="telegram",
            provider_subject=identity.telegram_user_id,
            user_id=identity.user_id,
            user_type=user_type,
            username=identity.telegram_username or identity.user_id,
            roles=roles,
            allowed_subjects=(DATA_BUS_ECHO_SUBJECT,),
        )
        return {
            "ok": True,
            "schema": "kdcube.federated_token_claim.v1",
            "federated_token": grant.token,
            "session_id": grant.session.session_id,
            "expires_at": grant.expires_at,
            "bundle_id": BUNDLE_ID,
            "allowed_subjects": [DATA_BUS_ECHO_SUBJECT],
        }

    async def _telegram_memory_widget_call(
        self,
        operation: str,
        *,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        identity = await telegram_widget_auth.resolve_identity(
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

    # Public Telegram bridge APIs for Telegram-authenticated clients. These are
    # operation endpoints, not separate widget surfaces in bundle config.
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

    def configuration_defaults(self) -> Dict[str, Any]:
        versatile_defaults = {
            "visibility": {
                "bundle": {
                    "allowed_roles": [],
                },
                "api": {
                    "telegram_user_admin_data": {
                        "user_types": [],
                        "roles": [TELEGRAM_ADMIN_ROLE],
                    },
                    "telegram_user_admin_upsert": {
                        "user_types": [],
                        "roles": [TELEGRAM_ADMIN_ROLE],
                    },
                    "telegram_user_admin_delete": {
                        "user_types": [],
                        "roles": [TELEGRAM_ADMIN_ROLE],
                    },
                },
                "widget": {
                    "memories": {"user_types": [], "roles": []},
                    "pinboard": {"user_types": [], "roles": []},
                    "telegram_miniapp": {"user_types": [], "roles": []},
                    "usage_card": {"user_types": [], "roles": []},
                    "versatile_chat": {"user_types": [], "roles": []},
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
            "canvas": {
                "artifact_prefix": CANVAS_ARTIFACT_PREFIX,
                "origin_prefix": CANVAS_ORIGIN_PREFIX,
                "state_event_source_id": CANVAS_STATE_EVENT_SOURCE_ID,
                "ui_event_type": CANVAS_UI_EVENT_TYPE,
                "artifact_resolver_name": CANVAS_ARTIFACT_RESOLVER_NAME,
                "data_bus_subject": CANVAS_DATA_BUS_SUBJECT,
                "revision_retention": 80,
            },
            "telemetry_sink": {
                "endpoint_url": "",
                "auth_header": "",
            },
            "memory": {
                "enabled": True,
                "announce": {
                    "enabled": True,
                    "limit": 8,
                    "scope_filter": "all_user_memories",
                },
                "tools": {
                    "enabled": True,
                    "allow_write": True,
                    "default_scope_filter": "current_bundle",
                },
                "widget": {
                    "enabled": True,
                    "allow_write": True,
                    "default_scope_filter": "current_bundle",
                    "allow_all_user_memories": True,
                    "limit": 30,
                },
            },
            "ui": {
                "main_view": {
                    "src_folder": "ui/scene",
                    "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    "shared_sources": {
                        "canvas_component": {
                            "src_folder": "sdk://solutions/canvas/ui/component",
                            "target": "_shared/canvas-component",
                        },
                        "scene_runtime": {
                            "src_folder": "sdk://solutions/scene",
                            "target": "_shared/scene-runtime",
                        },
                    },
                },
                "widgets": {
                    "versatile_chat": chat_widget_ui_config(),
                    "memories": {
                        "enabled": True,
                        "src_folder": "sdk://context/memory/ui/widget/memories",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                    "usage_card": {
                        "enabled": True,
                        "src_folder": "sdk://infra/economics/ui/widget/usage-card",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                    "telegram_miniapp": {
                        "enabled": True,
                        "src_folder": "ui/widgets/telegram_miniapp",
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
                    "pinboard": {
                        "enabled": True,
                        # Standalone Pin Board: hosts the canvas component as
                        # its own iframe so a host page can broker it next to
                        # chat / memory instead of embedding the whole scene.
                        # The component is materialized into the widget build
                        # the same way the scene materializes it, so the
                        # widget's vite alias resolves to the staged copy.
                        "src_folder": "sdk://solutions/canvas/ui/widget/pinboard",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
                            "canvas_component": {
                                "src_folder": "sdk://solutions/canvas/ui/component",
                                "target": "_shared/canvas-component",
                            },
                        },
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), versatile_defaults)

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        opus_46 = "claude-opus-4-6"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},
            "memory.reconciler": {"provider": "anthropic", "model": sonnet_45},
            "memory.reconciler.lite": {"provider": "anthropic", "model": haiku_4},
            "memory.reconciler.regular": {"provider": "anthropic", "model": sonnet_45},
            "memory.reconciler.strong": {"provider": "anthropic", "model": opus_46},
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
        main_view_cfg.setdefault("src_folder", "ui/scene")
        main_view_cfg.setdefault(
            "build_command",
            "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
        )
        shared_sources = dict(main_view_cfg.get("shared_sources") or {})
        shared_sources.setdefault(
            "canvas_component",
            {
                "src_folder": "sdk://solutions/canvas/ui/component",
                "target": "_shared/canvas-component",
            },
        )
        shared_sources.setdefault(
            "scene_runtime",
            {
                "src_folder": "sdk://solutions/scene",
                "target": "_shared/scene-runtime",
            },
        )
        main_view_cfg["shared_sources"] = shared_sources
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
