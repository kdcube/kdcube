from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from kdcube_cli.management.errors import ManagementCliError
from kdcube_cli.management.validation import validate_web_url

DEPLOYMENT_INSPECT = "kdcube.management.deployment.inspect"
APPLICATION_SURFACES_READ = "kdcube.management.application.surfaces.read"
APPLICATION_RELOAD = "kdcube.management.application.reload"
SECRET_METADATA_READ = "kdcube.management.secret.metadata.read"
SECRET_VALUE_READ = "kdcube.management.secret.value.read"
SECRET_VALUE_WRITE = "kdcube.management.secret.value.write"
SECRET_DELETE = "kdcube.management.secret.delete"
SECRET_OPERATIONS = frozenset(
    {
        SECRET_METADATA_READ,
        SECRET_VALUE_READ,
        SECRET_VALUE_WRITE,
        SECRET_DELETE,
    }
)
USER_PROPS_IMPORT = "kdcube.management.user_props.import"
USER_PROPS_IMPORT_SCHEMA = "kdcube.management.user_props.import.v1"
# Operations whose request digest binds values: KDCube keys it, so the CLI never computes or checks it.
KEYED_DIGEST_OPERATIONS = frozenset({*SECRET_OPERATIONS, USER_PROPS_IMPORT})
DEFAULT_MANAGEMENT_SCOPE = f"{DEPLOYMENT_INSPECT} {APPLICATION_SURFACES_READ}"

MANAGEMENT_REQUEST_SCHEMA = "kdcube.management.request.v1"
MANAGEMENT_RESULT_SCHEMA = "kdcube.management.result.v1"
MANAGEMENT_ERROR_SCHEMA = "kdcube.management.error.v1"

_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CHOICES = frozenset({"allow_once", "allow_always"})
# A request-bound permit (application reload), or a Card grant (Once/Always) for a resource or operation the
# Card does not hold yet (secret operations, W929). Both open the same Connection Hub consent page.
CONSENT_RECOVERY_REASONS = frozenset({"delegated_request_permit_required", "delegated_grant_required"})
_SECRET_SCOPES = frozenset({"platform", "bundle", "user"})
_SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,511}$")
# A platform user id is one key segment; Cognito ids are cognito:<uuid> (W927). The AWS provider encodes it
# for the document name, so ":" is allowed here, and "." (the key separator) still is not.
_SECRET_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_@:-]{0,255}$")
_MAX_SECRET_VALUE_BYTES = 64 * 1024


