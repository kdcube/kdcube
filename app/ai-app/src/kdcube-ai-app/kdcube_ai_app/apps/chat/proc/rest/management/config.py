# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-owned configuration for delegated KDCube management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on", "enabled"}


def _positive_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _configured_bool(
    value: Any,
    *,
    name: str,
    section: str = "secret export",
) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{section} {name} policy is invalid")
    return value


def _configured_int(
    value: Any,
    *,
    name: str,
    section: str = "secret export",
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{section} {name} policy is invalid")
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: Any, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"human approval {name} must be a list")
    rows = tuple(_text(item) for item in value)
    if any(not item for item in rows) or len(set(rows)) != len(rows):
        raise ValueError(f"human approval {name} is invalid")
    return rows


def _origin_list(value: Any, *, name: str) -> tuple[str, ...]:
    rows = tuple(item.rstrip("/") for item in _string_list(value, name=name))
    if len(set(rows)) != len(rows):
        raise ValueError(f"human approval {name} is invalid")
    return rows


@dataclass(frozen=True)
class CognitoFreshAuthenticationProvider:
    alias: str
    region: str
    user_pool_id: str
    app_client_id: str
    hosted_ui_domain: str

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"


@dataclass(frozen=True)
class GoogleFreshAuthenticationConfig:
    client_id: str
    jwks_url: str


@dataclass(frozen=True)
class WebAuthnApprovalConfig:
    enabled: bool
    rp_id: str
    rp_name: str
    allowed_origins: tuple[str, ...]
    credential_policy: str
    trusted_attestation_root_files: dict[str, tuple[str, ...]]
    timeout_milliseconds: int
    max_credentials_per_user: int

    def validate(self) -> None:
        if self.credential_policy not in {
            "verified_passkey",
            "single_device",
            "attested_hardware",
        }:
            raise ValueError("human approval WebAuthn credential policy is invalid")
        if not self.rp_name or len(self.rp_name) > 128:
            raise ValueError("human approval WebAuthn RP name is invalid")
        if self.rp_id and (
            ":" in self.rp_id or "/" in self.rp_id or len(self.rp_id) > 253
        ):
            raise ValueError("human approval WebAuthn RP id is invalid")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or (
                    parsed.scheme == "http"
                    and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
            ):
                raise ValueError("human approval WebAuthn origin is invalid")
        if not 10000 <= self.timeout_milliseconds <= 300000:
            raise ValueError("human approval WebAuthn timeout is invalid")
        if not 1 <= self.max_credentials_per_user <= 32:
            raise ValueError("human approval WebAuthn credential limit is invalid")
        for fmt, paths in self.trusted_attestation_root_files.items():
            if not fmt or not paths:
                raise ValueError(
                    "human approval WebAuthn attestation roots are invalid"
                )
            for path in paths:
                if not Path(path).is_absolute():
                    raise ValueError(
                        "human approval WebAuthn attestation roots must be absolute paths"
                    )
        if (
            self.credential_policy == "attested_hardware"
            and not self.trusted_attestation_root_files
        ):
            raise ValueError(
                "attested hardware requires configured attestation trust roots"
            )


