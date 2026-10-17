# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Public HTTP routes for delegated management of a running KDCube."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from connection_hub.connection_edges import request_origin
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kdcube_ai_app.apps.chat.proc.rest.management.admission import (
    ConnectionHubAdmissionClient,
)
from kdcube_ai_app.apps.chat.proc.rest.management.config import (
    DelegatedManagementConfig,
)
from kdcube_ai_app.apps.chat.proc.rest.management.contracts import (
    INSPECT_OPERATION,
    RELOAD_OPERATION,
    SURFACES_OPERATION,
    management_error,
    management_resource,
    validate_application_id,
    validate_invocation_id,
)
from kdcube_ai_app.apps.chat.proc.rest.management.effect_ledger import (
    RedisEffectLedger,
)
from kdcube_ai_app.apps.chat.proc.rest.management.http_input import read_json_object
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval_routes import (
    router as human_approval_router,
)
from kdcube_ai_app.apps.chat.proc.rest.management.runtime import (
    KDCubeManagementRuntime,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    MAX_SECRET_VALUE_BYTES,
    SECRET_DELETE_OPERATION,
    SECRET_METADATA_OPERATION,
    SECRET_READ_OPERATION,
    SECRET_RESOURCE_SELECTOR,
    SECRET_WRITE_OPERATION,
    SecretTarget,
    validate_secret_value,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export_routes import (
    SECRET_RESPONSE_HEADERS,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_export_routes import (
    router as secret_export_router,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_runtime import (
    KDCubeSecretRuntime,
)
from kdcube_ai_app.apps.chat.proc.rest.management.service import (
    DelegatedManagementService,
    ManagementResponse,
)
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_contracts import (
    MAX_USER_PROPS_REQUEST_BYTES,
    USER_PROPS_IMPORT_OPERATION,
    USER_PROPS_RESOURCE_SELECTOR,
    UserPropRow,
    UserPropsRequestInvalid,
    approval_context as user_props_approval_context,
    import_body as user_props_import_body,
    parse_user_props_import,
    user_props_resource,
)
from kdcube_ai_app.apps.chat.proc.rest.management.user_props_runtime import (
    KDCubeUserPropsRuntime,
)
from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings

router = APIRouter(prefix="/management/v1")
router.include_router(human_approval_router)
router.include_router(secret_export_router)


def _origin(request: Request) -> str:
    return request_origin(request).rstrip("/")


def _bearer(request: Request) -> str:
    scheme, separator, value = request.headers.get("authorization", "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return value.strip()


def _configuration() -> tuple[Any, DelegatedManagementConfig, str]:
    settings = get_settings()
    config = DelegatedManagementConfig.from_settings(settings)
    config.validate()
    resource = management_resource(config.tenant, config.project)
    return settings, config, resource


def _error_response(
    *,
    status_code: int,
    operation: str,
    resource: str,
    config: DelegatedManagementConfig,
    invocation_id: str,
    code: str,
    message: str,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=management_error(
            operation=operation,
            resource=resource,
            tenant=config.tenant,
            project=config.project,
            invocation_id=invocation_id,
            code=code,
            message=message,
            retryable=retryable,
        ),
        headers=headers,
    )


async def _service(request: Request) -> tuple[DelegatedManagementService, str]:
    settings, config, resource = _configuration()
    if not config.enabled:
        raise RuntimeError("delegated_management_disabled")
    secret = str(
        await get_secret(
            f"b:{config.service_secret_ref}",
            bundle_id=config.connection_hub_app_id,
        )
        or ""
    )
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("delegated_management_secret_unavailable")
    redis = getattr(request.app.state, "redis_async", None)
    if redis is None:
        raise RuntimeError("delegated_management_redis_unavailable")
    runtime_instance = str(getattr(settings, "INSTANCE_ID", None) or "").strip()
    if not runtime_instance:
        raise RuntimeError("delegated_management_runtime_identity_unavailable")
    return (
        DelegatedManagementService(
            tenant=config.tenant,
            project=config.project,
            resource=resource,
            runtime_instance=runtime_instance,
            admission=ConnectionHubAdmissionClient(
                admission_url=config.resolved_admission_url(settings),
                service_id=config.service_id,
                service_secret=secret,
                timeout_seconds=config.admission_timeout_seconds,
            ),
            ledger=RedisEffectLedger(
                redis,
                tenant=config.tenant,
                project=config.project,
                pending_seconds=config.effect_pending_seconds,
            ),
            runtime=KDCubeManagementRuntime(
                request,
                tenant=config.tenant,
                project=config.project,
            ),
            secret_runtime=KDCubeSecretRuntime(
                request,
                tenant=config.tenant,
                project=config.project,
            ),
            request_digest_secret=secret,
            user_props_runtime=KDCubeUserPropsRuntime(),
        ),
        resource,
    )


async def _invoke(
    request: Request,
    *,
    operation: str,
    application_id: str = "",
    body: dict[str, Any] | None = None,
    resource_override: str = "",
    approval_context: dict[str, str] | None = None,
    secret_target: SecretTarget | None = None,
    response_headers: dict[str, str] | None = None,
    user_props: tuple[UserPropRow, ...] | None = None,
) -> JSONResponse:
    _settings, config, resource = _configuration()
    effective_resource = str(resource_override or resource).strip()
    metadata_url = (
        f"{_origin(request)}/api/integrations/management/v1/"
        ".well-known/oauth-protected-resource"
    )
    bearer = _bearer(request)
    if not bearer:
        return _error_response(
            status_code=401,
            operation=operation,
            resource=effective_resource,
            config=config,
            invocation_id="",
            code="delegated_bearer_missing",
            message="A Connection Hub delegated bearer is required.",
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'
            },
        )
    try:
        invocation_id = validate_invocation_id(
            request.headers.get("idempotency-key", "")
        )
    except ValueError as exc:
        return _error_response(
            status_code=400,
            operation=operation,
            resource=effective_resource,
            config=config,
            invocation_id="",
            code="idempotency_key_invalid",
            message=str(exc),
        )
    try:
        service, _ = await _service(request)
    except RuntimeError as exc:
        code = str(exc)
        status = 404 if code == "delegated_management_disabled" else 503
        return _error_response(
            status_code=status,
            operation=operation,
            resource=effective_resource,
            config=config,
            invocation_id=invocation_id,
            code=code,
            message="Delegated KDCube management is unavailable.",
            retryable=status == 503,
        )
    extra: dict[str, Any] = {"user_props": user_props} if user_props is not None else {}
    response: ManagementResponse = await service.execute(
        operation=operation,
        delegated_bearer=bearer,
        invocation_id=invocation_id,
        application_id=application_id,
        body=body,
        resource=effective_resource,
        approval_context=approval_context,
        secret_target=secret_target,
        **extra,
    )
    return JSONResponse(
        status_code=response.status_code,
        content=dict(response.payload),
        headers=response_headers,
    )


@router.get(
    "/.well-known/oauth-protected-resource",
    name="delegated_management_protected_resource_metadata",
)
async def protected_resource_metadata(request: Request) -> JSONResponse:
    _settings, config, resource = _configuration()
    if not config.enabled:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    origin = _origin(request)
    encoded = (
        quote(config.tenant, safe="-._~"),
        quote(config.project, safe="-._~"),
        quote(config.connection_hub_app_id, safe="-._~@"),
    )
    issuer = (
        f"{origin}/api/integrations/bundles/{encoded[0]}/{encoded[1]}/"
        f"{encoded[2]}/public/oauth"
    )
    return JSONResponse(
        {
            "resource": resource,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "kdcube_management_resources": [resource, SECRET_RESOURCE_SELECTOR, USER_PROPS_RESOURCE_SELECTOR],
            "kdcube_management_operations": {
                INSPECT_OPERATION: {
                    "method": "GET",
                    "path": "/api/integrations/management/v1/deployment",
                },
                SURFACES_OPERATION: {
                    "method": "GET",
                    "path": (
                        "/api/integrations/management/v1/applications/"
                        "{application_id}/surfaces"
                    ),
                },
                RELOAD_OPERATION: {
                    "method": "POST",
                    "path": (
                        "/api/integrations/management/v1/applications/"
                        "{application_id}/reload"
                    ),
                },
                SECRET_METADATA_OPERATION: {
                    "method": "POST",
                    "path": "/api/integrations/management/v1/secrets/metadata/read",
                    "resource_selector": SECRET_RESOURCE_SELECTOR,
                },
                SECRET_READ_OPERATION: {
                    "method": "POST",
                    "path": "/api/integrations/management/v1/secrets/value/read",
                    "resource_selector": SECRET_RESOURCE_SELECTOR,
                },
                SECRET_WRITE_OPERATION: {
                    "method": "POST",
                    "path": "/api/integrations/management/v1/secrets/value/write",
                    "resource_selector": SECRET_RESOURCE_SELECTOR,
                },
                SECRET_DELETE_OPERATION: {
                    "method": "POST",
                    "path": "/api/integrations/management/v1/secrets/delete",
                    "resource_selector": SECRET_RESOURCE_SELECTOR,
                },
                USER_PROPS_IMPORT_OPERATION: {
                    "method": "POST",
                    "path": "/api/integrations/management/v1/user-props/import",
                    "resource_selector": USER_PROPS_RESOURCE_SELECTOR,
                },
            },
        }
    )


async def _secret_payload(
    request: Request,
    *,
    include_value: bool,
) -> tuple[SecretTarget, dict[str, Any]]:
    maximum = MAX_SECRET_VALUE_BYTES + 4096 if include_value else 4096
    payload = await read_json_object(request, maximum_bytes=maximum)
    allowed = {"scope", "bundle_id", "user_id", "key"}
    required = {"scope", "key"}
    if include_value:
        allowed.add("value")
        required.add("value")
    if set(payload) - allowed or not required.issubset(payload):
        raise ValueError("secret request contains unexpected or missing fields")
    target = SecretTarget.from_mapping(payload)
    exact: dict[str, Any] = target.public_dict()
    if include_value:
        exact["value"] = validate_secret_value(payload.get("value"))
    return target, exact


async def _invoke_secret(
    request: Request,
    *,
    operation: str,
    include_value: bool = False,
) -> JSONResponse:
    try:
        target, body = await _secret_payload(request, include_value=include_value)
        _settings, config, _resource = _configuration()
        resource = target.resource(tenant=config.tenant, project=config.project)
    except ValueError as exc:
        _settings, config, resource = _configuration()
        return _error_response(
            status_code=400,
            operation=operation,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="secret_request_invalid",
            message=str(exc),
            headers=SECRET_RESPONSE_HEADERS,
        )
    return await _invoke(
        request,
        operation=operation,
        body=body,
        resource_override=resource,
        approval_context=target.approval_context(),
        secret_target=target,
        response_headers=SECRET_RESPONSE_HEADERS,
    )


@router.post("/secrets/metadata/read")
async def read_secret_metadata(request: Request) -> JSONResponse:
    return await _invoke_secret(request, operation=SECRET_METADATA_OPERATION)


@router.post("/secrets/value/read")
async def read_secret_value(request: Request) -> JSONResponse:
    return await _invoke_secret(request, operation=SECRET_READ_OPERATION)


@router.post("/secrets/value/write")
async def write_secret_value(request: Request) -> JSONResponse:
    return await _invoke_secret(
        request,
        operation=SECRET_WRITE_OPERATION,
        include_value=True,
    )


@router.post("/secrets/delete")
async def delete_secret(request: Request) -> JSONResponse:
    return await _invoke_secret(request, operation=SECRET_DELETE_OPERATION)


@router.post("/user-props/import")
async def import_user_props(request: Request) -> JSONResponse:
    """W890: insert exported user props, never overwriting; counts only (inserted, equal, different)."""
    _settings, config, _resource = _configuration()
    try:
        resource = user_props_resource(tenant=config.tenant, project=config.project)
        payload = await read_json_object(request, maximum_bytes=MAX_USER_PROPS_REQUEST_BYTES)
        rows = parse_user_props_import(payload)
    except (UserPropsRequestInvalid, ValueError) as exc:
        return _error_response(
            status_code=400,
            operation=USER_PROPS_IMPORT_OPERATION,
            resource=USER_PROPS_RESOURCE_SELECTOR,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="user_props_request_invalid",
            message=str(exc) if isinstance(exc, UserPropsRequestInvalid) else "The request is invalid.",
            headers=SECRET_RESPONSE_HEADERS,
        )
    return await _invoke(
        request,
        operation=USER_PROPS_IMPORT_OPERATION,
        body=user_props_import_body(rows),
        resource_override=resource,
        approval_context=user_props_approval_context(rows),
        user_props=rows,
        response_headers=SECRET_RESPONSE_HEADERS,
    )


@router.get("/deployment")
async def inspect_deployment(request: Request) -> JSONResponse:
    if await request.body():
        settings, config, resource = _configuration()
        del settings
        return _error_response(
            status_code=400,
            operation=INSPECT_OPERATION,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="request_body_not_allowed",
            message="Deployment inspection accepts no request body.",
        )
    return await _invoke(request, operation=INSPECT_OPERATION)


@router.get("/applications/{application_id}/surfaces")
async def application_surfaces(
    application_id: str, request: Request
) -> JSONResponse:
    try:
        exact_application_id = validate_application_id(application_id)
    except ValueError as exc:
        _settings, config, resource = _configuration()
        return _error_response(
            status_code=400,
            operation=SURFACES_OPERATION,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="application_id_invalid",
            message=str(exc),
        )
    if await request.body():
        _settings, config, resource = _configuration()
        return _error_response(
            status_code=400,
            operation=SURFACES_OPERATION,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="request_body_not_allowed",
            message="Surface discovery accepts no request body.",
        )
    return await _invoke(
        request,
        operation=SURFACES_OPERATION,
        application_id=exact_application_id,
    )


@router.post("/applications/{application_id}/reload")
async def reload_application(application_id: str, request: Request) -> JSONResponse:
    try:
        exact_application_id = validate_application_id(application_id)
    except ValueError as exc:
        _settings, config, resource = _configuration()
        return _error_response(
            status_code=400,
            operation=RELOAD_OPERATION,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="application_id_invalid",
            message=str(exc),
        )
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if payload != {}:
        _settings, config, resource = _configuration()
        return _error_response(
            status_code=400,
            operation=RELOAD_OPERATION,
            resource=resource,
            config=config,
            invocation_id=request.headers.get("idempotency-key", ""),
            code="reload_request_invalid",
            message="Application reload requires the exact JSON body {}.",
        )
    return await _invoke(
        request,
        operation=RELOAD_OPERATION,
        application_id=exact_application_id,
        body={},
    )


__all__ = ["router"]
