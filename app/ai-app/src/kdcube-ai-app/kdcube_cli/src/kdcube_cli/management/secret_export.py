from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from kdcube_cli.management.callback import LoopbackCallbackServer
from kdcube_cli.management.errors import ManagementCliError
from kdcube_cli.management.models import (
    ManagementSecretTarget,
    ManagementTarget,
)
from kdcube_cli.management.pkce import generate_pkce
from kdcube_cli.management.validation import validate_web_url

SECRET_EXPORT_REQUEST_SCHEMA = "kdcube.management.secret_export.request.v1"
SECRET_EXPORT_START_SCHEMA = "kdcube.management.secret_export.start.v1"
SECRET_EXPORT_RESULT_SCHEMA = "kdcube.management.secret_export.result.v1"
SECRET_EXPORT_ERROR_SCHEMA = "kdcube.management.secret_export.error.v1"

# The server permits at most 8 MiB of UTF-8 secret values. JSON can expand one
# input byte to a six-byte Unicode escape, so retain a finite envelope above
# that protocol maximum rather than rejecting a valid bounded export.
MAX_SECRET_EXPORT_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_SECRET_EXPORT_TARGETS = 4096
MAX_EXPORTED_SECRET_BYTES = 64 * 1024
MAX_EXPORTED_SECRET_TOTAL_BYTES = 8 * 1024 * 1024
MAX_APPROVAL_CLOCK_SKEW_SECONDS = 30
MAX_APPROVAL_EVIDENCE_AGE_SECONDS = 900
MAX_SECRET_EXPORT_TRANSACTION_SECONDS = 900

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,512}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_METHOD_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_ASSURANCE_RANK = {
    "session_confirmation": 1,
    "fresh_authentication": 2,
    "user_verification": 3,
}

BrowserOpener = Callable[[str], bool]
CallbackFactory = Callable[..., LoopbackCallbackServer]


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json_object(raw: bytes) -> dict[str, Any]:
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise TypeError("JSON response is not an object")
    return value


def _fixed_error(
    code: str,
    message: str = "KDCube rejected the secret export request.",
) -> ManagementCliError:
    exact = str(code or "").strip()
    if not _ERROR_CODE_RE.fullmatch(exact):
        exact = "secret_export_response_invalid"
        message = "KDCube returned an invalid secret export response."
    return ManagementCliError(exact, message)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


MAX_BOOTSTRAP_USER_PROPS = 20000


@dataclass(frozen=True)
class SecretExportRequest:
    target: ManagementTarget
    callback_uri: str
    state: str
    code_challenge: str
    targets: tuple[ManagementSecretTarget, ...]
    selection: str = ""
    # W885: with the bootstrap selection, user secrets and the user props travel only with user_data.
    user_data: bool = False

    @property
    def user_props(self) -> bool:
        return self.selection == "bootstrap" and self.user_data

    @classmethod
    def create(
        cls,
        *,
        target: ManagementTarget,
        callback_uri: str,
        state: str,
        code_challenge: str,
        targets: Sequence[ManagementSecretTarget] = (),
        selection: str = "",
        user_data: bool = False,
    ) -> SecretExportRequest:
        callback = validate_web_url(
            callback_uri,
            code="secret_export_callback_invalid",
            allow_query=False,
            loopback_only=True,
        )
        parsed_callback = urlsplit(callback)
        if (
            parsed_callback.scheme != "http"
            or parsed_callback.hostname not in {"127.0.0.1", "::1"}
            or parsed_callback.port is None
            or parsed_callback.path != "/callback"
        ):
            raise _fixed_error(
                "secret_export_callback_invalid",
                "The secret export callback is invalid.",
            )
        exact_state = str(state or "").strip()
        exact_challenge = str(code_challenge or "").strip()
        if not _TOKEN_RE.fullmatch(exact_state) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", exact_challenge
        ):
            raise _fixed_error(
                "secret_export_pkce_invalid",
                "The secret export PKCE parameters are invalid.",
            )
        ordered = tuple(sorted(targets, key=lambda item: item.identity))
        exact_selection = str(selection or "").strip()
        if exact_selection not in {"", "all", "bootstrap"} or bool(ordered) == bool(
            exact_selection
        ) or (user_data and exact_selection != "bootstrap"):
            raise _fixed_error(
                "secret_export_targets_invalid",
                "The secret export must select all secrets or name exact targets.",
            )
        if ordered and (
            len(ordered) > MAX_SECRET_EXPORT_TARGETS
            or len({item.identity for item in ordered}) != len(ordered)
        ):
            raise _fixed_error(
                "secret_export_targets_invalid",
                "The secret export must name distinct exact secret targets.",
            )
        return cls(
            target=target,
            callback_uri=callback,
            state=exact_state,
            code_challenge=exact_challenge,
            targets=ordered,
            selection=exact_selection,
            user_data=bool(user_data),
        )

    @property
    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "callback_uri": self.callback_uri,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }
        if self.selection:
            payload["selection"] = self.selection
            if self.selection == "bootstrap":
                payload["user_data"] = self.user_data
        else:
            payload["targets"] = [target.to_dict() for target in self.targets]
        return payload

    def canonical_payload_for(
        self,
        targets: Sequence[ManagementSecretTarget],
    ) -> dict[str, Any]:
        return {
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "tenant": self.target.tenant,
            "project": self.target.project,
            "callback_uri": self.callback_uri,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "targets": [target.to_dict() for target in targets],
            **({"user_props": True} if self.user_props else {}),
        }

    def request_digest_for(
        self,
        targets: Sequence[ManagementSecretTarget],
    ) -> str:
        return _digest(self.canonical_payload_for(targets))

    @property
    def request_digest(self) -> str:
        """Return the digest for an exact client-selected manifest.

        Whole-deployment export cannot have a client-computed digest because
        KDCube first freezes the provider inventory. Callers handling an
        ``all`` selection use the digest returned by ``start``.
        """

        if self.selection:
            raise _fixed_error(
                "secret_export_digest_unavailable",
                "The whole-export digest is available after KDCube freezes the inventory.",
            )
        return self.request_digest_for(self.targets)


