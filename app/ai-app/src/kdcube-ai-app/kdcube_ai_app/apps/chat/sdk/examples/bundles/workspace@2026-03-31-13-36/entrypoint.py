from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import logging
from typing import Any, Dict, Mapping, Optional

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.comm.sink import StatsTelemetrySink
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramUserAdminStorage,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp as telegram_webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chat import chat_widget_ui_config
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
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
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, api, on_job, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from .event_filter import BundleEventFilter
from .agents.main import WorkspaceWorkflow
from .services import telemetry as telemetry_service
from .services.canvas import CanvasRuntimeConfig, WorkspaceCanvasService, payload_from_call
from .services import platform_session_issuer

BUNDLE_ID = "workspace@2026-03-31-13-36"
WORKFLOW_NAME = "workspace"
TELEGRAM_ADMIN_ROLE = "kdcube:role:super-admin"
TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEMETRY_SINK_TOKEN_SECRET = "b:telemetry_sink.auth.token"
EVENT_RECORD_MAX = 200
DATA_BUS_ECHO_SUBJECT = "workspace.echo"
CANVAS_ARTIFACT_PREFIX = "canvas"
CANVAS_ORIGIN_PREFIX = "canvas"
CANVAS_STATE_EVENT_SOURCE_ID = "canvas.state"
CANVAS_UI_EVENT_TYPE = "canvas.patch.applied"
CANVAS_ARTIFACT_RESOLVER_NAME = "canvas.bundle_artifact_storage"
CANVAS_DATA_BUS_SUBJECT = "canvas.patch"
_log = logging.getLogger("kdcube.bundle.workspace")


def _api_visibility(
    alias: str,
    *,
    route: str = "operations",
    method: str = "POST",
    user_types: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
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
    user_types: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
) -> Dict[str, Any]:
    base = f"surfaces.as_provider.widget.{alias}.visibility"
    return {
        "user_types": user_types,
        "user_types_config": f"{base}.user_types",
        "roles": roles,
        "roles_config": f"{base}.roles",
    }


def _storage_root_or_error(entrypoint: Any) -> Path:
    storage_root = entrypoint.bundle_storage_root()
    if not storage_root:
        raise RuntimeError("Bundle storage backend is not configured for this bundle.")
    return storage_root


def _payload(data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(data, Mapping):
        nested = data.get("data")
        if isinstance(nested, Mapping):
            merged.update({str(k): v for k, v in nested.items()})
        else:
            merged.update({str(k): v for k, v in data.items()})
    for key, value in kwargs.items():
        if key not in {"request", "alias", "route", "endpoint_alias"} and value is not None:
            merged[key] = value
    return merged


def _telegram_user_admin_storage(entrypoint: Any) -> TelegramUserAdminStorage:
    return TelegramUserAdminStorage(_storage_root_or_error(entrypoint))


class _WorkspaceAutomationWidgets:
    @staticmethod
    async def payload(entrypoint: Any, **kwargs) -> Dict[str, Any]:
        del entrypoint, kwargs
        return {
            "user_id": "",
            "automations": [],
            "count": 0,
            "supported_now": {
                "automations": False,
                "reason": "workspace reference webapp demonstrates memory, conversations, and Telegram admin.",
            },
        }


class _WorkspaceSettingsWidgets:
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


class _WorkspaceAutomationOperations:
    @staticmethod
    async def list_automations(*args, **kwargs) -> Dict[str, Any]:
        del args, kwargs
        return {"ok": True, "automations": [], "count": 0}


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
    settings_widgets_module=_WorkspaceSettingsWidgets,
    automation_widgets_module=_WorkspaceAutomationWidgets,
    telegram_user_admin_module=telegram_user_admin,
    bundle_id=BUNDLE_ID,
)
telegram_widget_ops.configure_telegram_widget_ops(
    automation_operations_module=_WorkspaceAutomationOperations,
    telegram_user_admin_module=telegram_user_admin,
    telegram_widget_auth_module=telegram_widget_auth,
    webapp_module=telegram_webapp,
    bundle_id=BUNDLE_ID,
)


