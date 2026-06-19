from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.canvas import api as canvas_api
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import (
    CallableCanvasObjectResolver,
    CanvasArtifactResolver,
    build_default_canvas_resolver_registry,
    object_ref_from_payload,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import CanvasPinSearch
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import (
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_NAMESPACE,
    CanvasPinSearchNamedServiceProvider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.storage import CanvasStore
from kdcube_ai_app.apps.chat.sdk.solutions.chat.events.resolver import (
    conversation_ref_capabilities,
    resolve_conversation_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceClient,
    NamedServiceContext,
    NamedServiceRegistry,
    NamedServiceRequest,
    NamedServiceResponse,
    TRANSPORT_LOCAL,
    named_service_canvas_resolver_namespaces,
    register_configured_named_service_canvas_resolvers,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import resolve_event_ref_action
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import DataBusResult
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse


# UI layout operations (drag / resize) change only a card's position/size — never
# its searchable text (card_text excludes placement). A patch that is ONLY these ops
# is a no-op for the pin index, so we skip the index op entirely rather than re-take
# the per-user lock and re-sync card metadata on every drag. Mirrors the layout op
# set in canvas.tools_core (agent_visible_canvas_operations).
_LAYOUT_ONLY_PATCH_OPS = {"move_card", "resize_card"}


def _patch_operations(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    patch = payload.get("patch") if isinstance(payload.get("patch"), Mapping) else payload
    if not isinstance(patch, Mapping):
        return []
    operations = patch.get("operations")
    if isinstance(operations, list):
        return [op for op in operations if isinstance(op, Mapping)]
    if patch.get("op"):
        return [patch]
    return []


def is_layout_only_patch(payload: Mapping[str, Any]) -> bool:
    """True iff a canvas patch carries ops and EVERY op is a layout op (move/resize).
    Such a patch never changes indexed text, so the pin index can skip it. Returns
    False for an empty/unknown op set so an ambiguous patch is indexed rather than
    silently dropped."""
    operations = _patch_operations(payload)
    if not operations:
        return False
    return all(str(op.get("op") or "") in _LAYOUT_ONLY_PATCH_OPS for op in operations)


@dataclass(frozen=True)
class CanvasRuntimeConfig:
    bundle_id: str
    artifact_prefix: str
    origin_prefix: str
    state_event_source_id: str
    ui_event_type: str
    artifact_resolver_name: str


def payload_from_call(data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    if isinstance(data, Mapping):
        return {str(k): v for k, v in data.items()}
    return {
        str(k): v
        for k, v in kwargs.items()
        if k not in {"request", "alias", "route", "endpoint_alias"} and v is not None
    }


def protocol_string(payload: Mapping[str, Any], field: str, default: str = "") -> str:
    value = payload.get(field)
    if value is None:
        return default
    text = str(value).strip()
    if text:
        return text
    return default


class VersatileCanvasService:
    def __init__(
        self,
        entrypoint: Any,
        *,
        config: CanvasRuntimeConfig,
        logger: logging.Logger,
    ) -> None:
        self.entrypoint = entrypoint
        self.config = config
        self.logger = logger
        # Generic canvas pin search — derives its embedder + economics guard from
        # the host entrypoint; any bundle mounting canvas reuses the same mechanism.
        self.pins = CanvasPinSearch(entrypoint, logger=logger)
        self._named_service_provider: CanvasPinSearchNamedServiceProvider | None = None

    def resolve_user_id(self, payload: Mapping[str, Any]) -> str:
        value = payload.get("user_id")
        if value is not None and str(value).strip():
            return str(value).strip()
        comm_user_id = getattr(getattr(self.entrypoint, "comm", None), "user_id", None)
        if comm_user_id is not None and str(comm_user_id).strip():
            return str(comm_user_id).strip()
        return "anonymous"

    def storage_root_or_error(self):
        storage_root = self.entrypoint.bundle_storage_root()
        if not storage_root:
            raise RuntimeError("Bundle storage backend is not configured for this bundle.")
        return storage_root

    def log_failure(self, alias: str, payload: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
        if result.get("ok") is not False:
            return result
        error_text = result.get("error")
        if error_text is None:
            error_text = result.get("detail")
        if error_text is None:
            error_text = result.get("message")
        if error_text is None:
            error_text = "operation failed"
        self.logger.warning(
            "[canvas] operation failed alias=%s error=%s context=%s",
            alias,
            str(error_text)[:500],
            {
                "canvas_id": result.get("canvas_id") if result.get("canvas_id") is not None else payload.get("canvas_id"),
                "canvas_name": result.get("canvas_name") if result.get("canvas_name") is not None else payload.get("canvas_name"),
                "revision": result.get("revision"),
                "expected_revision": result.get("expected_revision"),
                "current_revision": result.get("current_revision"),
            },
        )
        return result

    def store(self, payload: Mapping[str, Any], *, user_id: str | None = None) -> CanvasStore:
        ident = self.entrypoint.runtime_identity()
        tenant = protocol_string(payload, "tenant", protocol_string(ident, "tenant", "default"))
        project = protocol_string(payload, "project", protocol_string(ident, "project", "default"))
        revision_retention = self.entrypoint.bundle_prop("canvas.revision_retention", 80)
        if revision_retention is None:
            revision_retention = 80
        resolved_user_id = user_id
        if resolved_user_id is None or not str(resolved_user_id).strip():
            resolved_user_id = self.resolve_user_id(payload)
        return CanvasStore(
            tenant=tenant,
            project=project,
            bundle_id=self.config.bundle_id,
            user_id=resolved_user_id,
            storage_root=self.storage_root_or_error(),
            revision_retention=int(revision_retention),
            artifact_prefix=self.config.artifact_prefix,
            origin_prefix=self.config.origin_prefix,
            state_event_source_id=self.config.state_event_source_id,
            ui_event_type=self.config.ui_event_type,
            artifact_resolver_name=self.config.artifact_resolver_name,
        )

    def target(self) -> Dict[str, str]:
        return {
            "agent_id": "canvas",
            "surface": "canvas",
            "story_kind": "canvas",
            "conversation_role": "canvas",
        }

    def named_service_provider(self) -> CanvasPinSearchNamedServiceProvider:
        provider = self._named_service_provider
        if provider is None:
            provider = CanvasPinSearchNamedServiceProvider(
                search_handler=self._named_service_search,
                upsert_handler=self._named_service_upsert,
            )
            self._named_service_provider = provider
        return provider

    def named_service_registry(self) -> NamedServiceRegistry:
        registry = NamedServiceRegistry()
        registry.register(self.named_service_provider())
        return registry

    def _named_service_context(self, payload: Mapping[str, Any]) -> NamedServiceContext:
        ident = self.entrypoint.runtime_identity()
        user_id = self.resolve_user_id(payload)
        actor_value = payload.get("actor")
        if isinstance(actor_value, Mapping):
            actor = dict(actor_value)
        else:
            actor = {"name": str(actor_value or user_id or "user"), "user_id": user_id}
        return NamedServiceContext(
            tenant=protocol_string(payload, "tenant", protocol_string(ident, "tenant", "default")),
            project=protocol_string(payload, "project", protocol_string(ident, "project", "default")),
            user_id=user_id,
            bundle_id=self.config.bundle_id,
            actor=actor,
        )

    def _named_service_client(self, payload: Mapping[str, Any]) -> NamedServiceClient:
        return NamedServiceClient(
            self.named_service_registry(),
            context=self._named_service_context(payload),
            transport=TRANSPORT_LOCAL,
        )

    def _legacy_result_from_named_response(
        self,
        response: NamedServiceResponse,
        *,
        user_id: str,
        fallback_alias: str,
    ) -> Dict[str, Any]:
        raw = response.extra.get("raw_result") if response.ok else None
        if isinstance(raw, Mapping):
            result = dict(raw)
            result.setdefault("ok", response.ok)
            result.setdefault("user_id", user_id)
            return result
        if response.ok:
            result: Dict[str, Any] = {
                "ok": True,
                "user_id": user_id,
                "items": response.items,
                "results": response.items,
                "count": len(response.items),
            }
            if response.next_cursor:
                result["next_cursor"] = response.next_cursor
            return result
        error = response.error
        return {
            "ok": False,
            "user_id": user_id,
            "error": error.message if error is not None else f"{fallback_alias} failed",
            "code": error.code if error is not None else "named_service_error",
            "status": response.status,
            "details": error.details if error is not None else {},
        }

    async def _named_service_search(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> Mapping[str, Any]:
        payload = dict(request.filters or {})
        if request.query is not None:
            payload["query"] = request.query
        if request.limit is not None:
            payload["limit"] = request.limit
        if request.cursor is not None:
            payload["cursor"] = request.cursor
        if ctx.tenant and "tenant" not in payload:
            payload["tenant"] = ctx.tenant
        if ctx.project and "project" not in payload:
            payload["project"] = ctx.project
        if ctx.user_id and "user_id" not in payload:
            payload["user_id"] = ctx.user_id
        user_id = self.resolve_user_id(payload)
        return await self.pins.search(
            store=self.store(payload, user_id=user_id),
            user_id=user_id,
            payload=payload,
        )

    def _patch_payload_from_named_object(self, obj: Mapping[str, Any], base_revision: Any = None) -> Dict[str, Any]:
        patch = obj.get("patch")
        if isinstance(patch, Mapping):
            out = dict(patch)
        elif isinstance(obj.get("operations"), list):
            out = {
                "schema": "kdcube.canvas.patch.v1",
                "operations": [dict(op) for op in obj.get("operations") if isinstance(op, Mapping)],
            }
        elif obj.get("op"):
            out = {
                key: value
                for key, value in dict(obj).items()
                if key
                not in {
                    "object_kind",
                    "canvas_name",
                    "canvas_id",
                    "board",
                    "base_revision",
                    "object_ref",
                    "actor",
                }
            }
        else:
            card_payload = obj.get("card") if isinstance(obj.get("card"), Mapping) else obj
            card_id = protocol_string(obj, "card_id") or protocol_string(card_payload, "id") if isinstance(card_payload, Mapping) else ""
            if card_id:
                updates = obj.get("set") if isinstance(obj.get("set"), Mapping) else {
                    key: value
                    for key, value in dict(card_payload).items()
                    if key
                    not in {
                        "object_kind",
                        "canvas_name",
                        "canvas_id",
                        "board",
                        "card",
                        "card_id",
                        "content",
                        "base_revision",
                    }
                }
                op: Dict[str, Any] = {"op": "update_card", "card_id": card_id, "set": dict(updates or {})}
                if "content" in obj:
                    op["content"] = obj.get("content")
                elif isinstance(card_payload, Mapping) and "content" in card_payload:
                    op["content"] = card_payload.get("content")
            else:
                op = {
                    "op": "new_card",
                    "card": {
                        key: value
                        for key, value in dict(card_payload).items()
                        if key not in {"object_kind", "canvas_name", "canvas_id", "board", "card", "base_revision"}
                    },
                }
            out = {"schema": "kdcube.canvas.patch.v1", "operations": [op]}
        if base_revision is not None and "base_revision" not in out:
            out["base_revision"] = base_revision
        return out

    async def _named_service_upsert(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> Mapping[str, Any]:
        obj = dict(request.object or {})
        if ctx.tenant and "tenant" not in obj:
            obj["tenant"] = ctx.tenant
        if ctx.project and "project" not in obj:
            obj["project"] = ctx.project
        if ctx.user_id and "user_id" not in obj:
            obj["user_id"] = ctx.user_id
        actor = ctx.actor if isinstance(ctx.actor, Mapping) else {}
        if actor and "actor" not in obj:
            obj["actor"] = actor.get("name") or actor.get("user_id") or ctx.user_id
        object_kind = protocol_string(obj, "object_kind", CANVAS_CARD_OBJECT_KIND)
        base_revision = request.base_revision or obj.get("base_revision")
        user_id = self.resolve_user_id(obj)
        if object_kind == CANVAS_BOARD_OBJECT_KIND and "patch" not in obj and "operations" not in obj and not obj.get("op"):
            return canvas_api.write(
                payload=obj,
                store=self.store(obj, user_id=user_id),
                user_id=user_id,
                target=self.target(),
            )
        patch = self._patch_payload_from_named_object(obj, base_revision=base_revision)
        patch_payload = {
            **obj,
            "patch": patch,
        }
        return canvas_api.patch(
            payload=patch_payload,
            store=self.store(patch_payload, user_id=user_id),
            user_id=user_id,
            target=self.target(),
        )

    def _canvas_payload_for_result(self, payload: Mapping[str, Any], result: Mapping[str, Any]) -> Dict[str, Any]:
        merged = dict(payload if payload is not None else {})
        if result.get("canvas_id") is not None:
            merged["canvas_id"] = result.get("canvas_id")
        if result.get("canvas_name") is not None:
            merged["canvas_name"] = result.get("canvas_name")
        return merged

    async def _index_pins_after_update(
        self,
        alias: str,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        result: Mapping[str, Any],
    ) -> None:
        if result.get("ok") is False:
            return
        # A pure drag/resize (layout-only patch) changes no indexed text — skip the
        # index op so dragging a card never triggers reindexing or lock contention.
        if alias == "canvas_patch" and is_layout_only_patch(payload):
            self.logger.info(
                "[canvas.pins.index] skip layout-only patch user_id=%s canvas_id=%s",
                user_id,
                result.get("canvas_id"),
            )
            return
        index_payload = self._canvas_payload_for_result(payload, result)
        try:
            indexed = await self.pins.index(
                store=self.store(index_payload, user_id=user_id),
                user_id=user_id,
                payload=index_payload,
            )
            if indexed.get("ok") is False:
                self.logger.warning("[canvas.pins.index] update failed alias=%s result=%s", alias, indexed)
                return
            self.logger.info(
                "[canvas.pins.index] updated alias=%s user_id=%s canvas_id=%s indexed=%s",
                alias,
                user_id,
                indexed.get("board"),
                indexed.get("indexed"),
            )
        except Exception:
            self.logger.warning(
                "[canvas.pins.index] update exception alias=%s user_id=%s",
                alias,
                user_id,
                exc_info=True,
            )

    async def _clear_pins_after_delete(
        self,
        alias: str,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        result: Mapping[str, Any],
    ) -> None:
        if result.get("ok") is False:
            return
        index_payload = self._canvas_payload_for_result(payload, result)
        try:
            cleared = await self.pins.clear(
                store=self.store(index_payload, user_id=user_id),
                user_id=user_id,
                payload=index_payload,
            )
            if cleared.get("ok") is False:
                self.logger.warning("[canvas.pins.index] clear failed alias=%s result=%s", alias, cleared)
                return
            self.logger.info(
                "[canvas.pins.index] cleared alias=%s user_id=%s canvas_id=%s removed=%s",
                alias,
                user_id,
                cleared.get("board"),
                cleared.get("removed"),
            )
        except Exception:
            self.logger.warning(
                "[canvas.pins.index] clear exception alias=%s user_id=%s",
                alias,
                user_id,
                exc_info=True,
            )

    def object_resolvers(self, payload: Mapping[str, Any], *, user_id: str):
        store = self.store(payload, user_id=user_id)
        registry = build_default_canvas_resolver_registry(store)
        ident = self.entrypoint.runtime_identity()
        tenant = protocol_string(payload, "tenant", protocol_string(ident, "tenant", "default"))
        project = protocol_string(payload, "project", protocol_string(ident, "project", "default"))

        async def _resolve_fi(
            action_payload: Mapping[str, Any],
            resolver_user_id: str,
            action: str,
        ) -> Mapping[str, Any]:
            return await resolve_event_ref_action(
                {**dict(action_payload if action_payload is not None else {}), "action": action},
                tenant=tenant,
                project=project,
                user_id=resolver_user_id,
                storage_path=str(getattr(self.entrypoint.settings, "STORAGE_PATH", "")),
                require_embedded_conversation=True,
            )

        registry.register(
            CallableCanvasObjectResolver(
                namespace="fi",
                resolver="react.event_ref",
                resolver_status="implemented",
                capabilities={"preview": False, "open": False, "download": True, "rehost": False},
                handler=_resolve_fi,
            )
        )

        async def _fetch_conversation_details(
            fetch_user_id: str,
            conversation_id: str,
            bundle_id: Optional[str],
        ) -> Optional[Mapping[str, Any]]:
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            conv_idx = ConvIndex(pool=self.entrypoint.pg_pool)
            await conv_idx.init()
            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=ConversationStore(self.entrypoint.settings.STORAGE_PATH),
                model_service=self.entrypoint.models_service,
            )
            return await ctx_client.get_conversation_details(
                user_id=fetch_user_id,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
            )

        async def _resolve_conv(
            action_payload: Mapping[str, Any],
            resolver_user_id: str,
            action: str,
        ) -> Mapping[str, Any]:
            return await resolve_conversation_ref_action(
                {**dict(action_payload if action_payload is not None else {}), "action": action},
                user_id=resolver_user_id,
                fetch_details=_fetch_conversation_details,
            )

        registry.register(
            CallableCanvasObjectResolver(
                namespace="conv",
                resolver="sdk.chat.conversation",
                resolver_status="implemented",
                capabilities=conversation_ref_capabilities(),
                handler=_resolve_conv,
            )
        )

        register_configured_named_service_canvas_resolvers(
            registry,
            namespaces=named_service_canvas_resolver_namespaces(self.entrypoint.bundle_props),
            tenant=tenant,
            project=project,
            logger=self.logger,
        )
        return registry

    async def apply_patch_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            object_payload = dict(payload if payload is not None else {})
            object_payload.setdefault("object_kind", CANVAS_BOARD_OBJECT_KIND)
            if "patch" not in object_payload:
                object_payload["patch"] = dict(payload if payload is not None else {})
            response = await self._named_service_client(payload).upsert(
                namespace=CANVAS_NAMESPACE,
                object=object_payload,
                object_ref=protocol_string(payload, "object_ref") or None,
                base_revision=payload.get("base_revision"),
            )
            result = self._legacy_result_from_named_response(
                response,
                user_id=user_id,
                fallback_alias="canvas_patch",
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_patch", payload, result)
        await self._index_pins_after_update("canvas_patch", payload, user_id=user_id, result=result)
        return result

    async def search(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            filters = {
                key: value
                for key, value in dict(payload if payload is not None else {}).items()
                if key not in {"query", "limit", "cursor"}
            }
            response = await self._named_service_client(payload).search(
                namespace=CANVAS_NAMESPACE,
                query=protocol_string(payload, "query"),
                limit=int(payload.get("limit") or 20),
                cursor=protocol_string(payload, "cursor") or None,
                filters=filters,
            )
            result = self._legacy_result_from_named_response(
                response,
                user_id=user_id,
                fallback_alias="canvas_search",
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_search", payload, result)
        return result

    async def write(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            result = canvas_api.write(
                payload=payload,
                store=self.store(payload, user_id=user_id),
                user_id=user_id,
                target=self.target(),
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_write", payload, result)
        await self._index_pins_after_update("canvas_write", payload, user_id=user_id, result=result)
        return result

    async def delete(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            result = canvas_api.delete(
                payload=payload,
                store=self.store(payload, user_id=user_id),
                user_id=user_id,
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_delete", payload, result)
        await self._clear_pins_after_delete("canvas_delete", payload, user_id=user_id, result=result)
        return result

    async def attachment_upload(
        self,
        payload: Mapping[str, Any],
        *,
        uploaded_files: list[Any],
    ) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            result = canvas_api.upload_attachments(
                payload=payload,
                uploaded_files=uploaded_files,
                store=self.store(payload, user_id=user_id),
                user_id=user_id,
            )
        except Exception as exc:
            self.logger.exception("[canvas.attachment_upload] failed")
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_attachment_upload", payload, result)
        return result

    async def object_action(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            result = await canvas_api.object_action(
                payload=payload,
                registry=self.object_resolvers(payload, user_id=user_id),
                user_id=user_id,
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure("canvas_object_action", payload, result)
        return result

    async def object_download(self, payload: Mapping[str, Any]) -> BundleBinaryResponse:
        user_id = self.resolve_user_id(payload)
        ref = object_ref_from_payload(payload) or protocol_string(payload, "artifact_ref")
        mime = protocol_string(payload, "mime", "application/octet-stream")
        registry = self.object_resolvers(payload, user_id=user_id)
        resolver = registry.resolver_for_ref(ref)
        if not isinstance(resolver, CanvasArtifactResolver):
            raise RuntimeError("download_not_supported_by_this_resolver")
        data, meta = resolver.download_bytes(ref, mime=mime)
        filename = protocol_string(payload, "filename", str(meta.get("filename") or "canvas-artifact"))
        media_type = protocol_string(payload, "mime", str(meta.get("mime") or "application/octet-stream"))
        return BundleBinaryResponse(
            content=data,
            filename=filename,
            media_type=media_type or "application/octet-stream",
        )

    def operation(self, alias: str, payload: Mapping[str, Any], operation: Any) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        try:
            result = operation(user_id=user_id)
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "error": str(exc)}
        self.log_failure(alias, payload, result)
        return result

    async def data_bus_patch_result(self, ctx: Any, message: Any) -> DataBusResult:
        payload = dict(message.payload if message.payload is not None else {})
        actor = dict(message.actor if message.actor is not None else {})
        if ctx.tenant and "tenant" not in payload:
            payload["tenant"] = ctx.tenant
        if ctx.project and "project" not in payload:
            payload["project"] = ctx.project
        if actor.get("user_id") and "user_id" not in payload:
            payload["user_id"] = actor["user_id"]
        if actor.get("fingerprint") and "fingerprint" not in payload:
            payload["fingerprint"] = actor["fingerprint"]
        if actor.get("roles") and "roles" not in payload:
            payload["roles"] = list(actor.get("roles") if actor.get("roles") is not None else [])
        if actor.get("user_id") and "actor" not in payload:
            payload["actor"] = actor["user_id"]
        if message.object_ref and "object_ref" not in payload:
            payload["object_ref"] = message.object_ref

        result = await self.apply_patch_payload(payload)
        if result.get("ok") is False and result.get("current_revision") is not None:
            return DataBusResult.conflict(message, result)
        return DataBusResult.ok(message, result)
