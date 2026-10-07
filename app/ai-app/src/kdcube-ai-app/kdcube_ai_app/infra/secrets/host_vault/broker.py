# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Stateless ``kdcube-secrets`` broker adapter.

Translates the existing internal secrets-service operations (``get``,
``set``, ``delete`` by a KDCube secrets-manager key) into host-vault protocol
requests over the deployment's mTLS identity. Nothing is cached: no value,
no negative result, no last response. A mutation is acknowledged to the
caller only when the vault answered ``ok`` (the store committed).

Namespace binding: the broker runs inside the deployment and knows the
canonical tenant/project. The HTTP adapter binds one logical application for
the whole deployment-wide secrets manager (``kdcube-runtime``); library users
may bind a narrower trusted application. The secret reference is DERIVED here
from the internal key, so no external party ever names a vault path.

The bearer model of the existing service (``X-KDCUBE-SECRET-TOKEN`` /
``X-KDCUBE-ADMIN-TOKEN``) is deliberately absent: possession of a copied
string is not workload identity, and this adapter offers no way to present
one to the vault.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.infra.secrets.host_vault.protocol import (
    MAX_LIST_NAMES,
    ErrorCode,
    Operation,
    SecretNamespace,
    SecretReference,
    VaultError,
    VaultRequest,
    VaultResponse,
    custody_qualification,
)

LOGGER = logging.getLogger("kdcube.host_vault.broker")


class VaultTransport:
    """What the broker needs from the wire: one call, one response."""

    def call(self, request: VaultRequest) -> VaultResponse:  # pragma: no cover - protocol
        raise NotImplementedError


@dataclass(frozen=True)
class BrokerResult:
    ok: bool
    code: ErrorCode
    generation: int | None = None


@dataclass(frozen=True)
class BrokerReadResult:
    ok: bool
    code: ErrorCode
    value: str | None = None
    generation: int | None = None


@dataclass(frozen=True)
class BrokerListResult:
    ok: bool
    code: ErrorCode
    names: tuple[str, ...] = ()


class SecretsBroker:
    def __init__(self, *, transport: VaultTransport, tenant: str, project: str) -> None:
        self._transport = transport
        self._tenant = tenant
        self._project = project

    def _reference(self, application: str, key: str) -> SecretReference:
        namespace = SecretNamespace(tenant=self._tenant, project=self._project, application=application)
        return SecretReference.derive(namespace=namespace, internal_key=key)

    def _call(self, request: VaultRequest) -> VaultResponse:
        response = self._transport.call(request)
        if not response.ok and response.code is ErrorCode.INTERNAL:
            LOGGER.warning("[kdcube-secrets] vault internal failure request=%s", request.request_id)
        return response

    # ── the internal secrets-service operations ───────────────────────────

    def qualify_custody(self, *, application: str, metadata_key: str) -> BrokerResult:
        """Fresh authenticated assertion; health or an old server never suffice."""
        try:
            selector = self._reference(application, metadata_key)
            if not selector.name.endswith(".__keys"):
                raise VaultError(ErrorCode.INVALID_REQUEST)
            request = VaultRequest.new(Operation.QUALIFY, selector)
            response = self._call(request)
            # Also validate direct/in-process ports through the wire grammar.
            response = VaultResponse.from_wire(response.to_wire())
            if (response.ok is not True or response.request_id != request.request_id
                    or response.extra != {"custody": custody_qualification(selector)}):
                return BrokerResult(ok=False, code=ErrorCode.BACKEND_UNAVAILABLE)
            return BrokerResult(ok=True, code=ErrorCode.OK)
        except Exception:
            return BrokerResult(ok=False, code=ErrorCode.BACKEND_UNAVAILABLE)

    def read(self, *, application: str, key: str) -> BrokerReadResult:
        """Read one value while retaining the fixed protocol result code."""
        try:
            response = self._call(VaultRequest.new(Operation.GET, self._reference(application, key)))
        except VaultError as exc:
            return BrokerReadResult(ok=False, code=exc.code)
        return BrokerReadResult(
            ok=response.ok,
            code=response.code,
            value=response.value if response.ok else None,
            generation=response.generation,
        )

    def get(self, *, application: str, key: str) -> str | None:
        """Read one value. ``None`` for not found, forbidden, or unreachable:
        the same shape ``SecretsServiceSecretsManager.get_secret`` returns,
        preserving the existing reader contract."""
        result = self.read(application=application, key=key)
        return result.value if result.ok else None

    def list_names(self, *, application: str, metadata_key: str) -> BrokerListResult:
        """List names under one ``.__keys`` selector, never values."""

        try:
            selector = self._reference(application, metadata_key)
            response = self._call(VaultRequest.new(Operation.LIST, selector))
            if not response.ok:
                return BrokerListResult(ok=False, code=response.code)
            raw_names = response.extra.get("names")
            if not isinstance(raw_names, list) or len(raw_names) > MAX_LIST_NAMES:
                return BrokerListResult(ok=False, code=ErrorCode.INTERNAL)
            prefix = metadata_key[: -len("__keys")]
            names: set[str] = set()
            for raw_name in raw_names:
                if not isinstance(raw_name, str):
                    return BrokerListResult(ok=False, code=ErrorCode.INTERNAL)
                name = raw_name.strip()
                if not name.startswith(prefix) or name.endswith(".__keys"):
                    return BrokerListResult(ok=False, code=ErrorCode.INTERNAL)
                try:
                    self._reference(application, name)
                except VaultError:
                    return BrokerListResult(ok=False, code=ErrorCode.INTERNAL)
                names.add(name)
            return BrokerListResult(
                ok=True,
                code=ErrorCode.OK,
                names=tuple(sorted(names)),
            )
        except VaultError as exc:
            return BrokerListResult(ok=False, code=exc.code)

    def set(self, *, application: str, key: str, value: str, expected_generation: int | None = None) -> BrokerResult:
        response = self._call(VaultRequest.new(
            Operation.SET, self._reference(application, key), value=value, expected_generation=expected_generation,
        ))
        return BrokerResult(ok=response.ok, code=response.code, generation=response.generation)

    def rotate(self, *, application: str, key: str, value: str, expected_generation: int | None = None) -> BrokerResult:
        response = self._call(VaultRequest.new(
            Operation.ROTATE, self._reference(application, key), value=value, expected_generation=expected_generation,
        ))
        return BrokerResult(ok=response.ok, code=response.code, generation=response.generation)

    def delete(self, *, application: str, key: str, expected_generation: int | None = None) -> BrokerResult:
        response = self._call(VaultRequest.new(
            Operation.DELETE, self._reference(application, key), expected_generation=expected_generation,
        ))
        if not response.ok and response.code is ErrorCode.NOT_FOUND:
            # Deleting an absent value is a settled state, as the existing
            # service treats a 404 delete.
            return BrokerResult(ok=True, code=ErrorCode.NOT_FOUND)
        return BrokerResult(ok=response.ok, code=response.code, generation=response.generation)

    def health(self) -> dict[str, Any]:
        try:
            response = self._call(VaultRequest.new(Operation.HEALTH))
        except VaultError as exc:
            return {"ok": False, "code": exc.code.value}
        return {"ok": response.ok, "code": response.code.value, **{k: v for k, v in response.extra.items() if k == "deployment_id"}}


__all__ = [
    "BrokerListResult",
    "BrokerReadResult",
    "BrokerResult",
    "SecretsBroker",
    "VaultTransport",
]