@dataclass(frozen=True)
class SecretExportStart:
    transaction_id: str
    request_digest: str
    authorization_url: str
    required_assurance: str
    expires_at: int
    targets: tuple[ManagementSecretTarget, ...]
    target_count: int = 0


@dataclass(frozen=True)
class ExportedSecret:
    target: ManagementSecretTarget
    value: str = field(repr=False)


@dataclass(frozen=True)
class SecretExportResult:
    transaction_id: str
    request_digest: str
    assurance: str
    approval_method: str
    approval_verified_at: int
    values: tuple[ExportedSecret, ...] = field(repr=False)
    user_props: tuple[dict[str, str], ...] = field(default=(), repr=False)


class SecretExportTransport(Protocol):
    async def post(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
    ) -> tuple[int, Mapping[str, Any]]: ...


class HttpxSecretExportTransport:
    def __init__(self, *, transport: Any = None, timeout_seconds: float = 60.0) -> None:
        self._transport = transport
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))

    async def post(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
    ) -> tuple[int, Mapping[str, Any]]:
        try:
            import httpx2

            async with (
                httpx2.AsyncClient(
                    timeout=httpx2.Timeout(self._timeout_seconds),
                    follow_redirects=False,
                    transport=self._transport,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=dict(payload),
                ) as response,
            ):
                if response.status_code < 200 or response.status_code >= 600:
                    raise _fixed_error(
                        "secret_export_http_status_invalid",
                        "KDCube returned an invalid secret export status.",
                    )
                if 300 <= response.status_code < 400:
                    raise _fixed_error(
                        "secret_export_redirect_rejected",
                        "KDCube redirected a secret export protocol request.",
                    )
                content_types = response.headers.get_list("content-type")
                if (
                    len(content_types) != 1
                    or content_types[0].partition(";")[0].strip().lower()
                    != "application/json"
                    or response.headers.get_list("content-encoding")
                ):
                    raise _fixed_error(
                        "secret_export_response_invalid",
                        "KDCube returned an invalid secret export response.",
                    )
                lengths = response.headers.get_list("content-length")
                if lengths and (
                    len(lengths) != 1 or not lengths[0].strip().isdecimal()
                ):
                    raise _fixed_error(
                        "secret_export_response_invalid",
                        "KDCube returned an invalid secret export response.",
                    )
                if lengths and int(lengths[0]) > MAX_SECRET_EXPORT_RESPONSE_BYTES:
                    raise _fixed_error(
                        "secret_export_response_too_large",
                        "The KDCube secret export response is too large.",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_SECRET_EXPORT_RESPONSE_BYTES:
                        raise _fixed_error(
                            "secret_export_response_too_large",
                            "The KDCube secret export response is too large.",
                        )
                status = int(response.status_code)
        except ManagementCliError:
            raise
        except Exception:  # noqa: BLE001
            raise _fixed_error(
                "secret_export_request_failed",
                "The selected KDCube secret export service could not be reached.",
            ) from None
        try:
            decoded = _decode_json_object(bytes(body))
        except (TypeError, UnicodeError, ValueError):
            raise _fixed_error(
                "secret_export_response_invalid",
                "KDCube returned an invalid secret export response.",
            ) from None
        return status, decoded


class SecretExportClient:
    def __init__(self, *, transport: SecretExportTransport) -> None:
        self._transport = transport

    @staticmethod
    def _raise_response(status: int, payload: Mapping[str, Any]) -> None:
        if status == 200:
            return
        error = payload.get("error")
        code = error.get("code") if isinstance(error, Mapping) else ""
        raise _fixed_error(str(code or "secret_export_rejected"))

    async def start(self, request: SecretExportRequest) -> SecretExportStart:
        status, payload = await self._transport.post(
            url=request.target.url(request.target.route("secrets/export/start")),
            payload=request.payload,
        )
        self._raise_response(status, payload)
        if (
            set(payload)
            != {
                "schema",
                "ok",
                "transaction_id",
                "request_digest",
                "authorization_url",
                "required_assurance",
                "expires_at",
                "target_count",
                "targets",
            }
            or payload.get("schema") != SECRET_EXPORT_START_SCHEMA
            or payload.get("ok") is not True
        ):
            raise _fixed_error("secret_export_response_invalid")
        transaction_id = str(payload.get("transaction_id") or "")
        request_digest = str(payload.get("request_digest") or "")
        assurance = str(payload.get("required_assurance") or "")
        expires_at = payload.get("expires_at")
        target_count = payload.get("target_count")
        raw_targets = payload.get("targets")
        try:
            if not isinstance(raw_targets, list):
                raise TypeError
            frozen_targets = tuple(
                sorted(
                    (
                        ManagementSecretTarget.create(
                            scope=item.get("scope"),
                            key=item.get("key"),
                            bundle_id=item.get("bundle_id", ""),
                            user_id=item.get("user_id", ""),
                        )
                        for item in raw_targets
                        if isinstance(item, Mapping)
                        and not (
                            set(item)
                            - {"scope", "key", "bundle_id", "user_id"}
                        )
                    ),
                    key=lambda item: item.identity,
                )
            )
        except (ManagementCliError, TypeError, ValueError):
            raise _fixed_error("secret_export_response_invalid") from None
        now = int(time.time())
        exact_targets_valid = (
            bool(frozen_targets)
            and target_count == len(frozen_targets)
            and request_digest == request.request_digest_for(frozen_targets)
            and frozen_targets == request.targets
        )
        whole_targets_hidden = (
            request.selection in {"all", "bootstrap"}
            and not frozen_targets
            and isinstance(target_count, int)
            and not isinstance(target_count, bool)
            and (0 if request.user_props else 1) <= target_count <= MAX_SECRET_EXPORT_TARGETS
        )
        if (
            not _TOKEN_RE.fullmatch(transaction_id)
            or not _DIGEST_RE.fullmatch(request_digest)
            or assurance not in _ASSURANCE_RANK
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= now
            or expires_at > now + MAX_SECRET_EXPORT_TRANSACTION_SECONDS
            or isinstance(target_count, bool)
            or not isinstance(target_count, int)
            or not (exact_targets_valid or whole_targets_hidden)
            or len({item.identity for item in frozen_targets}) != len(frozen_targets)
        ):
            raise _fixed_error("secret_export_response_invalid")
        authorization_url = validate_web_url(
            payload.get("authorization_url"),
            code="secret_export_response_invalid",
            allow_query=True,
        )
        parsed = urlsplit(authorization_url)
        expected_path = request.target.route("secrets/export/authorize")
        expected_path = f"{urlsplit(request.target.public_base_url).path.rstrip('/')}{expected_path}"
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=4,
            )
        except ValueError:
            raise _fixed_error("secret_export_response_invalid") from None
        if (
            _origin(authorization_url) != _origin(request.target.public_base_url)
            or parsed.path != expected_path
            or query != {"transaction": [transaction_id]}
        ):
            raise _fixed_error("secret_export_response_invalid")
        return SecretExportStart(
            transaction_id=transaction_id,
            request_digest=request_digest,
            authorization_url=authorization_url,
            required_assurance=assurance,
            expires_at=expires_at,
            target_count=target_count,
            targets=frozen_targets,
        )

    async def exchange(
        self,
        request: SecretExportRequest,
        start: SecretExportStart,
        *,
        code: str,
        code_verifier: str,
    ) -> SecretExportResult:
        status, payload = await self._transport.post(
            url=request.target.url(request.target.route("secrets/export/exchange")),
            payload={
                "transaction_id": start.transaction_id,
                "code": code,
                "code_verifier": code_verifier,
            },
        )
        self._raise_response(status, payload)
        expected_result_fields = {
            "schema",
            "ok",
            "transaction_id",
            "request_digest",
            "target",
            "approval",
            "values",
        } | ({"user_props"} if request.user_props else set())
        if (
            set(payload) != expected_result_fields
            or payload.get("schema") != SECRET_EXPORT_RESULT_SCHEMA
            or payload.get("ok") is not True
        ):
            raise _fixed_error("secret_export_response_invalid")
        if (
            payload.get("transaction_id") != start.transaction_id
            or payload.get("request_digest") != start.request_digest
        ):
            raise _fixed_error("secret_export_response_invalid")
        response_target = payload.get("target")
        if response_target != {
            "tenant": request.target.tenant,
            "project": request.target.project,
        }:
            raise _fixed_error("secret_export_response_invalid")
        approval = payload.get("approval")
        if not isinstance(approval, Mapping) or set(approval) != {
            "assurance",
            "method",
            "verified_at",
        }:
            raise _fixed_error("secret_export_response_invalid")
        assurance = str(approval.get("assurance") or "")
        method = str(approval.get("method") or "").strip()
        verified_at = approval.get("verified_at")
        required_rank = _ASSURANCE_RANK.get(start.required_assurance)
        now = int(time.time())
        if (
            assurance not in _ASSURANCE_RANK
            or required_rank is None
            or _ASSURANCE_RANK[assurance] < required_rank
            or not _METHOD_RE.fullmatch(method)
            or isinstance(verified_at, bool)
            or not isinstance(verified_at, int)
            or verified_at <= 0
            or verified_at > now + MAX_APPROVAL_CLOCK_SKEW_SECONDS
            or now - verified_at > MAX_APPROVAL_EVIDENCE_AGE_SECONDS
        ):
            raise _fixed_error("secret_export_response_invalid")
        raw_values = payload.get("values")
        expected_target_count = start.target_count or len(start.targets)
        if not isinstance(raw_values, list) or (
            len(raw_values) != expected_target_count and not (request.user_props and not raw_values)
        ):
            raise _fixed_error("secret_export_response_invalid")
        values: list[ExportedSecret] = []
        total_value_bytes = 0
        try:
            for raw_value in raw_values:
                if not isinstance(raw_value, Mapping):
                    raise TypeError
                expected_fields = {"scope", "key", "value"}
                if raw_value.get("scope") == "bundle":
                    expected_fields.add("bundle_id")
                elif raw_value.get("scope") == "user":
                    expected_fields.add("user_id")
                    if raw_value.get("bundle_id"):
                        expected_fields.add("bundle_id")
                if set(raw_value) != expected_fields:
                    raise ValueError
                secret_target = ManagementSecretTarget.create(
                    scope=raw_value.get("scope"),
                    key=raw_value.get("key"),
                    bundle_id=raw_value.get("bundle_id", ""),
                    user_id=raw_value.get("user_id", ""),
                )
                value = raw_value.get("value")
                if not isinstance(value, str):
                    raise TypeError
                value_bytes = len(value.encode("utf-8"))
                total_value_bytes += value_bytes
                if (
                    value_bytes > MAX_EXPORTED_SECRET_BYTES
                    or total_value_bytes > MAX_EXPORTED_SECRET_TOTAL_BYTES
                ):
                    raise ValueError
                values.append(ExportedSecret(target=secret_target, value=value))
        except (ManagementCliError, TypeError, UnicodeError, ValueError):
            raise _fixed_error("secret_export_response_invalid") from None
        value_targets = tuple(item.target for item in values)
        if (
            len({item.identity for item in value_targets}) != len(value_targets)
            or value_targets
            != tuple(sorted(value_targets, key=lambda item: item.identity))
            or (
                start.targets
                and tuple(item.identity for item in value_targets)
                != tuple(item.identity for item in start.targets)
            )
            or (
                not start.targets
                and request.request_digest_for(value_targets) != start.request_digest
            )
        ):
            raise _fixed_error("secret_export_response_invalid")
        user_props: list[dict[str, str]] = []
        if request.user_props:
            raw_props = payload.get("user_props")
            fields = {"user_id", "bundle_id", "key", "subsystem", "value_json"}
            if not isinstance(raw_props, list) or len(raw_props) > MAX_BOOTSTRAP_USER_PROPS or any(
                not isinstance(row, Mapping) or set(row) != fields
                or not all(isinstance(row[name], str) for name in fields)
                for row in raw_props
            ):
                raise _fixed_error("secret_export_response_invalid")
            user_props = [dict(row) for row in raw_props]
            identities = [(row["user_id"], row["bundle_id"], row["key"]) for row in user_props]
            if len(set(identities)) != len(identities) or identities != sorted(identities):
                raise _fixed_error("secret_export_response_invalid")
        return SecretExportResult(
            transaction_id=start.transaction_id,
            request_digest=start.request_digest,
            assurance=assurance,
            approval_method=method,
            approval_verified_at=verified_at,
            values=tuple(values),
            user_props=tuple(user_props),
        )