def _text(value: Any, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ManagementCliError(
            "management_value_invalid",
            "A KDCube management value is invalid.",
        )
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        raise ManagementCliError(
            "management_value_invalid",
            "A KDCube management value is invalid.",
        )
    return candidate


def _optional_text(value: Any, *, maximum: int = 4096) -> str:
    if value is None or value == "":
        return ""
    return _text(value, maximum=maximum)


def _integer(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManagementCliError(
            "management_value_invalid",
            "A KDCube management value is invalid.",
        )
    return value


def _coordinate_segment(value: str) -> str:
    return quote(_text(value, maximum=256), safe="-._~")


def _application_id(value: str) -> str:
    candidate = _text(value, maximum=256)
    if (
        candidate in {".", ".."}
        or any(character in candidate for character in ("*", "/", "\\", "?", "#"))
        or "://" in candidate
        or any(ord(character) < 33 or ord(character) == 127 for character in candidate)
    ):
        raise ManagementCliError(
            "management_application_id_invalid",
            "The KDCube application identifier must name one exact application.",
        )
    return candidate


def _invocation_id(value: str | None = None) -> str:
    candidate = str(value or secrets.token_urlsafe(32)).strip()
    if (
        not candidate
        or len(candidate) > 256
        or any(ord(character) < 33 or ord(character) > 126 for character in candidate)
    ):
        raise ManagementCliError(
            "management_invocation_id_invalid",
            "The KDCube management invocation identifier is invalid.",
        )
    return candidate


def _prop_value(value: Any) -> str:
    """A prop's JSON value as text, re-encoded canonically (the runtime does the same)."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > 64 * 1024:
        raise ManagementCliError("management_value_invalid", "A user prop value is invalid.")
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ManagementCliError("management_value_invalid", "A user prop value is invalid.") from None
    if parsed is None:
        raise ManagementCliError("management_value_invalid", "A user prop value is invalid.")
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _secret_scope(value: str) -> str:
    candidate = _text(value, maximum=32).lower()
    if candidate not in _SECRET_SCOPES:
        raise ManagementCliError(
            "management_secret_scope_invalid",
            "The secret scope must be platform, bundle, or user.",
        )
    return candidate


def _secret_key(value: str, *, scope: str) -> str:
    candidate = _text(value, maximum=512)
    if (
        not _SECRET_KEY_RE.fullmatch(candidate)
        or ".." in candidate
        or candidate.endswith((".", ".__keys"))
        or candidate == "__keys"
        or (scope == "platform" and not candidate.startswith("platform."))
        or (
            scope in {"bundle", "user"}
            and candidate.startswith(("platform.", "bundles.", "users."))
        )
    ):
        raise ManagementCliError(
            "management_secret_key_invalid",
            "The secret key must name one exact non-metadata key.",
        )
    return candidate


def _secret_value(value: Any) -> str:
    if (
        not isinstance(value, str)
        or value == ""
        or len(value.encode("utf-8")) > _MAX_SECRET_VALUE_BYTES
    ):
        raise ManagementCliError(
            "management_secret_value_invalid",
            "The secret value must be a non-empty string no larger than 65536 UTF-8 bytes.",
        )
    return value


@dataclass(frozen=True)
class ManagementSecretTarget:
    scope: str
    key: str
    bundle_id: str = ""
    user_id: str = ""

    @classmethod
    def create(
        cls,
        *,
        scope: str,
        key: str,
        bundle_id: str = "",
        user_id: str = "",
    ) -> ManagementSecretTarget:
        normalized_scope = _secret_scope(scope)
        normalized_key = _secret_key(key, scope=normalized_scope)
        normalized_bundle = ""
        normalized_user = ""
        if normalized_scope == "bundle":
            normalized_bundle = _application_id(bundle_id)
            if user_id:
                raise ManagementCliError(
                    "management_secret_scope_invalid",
                    "A user identifier is valid only for a user secret.",
                )
        elif normalized_scope == "user":
            normalized_user = _text(user_id, maximum=256)
            if (
                not _SECRET_USER_RE.fullmatch(normalized_user)
                or normalized_user in {"_", "__keys"}
            ):
                raise ManagementCliError(
                    "management_secret_user_invalid",
                    "The user identifier must name one exact user.",
                )
            if bundle_id:
                normalized_bundle = _application_id(bundle_id)
        elif bundle_id or user_id:
            raise ManagementCliError(
                "management_secret_scope_invalid",
                "Bundle and user identifiers are valid only for their secret scopes.",
            )
        return cls(
            scope=normalized_scope,
            key=normalized_key,
            bundle_id=normalized_bundle,
            user_id=normalized_user,
        )

    @classmethod
    def from_provider_key(cls, value: str) -> ManagementSecretTarget:
        key = _text(value, maximum=1024)
        if key.startswith("platform."):
            return cls.create(scope="platform", key=key)
        if key.startswith("bundles.") and ".secrets." in key:
            bundle_id, tail = key[len("bundles.") :].split(".secrets.", 1)
            return cls.create(scope="bundle", bundle_id=bundle_id, key=tail)
        if key.startswith("users."):
            user_path = key[len("users.") :]
            bundle_marker = user_path.find(".bundles.")
            secrets_marker = user_path.find(".secrets.")
            if bundle_marker >= 0 and (
                secrets_marker < 0 or bundle_marker < secrets_marker
            ):
                user_id, bundle_path = user_path.split(".bundles.", 1)
                if ".secrets." not in bundle_path:
                    raise ManagementCliError(
                        "management_secret_key_invalid",
                        "The user bundle secret identity is invalid.",
                    )
                bundle_id, tail = bundle_path.split(".secrets.", 1)
                return cls.create(
                    scope="user",
                    user_id=user_id,
                    bundle_id=bundle_id,
                    key=tail,
                )
            if ".secrets." in user_path:
                user_id, tail = user_path.split(".secrets.", 1)
                return cls.create(scope="user", user_id=user_id, key=tail)
        raise ManagementCliError(
            "management_secret_key_invalid",
            "The secret identity must use platform, bundle, or user scope.",
        )

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.scope, self.user_id, self.bundle_id, self.key)

    @property
    def provider_key(self) -> str:
        if self.scope == "bundle":
            return f"bundles.{self.bundle_id}.secrets.{self.key}"
        if self.scope == "user":
            if self.bundle_id:
                return (
                    f"users.{self.user_id}.bundles.{self.bundle_id}.secrets."
                    f"{self.key}"
                )
            return f"users.{self.user_id}.secrets.{self.key}"
        return self.key

    def to_dict(self) -> dict[str, str]:
        result = {"scope": self.scope, "key": self.key}
        if self.bundle_id:
            result["bundle_id"] = self.bundle_id
        if self.user_id:
            result["user_id"] = self.user_id
        return result


def _secret_body(
    *,
    scope: str,
    key: str,
    bundle_id: str = "",
    user_id: str = "",
    value: Any = None,
    include_value: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = ManagementSecretTarget.create(
        scope=scope,
        key=key,
        bundle_id=bundle_id,
        user_id=user_id,
    ).to_dict()
    if include_value:
        body["value"] = _secret_value(value)
    return body


@dataclass(frozen=True)
class ManagementTarget:
    public_base_url: str
    tenant: str
    project: str
    session_target_key: str

    @classmethod
    def create(
        cls,
        *,
        public_base_url: str,
        tenant: str,
        project: str,
        session_target_key: str | None = None,
    ) -> ManagementTarget:
        base = validate_web_url(
            public_base_url,
            code="management_endpoint_invalid",
            allow_query=False,
        ).rstrip("/")
        normalized_tenant = _text(tenant, maximum=256)
        normalized_project = _text(project, maximum=256)
        return cls(
            public_base_url=base,
            tenant=normalized_tenant,
            project=normalized_project,
            session_target_key=(
                _text(session_target_key, maximum=8192)
                if session_target_key
                else f"endpoint:{base}:{normalized_tenant}:{normalized_project}"
            ),
        )

    @property
    def resource(self) -> str:
        return (
            "urn:kdcube:management:deployment:"
            f"{_coordinate_segment(self.tenant)}:{_coordinate_segment(self.project)}"
        )

    def secret_resource(self, body: Mapping[str, Any]) -> str:
        secret_target = ManagementSecretTarget.create(
            scope=body.get("scope"),
            key=body.get("key"),
            bundle_id=body.get("bundle_id", ""),
            user_id=body.get("user_id", ""),
        )
        if secret_target.scope == "user":
            scope_id = secret_target.user_id
            if secret_target.bundle_id:
                scope_id += f"~{secret_target.bundle_id}"
        else:
            scope_id = secret_target.bundle_id or "_"
        encoded = (
            quote(self.tenant, safe="-._~"),
            quote(self.project, safe="-._~"),
            quote(secret_target.scope, safe="-._~"),
            quote(scope_id, safe="-._~@"),
            quote(secret_target.key, safe="-._~@"),
        )
        return "urn:kdcube:management:secret:" + ":".join(encoded)

    @property
    def protected_resource_metadata_url(self) -> str:
        return (
            f"{self.public_base_url}/api/integrations/management/v1/"
            ".well-known/oauth-protected-resource"
        )

    @property
    def user_props_resource(self) -> str:
        encoded = (quote(self.tenant, safe="-._~"), quote(self.project, safe="-._~"))
        return "urn:kdcube:management:user-props:" + ":".join(encoded)

    def route(self, suffix: str) -> str:
        return f"/api/integrations/management/v1/{suffix.lstrip('/')}"

    def url(self, path: str) -> str:
        return f"{self.public_base_url}{path}"

    def is_consent_path(self, path: str) -> bool:
        base_path = urlsplit(self.public_base_url).path.rstrip("/")
        expected = (
            f"{base_path}/api/integrations/bundles/"
            f"{quote(self.tenant, safe='')}/{quote(self.project, safe='')}/"
            "connection-hub%401-0/widgets/connections_settings"
        )
        return _decoded_path_segments(path) == _decoded_path_segments(expected)


@dataclass(frozen=True)
class ManagementRequest:
    target: ManagementTarget
    operation: str
    method: str
    path: str
    application_id: str
    invocation_id: str
    resource: str
    body: Mapping[str, Any] = field(repr=False)

    @classmethod
    def inspect(
        cls,
        target: ManagementTarget,
        *,
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        return cls(
            target=target,
            operation=DEPLOYMENT_INSPECT,
            method="GET",
            path=target.route("deployment"),
            application_id="",
            invocation_id=_invocation_id(invocation_id),
            resource=target.resource,
            body={},
        )

    @classmethod
    def surfaces(
        cls,
        target: ManagementTarget,
        *,
        application_id: str,
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        exact = _application_id(application_id)
        segment = quote(exact, safe="-._~")
        return cls(
            target=target,
            operation=APPLICATION_SURFACES_READ,
            method="GET",
            path=target.route(f"applications/{segment}/surfaces"),
            application_id=exact,
            invocation_id=_invocation_id(invocation_id),
            resource=target.resource,
            body={},
        )

    @classmethod
    def reload(
        cls,
        target: ManagementTarget,
        *,
        application_id: str,
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        exact = _application_id(application_id)
        segment = quote(exact, safe="-._~")
        return cls(
            target=target,
            operation=APPLICATION_RELOAD,
            method="POST",
            path=target.route(f"applications/{segment}/reload"),
            application_id=exact,
            invocation_id=_invocation_id(invocation_id),
            resource=target.resource,
            body={},
        )

    @classmethod
    def secret_metadata(
        cls,
        target: ManagementTarget,
        *,
        scope: str,
        key: str,
        bundle_id: str = "",
        user_id: str = "",
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        return cls._secret(
            target,
            operation=SECRET_METADATA_READ,
            path="secrets/metadata/read",
            scope=scope,
            key=key,
            bundle_id=bundle_id,
            user_id=user_id,
            invocation_id=invocation_id,
        )

    @classmethod
    def secret_read(
        cls,
        target: ManagementTarget,
        *,
        scope: str,
        key: str,
        bundle_id: str = "",
        user_id: str = "",
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        return cls._secret(
            target,
            operation=SECRET_VALUE_READ,
            path="secrets/value/read",
            scope=scope,
            key=key,
            bundle_id=bundle_id,
            user_id=user_id,
            invocation_id=invocation_id,
        )

    @classmethod
    def secret_write(
        cls,
        target: ManagementTarget,
        *,
        scope: str,
        key: str,
        value: str,
        bundle_id: str = "",
        user_id: str = "",
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        return cls._secret(
            target,
            operation=SECRET_VALUE_WRITE,
            path="secrets/value/write",
            scope=scope,
            key=key,
            bundle_id=bundle_id,
            user_id=user_id,
            value=value,
            include_value=True,
            invocation_id=invocation_id,
        )

    @classmethod
    def secret_delete(
        cls,
        target: ManagementTarget,
        *,
        scope: str,
        key: str,
        bundle_id: str = "",
        user_id: str = "",
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        return cls._secret(
            target,
            operation=SECRET_DELETE,
            path="secrets/delete",
            scope=scope,
            key=key,
            bundle_id=bundle_id,
            user_id=user_id,
            invocation_id=invocation_id,
        )

    @classmethod
    def _secret(
        cls,
        target: ManagementTarget,
        *,
        operation: str,
        path: str,
        scope: str,
        key: str,
        bundle_id: str,
        user_id: str,
        invocation_id: str | None,
        value: Any = None,
        include_value: bool = False,
    ) -> ManagementRequest:
        body = _secret_body(
            scope=scope,
            key=key,
            bundle_id=bundle_id,
            user_id=user_id,
            value=value,
            include_value=include_value,
        )
        return cls(
            target=target,
            operation=operation,
            method="POST",
            path=target.route(path),
            application_id="",
            invocation_id=_invocation_id(invocation_id),
            resource=target.secret_resource(body),
            body=body,
        )

    @classmethod
    def user_props_import(
        cls,
        target: ManagementTarget,
        *,
        rows: list[Mapping[str, Any]],
        invocation_id: str | None = None,
    ) -> ManagementRequest:
        """W890: one bounded batch of exported user props; KDCube inserts, never overwrites, and returns
        counts only."""
        exact = [
            {name: _text(row.get(name), maximum=512) for name in ("user_id", "bundle_id", "key", "subsystem")}
            | {"value_json": _prop_value(row.get("value_json"))}
            for row in rows
        ]
        if not exact:
            raise ManagementCliError("management_value_invalid", "A user props import needs at least one row.")
        exact.sort(key=lambda row: (row["user_id"], row["bundle_id"], row["key"]))
        return cls(
            target=target,
            operation=USER_PROPS_IMPORT,
            method="POST",
            path=target.route("user-props/import"),
            application_id="",
            invocation_id=_invocation_id(invocation_id),
            resource=target.user_props_resource,
            body={"schema": USER_PROPS_IMPORT_SCHEMA, "rows": exact},
        )

    @property
    def target_key(self) -> str:
        return self.target.session_target_key

    @property
    def url(self) -> str:
        return self.target.url(self.path)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "body": dict(self.body),
            "operation": self.operation,
            "resource": self.resource,
            "schema": MANAGEMENT_REQUEST_SCHEMA,
        }

    @property
    def request_digest(self) -> str:
        # Secret-operation digests are keyed and calculated only by KDCube.
        # Returning an unkeyed value digest here would create an offline
        # verifier for low-entropy provider credentials.
        if self.operation in KEYED_DIGEST_OPERATIONS:
            return ""
        encoded = json.dumps(
            self.canonical_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ConsentRecovery:
    authorization_url: str
    access_id: str
    resource: str
    operation: str
    application_id: str
    invocation_id: str
    request_digest: str
    card_revision: int
    catalog_version: str
    expires_at: int
    choices: tuple[str, ...]
    reason: str = "delegated_request_permit_required"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        request: ManagementRequest,
    ) -> ConsentRecovery:
        if value.get("type") != "consent_required" or value.get("reason") not in (
            CONSENT_RECOVERY_REASONS
        ):
            raise ManagementCliError(
                "management_recovery_invalid",
                "The KDCube management recovery response is invalid.",
            )
        authorization_url = validate_web_url(
            value.get("authorization_url"),
            code="management_recovery_invalid",
        )
        expected_origin = urlsplit(request.target.public_base_url)
        recovery_origin = urlsplit(authorization_url)
        if (recovery_origin.scheme, recovery_origin.netloc) != (
            expected_origin.scheme,
            expected_origin.netloc,
        ):
            raise ManagementCliError(
                "management_recovery_origin_mismatch",
                "The consent page belongs to a different KDCube endpoint.",
            )
        if not request.target.is_consent_path(recovery_origin.path):
            raise ManagementCliError(
                "management_recovery_path_mismatch",
                "The consent page is not the selected KDCube management surface.",
            )
        choices_raw = value.get("choices")
        if not isinstance(choices_raw, list):
            raise ManagementCliError(
                "management_recovery_invalid",
                "The KDCube management recovery response is invalid.",
            )
        choices = tuple(str(item) for item in choices_raw)
        card_revision = _integer(value.get("card_revision"), minimum=1)
        expires_at = _integer(value.get("expires_at"), minimum=1)
        recovery = cls(
            authorization_url=authorization_url,
            access_id=_text(value.get("access_id"), maximum=256),
            resource=_text(value.get("resource"), maximum=8192),
            operation=_text(value.get("operation"), maximum=256),
            application_id=(
                _application_id(value.get("application_id"))
                if value.get("application_id")
                else ""
            ),
            invocation_id=_text(value.get("invocation_id"), maximum=256),
            request_digest=_text(value.get("request_digest"), maximum=64),
            card_revision=card_revision,
            # A request permit is bound to the catalog it was approved under; a Card grant (W929) is not.
            catalog_version=(
                _text(value.get("catalog_version"), maximum=256)
                if value.get("reason") == "delegated_request_permit_required" or value.get("catalog_version")
                else ""
            ),
            expires_at=expires_at,
            choices=choices,
            reason=str(value.get("reason")),
        )
        digest_matches = (
            request.operation in KEYED_DIGEST_OPERATIONS
            or recovery.request_digest == request.request_digest
        )
        if (
            recovery.resource != request.resource
            or recovery.operation != request.operation
            or recovery.application_id != request.application_id
            or recovery.invocation_id != request.invocation_id
            or not digest_matches
            or not _HEX_DIGEST_RE.fullmatch(recovery.request_digest)
            or not recovery.choices
            or any(choice not in _CHOICES for choice in recovery.choices)
            or len(set(recovery.choices)) != len(recovery.choices)
        ):
            raise ManagementCliError(
                "management_recovery_request_mismatch",
                "The consent response does not match the original management request.",
            )
        return recovery


def _decoded_path_segments(value: str) -> tuple[str, ...]:
    return tuple(unquote(segment) for segment in value.split("/"))


@dataclass(frozen=True)
class ManagementResult:
    operation: str
    resource: str
    invocation_id: str
    replay: bool
    authority: Mapping[str, Any]
    result: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        request: ManagementRequest,
    ) -> ManagementResult:
        target = value.get("target")
        invocation = value.get("invocation")
        authority = value.get("authority")
        result = value.get("result")
        if (
            value.get("schema") != MANAGEMENT_RESULT_SCHEMA
            or value.get("ok") is not True
            or value.get("operation") != request.operation
            or value.get("resource") != request.resource
            or not isinstance(target, Mapping)
            or target.get("tenant") != request.target.tenant
            or target.get("project") != request.target.project
            or not isinstance(invocation, Mapping)
            or invocation.get("id") != request.invocation_id
            or not isinstance(invocation.get("replay"), bool)
            or not isinstance(authority, Mapping)
            or not isinstance(result, Mapping)
        ):
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned an invalid management result.",
            )
        return cls(
            operation=request.operation,
            resource=request.resource,
            invocation_id=request.invocation_id,
            replay=bool(invocation["replay"]),
            authority=_public_authority(authority),
            result=_public_result(result, request=request),
        )


@dataclass(frozen=True)
class ManagementDenial:
    status: int
    code: str
    retryable: bool
    recovery: ConsentRecovery | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        status: int,
        request: ManagementRequest,
    ) -> ManagementDenial:
        target = value.get("target")
        error = value.get("error")
        if (
            value.get("schema") != MANAGEMENT_ERROR_SCHEMA
            or value.get("ok") is not False
            or value.get("operation") != request.operation
            or value.get("resource") != request.resource
            or value.get("invocation_id") != request.invocation_id
            or not isinstance(target, Mapping)
            or target.get("tenant") != request.target.tenant
            or target.get("project") != request.target.project
            or not isinstance(error, Mapping)
            or not isinstance(error.get("retryable"), bool)
        ):
            raise ManagementCliError(
                "management_error_invalid",
                "KDCube returned an invalid management error.",
            )
        code = str(error.get("code") or "")
        if not _ERROR_CODE_RE.fullmatch(code):
            raise ManagementCliError(
                "management_error_invalid",
                "KDCube returned an invalid management error.",
            )
        recovery_raw = value.get("recovery")
        recovery = (
            ConsentRecovery.from_mapping(recovery_raw, request=request)
            if isinstance(recovery_raw, Mapping)
            else None
        )
        return cls(
            status=int(status),
            code=code,
            retryable=bool(error["retryable"]),
            recovery=recovery,
        )


def _public_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in (
        "decision_id",
        "caller_profile",
        "access_id",
        "card_catalog_version",
        "active_catalog_version",
    ):
        if key in value:
            output[key] = _text(value[key], maximum=512)
    for key in (
        "card_revision",
        "invocation_policy_revision",
        "request_permit_revision",
    ):
        if key in value:
            output[key] = _integer(value[key], minimum=1)
    return output


def _public_result(
    value: Mapping[str, Any],
    *,
    request: ManagementRequest,
) -> dict[str, Any]:
    if request.operation == DEPLOYMENT_INSPECT:
        return _deployment_result(value)
    if request.operation == APPLICATION_SURFACES_READ:
        return _surfaces_result(value, application_id=request.application_id)
    if request.operation == APPLICATION_RELOAD:
        return _reload_result(value, application_id=request.application_id)
    if request.operation in {
        SECRET_METADATA_READ,
        SECRET_VALUE_READ,
        SECRET_VALUE_WRITE,
        SECRET_DELETE,
    }:
        return _secret_result(value, request=request)
    if request.operation == USER_PROPS_IMPORT:
        return _user_props_import_result(value, request=request)
    raise ManagementCliError(
        "management_result_invalid",
        "KDCube returned an invalid management result.",
    )


def _user_props_import_result(value: Mapping[str, Any], *, request: ManagementRequest) -> dict[str, Any]:
    counts = {name: value.get(name) for name in ("inserted", "equal", "different", "received")}
    if (
        value.get("state") != "imported"
        or any(type(count) is not int or count < 0 for count in counts.values())
        or counts["received"] != len(request.body.get("rows") or ())
        or counts["inserted"] + counts["equal"] + counts["different"] != counts["received"]
    ):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid user props import receipt.",
        )
    return {**counts, "state": "imported"}


def _deployment_result(value: Mapping[str, Any]) -> dict[str, Any]:
    readiness = value.get("readiness")
    applications = value.get("applications")
    if readiness not in {"ready", "not_ready"} or not isinstance(applications, list):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid deployment result.",
        )
    if len(applications) > 4096:
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid deployment result.",
        )
    projected: list[dict[str, Any]] = []
    for item in applications:
        if not isinstance(item, Mapping):
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned an invalid deployment result.",
            )
        application_id = _application_id(item.get("application_id"))
        state = item.get("preparation_state")
        declared = item.get("declared")
        readiness_required = item.get("readiness_required")
        if (
            state not in {"ready", "preparing", "failed", "unknown"}
            or not isinstance(declared, bool)
            or not isinstance(readiness_required, bool)
        ):
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned an invalid deployment result.",
            )
        projected.append(
            {
                "application_id": application_id,
                "declared": declared,
                "preparation_state": state,
                "generation": _optional_text(item.get("generation")),
                "readiness_required": readiness_required,
            }
        )
    if [item["application_id"] for item in projected] != sorted(
        item["application_id"] for item in projected
    ):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an unsorted deployment application list.",
        )
    return {
        "platform_release": _optional_text(value.get("platform_release")),
        "readiness": readiness,
        "applications": projected,
    }


def _surfaces_result(
    value: Mapping[str, Any],
    *,
    application_id: str,
) -> dict[str, Any]:
    if value.get("application_id") != application_id or not isinstance(
        value.get("surfaces"), Mapping
    ):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid application-surface result.",
        )
    surfaces = value["surfaces"]
    specifications = {
        "api": ("alias", "method", "path"),
        "mcp": ("alias", "transport", "path"),
        "widgets": ("alias", "path"),
        "jobs": ("alias",),
        "messaging": ("kind",),
    }
    projected: dict[str, list[dict[str, str]]] = {}
    for family, fields in specifications.items():
        rows = surfaces.get(family, [])
        if not isinstance(rows, list) or len(rows) > 4096:
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned an invalid application-surface result.",
            )
        projected[family] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ManagementCliError(
                    "management_result_invalid",
                    "KDCube returned an invalid application-surface result.",
                )
            selected = {field: _text(row.get(field)) for field in fields}
            if "path" in selected and not selected["path"].startswith("/"):
                raise ManagementCliError(
                    "management_result_invalid",
                    "KDCube returned an invalid application-surface path.",
                )
            projected[family].append(selected)
    return {"application_id": application_id, "surfaces": projected}


def _reload_result(
    value: Mapping[str, Any],
    *,
    application_id: str,
) -> dict[str, Any]:
    changed = value.get("changed_application_ids")
    if (
        value.get("application_id") != application_id
        or value.get("state") != "completed"
        or not isinstance(changed, list)
        or len(changed) > 4096
    ):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid application-reload result.",
        )
    changed_ids = [_application_id(item) for item in changed]
    return {
        "application_id": application_id,
        "state": "completed",
        "changed_application_ids": changed_ids,
        "generation": _optional_text(value.get("generation")),
    }


def _secret_result(
    value: Mapping[str, Any],
    *,
    request: ManagementRequest,
) -> dict[str, Any]:
    expected = dict(request.body)
    scope = expected["scope"]
    key = expected["key"]
    if value.get("scope") != scope or value.get("key") != key:
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned a secret result for another target.",
        )
    bundle_id = expected.get("bundle_id", "")
    if value.get("bundle_id", "") != bundle_id:
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned a secret result for another target.",
        )
    projected: dict[str, Any] = {"scope": scope, "key": key}
    if bundle_id:
        projected["bundle_id"] = bundle_id

    if request.operation == SECRET_METADATA_READ:
        exists = value.get("exists")
        writable = value.get("writable")
        if not isinstance(exists, bool) or not isinstance(writable, bool):
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned invalid secret metadata.",
            )
        projected.update(
            {
                "exists": exists,
                "provider": _text(value.get("provider"), maximum=128),
                "writable": writable,
            }
        )
        return projected
    if request.operation == SECRET_VALUE_READ:
        projected["value"] = _secret_value(value.get("value"))
        return projected
    if request.operation == SECRET_VALUE_WRITE:
        if value.get("state") != "stored" or not isinstance(value.get("created"), bool):
            raise ManagementCliError(
                "management_result_invalid",
                "KDCube returned an invalid secret-write receipt.",
            )
        projected.update(
            {
                "created": value["created"],
                "provider": _text(value.get("provider"), maximum=128),
                "state": "stored",
            }
        )
        return projected
    if value.get("state") != "deleted" or not isinstance(value.get("existed"), bool):
        raise ManagementCliError(
            "management_result_invalid",
            "KDCube returned an invalid secret-delete receipt.",
        )
    projected.update(
        {
            "existed": value["existed"],
            "provider": _text(value.get("provider"), maximum=128),
            "state": "deleted",
        }
    )
    return projected
