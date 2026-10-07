# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The host vault service: one request in, one response out.

Order of checks, each fail-closed and audited:

1. identity: the presented certificate must map to a live trust record
   (chain validity and key possession were proven by the transport);
2. request: protocol version, operation, bounded reference, bounds;
3. process-lifetime replay: ``issued_at`` within the skew window; a mutating
   request id still present in this process's bounded cache answers the
   recorded result when its body digest matches, and
   ``replay_rejected`` when it does not (an attacker replaying a captured
   request cannot change its effect, and a broker retry after a lost
   response gets the original outcome instead of a second commit);
4. authorization: the record's namespace ACL must cover the reference's
   namespace exactly; cross-tenant, cross-project, and cross-application
   references are ``forbidden``;
5. storage: atomic operation with generation checks;
6. audit: identity, operation, reference digest, generations, request id,
   result, time. No names, no values, no backend text.

The service is transport-agnostic: ``handle`` takes the peer certificate PEM
the transport verified, never a body field claiming an identity.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.infra.secrets.host_vault.audit import (
    AuditSink,
    MemoryAuditSink,
    event_now,
)
from kdcube_ai_app.infra.secrets.host_vault.identity import TrustRecord, TrustRegistry
from kdcube_ai_app.infra.secrets.host_vault.protocol import (
    MAX_LIST_NAMES,
    MAX_REQUEST_SKEW_SECONDS,
    MUTATING_OPERATIONS,
    ErrorCode,
    Operation,
    VaultError,
    VaultRequest,
    VaultResponse,
    custody_qualification,
    sanitize_failure,
)
from kdcube_ai_app.infra.secrets.host_vault.storage import DurableSecretStore

LOGGER = logging.getLogger("kdcube.host_vault.service")
REPLAY_CACHE_SIZE = 10_000


@dataclass
class _Seen:
    body_digest: str
    response: VaultResponse


class HostVaultService:
    def __init__(
        self,
        *,
        store: DurableSecretStore,
        registry: TrustRegistry,
        audit: AuditSink | None = None,
        skew_seconds: int = MAX_REQUEST_SKEW_SECONDS,
    ) -> None:
        self._store = store
        self._registry = registry
        self._audit = audit or MemoryAuditSink()
        self._skew = skew_seconds
        self._seen: collections.OrderedDict[tuple[str, str], _Seen] = collections.OrderedDict()
        self._lock = threading.Lock()

    # ── entry ─────────────────────────────────────────────────────────────

    def handle(self, body: Any, *, peer_cert_pem: bytes | None, now: float | None = None) -> VaultResponse:
        moment = now if now is not None else time.time()
        request_id = str((body or {}).get("request_id") or "") if isinstance(body, dict) else ""
        identity: TrustRecord | None = None
        request: VaultRequest | None = None
        try:
            if not peer_cert_pem:
                raise VaultError(ErrorCode.UNAUTHENTICATED, detail="no client certificate")
            identity = self._registry.identify(peer_cert_pem, now=moment)
            request = VaultRequest.from_wire(body, deployment_id=identity.deployment_id)
            response = self._dispatch(request, identity, moment)
        except Exception as exc:  # noqa: BLE001 - protocol boundary returns fixed errors
            error = sanitize_failure(exc)
            if error.code is ErrorCode.INTERNAL:
                LOGGER.warning("[host-vault] internal failure request=%s detail=%s", request_id, error.detail)
            response = VaultResponse.failure(error, request_id=request_id)
        self._record(identity, request, request_id, response, moment)
        return response

    # ── checks ────────────────────────────────────────────────────────────

    def _dispatch(self, request: VaultRequest, identity: TrustRecord, moment: float) -> VaultResponse:
        if abs(moment - request.issued_at) > self._skew:
            raise VaultError(ErrorCode.REPLAY_REJECTED, "The request is outside the accepted time window.")
        if request.operation is Operation.HEALTH:
            return VaultResponse.success(
                request,
                deployment_id=identity.deployment_id,
            )
        assert request.reference is not None
        if not identity.allows(request.reference.namespace):
            raise VaultError(ErrorCode.FORBIDDEN)
        if request.operation is Operation.QUALIFY:
            # Existing read/write wildcard ACL behavior is unchanged. A
            # custody assertion requires this namespace explicitly enrolled.
            if request.reference.namespace.path not in identity.namespaces:
                raise VaultError(ErrorCode.FORBIDDEN)
            qualifier = getattr(self._store, "qualify_custody", None)
            if not callable(qualifier):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            try:
                if qualifier() is not None:
                    raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            except Exception:
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None
            return VaultResponse.success(request, custody=custody_qualification(request.reference))
        if request.operation in MUTATING_OPERATIONS:
            return self._mutate(request, identity)
        return self._read(request)

    def _read(self, request: VaultRequest) -> VaultResponse:
        if request.operation is Operation.LIST:
            assert request.reference is not None
            prefix = request.reference.name[: -len("__keys")]
            names = [
                name
                for name in self._store.list_names(
                    request.reference.namespace,
                    prefix=prefix,
                    limit=MAX_LIST_NAMES,
                )
                if not name.endswith(".__keys")
            ]
            return VaultResponse.success(request, names=names)
        found = self._store.get(request.reference)  # type: ignore[arg-type]
        if found is None:
            raise VaultError(ErrorCode.NOT_FOUND)
        record, value = found
        return VaultResponse.success(request, value=value.decode("utf-8"), generation=record.generation)

    def _mutate(self, request: VaultRequest, identity: TrustRecord) -> VaultResponse:
        key = (identity.deployment_id, request.request_id)
        digest = request.body_digest()
        with self._lock:
            seen = self._seen.get(key)
            if seen is not None:
                if seen.body_digest != digest:
                    raise VaultError(ErrorCode.REPLAY_REJECTED)
                return seen.response
            reference = request.reference
            assert reference is not None
            if request.operation is Operation.DELETE:
                record = self._store.delete(
                    reference,
                    expected_generation=request.expected_generation,
                )
            else:
                if (
                    request.operation is Operation.ROTATE
                    and request.expected_generation is None
                    and self._store.get(reference) is None
                ):
                    # A rotation supersedes an EXISTING value; without a stated
                    # generation it must at least find one.
                    raise VaultError(ErrorCode.NOT_FOUND)
                record = self._store.put(
                    reference,
                    (request.value or "").encode("utf-8"),
                    expected_generation=request.expected_generation,
                )
            response = VaultResponse.success(
                request,
                generation=record.generation,
            )
            self._seen[key] = _Seen(body_digest=digest, response=response)
            while len(self._seen) > REPLAY_CACHE_SIZE:
                self._seen.popitem(last=False)
            return response

    def _record(self, identity: TrustRecord | None, request: VaultRequest | None, request_id: str,
                response: VaultResponse, moment: float) -> None:
        try:
            self._audit.append(event_now(
                deployment_id=identity.deployment_id if identity else "",
                fingerprint=identity.fingerprint if identity else "",
                application=(request.reference.namespace.application if request and request.reference else ""),
                operation=request.operation.value if request else "",
                reference_digest=(request.reference.digest if request and request.reference else ""),
                request_id=request.request_id if request else request_id,
                code=response.code.value,
                generation=response.generation,
                expected_generation=request.expected_generation if request else None,
            ))
        except Exception:
            LOGGER.warning("[host-vault] audit append failed", exc_info=True)


__all__ = ["REPLAY_CACHE_SIZE", "HostVaultService"]