class BrowserSecretExportService:
    def __init__(
        self,
        *,
        client: SecretExportClient,
        browser_opener: BrowserOpener = webbrowser.open,
        callback_factory: CallbackFactory = LoopbackCallbackServer,
    ) -> None:
        self._client = client
        self._browser_opener = browser_opener
        self._callback_factory = callback_factory

    async def export(
        self,
        *,
        target: ManagementTarget,
        targets: Sequence[ManagementSecretTarget],
        selection: str = "",
        user_data: bool = False,
        timeout_seconds: float = 300.0,
        browser_opener: BrowserOpener | None = None,
    ) -> SecretExportResult:
        pkce = generate_pkce()
        callback = self._callback_factory(
            expected_state=pkce.state,
            expected_issuer=_origin(target.public_base_url),
            issuer_required=True,
        )
        try:
            request = SecretExportRequest.create(
                target=target,
                callback_uri=callback.redirect_uri,
                state=pkce.state,
                code_challenge=pkce.code_challenge,
                targets=targets,
                selection=selection,
                user_data=user_data,
            )
            start = await self._client.start(request)
            try:
                opener = browser_opener or self._browser_opener
                opened = bool(opener(start.authorization_url))
            except Exception:  # noqa: BLE001
                raise _fixed_error(
                    "secret_export_browser_open_failed",
                    "KDCube CLI could not open the secret export approval page.",
                ) from None
            if not opened:
                raise _fixed_error(
                    "secret_export_browser_open_failed",
                    "The browser did not accept the secret export approval page.",
                )
            callback_result = await asyncio.to_thread(
                partial(callback.wait, timeout_seconds=timeout_seconds)
            )
            return await self._client.exchange(
                request,
                start,
                code=callback_result.code,
                code_verifier=pkce.code_verifier,
            )
        finally:
            callback.close()


__all__ = [
    "MAX_EXPORTED_SECRET_TOTAL_BYTES",
    "MAX_SECRET_EXPORT_RESPONSE_BYTES",
    "SECRET_EXPORT_ERROR_SCHEMA",
    "SECRET_EXPORT_REQUEST_SCHEMA",
    "SECRET_EXPORT_RESULT_SCHEMA",
    "SECRET_EXPORT_START_SCHEMA",
    "BrowserSecretExportService",
    "ExportedSecret",
    "HttpxSecretExportTransport",
    "ManagementSecretTarget",
    "SecretExportClient",
    "SecretExportRequest",
    "SecretExportResult",
    "SecretExportStart",
    "SecretExportTransport",
]
