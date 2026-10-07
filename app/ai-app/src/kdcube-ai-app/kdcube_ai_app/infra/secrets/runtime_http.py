# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Scoped HTTP boundary for the common immutable runtime-secret contract.

Composition supplies the trusted credential policy and selected store factory.
No caller selects a backend, supplies a grant, or changes a record's deadline.
Legacy generic secret routes must reject runtime keys using the guard below.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from kdcube_ai_app.infra.secrets.runtime_contract import (
    RuntimeScopePolicy, qualification, runtime_key_namespace, valid_namespace,
)

_REF = re.compile(r"[0-9a-f]{32}")
_MAX_BODY_BYTES = 512 * 1024
_NO_STORE = {"Cache-Control": "no-store"}


class RuntimeSecretStore(Protocol):
    def qualify(self) -> None: ...
    def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool: ...
    def get(self, *, secret_ref: str) -> str | None: ...
    def delete(self, *, secret_ref: str) -> None: ...
    def purge_expired(self, *, now: int, limit: int) -> int: ...


def _refuse(code: str, status: int) -> HTTPException:
    return HTTPException(status_code=status, detail=code, headers=_NO_STORE)


def reject_legacy_runtime_key(key: str) -> None:
    """Require the scoped expiry-aware API, even when legacy headers are unset."""
    if runtime_key_namespace(key) is not None:
        raise _refuse("runtime_secret_scoped_api_required", 403)


def _unique_fields(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError
        payload[key] = value
    return payload


def _reject_constant(_value):
    raise ValueError


async def _body(request: Request, fields: set[str]) -> dict:
    # Manual bounded decoding keeps secret values out of FastAPI's default
    # request-validation error payload (which includes the rejected input).
    raw = bytearray()
    async for part in request.stream():
        if len(raw) + len(part) > _MAX_BODY_BYTES:
            raise _refuse("runtime_secret_request_too_large", 413)
        raw.extend(part)
    try:
        payload = json.loads(bytes(raw), object_pairs_hook=_unique_fields,
                             parse_constant=_reject_constant)
        if type(payload) is not dict or set(payload) != fields:
            raise ValueError
        return payload
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise _refuse("runtime_secret_request_invalid", 400) from None


def _reference(secret_ref: object) -> None:
    if type(secret_ref) is not str or _REF.fullmatch(secret_ref) is None:
        raise _refuse("runtime_secret_reference_invalid", 400)


def _call(operation: Callable, **kwargs):
    try:
        return operation(**kwargs)
    except Exception as exc:
        # Only fixed recognized codes cross the boundary. Never forward a
        # backend exception, path, response text, or a secret-bearing value.
        code = str(exc)
        status = {
            "runtime_secret_scope_invalid": 400,
            "runtime_secret_scope_forbidden": 403,
            "runtime_secret_reference_invalid": 400,
            "runtime_secret_value_invalid": 400,
            "runtime_secret_purge_invalid": 400,
            "runtime_secret_expired": 410,
            "runtime_secret_conflict": 409,
        }.get(code)
        if status is not None:
            raise _refuse(code, status) from None
        raise _refuse("runtime_secret_storage_unavailable", 503) from None


def install_runtime_routes(app: FastAPI, *, policy: RuntimeScopePolicy,
                           store_factory: Callable[[str], RuntimeSecretStore]) -> None:
    """Install one protocol for the selected provider, default-closed on policy.

    Qualification requires both credential grants and the store's actual
    qualification operation. An available but unqualified provider stays 503.
    The factory is trusted composition; it never comes from request data.
    """
    def authorize(request: Request, namespace: str, *kinds: str) -> None:
        if not valid_namespace(namespace):
            raise _refuse("runtime_secret_scope_forbidden", 403)
        for kind in kinds:
            header = "X-KDCUBE-SECRET-TOKEN" if kind == "read" else "X-KDCUBE-ADMIN-TOKEN"
            tokens = request.headers.getlist(header)
            if len(tokens) != 1 or not policy.authorized(
                namespace=namespace, token=tokens[0], kind=kind,
            ):
                raise _refuse("runtime_secret_scope_forbidden", 403)

    def store(namespace: str) -> RuntimeSecretStore:
        return _call(store_factory, namespace=namespace)

    @app.get("/runtime-secrets/{namespace}/qualification")
    def qualify(namespace: str, request: Request):
        authorize(request, namespace, "read", "write")
        if _call(store(namespace).qualify) is not None:
            raise _refuse("runtime_secret_storage_unavailable", 503)
        return JSONResponse(qualification(namespace), headers=_NO_STORE)

    @app.post("/runtime-secrets/{namespace}/create")
    async def create(namespace: str, request: Request):
        authorize(request, namespace, "write")
        payload = await _body(request, {"secret_ref", "value", "expires_at"})
        _reference(payload["secret_ref"])
        try:
            if (type(payload["value"]) is not str
                    or len(payload["value"].encode("utf-8")) > 65536
                    or type(payload["expires_at"]) is not int or payload["expires_at"] <= 0):
                raise ValueError
        except (ValueError, UnicodeError):
            raise _refuse("runtime_secret_value_invalid", 400) from None
        created = await run_in_threadpool(_call, store(namespace).create, **payload)
        if type(created) is not bool:
            raise _refuse("runtime_secret_storage_unavailable", 503)
        if not created:
            raise _refuse("runtime_secret_conflict", 409)
        return JSONResponse({"status": "ok", "created": True}, headers=_NO_STORE)

    @app.get("/runtime-secrets/{namespace}/secret/{secret_ref}")
    def get(namespace: str, secret_ref: str, request: Request):
        authorize(request, namespace, "read")
        _reference(secret_ref)
        value = _call(store(namespace).get, secret_ref=secret_ref)
        if value is None:
            raise _refuse("runtime_secret_not_found", 404)
        if type(value) is not str:
            raise _refuse("runtime_secret_storage_unavailable", 503)
        return JSONResponse({"value": value}, headers=_NO_STORE)

    @app.delete("/runtime-secrets/{namespace}/secret/{secret_ref}")
    def delete(namespace: str, secret_ref: str, request: Request):
        authorize(request, namespace, "write")
        _reference(secret_ref)
        if _call(store(namespace).delete, secret_ref=secret_ref) is not None:
            raise _refuse("runtime_secret_storage_unavailable", 503)
        return JSONResponse({"status": "ok"}, headers=_NO_STORE)

    @app.post("/runtime-secrets/{namespace}/purge")
    async def purge(namespace: str, request: Request):
        authorize(request, namespace, "write")
        payload = await _body(request, {"now", "limit"})
        if (type(payload["now"]) is not int or payload["now"] <= 0
                or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= 1000):
            raise _refuse("runtime_secret_purge_invalid", 400)
        removed = await run_in_threadpool(_call, store(namespace).purge_expired, **payload)
        if type(removed) is not int or not 0 <= removed <= payload["limit"]:
            raise _refuse("runtime_secret_storage_unavailable", 503)
        return JSONResponse({"status": "ok", "removed": removed}, headers=_NO_STORE)
