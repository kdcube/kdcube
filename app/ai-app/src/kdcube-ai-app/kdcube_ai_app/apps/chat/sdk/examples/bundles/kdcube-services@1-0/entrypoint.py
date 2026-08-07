from __future__ import annotations

import importlib
import logging
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
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
from kdcube_ai_app.apps.chat.sdk.integrations.file_delivery import (
    fetch_mail_attachment,
    fetch_mail_message_snapshot,
    fetch_slack_file,
)
from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    MAX_STAGED_FILE_BYTES,
    new_staged_ref,
    save_staged,
    staging_root,
)
from kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service import (
    make_docs_named_service_provider,
    parse_docs_export_ref,
    parse_docs_ref,
)
from kdcube_ai_app.apps.chat.sdk.integrations.mail import make_mail_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import parse_mail_ref
from kdcube_ai_app.apps.chat.sdk.integrations.sheets import (
    make_sheets_named_service_provider,
    parse_sheets_ref,
)
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
    make_linkedin_named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.integrations.slack import make_slack_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import parse_slack_ref
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.relay import (
    NAMED_SERVICE_RELAY_SUBJECT,
    handle_named_service_relay,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.infra.plugin.bundle_loader import (
    api,
    bundle_entrypoint,
    bundle_id,
    data_bus_handler,
    mcp,
    ui_widget,
)
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from .services.conversations.named_service import build_conversation_named_service_provider
from .services.named_services import NamedServicesMcpBridge
from .services.named_services.request_scope import get_public_base_url
from .services.productivity import (
    GoogleDocsService,
    GoogleSheetsService,
    bind_docs_service,
    bind_service as bind_productivity_service,
    fetch_google_docs_export,
    fetch_google_docs_snapshot,
    fetch_google_sheets_snapshot,
)
from .surfaces.mcp import conversations as conversations_mcp_module
from .surfaces.mcp import named_services as named_services_mcp_module
from .surfaces.mcp import productivity as productivity_mcp_module


LOGGER = logging.getLogger("kdcube.bundles.kdcube-services")

BUNDLE_ID = "kdcube-services@1-0"
WORKFLOW_NAME = "kdcube_services"
# Single descriptor key for the bundle's download-link signing secret. Configure it
# in bundles.secrets.yaml under this bundle: conversations.file_download_secret.
# One secret signs every out-of-band download this bundle serves (conv:fi:
# artifacts, mail attachments, Slack files, Sheets snapshots) — the token
# payload, not the key, scopes each link to its exact object and requester.
CONV_FILE_DOWNLOAD_SECRET_KEY = "conversations.file_download_secret"
STORAGE_WIDGET_SRC = "sdk://solutions/storage/ui.widget.storage"
APP_CONFIG_WIDGET_SRC = "sdk://solutions/app_config/ui/widget"
AGENTIC_CONFIG_WIDGET_SRC = "sdk://solutions/agentic_config/ui/widget"


def _content_disposition(filename: str) -> str:
    """RFC 5987 Content-Disposition for arbitrary filenames.

    HTTP headers are latin-1; real-world names carry characters outside it
    (macOS screenshots embed U+202F before AM/PM). Ship an ASCII fallback plus
    the UTF-8 `filename*` form so browsers restore the exact original name."""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'") or "file.bin"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
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
            "named_services": {
                "schema_search": {
                    "vector_backend": "faiss-local",
                },
            },
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
                        "productivity": {
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
                    "app_config": {
                        "enabled": True,
                        "src_folder": APP_CONFIG_WIDGET_SRC,
                        "build_command": WIDGET_BUILD_COMMAND,
                        "shared_sources": {
                            "components_core": {
                                "src_folder": "npm://components-core/src",
                                "target": "_shared/components-core",
                            },
                            "components_react": {
                                "src_folder": "npm://components-react/src",
                                "target": "_shared/components-react",
                            },
                        },
                    },
                    "agentic_instructions": {
                        "enabled": True,
                        "src_folder": AGENTIC_CONFIG_WIDGET_SRC,
                        "build_command": WIDGET_BUILD_COMMAND,
                        "shared_sources": {
                            "components_core": {
                                "src_folder": "npm://components-core/src",
                                "target": "_shared/components-core",
                            },
                            "components_react": {
                                "src_folder": "npm://components-react/src",
                                "target": "_shared/components-react",
                            },
                        },
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

    @api(
        alias="app_config_widget",
        route="operations",
        user_types=("privileged",),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:squares-2x2",
            "lucide": "LayoutGrid",
        },
        alias="app_config",
        user_types=("privileged",),
    )
    def app_config_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "App Config is served from sdk://solutions/app_config/ui/widget after build."
            "</div>"
        ]

    @api(
        alias="agentic_instructions_widget",
        route="operations",
        user_types=("privileged",),
    )
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="agentic_instructions",
        user_types=("privileged",),
    )
    def agentic_instructions_widget(self, **kwargs):
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
            "Agent Instructions is served from sdk://solutions/agentic_config/ui/widget after build."
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

    @mcp(
        alias="productivity",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.productivity.auth",
    )
    def productivity_mcp(self, request=None, **kwargs):
        """The reference PURE-MCP door: plain tools over connected accounts.

        Each tool declares its connected-account requirements (ToolClaimPolicy
        shape) and enforces them in the tool body via
        ``enforce_tool_requirements`` - see ``surfaces/mcp/productivity.py``.
        """
        del kwargs
        # Bind the wrapped provider tool modules to THIS entrypoint so their
        # internal credential resolution reaches the Connection Hub registry
        # (redis/bundle props) - the same binding the mail/slack named-service
        # providers apply.
        from kdcube_ai_app.apps.chat.sdk.integrations.google import (
            gmail_tools as gmail_tools_module,
        )
        from kdcube_ai_app.apps.chat.sdk.integrations.slack import (
            tools as slack_tools_module,
        )

        from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import (
            tools as linkedin_tools_module,
        )

        gmail_tools_module.bind_service(self)
        slack_tools_module.bind_service(self)
        linkedin_tools_module.bind_service(self)
        bind_productivity_service(self)
        bind_docs_service(self)
        actor = getattr(self.comm_context, "actor", None)
        return productivity_mcp_module.build_productivity_mcp_app(
            name="KDCube productivity",
            config_factory=lambda: dict(
                self.bundle_prop("surfaces.as_provider.mcp.productivity", {}) or {}
            ),
            tenant_factory=lambda: str(getattr(actor, "tenant_id", None) or ""),
            project_factory=lambda: str(getattr(actor, "project_id", None) or ""),
            request=request,
        )

    @data_bus_handler(
        subject=NAMED_SERVICE_RELAY_SUBJECT,
        idempotency="required",
    )
    async def named_service_relay(self, ctx, message):
        """Serve named-service calls relayed from detached runtimes.

        The exec supervisor and subprocess runtimes hold no live registry
        caller; they publish the request to this bundle's Data Bus stream.
        The worker binds the message actor as the request context, this
        handler dispatches through the bundle's own named-service registry,
        and the recorded response answers redeliveries without re-running
        the action.
        """
        return await handle_named_service_relay(ctx, message)

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
        providers.append(
            make_mail_named_service_provider(
                entrypoint=self,
                bundle_id=self._named_services_bundle_id(),
                file_url_factory=self._integration_file_url,
                upload_slot_factory=self._integration_upload_slot,
            )
        )
        providers.append(
            make_slack_named_service_provider(
                entrypoint=self,
                bundle_id=self._named_services_bundle_id(),
                file_url_factory=self._integration_file_url,
                upload_slot_factory=self._integration_upload_slot,
            )
        )
        # Write-only namespace: LinkedIn exposes no content reads, so no
        # file_url_factory. Uploads still need a slot — outbound images must
        # reach KDCube as staged bytes, not as base64 in model context.
        providers.append(
            make_linkedin_named_service_provider(
                entrypoint=self,
                bundle_id=self._named_services_bundle_id(),
                staging_root_factory=lambda: staging_root(
                    str(getattr(self.settings, "STORAGE_PATH", "") or "")
                ),
                upload_slot_factory=self._integration_upload_slot,
            )
        )
        # The sheets namespace is an ontology adapter over the same governed
        # service used by public/mcp/productivity. Credential resolution and
        # Google execution therefore have one implementation for both doors.
        bind_productivity_service(self)
        sheets_service = GoogleSheetsService()
        providers.append(
            make_sheets_named_service_provider(
                execute_operation=sheets_service.execute,
                bundle_id=self._named_services_bundle_id(),
                file_url_factory=self._integration_file_url,
            )
        )
        # The docs namespace is the same ontology-adapter pattern over the
        # governed Docs service used by public/mcp/productivity.
        bind_docs_service(self)
        docs_service = GoogleDocsService()
        providers.append(
            make_docs_named_service_provider(
                execute_operation=docs_service.execute,
                bundle_id=self._named_services_bundle_id(),
                file_url_factory=self._integration_file_url,
            )
        )
        # Stored agent instruction sets (instr:custom:<id>[:<version>]):
        # reads serve pickers/widgets; writes are admin-gated in the provider.
        from kdcube_ai_app.apps.chat.sdk.solutions.agentic_config.named_service import (
            AgenticInstructionsNamedService,
        )
        providers.append(
            AgenticInstructionsNamedService(pool_factory=lambda: self.pg_pool)
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

    async def _integration_file_url(self, ns_ctx: Any, info: Any) -> Dict[str, Any] | None:
        """Mint a short-lived absolute download URL for integration content.

        Called by mail, Slack, and Sheets named-service providers on turnless
        transports. Same signing secret and token shape as conv:fi: downloads;
        the token binds the exact object ref + requester, so the public route
        trusts the signature, not the request. Returns None when the public
        origin is unknown or the signing secret is not configured."""
        base = get_public_base_url()
        if not base:
            return None
        info = info if isinstance(info, dict) else {}
        ref = str(info.get("ref") or "").strip()
        if not ref:
            return None
        secret = await self._conv_download_secret()
        if not secret:
            LOGGER.warning(
                "[kdcube-services] %s not configured in bundles.secrets.yaml for %s; "
                "integration binaries will not get download URLs.",
                CONV_FILE_DOWNLOAD_SECRET_KEY, self._named_services_bundle_id(),
            )
            return None
        try:
            token, expires_at = mint_file_download_token(
                secret,
                fi_ref=ref,
                user_id=str(getattr(ns_ctx, "user_id", "") or ""),
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
            )
            url = bundle_operation_url(
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
                bundle_id=self._named_services_bundle_id(),
                operation="integration_file_download",
                route="public",
                query={"object_ref": ref, "download_token": token},
                base_url=base,
                strict=True,
            )
        except Exception:
            return None
        return {"url": url, "expires_at": expires_at}

    async def _integration_upload_slot(self, ns_ctx: Any, info: Any) -> Dict[str, Any] | None:
        """Mint a signed single-use upload slot for one inbound file.

        Called by the mail/slack named services on ``request_upload``. The
        client PUTs raw bytes to the returned URL over plain HTTP (never
        through the model's context) and then references the returned
        ``staged:`` ref in send/upload payloads. Same signing secret and
        token shape as the download links."""
        base = get_public_base_url()
        if not base:
            return None
        info = info if isinstance(info, dict) else {}
        filename = str(info.get("filename") or "").strip()
        if not filename:
            return None
        secret = await self._conv_download_secret()
        if not secret:
            LOGGER.warning(
                "[kdcube-services] %s not configured in bundles.secrets.yaml for %s; "
                "integration uploads are unavailable.",
                CONV_FILE_DOWNLOAD_SECRET_KEY, self._named_services_bundle_id(),
            )
            return None
        try:
            staged_ref = new_staged_ref(filename)
            token, expires_at = mint_file_download_token(
                secret,
                fi_ref=staged_ref,
                user_id=str(getattr(ns_ctx, "user_id", "") or ""),
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
            )
            url = bundle_operation_url(
                tenant=str(getattr(ns_ctx, "tenant", "") or ""),
                project=str(getattr(ns_ctx, "project", "") or ""),
                bundle_id=self._named_services_bundle_id(),
                operation="integration_file_upload",
                route="public",
                query={"object_ref": staged_ref, "upload_token": token},
                base_url=base,
                strict=True,
            )
        except Exception:
            return None
        return {
            "upload_url": url,
            "staged_ref": staged_ref,
            "expires_at": expires_at,
            "max_bytes": MAX_STAGED_FILE_BYTES,
        }

    @api(method="POST", alias="integration_file_upload", route="public")
    async def integration_file_upload(self, request: Any = None, object_ref: str = "", upload_token: str = "", **kwargs):
        """Session-less signed upload of one inbound integration file.

        The token (minted by ``request_upload``) binds the staged ref +
        requester; the raw request body is the file. Bytes land in the shared
        staging area and are consumed (single-use) by the send/upload action
        that references the staged ref."""
        del kwargs
        try:
            from starlette.responses import JSONResponse
        except Exception:  # pragma: no cover
            from fastapi.responses import JSONResponse  # type: ignore

        ref = str(object_ref or "").strip()
        token = str(upload_token or "").strip()
        if request is not None:
            ref = ref or str(request.query_params.get("object_ref") or "").strip()
            token = token or str(request.query_params.get("upload_token") or "").strip()
        if not ref or not token or request is None:
            return JSONResponse(status_code=400, content={"error": "upload_request_invalid"})
        secret = await self._conv_download_secret()
        if not secret:
            return JSONResponse(status_code=503, content={"error": "upload_not_configured"})
        try:
            verify_file_download_token(secret, token, fi_ref=ref)
        except ValueError as exc:
            return JSONResponse(status_code=403, content={"error": "upload_token_rejected", "message": str(exc)})
        data = await request.body()
        if not data:
            return JSONResponse(status_code=400, content={"error": "upload_body_empty"})
        try:
            save_staged(
                staging_root(str(getattr(self.settings, "STORAGE_PATH", "") or "")),
                ref,
                data,
            )
        except ValueError as exc:
            return JSONResponse(status_code=413, content={"error": "upload_too_large", "message": str(exc)})
        return JSONResponse(
            status_code=200,
            content={"ok": True, "staged_ref": ref, "size_bytes": len(data)},
        )

    @api(method="GET", alias="integration_file_download", route="public")
    async def integration_file_download(self, request: Any = None, object_ref: str = "", download_token: str = "", **kwargs):
        """Session-less signed download for one integration binary.

        The token (minted after the delegated caller is authorized during
        object.get / a download action) binds tenant/project/user + the exact
        object ref. This route treats that token as the short-lived download
        capability, re-resolves the connected provider credential and claim
        under its bound identity, and streams the bytes — no platform session,
        no chat turn."""
        del kwargs
        try:
            from starlette.responses import JSONResponse, Response, StreamingResponse
        except Exception:  # pragma: no cover
            from fastapi.responses import JSONResponse, Response, StreamingResponse  # type: ignore

        ref = str(object_ref or "").strip()
        token = str(download_token or "").strip()
        if request is not None:
            ref = ref or str(request.query_params.get("object_ref") or "").strip()
            token = token or str(request.query_params.get("download_token") or "").strip()
        if not ref or not token:
            return JSONResponse(status_code=400, content={"error": "download_request_invalid"})
        secret = await self._conv_download_secret()
        if not secret:
            return JSONResponse(status_code=503, content={"error": "download_not_configured"})
        try:
            payload = verify_file_download_token(secret, token, fi_ref=ref)
        except ValueError as exc:
            return JSONResponse(status_code=403, content={"error": "download_token_rejected", "message": str(exc)})

        user_id = str(payload.get("user_id") or "")
        tenant = str(payload.get("tenant") or "")
        project = str(payload.get("project") or "")
        mail_parsed = parse_mail_ref(ref)
        slack_parsed = parse_slack_ref(ref)
        try:
            sheets_parsed = parse_sheets_ref(ref)
        except ValueError:
            sheets_parsed = {}
        try:
            docs_parsed = parse_docs_ref(ref)
        except ValueError:
            docs_parsed = {}
        try:
            docs_export_parsed = parse_docs_export_ref(ref)
        except ValueError:
            docs_export_parsed = {}
        if mail_parsed.get("kind") == "attachment":
            result = await fetch_mail_attachment(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                account_id=mail_parsed["account_id"],
                message_id=mail_parsed["message_id"],
                attachment_id=mail_parsed["attachment_id"],
            )
        elif mail_parsed.get("kind") == "message":
            result = await fetch_mail_message_snapshot(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                account_id=mail_parsed["account_id"],
                message_id=mail_parsed["message_id"],
                object_ref=ref,
            )
        elif slack_parsed.get("kind") == "file":
            result = await fetch_slack_file(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                account_id=slack_parsed["account_id"],
                file_id=slack_parsed["file_id"],
            )
        elif sheets_parsed.get("kind") in {"spreadsheet", "tab"}:
            result = await fetch_google_sheets_snapshot(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                object_ref=ref,
                bundle_id=self._named_services_bundle_id(),
            )
        elif docs_export_parsed.get("document_id"):
            result = await fetch_google_docs_export(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                object_ref=ref,
            )
        elif docs_parsed.get("document_id"):
            result = await fetch_google_docs_snapshot(
                self,
                user_id=user_id,
                tenant=tenant,
                project=project,
                object_ref=ref,
                bundle_id=self._named_services_bundle_id(),
            )
        else:
            return JSONResponse(status_code=400, content={"error": "download_ref_unsupported"})
        if not result.get("ok"):
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            content = {"error": str(error.get("code") or "download_failed"), "message": str(error.get("message") or "")}
            if isinstance(result.get("resolution"), dict):
                content["resolution"] = result["resolution"]
            return JSONResponse(status_code=int(result.get("status") or 500), content=content)

        chunks = result.get("chunks")
        if chunks is not None:
            filename = str(result.get("filename") or "spreadsheet.sheets.json")
            mime = str(result.get("mime_type") or "application/json")
            headers = {
                **dict(result.get("headers") or {}),
                "Content-Disposition": _content_disposition(filename),
                "Cache-Control": "private, no-store",
            }
            return StreamingResponse(
                chunks,
                status_code=int(result.get("status") or 200),
                media_type=mime,
                headers=headers,
            )

        data = result.get("data") or b""
        filename = str(result.get("filename") or "file.bin")
        mime = str(result.get("mime_type") or "application/octet-stream")
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Content-Disposition": _content_disposition(filename),
                "Content-Length": str(len(data)),
                "Cache-Control": "private, no-store",
            },
        )

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
                "Content-Disposition": _content_disposition(filename),
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

    @api(method="POST", alias="agentic_instructions", route="operations", user_types=("registered", "paid", "privileged"))
    async def agentic_instructions(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Operations facade over the ``instr`` stored-instruction-sets provider.

        The widget-facing surface for authoring instruction sets; the SAME
        provider that serves the governed named-services door answers here, so
        both transports share one contract and one admin gate (writes require
        an administrator identity — enforced in the provider, not the widget).

        ``body.data.action``:

        - ``list``    ``{include_retired?, q?, tags?}`` → latest version per
                      id; ``q`` filters id/name/description, ``tags`` requires
                      every named tag.
        - ``blocks``  → the built-in block catalog (name, tier, description,
                      tags) the constructor offers alongside stored units.
        - ``get``     ``{ref}`` → one version + its version history
                      (``ref`` = ``instr:custom:<id>[:<version>]``).
        - ``save``    ``{instruction_id | ref, name, description?, items}`` →
                      the next immutable version (admin).
        - ``retire``  ``{ref}`` → retire the pinned version, or every version
                      when the ref is unpinned (admin).
        - ``preview`` ``{items, workspace_implementation?}`` → the composed
                      instruction body exactly as the runtime would build it
                      (stored refs expanded, capability tokens resolved),
                      with token counts (total + per segment).
        - ``project`` ``{draft: {react, consumer, workspace_implementation?},
                      include_text?}`` → the token breakdown of the REAL
                      system instruction the draft agent config would
                      produce (tool roster loaded and rendered at the
                      draft's catalog detail; unloadable tool kinds
                      reported in ``tools.skipped``).
        """
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity
        from kdcube_ai_app.apps.chat.sdk.solutions.agentic_config.instructions import (
            AgenticInstructionsStore,
            builtin_block_catalog,
            expand_instruction_items,
            has_custom_instruction_refs,
        )
        from kdcube_ai_app.apps.chat.sdk.solutions.agentic_config.named_service import (
            INSTR_NAMESPACE,
            AgenticInstructionsNamedService,
        )
        from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
            OBJECT_DELETE,
            OBJECT_GET,
            OBJECT_LIST,
            OBJECT_UPSERT,
            NamedServiceContext,
            NamedServiceRequest,
        )

        payload = self._agent_selection_payload(data, kwargs)
        action = str(payload.get("action") or "").strip().lower()
        base = self._agent_selection_identity()
        self.logger.log(
            f"[agentic_instructions] action={action or '<missing>'} user={base.get('user_id')} "
            f"tenant={base.get('tenant')} project={base.get('project')}"
        )

        if action == "blocks":
            return {"ok": True, "blocks": builtin_block_catalog()}

        if action == "project":
            from kdcube_ai_app.apps.chat.sdk.solutions.agentic_config.projection import (
                project_agent_config,
            )
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                return {
                    "ok": False,
                    "error": "invalid_request",
                    "message": "body.data.draft must be {react?, consumer?, workspace_implementation?}",
                }
            store = None
            if self.pg_pool is not None:
                store = AgenticInstructionsStore(
                    pg_pool=self.pg_pool,
                    tenant=base["tenant"],
                    project=base["project"],
                )
            bundle_root = None
            try:
                from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle_root
                spec = self.config.ai_bundle_spec
                if spec and spec.path and spec.module:
                    bundle_root = resolve_bundle_root(spec.path, spec.module)
            except Exception:
                bundle_root = None
            try:
                projection = await project_agent_config(
                    draft,
                    store=store,
                    bundle_root=bundle_root,
                    include_text=bool(payload.get("include_text")),
                )
            except Exception as exc:
                self.logger.log(
                    f"[agentic_instructions] project failed: {traceback.format_exc()}", "ERROR"
                )
                return {"ok": False, "error": str(exc), "status": 500}
            return {"ok": True, **projection}

        if action == "preview":
            raw_items = payload.get("items")
            if isinstance(raw_items, str):
                raw_items = [raw_items]
            items = [str(v or "").strip() for v in (raw_items or []) if str(v or "").strip()]
            workspace_implementation = str(
                payload.get("workspace_implementation") or "custom"
            ).strip() or "custom"
            expanded = items
            try:
                if has_custom_instruction_refs(items) and self.pg_pool is not None:
                    store = AgenticInstructionsStore(
                        pg_pool=self.pg_pool,
                        tenant=base["tenant"],
                        project=base["project"],
                    )
                    expanded = await expand_instruction_items(items, store=store)
                from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
                    normalize_instruction_blocks,
                )
                body = normalize_instruction_blocks(
                    expanded,
                    workspace_implementation=workspace_implementation,
                )
                # Per-item segmentation so the constructor can show which
                # section of the final instruction each block contributed and
                # jump from the composed view back to its source block.
                from kdcube_ai_app.apps.chat.sdk.util import token_count
                segments = []
                for source_item in expanded:
                    segment_body = normalize_instruction_blocks(
                        [source_item],
                        workspace_implementation=workspace_implementation,
                    )
                    segments.append({
                        "item": source_item,
                        "body": segment_body,
                        "tokens": token_count(segment_body),
                    })
            except Exception as exc:
                self.logger.log(f"[agentic_instructions] preview failed: {traceback.format_exc()}", "ERROR")
                return {"ok": False, "error": str(exc), "status": 500}
            return {
                "ok": True,
                "body": body,
                "items_expanded": expanded,
                "segments": segments,
                "tokens": token_count(body),
            }

        if self.pg_pool is None:
            return {"ok": False, "error": "storage_unavailable"}
        identity = dict(get_current_user_identity() or {})
        ctx = NamedServiceContext(
            tenant=base["tenant"],
            project=base["project"],
            user_id=base["user_id"],
            user_type=str(identity.get("user_type") or ""),
            roles=tuple(identity.get("roles") or ()),
            bundle_id=base["bundle_id"],
        )
        provider = AgenticInstructionsNamedService(pool_factory=lambda: self.pg_pool)
        ref = str(payload.get("ref") or "").strip()
        try:
            if action == "list":
                response = await provider.object_list(ctx, NamedServiceRequest(
                    operation=OBJECT_LIST,
                    namespace=INSTR_NAMESPACE,
                    filters={
                        "include_retired": bool(payload.get("include_retired")),
                        "q": str(payload.get("q") or ""),
                        "tags": payload.get("tags"),
                    },
                ))
            elif action == "get":
                response = await provider.object_get(ctx, NamedServiceRequest(
                    operation=OBJECT_GET,
                    namespace=INSTR_NAMESPACE,
                    object_ref=ref,
                ))
            elif action == "save":
                await AgenticInstructionsStore(
                    pg_pool=self.pg_pool,
                    tenant=base["tenant"],
                    project=base["project"],
                ).ensure_schema()
                response = await provider.object_upsert(ctx, NamedServiceRequest(
                    operation=OBJECT_UPSERT,
                    namespace=INSTR_NAMESPACE,
                    object_ref=ref,
                    payload={
                        "instruction_id": payload.get("instruction_id"),
                        "name": payload.get("name"),
                        "description": payload.get("description"),
                        "items": payload.get("items"),
                        "tags": payload.get("tags"),
                        "signals": payload.get("signals"),
                    },
                ))
            elif action == "retire":
                response = await provider.object_delete(ctx, NamedServiceRequest(
                    operation=OBJECT_DELETE,
                    namespace=INSTR_NAMESPACE,
                    object_ref=ref,
                ))
            else:
                return {
                    "ok": False,
                    "error": "invalid_action",
                    "message": "body.data.action must be list | blocks | get | save | retire | preview | project",
                }
        except Exception as exc:
            self.logger.log(f"[agentic_instructions] {action} failed: {traceback.format_exc()}", "ERROR")
            return {"ok": False, "error": str(exc), "status": 500}
        return response.to_dict()

    # ── platform admin apps ─────────────────────────────────────────────
    # The operator dashboards (economics control plane, conversation and
    # Redis browsers, gateway monitoring, the apps dashboard) are served by
    # THIS app — the always-running platform services app — so the control
    # plane links to one stable home instead of whichever app is loaded.

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:currency-dollar",
            "lucide": "CircleDollarSign",
        },
        alias="control_plane",
        user_types=("privileged",),
    )
    def control_plane(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="control_plane")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[control_plane]. Generating Control Plane Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No control plane interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "EconomicsDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Control Plane")
                return [html]
            except Exception:
                self.logger.log(f"Error loading control_plane by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:chat-bubble-left-right",
            "lucide": "MessageSquareMore",
        },
        alias="conversation_browser",
        user_types=("privileged",),
    )
    def conversation_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="conversation_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(
            f"[conversation_browser]. Generating Conversation Browser Admin Dashboard for user {user_id} ({user_type})"
        )

        bundle_root = self._bundle_root()
        default_content = "<p>No conversation browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "ConversationBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Conversation Browser")
                return [html]
            except Exception:
                self.logger.log(
                    f"Error loading conversation browser by user {user_id}: {traceback.format_exc()}",
                    "ERROR",
                )
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:arrows-right-left",
            "lucide": "ArrowLeftRight",
        },
        alias="svc_gateway",
        user_types=("privileged",),
    )
    def svc_gateway(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="svc_gateway")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[svc_gateway]. Generating Gateway Monitoring Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No gateway monitoring interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                monitoring_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.monitoring")
                fallback_path = Path(monitoring_mod.__file__).parent / "ControlPlaneMonitoringDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Gateway Monitoring")
                return [html]
            except Exception:
                self.logger.log(f"Error loading svc_gateway by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:circle-stack",
            "lucide": "Database",
        },
        alias="redis_browser",
        user_types=("privileged",),
    )
    def redis_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="redis_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[redis_browser]. Generating Redis Browser Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No redis browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.ingress.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "RedisBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Redis Browser")
                return [html]
            except Exception:
                self.logger.log(f"Error loading redis browser by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @api(route="operations", user_types=("privileged",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:cpu-chip",
            "lucide": "Bot",
        },
        alias="ai_bundles",
        user_types=("privileged",),
    )
    def ai_bundles(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="ai_bundles")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[ai_bundles]. Generating the Apps admin dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No apps dashboard available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        try:
            integrations_mod = importlib.import_module("kdcube_ai_app.apps.chat.proc.rest.integrations")
            fallback_path = Path(integrations_mod.__file__).parent / "AIBundleDashboard.tsx"
            content = fallback_path.read_text(encoding="utf-8")

            output_content = patch_dashboard(
                input_content=content,
                base_url=f"http://localhost:{get_settings().CHAT_APP_PORT}",
                access_token=None,
                default_tenant=self.settings.TENANT,
                default_project=self.settings.PROJECT,
                default_app_bundle_id=self.config.ai_bundle_spec.id,
                host_bundles_path=get_settings().HOST_BUNDLES_PATH,
                bundles_root=get_settings().PLATFORM.APPLICATIONS.BUNDLES_ROOT,
            )
            html = self._render_dashboard_html(content=output_content, title="Apps")
            return [html]
        except Exception:
            self.logger.log(f"Error loading ai_bundles by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]