@bundle_entrypoint(
    name=WORKFLOW_NAME,
    version="1.0.0",
    priority=100,
    allowed_roles_config="surfaces.as_provider.bundle.visibility.allowed_roles",
)
class WorkspaceEntrypoint(BaseEntrypointWithEconomics):
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
                orch = WorkspaceWorkflow(
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
                    "session_id": state["session_id"],
                    "conversation_id": state["conversation_id"],
                    "turn_id": state["turn_id"],
                }

                async def _run_workspace_turn() -> Dict[str, Any]:
                    return await orch.process(payload)

                res = await telegram_user_admin.run_with_queued_telegram_delivery(
                    self,
                    runner=_run_workspace_turn,
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

    def _canvas_service(self) -> WorkspaceCanvasService:
        service = getattr(self, "_workspace_canvas_service", None)
        if service is None:
            service = WorkspaceCanvasService(
                self,
                config=CanvasRuntimeConfig(
                    bundle_id=self._bundle_id(),
                    artifact_prefix=CANVAS_ARTIFACT_PREFIX,
                    origin_prefix=CANVAS_ORIGIN_PREFIX,
                    state_event_source_id=CANVAS_STATE_EVENT_SOURCE_ID,
                    ui_event_type=CANVAS_UI_EVENT_TYPE,
                    artifact_resolver_name=CANVAS_ARTIFACT_RESOLVER_NAME,
                    data_bus_subject=CANVAS_DATA_BUS_SUBJECT,
                ),
                logger=_log,
            )
            self._workspace_canvas_service = service
        return service

    def _canvas_store(self, payload: Mapping[str, Any], *, user_id: str | None = None):
        return self._canvas_service().store(payload, user_id=user_id)

    def _canvas_object_resolvers(self, payload: Mapping[str, Any], *, user_id: str):
        return self._canvas_service().object_resolvers(payload, user_id=user_id)

    def _canvas_target(self) -> Dict[str, str]:
        return self._canvas_service().target()

    async def _apply_canvas_patch_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return await self._canvas_service().apply_patch_payload(payload)

    def _named_service_providers(self) -> list:
        # Contribute the canvas pins provider; BaseEntrypoint owns the registry,
        # discovery, and on_bundle_load publishing.
        providers = list(super()._named_service_providers())
        providers.append(self._canvas_service().named_service_provider())
        return providers

    def _namespace_styles(self) -> Mapping[str, Any]:
        namespace_styles = self.bundle_prop("namespace_styles", {}) or {}
        return namespace_styles if isinstance(namespace_styles, Mapping) else {}

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
        alias="workspace_chat_widget",
        route="operations",
        **_api_visibility("workspace_chat_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-left-right",
            "lucide": "MessagesSquare",
        },
        alias="workspace_chat",
        **_widget_visibility("workspace_chat"),
    )
    def workspace_chat_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Workspace chat is served from sdk://solutions/chat/ui/widget after build."
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
        alias="namespace_presentation_config",
        route="public",
        **_api_visibility("namespace_presentation_config"),
    )
    async def namespace_presentation_config(self, **kwargs) -> Dict[str, Any]:
        del kwargs
        return {
            "ok": True,
            "namespace_styles": self._namespace_styles(),
        }

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
        components = scene_config.get("components")
        if not isinstance(components, Mapping):
            components = {}
        namespace_styles = self._namespace_styles()
        return {
            "ok": True,
            "components": dict(components),
            "external_panels": external_panels,
            "namespace_styles": namespace_styles,
        }

    @api(
        alias="capabilities_widget",
        route="operations",
        **_api_visibility("capabilities_widget"),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="capabilities",
        **_widget_visibility("capabilities"),
    )
    def capabilities_widget(self, **kwargs):
        # Static fallback served when the built widget is not yet on disk.
        # The platform routes the real UI from
        # sdk://solutions/chat/ui/widget-capabilities once the bundle build
        # ran: the full-page presentation of the SAME capability picker the
        # chat composer's "+" menu drives (agent_capabilities +
        # agent_selection_update operations on this bundle).
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Tools &amp; skills is served from sdk://solutions/chat/ui/widget-capabilities after build."
            "</div>"
        ]

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
            lambda *, user_id: canvas_api.read_pin(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
            ),
        )

    @api(method="POST", alias="canvas_object_action", route="operations", **_api_visibility("canvas_object_action"))
    async def canvas_object_action(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().object_action(payload)

    @api(method="GET", alias="canvas_object_download", route="operations", **_api_visibility("canvas_object_action"))
    async def canvas_object_download(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> BundleBinaryResponse:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().object_download(payload)

    @api(method="POST", alias="canvas_search", route="operations", **_api_visibility("canvas_search"))
    async def canvas_search(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().search(payload)

    @api(method="POST", alias="canvas_list", route="operations", **_api_visibility("canvas_list"))
    async def canvas_list(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_list",
            payload,
            lambda *, user_id: canvas_api.list_canvases(
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
                info_html=self.bundle_prop("canvas.info_html"),
            ),
        )

    @api(method="POST", alias="canvas_set_active", route="operations", **_api_visibility("canvas_set_active"))
    async def canvas_set_active(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_set_active",
            payload,
            lambda *, user_id: canvas_api.set_active(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
            ),
        )

    @api(method="POST", alias="canvas_archive", route="operations", **_api_visibility("canvas_archive"))
    async def canvas_archive(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_archive",
            payload,
            lambda *, user_id: canvas_api.archive(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
            ),
        )

    @api(method="POST", alias="canvas_delete", route="operations", **_api_visibility("canvas_delete"))
    async def canvas_delete(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().delete(payload)

    @api(method="POST", alias="canvas_read", route="operations", **_api_visibility("canvas_read"))
    async def canvas_read(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return self._canvas_service().operation(
            "canvas_read",
            payload,
            lambda *, user_id: canvas_api.read(
                payload=payload,
                store=self._canvas_store(payload, user_id=user_id),
                user_id=user_id,
            ),
        )

    @api(method="POST", alias="canvas_write", route="operations", **_api_visibility("canvas_write"))
    async def canvas_write(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._canvas_service().write(payload)

    @api(method="POST", alias="canvas_patch", route="operations", **_api_visibility("canvas_patch"))
    async def canvas_patch(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        payload = payload_from_call(data, **kwargs)
        return await self._apply_canvas_patch_payload(payload)

    @data_bus_handler(
        subject=CANVAS_DATA_BUS_SUBJECT,
        partition_by="object_ref",
        ordering="serial_per_partition",
        idempotency="required",
    )
    async def handle_canvas_patch_data_bus(self, ctx, message) -> DataBusResult:
        return await self._canvas_service().data_bus_patch_result(ctx, message)

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
            integration_id="telegram.kdcube_ref",
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
    )
    async def telegram_webhook(self, request: Any = None, **update) -> Dict[str, Any]:
        return await telegram_user_admin.handle_webhook(self, request=request, **update)

    # Config contract: docs/integrations/platform-session-issuer.md
    @api(method="POST", alias="auth_telegram_session", route="public")
    async def auth_telegram_session(
        self,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs,
    ):
        return await platform_session_issuer.issue_telegram_session(
            self,
            request=request,
            telegram_init_data=telegram_init_data,
            payload=kwargs,
            bundle_id=BUNDLE_ID,
        )

    # Config contract: docs/integrations/platform-session-issuer.md
    @api(method="GET", alias="platform_login", route="public")
    async def platform_login(
        self,
        request: Any = None,
        **kwargs,
    ):
        del kwargs
        return await platform_session_issuer.google_login_page(
            self,
            request=request,
            bundle_id=BUNDLE_ID,
        )

    # Config contract: docs/integrations/platform-session-issuer.md
    @api(method="POST", alias="auth_google_session", route="public")
    async def auth_google_session(
        self,
        request: Any = None,
        credential: str = "",
        id_token: str = "",
        **kwargs,
    ):
        return await platform_session_issuer.issue_google_session(
            self,
            request=request,
            credential=credential,
            id_token=id_token,
            payload=kwargs,
            bundle_id=BUNDLE_ID,
        )

    # Config contract: Connection Hub authority_registry entrypoints.consent.
    @api(method="POST", alias="delegated_consent", route="public")
    async def delegated_consent(
        self,
        data: Optional[Mapping[str, Any]] = None,
        **kwargs,
    ):
        return platform_session_issuer.delegated_consent_page(
            self,
            payload=_payload(data, **kwargs),
        )

    @api(method="GET", alias="telegram_profile", route="public")
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

    @api(method="GET", alias="conversations_list", route="public")
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

    @api(method="POST", alias="telegram_conversations_create", route="public")
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

    @api(method="POST", alias="telegram_conversations_switch", route="public")
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

    @api(method="POST", alias="telegram_conversations_delete", route="public")
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

    @api(method="POST", alias="telegram_miniapp_data", route="public")
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

    @api(method="POST", alias="telegram_webapp_user_admin_data", route="public")
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

    @api(method="POST", alias="telegram_webapp_user_admin_upsert", route="public")
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

    @api(method="POST", alias="telegram_webapp_user_admin_delete", route="public")
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
        workspace_defaults = {
            "surfaces": {
                "as_provider": {
                    "bundle": {
                        "visibility": {
                            "allowed_roles": [],
                        },
                    },
                    "api": {
                        "operations": {
                            "telegram_user_admin_data": {
                                "POST": {
                                    "visibility": {
                                        "user_types": [],
                                        "roles": [TELEGRAM_ADMIN_ROLE],
                                    },
                                },
                            },
                            "telegram_user_admin_upsert": {
                                "POST": {
                                    "visibility": {
                                        "user_types": [],
                                        "roles": [TELEGRAM_ADMIN_ROLE],
                                    },
                                },
                            },
                            "telegram_user_admin_delete": {
                                "POST": {
                                    "visibility": {
                                        "user_types": [],
                                        "roles": [TELEGRAM_ADMIN_ROLE],
                                    },
                                },
                            },
                        },
                    },
                    "widget": {
                        "pinboard": {"visibility": {"user_types": [], "roles": []}},
                        "telegram_miniapp": {"visibility": {"user_types": [], "roles": []}},
                        "usage_card": {"visibility": {"user_types": [], "roles": []}},
                        "workspace_chat": {"visibility": {"user_types": [], "roles": []}},
                    },
                },
            },
            "connections": {
                "connection_hub": {
                    "bundle_id": "connection-hub@1-0",
                },
            },
            "integrations": {},
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
            # workspace CONSUMES durable user memory via the `mem` named service
            # (surfaces.as_consumer.agents.main.tools[mem]) plus the announce
            # declared on that namespace; it owns no memory widget/provider/
            # maintenance config. Every memory surface (including the Telegram
            # Mini App memory tab) iframes the dedicated user-memories app, so
            # this entrypoint derives from the economics-only base and registers
            # no `sdk.memory` provider.
            "ui": {
                "main_view": {
                    "src_folder": "ui/scene",
                    "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    "shared_sources": {
                        "components_react_canvas": {
                            "src_folder": "npm://components-react/src/canvas",
                            "target": "_shared/components-react/canvas",
                        },
                        "components_core_canvas": {
                            "src_folder": "npm://components-core/src/canvas",
                            "target": "_shared/components-core/canvas",
                        },
                        "components_core_scene": {
                            "src_folder": "npm://components-core/src/scene",
                            "target": "_shared/components-core/scene",
                        },
                        "components_core_events": {
                            "src_folder": "npm://components-core/src/events",
                            "target": "_shared/components-core/events",
                        },
                    },
                },
                "widgets": {
                    # Engine selection is config-driven in the bundle config
                    # (bundles.yaml `workspace_chat.engine: local|package|package-ui`),
                    # expanded by the build pipeline. This is just the code-side default.
                    "workspace_chat": chat_widget_ui_config(),
                    "usage_card": {
                        "enabled": True,
                        "src_folder": "sdk://infra/economics/ui/widget/usage-card",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
                            "components_core": {
                                "src_folder": "npm://components-core/src",
                                "target": "_shared/components-core",
                            },
                        },
                    },
                    "capabilities": {
                        "enabled": True,
                        # Full-page capability picker: the SAME picker body the
                        # composer "+" menu drives, served as its own widget.
                        # Ops it needs (agent_capabilities /
                        # agent_selection_update) live on this entrypoint.
                        "src_folder": "sdk://solutions/chat/ui/widget-capabilities",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
                            "components_core": "npm://components-core/src",
                            "components_react": "npm://components-react/src",
                            "chat_ui_css": "npm://components-react/examples/standalone",
                        },
                    },
                    "telegram_miniapp": {
                        "enabled": True,
                        "src_folder": "ui/widgets/telegram_miniapp",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                        "shared_sources": {
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
                            "components_react_canvas": {
                                "src_folder": "npm://components-react/src/canvas",
                                "target": "_shared/components-react/canvas",
                            },
                            "components_core_canvas": {
                                "src_folder": "npm://components-core/src/canvas",
                                "target": "_shared/components-core/canvas",
                            },
                        },
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), workspace_defaults)

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
        main_view_cfg.setdefault("src_folder", "ui/scene")
        main_view_cfg.setdefault(
            "build_command",
            "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
        )
        shared_sources = dict(main_view_cfg.get("shared_sources") or {})
        shared_sources.setdefault(
            "components_react_canvas",
            {
                "src_folder": "npm://components-react/src/canvas",
                "target": "_shared/components-react/canvas",
            },
        )
        shared_sources.setdefault(
            "components_core_canvas",
            {
                "src_folder": "npm://components-core/src/canvas",
                "target": "_shared/components-core/canvas",
            },
        )
        shared_sources.setdefault(
            "components_core_scene",
            {
                "src_folder": "npm://components-core/src/scene",
                "target": "_shared/components-core/scene",
            },
        )
        shared_sources.setdefault(
            "components_core_events",
            {
                "src_folder": "npm://components-core/src/events",
                "target": "_shared/components-core/events",
            },
        )
        main_view_cfg["shared_sources"] = shared_sources
        ui_cfg["main_view"] = main_view_cfg
        config["ui"] = ui_cfg

        mcp = dict(config.get("mcp") or {})
        mcp.setdefault("services", {})
        config["mcp"] = mcp
        return config

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
