from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import (
    mint_file_download_token,
    verify_file_download_token,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import fi_path_from_conv_ref
from kdcube_ai_app.apps.chat.sdk.solutions.conversation import make_conversation_read_service
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    make_conversation_search_backend,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.plugin.bundle_loader import api, bundle_entrypoint, bundle_id, mcp, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

try:
    from .services.conversations.named_service import build_conversation_named_service_provider
    from .services.named_services import NamedServicesMcpBridge
    from .services.named_services.request_scope import get_public_base_url
    from .surfaces.mcp import conversations as conversations_mcp_module
    from .surfaces.mcp import named_services as named_services_mcp_module
except Exception:  # pragma: no cover - bundle loader may import as loose module
    from services.conversations.named_service import build_conversation_named_service_provider  # type: ignore
    from services.named_services import NamedServicesMcpBridge  # type: ignore
    from services.named_services.request_scope import get_public_base_url  # type: ignore
    from surfaces.mcp import conversations as conversations_mcp_module  # type: ignore
    from surfaces.mcp import named_services as named_services_mcp_module  # type: ignore


LOGGER = logging.getLogger("kdcube.bundles.kdcube-services")

BUNDLE_ID = "kdcube-services@1-0"
WORKFLOW_NAME = "kdcube_services"
# Single descriptor key for the conv:fi: download-link signing secret. Configure it
# in bundles.secrets.yaml under this bundle: conversations.file_download_secret.
CONV_FILE_DOWNLOAD_SECRET_KEY = "conversations.file_download_secret"
STORAGE_WIDGET_SRC = "sdk://solutions/storage/ui.widget.storage"
WIDGET_BUILD_COMMAND = (
    "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
)


@bundle_entrypoint(
    name=WORKFLOW_NAME,
    version="1.0.0",
    priority=100,
    allowed_roles_config="surfaces.as_provider.bundle.visibility.allowed_roles",
)
@bundle_id(id=BUNDLE_ID)
class KDCubeServicesEntrypoint(BaseEntrypoint):
    """Read-only KDCube service surfaces.

    This bundle provides normal proc-served KDCube surfaces for delegated
    external clients. It deliberately does not create a root platform `/mcp`
    endpoint; callers connect to this bundle's managed MCP URL.
    """

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
        )
        self.graph = self._build_graph()

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "surfaces": {
                "as_provider": {
                    "bundle": {"visibility": {"allowed_roles": []}},
                    "mcp": {
                        "conversations": {
                            "auth": {
                                "mode": "managed",
                                "authority_id": "delegated_client",
                                "selected_tool_grants": True,
                            },
                        },
                        "named_services": {
                            "auth": {
                                "mode": "managed",
                                "authority_id": "delegated_client",
                                "selected_tool_grants": True,
                            },
                        },
                    },
                },
            },
            "ui": {
                "widgets": {
                    "bundle_storage": {
                        "enabled": True,
                        "src_folder": STORAGE_WIDGET_SRC,
                        "build_command": WIDGET_BUILD_COMMAND,
                    },
                },
            },
        }

    @api(
        alias="bundle_storage_widget",
        route="operations",
        user_types=("privileged",),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:archive-box",
            "lucide": "Archive",
        },
        alias="bundle_storage",
        user_types=("privileged",),
    )
    def bundle_storage_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Bundle storage is served from sdk://solutions/storage/ui.widget.storage after build."
            "</div>"
        ]

    @mcp(
        alias="conversations",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.conversations.auth",
    )
    def conversations_mcp(self, request=None, **kwargs):
        del kwargs
        return conversations_mcp_module.build_conversations_mcp_app(
            name="KDCube conversations",
            read_service_factory=self._conversation_read_service,
            current_user_id_factory=self._runtime_user_id,
            request=request,
        )

    @mcp(
        alias="named_services",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.named_services.auth",
    )
    def named_services_mcp(self, request=None, **kwargs):
        actor = getattr(self.comm_context, "actor", None)
        return named_services_mcp_module.build_named_services_mcp_app(
            name="KDCube named services",
            config_factory=lambda: {},
            tenant_factory=lambda: str(getattr(actor, "tenant_id", None) or ""),
            project_factory=lambda: str(getattr(actor, "project_id", None) or ""),
            request=request,
            bridge_factory=NamedServicesMcpBridge,
        )

    # Publish the SDK conversation provider (read/export) as a named service.
    # The base entrypoint owns the registry, discovery, and on_bundle_load.
    def _named_service_providers(self) -> list:
        providers = list(super()._named_service_providers())
        providers.append(
            build_conversation_named_service_provider(
                pool_factory=lambda: self.pg_pool,
                model_service_factory=lambda: self.models_service,
                storage_path=str(getattr(self.settings, "STORAGE_PATH", "") or ""),
                bundle_id=self._named_services_bundle_id(),
                file_url_factory=self._conversation_file_url,
            )
        )
        return providers

    def _runtime_user_id(self) -> str:
        user = getattr(self.comm_context, "user", None)
        return str(getattr(user, "user_id", None) or "").strip()

    def _runtime_tenant(self) -> str:
        actor = getattr(self.comm_context, "actor", None)
        return str(getattr(actor, "tenant_id", None) or "").strip()

    def _runtime_project(self) -> str:
        actor = getattr(self.comm_context, "actor", None)
        return str(getattr(actor, "project_id", None) or "").strip()

    def _conversation_read_service(self):
        return make_conversation_read_service(
            pg_pool=self.pg_pool,
            tenant=self._runtime_tenant(),
            project=self._runtime_project(),
            model_service=self.models_service,
            store=ConversationStore(str(getattr(self.settings, "STORAGE_PATH", "") or "")),
        )

    # ── binary conv:fi: download URL (out-of-band, signed, session-less) ──────

    async def _conv_download_secret(self) -> str:
        """Signing secret for conv:fi: download tokens, resolved from the bundle's
        secret descriptor (``bundles.secrets.yaml``) under the single key
        ``conversations.file_download_secret``. No environment variables, no
        derived fallback: absent secret -> no download URL (binaries fall back to
        inline delivery). Mint (MCP call) and verify (download hit) resolve the
        same descriptor secret, so it is stable across worker processes."""
        bundle = self._named_services_bundle_id()
        ref = CONV_FILE_DOWNLOAD_SECRET_KEY
        value = await get_secret(f"b:{ref}", bundle_id=bundle)
        if not value and bundle:
            value = await get_secret(f"bundles.{bundle}.secrets.{ref}")
        return str(value or "").strip()

    async def _conversation_file_url(self, ns_ctx: Any, info: Any) -> Dict[str, Any] | None:
        """Mint a short-lived absolute download URL for a binary conv:fi: artifact.

        Called by the conv provider during object.get. The token binds the exact
        file + requester (tenant/project/user/conversation) so the download route
        trusts the signature, not the public request. Returns None (inline fallback)
        when the public origin is unknown or the signing secret is not configured."""
        base = get_public_base_url()
        if not base:
            return None
        info = info if isinstance(info, dict) else {}
        fi_ref = str(info.get("fi_ref") or "").strip()
        ref = str(info.get("ref") or "").strip()
        if not fi_ref or not ref:
            return None
        secret = await self._conv_download_secret()
        if not secret:
            LOGGER.warning(
                "[kdcube-services] %s not configured in bundles.secrets.yaml for %s; "
                "binary conv:fi: files will not get download URLs.",
                CONV_FILE_DOWNLOAD_SECRET_KEY, self._named_services_bundle_id(),
            )
            return None
        try:
            token, expires_at = mint_file_download_token(
                secret,
                fi_ref=fi_ref,
                user_id=str(getattr(ns_ctx, "user_id", "") or ""),
                conversation_id=str(info.get("conversation_id") or ""),
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
            )
            url = bundle_operation_url(
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
                bundle_id=self._named_services_bundle_id(),
                operation="conv_file_download",
                route="public",
                query={"object_ref": ref, "download_token": token},
                base_url=base,
                strict=True,
            )
        except Exception:
            return None
        return {"url": url, "expires_at": expires_at}

    @api(method="GET", alias="conv_file_download", route="public")
    async def conv_file_download(self, request: Any = None, object_ref: str = "", download_token: str = "", **kwargs):
        """Session-less signed download for a binary conv:fi: artifact.

        The token (minted during object.get) carries and binds tenant/project/user/
        conversation + the exact fi ref, so this route re-materializes the bytes
        under the token's identity and streams them — no platform session needed."""
        del kwargs
        try:
            from starlette.responses import JSONResponse, Response
        except Exception:  # pragma: no cover
            from fastapi.responses import JSONResponse, Response  # type: ignore

        ref = str(object_ref or "").strip()
        token = str(download_token or "").strip()
        if request is not None:
            ref = ref or str(request.query_params.get("object_ref") or "").strip()
            token = token or str(request.query_params.get("download_token") or "").strip()
        fi_ref = fi_path_from_conv_ref(ref)
        if not fi_ref or not token:
            return JSONResponse(status_code=400, content={"error": "download_request_invalid"})
        secret = await self._conv_download_secret()
        if not secret:
            return JSONResponse(status_code=503, content={"error": "download_not_configured"})
        try:
            payload = verify_file_download_token(secret, token, fi_ref=fi_ref)
        except ValueError as exc:
            return JSONResponse(status_code=403, content={"error": "download_token_rejected", "message": str(exc)})

        storage_path = str(getattr(self.settings, "STORAGE_PATH", "") or "")
        backend = make_conversation_search_backend(
            pg_pool=self.pg_pool,
            tenant=str(payload.get("tenant") or ""),
            project=str(payload.get("project") or ""),
            model_service=self.models_service,
            store=ConversationStore(storage_path),
            user_id=str(payload.get("user_id") or ""),
            conversation_id=str(payload.get("conversation_id") or ""),
        )
        result = await backend.materialize_file(
            fi_ref=fi_ref, conversation_id=str(payload.get("conversation_id") or ""),
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "error")
            status = 404 if reason in ("not_found", "unresolvable_ref") else 500
            return JSONResponse(status_code=status, content={"error": f"download_{reason}"})

        data = result.get("data") or b""
        filename = str(result.get("filename") or "file.bin")
        mime = str(result.get("mime") or "application/octet-stream")
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(data)),
                "Cache-Control": "private, no-store",
            },
        )

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def guide(state: BundleState) -> BundleState:
            state["final_answer"] = (
                "This bundle serves managed KDCube service MCP tools. Connect "
                "an external client to the KDCube services MCP surface."
            )
            return state

        g.add_node("guide", guide)
        g.add_edge(START, "guide")
        g.add_edge("guide", END)
        return g.compile()

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ):
        return await self.graph.ainvoke(state)
