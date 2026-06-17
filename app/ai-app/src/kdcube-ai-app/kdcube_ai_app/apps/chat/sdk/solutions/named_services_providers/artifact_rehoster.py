# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import pathlib
import re
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_ATTACHMENTS,
    build_physical_artifact_path,
    physical_path_to_logical_path,
)

from .client_tools import named_service_namespace_provider_configs_from_config
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint_stream
from .types import OBJECT_GET, NamedServiceRequest

LOGGER = logging.getLogger("kdcube.sdk.named_services.artifact_rehoster")


def _safe_filename(value: Any, *, default: str = "object.bin") -> str:
    name = pathlib.PurePosixPath(str(value or "").strip().strip("/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or default


def _safe_rel_segment(value: Any, *, default: str = "object") -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return segment or default


def _runtime_value(runtime: Any, name: str) -> str:
    return str(getattr(runtime, name, "") or "").strip()


def _embedded_success_json_response(response: Any) -> dict[str, Any] | None:
    """Return the original JSON response when a stream request got JSON instead."""

    err = getattr(response, "error", None)
    if getattr(err, "code", "") != "named_service_stream_body_missing":
        return None
    details = getattr(err, "details", None)
    if not isinstance(details, Mapping):
        return None
    embedded = details.get("response")
    if not isinstance(embedded, Mapping) or embedded.get("ok") is not True:
        return None
    return dict(embedded)


def _response_object(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    ret = payload.get("ret")
    if not isinstance(ret, Mapping):
        return {}
    obj = ret.get("object")
    return obj if isinstance(obj, Mapping) else {}


def _json_response_filename(ref: str, key: str, payload: Mapping[str, Any]) -> str:
    obj = _response_object(payload)
    identity = obj.get("identity")
    object_id = (
        obj.get("id")
        or (identity.get("object_id") if isinstance(identity, Mapping) else "")
        or str(ref or key or "").strip().rsplit(":", 1)[-1]
        or "object"
    )
    filename = _safe_filename(f"{object_id}.json", default="object.json")
    if not filename.lower().endswith(".json"):
        filename = f"{filename}.json"
    return filename


def _canonical_object_ref(payload: Mapping[str, Any], *, fallback: str) -> str:
    ret = payload.get("ret")
    if isinstance(ret, Mapping):
        for source in (ret.get("attrs"), ret.get("object"), ret.get("extra")):
            if isinstance(source, Mapping):
                value = str(source.get("object_ref") or source.get("ref") or "").strip()
                if value:
                    return value
    return str(fallback or "").strip()


def _json_artifact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ret = payload.get("ret")
    if not isinstance(ret, Mapping):
        return dict(payload)
    out: dict[str, Any] = {"ok": True}
    attrs = ret.get("attrs")
    if isinstance(attrs, Mapping):
        out["attrs"] = dict(attrs)
    obj = ret.get("object")
    if isinstance(obj, Mapping):
        out["object"] = dict(obj)
    items = ret.get("items")
    if isinstance(items, list):
        out["items"] = list(items)
    extra = ret.get("extra")
    if isinstance(extra, Mapping):
        out["extra"] = dict(extra)
    return out


def _compact_json_sidecar(payload: Mapping[str, Any]) -> dict[str, Any]:
    ret = payload.get("ret")
    attrs = dict(ret.get("attrs") or {}) if isinstance(ret, Mapping) and isinstance(ret.get("attrs"), Mapping) else {}
    return {
        "ok": True,
        "ret": {"attrs": attrs} if attrs else {},
        "error": None,
    }


class NamedServiceArtifactNamespaceRehoster:
    """ReAct artifact rehoster backed by a configured named-service namespace."""

    def __init__(
        self,
        *,
        namespace: str,
        provider_config: Mapping[str, Any],
        tenant: str = "",
        project: str = "",
    ) -> None:
        self.namespace = str(namespace or "").strip().lower().rstrip(":")
        self.provider_config = dict(provider_config or {})
        self.tenant = str(tenant or "").strip()
        self.project = str(project or "").strip()
        pull = self.provider_config.get("pull")
        self.operation = str(
            (pull.get("operation") if isinstance(pull, Mapping) else "")
            or OBJECT_GET
        ).strip() or OBJECT_GET

    def _endpoint(self, runtime: Any) -> NamedServiceEndpoint | None:
        provider_config = dict(self.provider_config)
        provider_config.setdefault("tenant", self.tenant or _runtime_value(runtime, "tenant"))
        provider_config.setdefault("project", self.project or _runtime_value(runtime, "project"))
        provider_configs = named_service_namespace_provider_configs_from_config(provider_config)
        if provider_configs:
            return NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=self.namespace)
        return NamedServiceEndpoint(namespace=self.namespace, tenant=provider_config.get("tenant"), project=provider_config.get("project"))

    async def __call__(
        self,
        *,
        ref: str,
        key: str,
        ctx_browser: Any,
        outdir: pathlib.Path,
        **context: Any,
    ) -> dict[str, Any]:
        runtime = getattr(ctx_browser, "runtime_ctx", None)
        turn_id = _runtime_value(runtime, "turn_id")
        endpoint = self._endpoint(runtime)
        if not turn_id:
            return {"missing": [{"object_ref": ref, "reason": "missing_turn_id"}]}

        LOGGER.info(
            "Named-service artifact rehost start: namespace=%s operation=%s provider=%s bundle=%s object_ref=%s",
            self.namespace,
            self.operation,
            endpoint.provider or "",
            endpoint.bundle_id,
            ref,
        )
        try:
            stream = await call_named_service_endpoint_stream(
                endpoint,
                NamedServiceRequest(
                    operation=self.operation,
                    provider=endpoint.provider,
                    namespace=self.namespace,
                    object_ref=str(ref or "").strip() or None,
                    response_mode="stream",
                    context={
                        "source": "react.pull",
                        "materialize": True,
                        "namespace": self.namespace,
                        "turn_id": turn_id,
                        "tool_id": context.get("tool_id"),
                        "tool_call_id": context.get("tool_call_id"),
                    },
                    payload={"key": str(key or "").strip()},
                ),
            )
        except Exception as exc:
            LOGGER.warning(
                "Named-service artifact rehost failed before provider response: namespace=%s operation=%s provider=%s bundle=%s object_ref=%s error=%s",
                self.namespace,
                self.operation,
                endpoint.provider or "",
                endpoint.bundle_id,
                ref,
                exc,
            )
            return {
                "errors": [{
                    "object_ref": ref,
                    "error": {
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "details": {},
                    },
                }]
            }
        response = stream.response
        json_response = _embedded_success_json_response(response)
        if json_response is not None:
            return await self._materialize_json_response(
                payload=json_response,
                ref=ref,
                key=key,
                outdir=outdir,
                turn_id=turn_id,
                endpoint=endpoint,
            )
        if not response.ok:
            LOGGER.warning(
                "Named-service artifact rehost provider error: namespace=%s operation=%s provider=%s bundle=%s object_ref=%s status=%s error=%s",
                self.namespace,
                self.operation,
                endpoint.provider or "",
                endpoint.bundle_id,
                ref,
                response.status,
                response.error.code if response.error else "",
            )
            return {
                "errors": [{
                    "object_ref": ref,
                    "error": response.error.to_dict() if response.error else {
                        "code": "named_service_stream_failed",
                        "message": "Named-service stream request failed",
                        "details": {"status": response.status},
                    },
                    "response": response.to_dict(),
                }]
            }
        filename = _safe_filename(
            stream.filename
            or pathlib.PurePosixPath(str(key or "").strip()).name
            or pathlib.PurePosixPath(str(ref or "").strip()).name,
            default="object.bin",
        )
        mime = str(stream.media_type or "").strip()
        if not mime:
            guessed, _ = mimetypes.guess_type(filename)
            mime = guessed or "application/octet-stream"
        digest = hashlib.sha1(str(ref or "").encode("utf-8")).hexdigest()[:16]
        relpath = f"named_services/{_safe_rel_segment(self.namespace)}/{digest}/{filename}"
        physical_path = build_physical_artifact_path(
            turn_id=turn_id,
            namespace=ARTIFACT_NAMESPACE_ATTACHMENTS,
            relpath=relpath,
        )
        target = resolve_artifact_path(pathlib.Path(outdir), physical_path, prefer_existing=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        size_bytes = 0
        try:
            with target.open("wb") as fh:
                async for chunk in stream.chunks:
                    payload = bytes(chunk or b"")
                    if not payload:
                        continue
                    size_bytes += len(payload)
                    await asyncio.to_thread(fh.write, payload)
        except Exception as exc:
            LOGGER.warning(
                "Named-service artifact rehost stream copy failed: namespace=%s operation=%s object_ref=%s target=%s error=%s",
                self.namespace,
                self.operation,
                ref,
                target,
                exc,
            )
            return {
                "errors": [{
                    "object_ref": ref,
                    "error": {
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "details": {"phase": "stream_copy", "bytes_written": size_bytes},
                    },
                    "response": response.to_dict(),
                }]
            }
        logical_path = physical_path_to_logical_path(physical_path)
        LOGGER.info(
            "Named-service artifact rehost complete: namespace=%s operation=%s provider=%s bundle=%s object_ref=%s logical_path=%s bytes=%s",
            self.namespace,
            self.operation,
            endpoint.provider or "",
            endpoint.bundle_id,
            ref,
            logical_path,
            size_bytes,
        )
        return {
            "materialized": [{
                "object_ref": ref,
                "logical_path": logical_path,
                "physical_path": physical_path,
                "namespace": ARTIFACT_NAMESPACE_ATTACHMENTS,
                "artifact_kind": "attachment",
                "mime": mime,
                "size_bytes": size_bytes,
                "file_count": 1,
                "response": response.to_dict(),
            }]
        }

    async def _materialize_json_response(
        self,
        *,
        payload: Mapping[str, Any],
        ref: str,
        key: str,
        outdir: pathlib.Path,
        turn_id: str,
        endpoint: NamedServiceEndpoint,
    ) -> dict[str, Any]:
        filename = _json_response_filename(ref, key, payload)
        mime = "application/json"
        digest = hashlib.sha1(str(ref or "").encode("utf-8")).hexdigest()[:16]
        relpath = f"named_services/{_safe_rel_segment(self.namespace)}/{digest}/{filename}"
        physical_path = build_physical_artifact_path(
            turn_id=turn_id,
            namespace=ARTIFACT_NAMESPACE_ATTACHMENTS,
            relpath=relpath,
        )
        target = resolve_artifact_path(pathlib.Path(outdir), physical_path, prefer_existing=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        artifact_payload = _json_artifact_payload(payload)
        body = json.dumps(artifact_payload, ensure_ascii=False, indent=2).encode("utf-8")
        await asyncio.to_thread(target.write_bytes, body)
        logical_path = physical_path_to_logical_path(physical_path)
        LOGGER.info(
            "Named-service artifact rehost JSON materialized: namespace=%s operation=%s provider=%s bundle=%s object_ref=%s logical_path=%s bytes=%s",
            self.namespace,
            self.operation,
            endpoint.provider or "",
            endpoint.bundle_id,
            _canonical_object_ref(payload, fallback=ref),
            logical_path,
            len(body),
        )
        object_ref = _canonical_object_ref(payload, fallback=ref)
        return {
            "materialized": [{
                "object_ref": object_ref,
                **({"requested_object_ref": ref} if object_ref != str(ref or "").strip() else {}),
                "logical_path": logical_path,
                "physical_path": physical_path,
                "namespace": ARTIFACT_NAMESPACE_ATTACHMENTS,
                "artifact_kind": "attachment",
                "mime": mime,
                "size_bytes": len(body),
                "file_count": 1,
                "response": _compact_json_sidecar(payload),
            }]
        }


def register_configured_named_service_artifact_rehosters(
    event_sources: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    tenant: str = "",
    project: str = "",
    logger: logging.Logger | None = None,
) -> int:
    """Register `react.pull` rehosters for configured named-service namespaces."""

    log = logger or LOGGER
    if namespaces is None:
        return 0
    if not isinstance(namespaces, Mapping):
        log.warning(
            "[react.pull] named_services.namespaces must be an object; got %s",
            type(namespaces).__name__,
        )
        return 0
    register = getattr(event_sources, "register_namespace_rehoster", None)
    if not callable(register):
        log.warning("[react.pull] event_sources does not support dynamic namespace rehoster registration")
        return 0

    registered = 0
    for raw_namespace, raw_config in namespaces.items():
        namespace = str(raw_namespace or "").strip().lower().rstrip(":")
        if not namespace:
            continue
        if not isinstance(raw_config, Mapping):
            log.warning("[react.pull] named service namespace=%s config must be an object", namespace)
            continue
        provider_configs = named_service_namespace_provider_configs_from_config(raw_config)
        endpoint = (
            NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=namespace, tenant=tenant, project=project)
            if provider_configs
            else NamedServiceEndpoint(namespace=namespace, tenant=tenant, project=project)
        )
        register(
            namespace,
            NamedServiceArtifactNamespaceRehoster(
                namespace=namespace,
                provider_config={
                    "providers": list(provider_configs),
                    "pull": dict(raw_config.get("pull") or {}) if isinstance(raw_config.get("pull"), Mapping) else {},
                },
                tenant=tenant,
                project=project,
            ),
            description=f"Named-service object rehoster for namespace {namespace!r}.",
            module=__name__,
            object_name=f"named_service_{namespace}_artifact_rehoster",
        )
        log.info(
            "[react.pull] registered named service artifact rehoster namespace=%s provider=%s bundle=%s",
            namespace,
            endpoint.provider or "<discovery>",
            endpoint.bundle_id or endpoint.module or "<discovery>",
        )
        registered += 1
    return registered


__all__ = [
    "NamedServiceArtifactNamespaceRehoster",
    "register_configured_named_service_artifact_rehosters",
]