@dataclass(frozen=True)
class HumanApprovalConfig:
    """Descriptor-owned adapters for stronger one-use human approval."""

    fresh_authentication_provider: str
    challenge_ttl_seconds: int
    http_timeout_seconds: float
    cognito_managed_login: bool
    cognito_providers: tuple[CognitoFreshAuthenticationProvider, ...]
    google: GoogleFreshAuthenticationConfig
    webauthn: WebAuthnApprovalConfig

    @classmethod
    def from_settings(cls, settings: Any) -> HumanApprovalConfig:
        plain = settings.plain
        try:
            platform = settings.connection_hub_platform_auth_config()
        except Exception:
            platform = {}
        platform = _mapping(platform)
        platform_provider = _mapping(platform.get("provider"))

        raw_cognito = _mapping(plain("management.human_approval.cognito", default={}))
        hosted_fallback = _text(
            raw_cognito.get("hosted_ui_domain")
            or platform.get("hosted_ui_domain")
            or _mapping(platform_provider.get("authenticator")).get("hosted_ui_domain")
        )
        providers: list[CognitoFreshAuthenticationProvider] = []
        for provider in list(
            getattr(getattr(settings, "AUTH", None), "COGNITO_TRUSTED_PROVIDERS", None)
            or []
        ):
            hosted = _text(getattr(provider, "hosted_ui_domain", None))
            if not hosted and not providers:
                hosted = hosted_fallback
            candidate = CognitoFreshAuthenticationProvider(
                alias=_text(getattr(provider, "alias", None)),
                region=_text(getattr(provider, "region", None)),
                user_pool_id=_text(getattr(provider, "user_pool_id", None)),
                app_client_id=_text(getattr(provider, "app_client_id", None)),
                hosted_ui_domain=hosted.rstrip("/"),
            )
            if all(
                (
                    candidate.alias,
                    candidate.region,
                    candidate.user_pool_id,
                    candidate.app_client_id,
                    candidate.hosted_ui_domain,
                )
            ):
                providers.append(candidate)

        raw_google = _mapping(plain("management.human_approval.google", default={}))
        resolved_authenticator = _mapping(platform.get("login_authenticator"))
        authenticator_provider = _mapping(resolved_authenticator.get("provider"))
        authenticator = _mapping(authenticator_provider.get("authenticator"))
        google = GoogleFreshAuthenticationConfig(
            client_id=_text(
                raw_google.get("client_id")
                or authenticator.get("client_id")
                or authenticator.get("audience")
            ),
            jwks_url=_text(
                raw_google.get("jwks_url")
                or authenticator.get("jwks_url")
                or "https://www.googleapis.com/oauth2/v3/certs"
            ),
        )

        raw_webauthn = _mapping(plain("management.human_approval.webauthn", default={}))
        raw_roots = _mapping(raw_webauthn.get("trusted_attestation_root_files"))
        roots = {
            _text(fmt): _string_list(paths, name="attestation root files")
            for fmt, paths in raw_roots.items()
        }
        webauthn = WebAuthnApprovalConfig(
            enabled=_configured_bool(
                raw_webauthn.get("enabled", False),
                name="WebAuthn enabled",
                section="human approval",
            ),
            rp_id=_text(raw_webauthn.get("rp_id")).lower(),
            rp_name=_text(raw_webauthn.get("rp_name") or "KDCube"),
            allowed_origins=_origin_list(
                raw_webauthn.get("allowed_origins", []),
                name="WebAuthn origins",
            ),
            credential_policy=_text(
                raw_webauthn.get("credential_policy") or "verified_passkey"
            ).lower(),
            trusted_attestation_root_files=roots,
            timeout_milliseconds=_configured_int(
                raw_webauthn.get("timeout_milliseconds", 60000),
                name="WebAuthn timeout",
                section="human approval",
            ),
            max_credentials_per_user=_configured_int(
                raw_webauthn.get("max_credentials_per_user", 8),
                name="WebAuthn credential limit",
                section="human approval",
            ),
        )

        result = cls(
            fresh_authentication_provider=_text(
                plain(
                    "management.human_approval.fresh_authentication_provider",
                    default="auto",
                )
            ).lower(),
            challenge_ttl_seconds=_configured_int(
                plain(
                    "management.human_approval.challenge_ttl_seconds",
                    default=180,
                ),
                name="challenge TTL",
                section="human approval",
            ),
            http_timeout_seconds=_positive_float(
                plain(
                    "management.human_approval.http_timeout_seconds",
                    default=10,
                ),
                10,
            ),
            cognito_managed_login=_configured_bool(
                raw_cognito.get("managed_login", False),
                name="Cognito managed login",
                section="human approval",
            ),
            cognito_providers=tuple(providers),
            google=google,
            webauthn=webauthn,
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.fresh_authentication_provider not in {
            "auto",
            "cognito",
            "google",
        }:
            raise ValueError("human approval fresh-authentication provider is invalid")
        if not 30 <= self.challenge_ttl_seconds <= 900:
            raise ValueError("human approval challenge TTL is invalid")
        if not 1 <= self.http_timeout_seconds <= 30:
            raise ValueError("human approval HTTP timeout is invalid")
        for provider in self.cognito_providers:
            parsed = urlsplit(provider.hosted_ui_domain)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("human approval Cognito domain is invalid")
        if self.google.client_id and len(self.google.client_id) > 512:
            raise ValueError("human approval Google client id is invalid")
        google_jwks = urlsplit(self.google.jwks_url)
        if (
            google_jwks.scheme != "https"
            or not google_jwks.hostname
            or google_jwks.username is not None
            or google_jwks.password is not None
        ):
            raise ValueError("human approval Google JWKS URL is invalid")
        self.webauthn.validate()


@dataclass(frozen=True)
class HumanSecretExportConfig:
    """Descriptor-owned policy for owner-performed plaintext export."""

    enabled: bool
    required_assurance: str
    max_evidence_age_seconds: int
    transaction_ttl_seconds: int
    consumed_tombstone_seconds: int
    max_targets: int
    max_total_value_bytes: int

    @classmethod
    def from_settings(cls, settings: Any) -> HumanSecretExportConfig:
        plain = settings.plain
        return cls(
            enabled=_configured_bool(
                plain("management.secret_export.enabled", default=False),
                name="enabled",
            ),
            required_assurance=_text(
                plain(
                    "management.secret_export.required_assurance",
                    default="session_confirmation",
                )
            ).lower(),
            max_evidence_age_seconds=_configured_int(
                plain(
                    "management.secret_export.max_evidence_age_seconds",
                    default=300,
                ),
                name="evidence age",
            ),
            transaction_ttl_seconds=_configured_int(
                plain(
                    "management.secret_export.transaction_ttl_seconds",
                    default=180,
                ),
                name="transaction TTL",
            ),
            consumed_tombstone_seconds=_configured_int(
                plain(
                    "management.secret_export.consumed_tombstone_seconds",
                    default=600,
                ),
                name="consumed tombstone",
            ),
            max_targets=_configured_int(
                plain("management.secret_export.max_targets", default=4096),
                name="target count",
            ),
            max_total_value_bytes=_configured_int(
                plain(
                    "management.secret_export.max_total_value_bytes",
                    default=1048576,
                ),
                name="result bytes",
            ),
        )

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("secret export enabled policy is invalid")
        if self.required_assurance not in {
            "session_confirmation",
            "fresh_authentication",
            "user_verification",
        }:
            raise ValueError("secret export assurance policy is invalid")
        limits = (
            ("evidence age", self.max_evidence_age_seconds, 900),
            ("transaction TTL", self.transaction_ttl_seconds, 900),
            ("consumed tombstone", self.consumed_tombstone_seconds, 86400),
            ("target count", self.max_targets, 4096),
            ("result bytes", self.max_total_value_bytes, 8 * 1024 * 1024),
        )
        for name, value, maximum in limits:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise ValueError(f"secret export {name} policy is invalid")


@dataclass(frozen=True)
class DelegatedManagementConfig:
    enabled: bool
    tenant: str
    project: str
    connection_hub_app_id: str
    service_id: str
    service_secret_ref: str
    admission_url: str
    admission_timeout_seconds: float = 10.0
    effect_pending_seconds: float = 120.0

    @classmethod
    def from_settings(cls, settings: Any) -> DelegatedManagementConfig:
        plain = settings.plain
        return cls(
            enabled=_bool(plain("management.delegated.enabled", default=False)),
            tenant=_text(getattr(settings, "TENANT", "")),
            project=_text(getattr(settings, "PROJECT", "")),
            connection_hub_app_id=_text(
                plain(
                    "management.delegated.connection_hub.app_id",
                    default="connection-hub@1-0",
                )
            ),
            service_id=_text(
                plain(
                    "management.delegated.connection_hub.service_id",
                    default="kdcube-management",
                )
            ),
            service_secret_ref=_text(
                plain(
                    "management.delegated.connection_hub.service_secret_ref",
                    default=(
                        "connections.delegated_credentials.admission.services."
                        "kdcube-management.signing_secret"
                    ),
                )
            ),
            admission_url=_text(
                plain(
                    "management.delegated.connection_hub.admission_url",
                    default="",
                )
            ),
            admission_timeout_seconds=_positive_float(
                plain(
                    "management.delegated.connection_hub.timeout_seconds",
                    default=10,
                ),
                10.0,
            ),
            effect_pending_seconds=_positive_float(
                plain("management.delegated.effect_pending_seconds", default=120),
                120.0,
            ),
        )

    def resolved_admission_url(self, settings: Any) -> str:
        if self.admission_url:
            return self.admission_url
        port = int(getattr(settings, "CHAT_PROCESSOR_PORT", None) or 8020)
        parts = (
            quote(self.tenant, safe="-._~"),
            quote(self.project, safe="-._~"),
            quote(self.connection_hub_app_id, safe="-._~@"),
        )
        return (
            f"http://127.0.0.1:{port}/api/integrations/bundles/"
            f"{parts[0]}/{parts[1]}/{parts[2]}/public/delegated_admission"
        )

    def validate(self) -> None:
        if not self.tenant or not self.project:
            raise ValueError("configured tenant/project are required")
        if not self.connection_hub_app_id:
            raise ValueError("Connection Hub application id is required")
        if not self.service_id or not self.service_secret_ref:
            raise ValueError("Connection Hub protected-service identity is required")


__all__ = [
    "CognitoFreshAuthenticationProvider",
    "DelegatedManagementConfig",
    "GoogleFreshAuthenticationConfig",
    "HumanApprovalConfig",
    "HumanSecretExportConfig",
    "WebAuthnApprovalConfig",
]
