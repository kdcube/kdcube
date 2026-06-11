# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import (
    CanvasObjectResolver,
    object_ref_from_payload,
)

from .transports.api_client import NamedServiceApiEndpoint, call_named_service_api_endpoint
from .types import OBJECT_ACTION, NamedServiceRequest

LOGGER = logging.getLogger("kdcube.sdk.named_services.canvas_resolver")


class NamedServiceCanvasObjectResolver(CanvasObjectResolver):
    """Canvas/chat object resolver backed by a configured named-service provider."""

    resolver_status = "configured"

    def __init__(
        self,
        *,
        namespace: str,
        endpoint: NamedServiceApiEndpoint,
        resolver: str | None = None,
        capabilities: Mapping[str, bool] | None = None,
    ) -> None:
        self.namespace = str(namespace or endpoint.namespace or "").strip().lower()
        self.endpoint = endpoint
        self.resolver = str(resolver or f"named_service.{endpoint.provider or self.namespace}").strip()
        source = dict(capabilities or {})
        self._capabilities = {
            "preview": bool(source.get("preview", True)),
            "open": bool(source.get("open", True)),
            "download": bool(source.get("download", False)),
            "rehost": bool(source.get("rehost", False)),
        }

    def capabilities_for_ref(self, ref: str) -> dict[str, bool]:
        del ref
        return dict(self._capabilities)

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        story_id: str,
        action: str,
    ) -> dict[str, Any]:
        object_ref = object_ref_from_payload(payload)
        base = self.base_response(ref=object_ref, action=action)
        if action in {"capabilities", "describe"}:
            return base
        LOGGER.info(
            "Named-service canvas resolver call start: namespace=%s provider=%s bundle=%s action=%s object_ref=%s user_id=%s story_id=%s",
            self.namespace,
            self.endpoint.provider or "",
            self.endpoint.bundle_id,
            action,
            object_ref,
            user_id,
            story_id,
        )
        response = await call_named_service_api_endpoint(
            self.endpoint,
            NamedServiceRequest(
                operation=OBJECT_ACTION,
                provider=self.endpoint.provider,
                namespace=self.namespace,
                object_ref=object_ref,
                action=action or "preview",
                context={
                    "source": "canvas.object_action",
                    "user_id": user_id,
                    "story_id": story_id,
                },
                payload=dict(payload or {}),
            ),
        )
        if not response.ok:
            message = response.error.message if response.error else "Named service resolver failed"
            code = response.error.code if response.error else "named_service_resolver_failed"
            LOGGER.warning(
                "Named-service canvas resolver call failed: namespace=%s provider=%s bundle=%s action=%s object_ref=%s status=%s error=%s",
                self.namespace,
                self.endpoint.provider or "",
                self.endpoint.bundle_id,
                action,
                object_ref,
                response.status,
                code,
            )
            return {
                **base,
                "ok": False,
                "status": response.status,
                "error": code,
                "message": message,
                "provider": dict(response.provider or {}),
                "data": dict(response.data or {}),
            }

        result: dict[str, Any] = {
            **base,
            "ok": True,
            "provider": dict(response.provider or {}),
            "object": dict(response.object or {}),
            "items": list(response.items or []),
            "data": dict(response.data or {}),
        }
        if response.capabilities:
            result["capabilities"] = dict(response.capabilities)
        if response.ui_event is not None:
            result["ui_event"] = dict(response.ui_event or {})
        if response.object_ref:
            result["object_ref"] = response.object_ref
            result["ref"] = response.object_ref
        object_payload = dict(response.object or {})
        for key in ("title", "summary", "mime", "story_id", "object_kind"):
            if object_payload.get(key) is not None:
                result[key] = object_payload.get(key)
        LOGGER.info(
            "Named-service canvas resolver call complete: namespace=%s provider=%s bundle=%s action=%s object_ref=%s status=%s ui_event=%s",
            self.namespace,
            self.endpoint.provider or "",
            self.endpoint.bundle_id,
            action,
            object_ref,
            response.status,
            bool(response.ui_event),
        )
        return result


def register_configured_named_service_canvas_resolvers(
    registry: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    tenant: str = "",
    project: str = "",
    logger: logging.Logger | None = None,
) -> int:
    """Register configured named-service resolvers into a canvas registry.

    Composition bundles use this helper when their canvas/chat surface should
    resolve object refs owned by another bundle, for example `task:` refs owned
    by a task tracker bundle. The helper is SDK-level so every scene bundle can
    reuse the same config shape.
    """

    log = logger or logging.getLogger(__name__)
    if namespaces is None:
        return 0
    if not isinstance(namespaces, Mapping):
        log.warning(
            "[canvas.object_action] named_services.namespaces must be an object; got %s",
            type(namespaces).__name__,
        )
        return 0

    registered = 0
    for raw_namespace, raw_config in namespaces.items():
        namespace = str(raw_namespace or "").strip().lower().rstrip(":")
        if not namespace:
            continue
        if not isinstance(raw_config, Mapping):
            log.warning(
                "[canvas.object_action] named service resolver config for namespace=%s must be an object",
                namespace,
            )
            continue
        provider_cfg = raw_config.get("provider")
        if not isinstance(provider_cfg, Mapping):
            log.warning(
                "[canvas.object_action] named service resolver namespace=%s skipped: provider is required",
                namespace,
            )
            continue
        bundle_id = str(provider_cfg.get("bundle_id") or "").strip()
        if not bundle_id:
            log.warning(
                "[canvas.object_action] named service resolver namespace=%s skipped: bundle_id is required",
                namespace,
            )
            continue

        capabilities = raw_config.get("capabilities")
        registry.register(
            NamedServiceCanvasObjectResolver(
                namespace=namespace,
                endpoint=NamedServiceApiEndpoint(
                    bundle_id=bundle_id,
                    operation=str(provider_cfg.get("operation") or "named_service").strip() or "named_service",
                    route=str(provider_cfg.get("route") or "operations").strip() or "operations",
                    tenant=str(provider_cfg.get("tenant") or tenant or "").strip() or None,
                    project=str(provider_cfg.get("project") or project or "").strip() or None,
                    provider=str(provider_cfg.get("provider") or "").strip() or None,
                    namespace=namespace,
                ),
                resolver=str(raw_config.get("resolver") or "").strip() or None,
                capabilities=capabilities if isinstance(capabilities, Mapping) else None,
            )
        )
        log.info(
            "[canvas.object_action] registered named service resolver namespace=%s provider=%s bundle=%s",
            namespace,
            str(provider_cfg.get("provider") or "").strip() or "",
            bundle_id,
        )
        registered += 1
    return registered


__all__ = [
    "NamedServiceCanvasObjectResolver",
    "register_configured_named_service_canvas_resolvers",
]
