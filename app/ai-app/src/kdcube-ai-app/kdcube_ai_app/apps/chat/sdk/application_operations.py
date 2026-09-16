# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Canonical identities for operations exposed by KDCube applications.

An operation belongs to the application, not to the transport carrying one
invocation. Decorators derive distinct identities for distinct exposures by
default. An app can deliberately reuse one explicit operation id across REST,
Data Bus, or another surface when those entrances perform the same governed
action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, unquote

from kdcube_ai_app.auth.role_hierarchy import (
    ADMIN_ROLE,
    PAID_ROLE,
    PRIVILEGED_ROLE,
    REGISTERED_ROLE,
    SUPER_ADMIN_ROLE,
    strongest_platform_role,
)


APPLICATION_OPERATION_URN_PREFIX = "urn:kdcube:application-operation:"
APPLICATION_OPERATION_POLICY_PROPERTY = "kdcube.application_operations"
APPLICATION_OPERATION_POLICY_SCHEMA_V1 = "kdcube.application_operations.v1"
APPLICATION_OPERATION_POLICY_SCHEMA_V2 = "kdcube.application_operations.v2"
# Preserve the original exported name for callers that intentionally create a
# v1 compatibility marker.
APPLICATION_OPERATION_POLICY_SCHEMA = APPLICATION_OPERATION_POLICY_SCHEMA_V1
APPLICATION_OPERATION_POLICY_MODE_SELECTED = "selected"
_SAFE_COMPONENT = "-._~"
_MAX_COMPONENT_LENGTH = 512
_PLATFORM_ROLES = {
    REGISTERED_ROLE,
    PAID_ROLE,
    PRIVILEGED_ROLE,
    ADMIN_ROLE,
    SUPER_ADMIN_ROLE,
}


class ApplicationOperationPolicyError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ApplicationOperationRolePolicy:
    default_role: str
    operation_roles: Mapping[str, str] = field(default_factory=dict)

    def role_for(self, operation_ref: object) -> str:
        operation = str(operation_ref or "").strip()
        return self.operation_roles.get(operation, self.default_role)


def normalize_application_operation_id(value: object) -> str:
    """Return one bounded local operation id or raise ``ValueError``."""

    operation_id = str(value or "").strip()
    if not operation_id:
        raise ValueError("application operation_id is required")
    if len(operation_id) > _MAX_COMPONENT_LENGTH:
        raise ValueError("application operation_id is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in operation_id):
        raise ValueError("application operation_id contains control characters")
    return operation_id


def application_operation_ref(*, application_id: object, operation_id: object) -> str:
    """Build the globally unique Card operation reference."""

    resolved_application_id = str(application_id or "").strip()
    if not resolved_application_id:
        raise ValueError("application_id is required for an application operation")
    if len(resolved_application_id) > _MAX_COMPONENT_LENGTH:
        raise ValueError("application_id is too long")
    resolved_operation_id = normalize_application_operation_id(operation_id)
    return (
        f"{APPLICATION_OPERATION_URN_PREFIX}"
        f"{quote(resolved_application_id, safe=_SAFE_COMPONENT)}:"
        f"{quote(resolved_operation_id, safe=_SAFE_COMPONENT)}"
    )


def parse_application_operation_ref(value: object) -> tuple[str, str] | None:
    """Parse a canonical reference, returning ``(application_id, operation_id)``."""

    reference = str(value or "").strip()
    if not reference.startswith(APPLICATION_OPERATION_URN_PREFIX):
        return None
    body = reference[len(APPLICATION_OPERATION_URN_PREFIX):]
    encoded_application_id, separator, encoded_operation_id = body.partition(":")
    if not separator or not encoded_application_id or not encoded_operation_id:
        return None
    application_id = unquote(encoded_application_id).strip()
    operation_id = unquote(encoded_operation_id).strip()
    try:
        expected = application_operation_ref(
            application_id=application_id,
            operation_id=operation_id,
        )
    except ValueError:
        return None
    if expected != reference:
        return None
    return application_id, operation_id


def api_application_operation_id(
    *,
    alias: object,
    method: object,
    route: object,
    operation_id: object | None = None,
) -> tuple[str, bool]:
    """Resolve an API's local operation id and whether the app chose it."""

    if str(operation_id or "").strip():
        return normalize_application_operation_id(operation_id), True
    resolved_alias = normalize_application_operation_id(alias)
    resolved_method = str(method or "POST").strip().upper() or "POST"
    resolved_route = str(route or "operations").strip().lower() or "operations"
    return (
        normalize_application_operation_id(
            f"api.{resolved_route}.{resolved_method.lower()}.{resolved_alias}"
        ),
        False,
    )


def data_bus_application_operation_id(
    *,
    subject: object,
    operation_id: object | None = None,
) -> tuple[str, bool]:
    """Resolve a Data Bus handler's local operation id and provenance."""

    if str(operation_id or "").strip():
        return normalize_application_operation_id(operation_id), True
    resolved_subject = normalize_application_operation_id(subject)
    return normalize_application_operation_id(f"data_bus.{resolved_subject}"), False


def api_application_operation_ref(
    *,
    application_id: object,
    alias: object,
    method: object,
    route: object,
    operation_id: object | None = None,
) -> str:
    """Build the canonical reference for one declared API exposure."""

    resolved, _explicit = api_application_operation_id(
        alias=alias,
        method=method,
        route=route,
        operation_id=operation_id,
    )
    return application_operation_ref(
        application_id=application_id,
        operation_id=resolved,
    )


def data_bus_application_operation_ref(
    *,
    application_id: object,
    subject: object,
    operation_id: object | None = None,
) -> str:
    """Build the canonical reference for one declared Data Bus exposure."""

    resolved, _explicit = data_bus_application_operation_id(
        subject=subject,
        operation_id=operation_id,
    )
    return application_operation_ref(
        application_id=application_id,
        operation_id=resolved,
    )


def application_operation_policy() -> dict[str, str]:
    """Return the durable Card marker for an explicit operation selection."""

    return {
        "schema": APPLICATION_OPERATION_POLICY_SCHEMA,
        "mode": APPLICATION_OPERATION_POLICY_MODE_SELECTED,
    }


def application_operation_policy_declared(properties: Mapping[str, Any] | None) -> bool:
    values = properties if isinstance(properties, Mapping) else {}
    return APPLICATION_OPERATION_POLICY_PROPERTY in values


def application_operation_policy_enabled(properties: Mapping[str, Any] | None) -> bool:
    """Whether a Card explicitly opted into selected application operations.

    Existing Cards can already contain an empty ``resource_operations["*"]``
    row for unrelated wildcard authority. Treating that row as opt-in would
    silently deny every application call after an upgrade. The explicit marker
    distinguishes a reviewed default-closed selection from that legacy shape.
    """

    values = properties if isinstance(properties, Mapping) else {}
    policy = values.get(APPLICATION_OPERATION_POLICY_PROPERTY)
    return bool(
        isinstance(policy, Mapping)
        and str(policy.get("schema") or "").strip()
        in {
            APPLICATION_OPERATION_POLICY_SCHEMA_V1,
            APPLICATION_OPERATION_POLICY_SCHEMA_V2,
        }
        and str(policy.get("mode") or "").strip()
        == APPLICATION_OPERATION_POLICY_MODE_SELECTED
    )


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        source = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set, frozenset)):
        source = value
    else:
        source = ()
    return tuple(
        dict.fromkeys(
            item
            for raw in source
            if (item := str(raw or "").strip())
        )
    )


def _application_operation_selection(
    identity_authority: Mapping[str, Any],
) -> frozenset[str]:
    resource_operations = identity_authority.get("resource_operations")
    if not isinstance(resource_operations, Mapping) or "*" not in resource_operations:
        raise ApplicationOperationPolicyError(
            "application_operation_selection_missing"
        )
    return frozenset(_string_values(resource_operations.get("*")))


def _application_operation_role_policy(
    identity_authority: Mapping[str, Any],
    *,
    selected_operations: frozenset[str],
) -> ApplicationOperationRolePolicy:
    raw = identity_authority.get(APPLICATION_OPERATION_POLICY_PROPERTY)
    if not isinstance(raw, Mapping):
        raise ApplicationOperationPolicyError("application_operation_policy_invalid")
    if (
        str(raw.get("mode") or "").strip()
        != APPLICATION_OPERATION_POLICY_MODE_SELECTED
    ):
        raise ApplicationOperationPolicyError(
            "application_operation_policy_mode_invalid"
        )
    resource_grants = identity_authority.get("resource_grants")
    if not isinstance(resource_grants, Mapping) or "*" not in resource_grants:
        raise ApplicationOperationPolicyError("application_default_role_missing")
    stored_roles = _string_values(resource_grants.get("*"))
    schema = str(raw.get("schema") or "").strip()
    if schema == APPLICATION_OPERATION_POLICY_SCHEMA_V1:
        default_role = strongest_platform_role(stored_roles)
        if not default_role:
            raise ApplicationOperationPolicyError("application_default_role_missing")
        return ApplicationOperationRolePolicy(default_role=default_role)
    if schema != APPLICATION_OPERATION_POLICY_SCHEMA_V2:
        raise ApplicationOperationPolicyError(
            "application_operation_policy_schema_invalid"
        )

    default_role = str(raw.get("default_role") or "").strip()
    if default_role not in _PLATFORM_ROLES:
        raise ApplicationOperationPolicyError("application_default_role_invalid")
    if set(stored_roles) != {default_role}:
        raise ApplicationOperationPolicyError("application_default_role_mismatch")
    raw_overrides = raw.get("operation_roles")
    if raw_overrides is None:
        raw_overrides = {}
    if not isinstance(raw_overrides, Mapping):
        raise ApplicationOperationPolicyError("application_operation_roles_invalid")
    overrides: dict[str, str] = {}
    for raw_operation, raw_role in raw_overrides.items():
        operation = str(raw_operation or "").strip()
        role = str(raw_role or "").strip()
        if not operation or operation not in selected_operations:
            raise ApplicationOperationPolicyError(
                "application_operation_override_not_selected"
            )
        if role not in _PLATFORM_ROLES:
            raise ApplicationOperationPolicyError(
                "application_operation_override_role_invalid"
            )
        if role != default_role:
            overrides[operation] = role
    return ApplicationOperationRolePolicy(
        default_role=default_role,
        operation_roles=overrides,
    )


def resolve_application_operation_role_policy(
    identity_authority: Mapping[str, Any] | None,
) -> tuple[frozenset[str], ApplicationOperationRolePolicy] | None:
    """Resolve a declared application policy from authenticated authority facts.

    Card binding is deliberately not required here. Older OAuth grants can
    carry a reviewed v1 policy snapshot without a live Card pointer. Callers
    that specifically require a live Card use the ``delegated_*`` helpers
    below, which add that binding requirement.
    """

    authority = identity_authority if isinstance(identity_authority, Mapping) else {}
    if not application_operation_policy_declared(authority):
        return None
    selected = _application_operation_selection(authority)
    policy = _application_operation_role_policy(
        authority,
        selected_operations=selected,
    )
    return selected, policy


def delegated_application_operation_selection(
    identity_authority: Mapping[str, Any] | None,
) -> frozenset[str] | None:
    """Return the Card's all-application selection, or ``None`` when absent.

    Presence is distinct from an empty selection. A delegated Card with the
    explicit policy marker and a ``"*"`` operation row is default-closed even
    when that row currently selects nothing. Other caller identities and
    pre-policy Cards keep their existing gates.
    """

    authority = identity_authority if isinstance(identity_authority, Mapping) else {}
    binding = authority.get("delegated_card_binding")
    if not isinstance(binding, Mapping) or not str(binding.get("access_id") or "").strip():
        return None
    resolved = resolve_application_operation_role_policy(authority)
    if resolved is None:
        return None
    selected, _policy = resolved
    return selected


def delegated_application_operation_role(
    identity_authority: Mapping[str, Any] | None,
    *,
    operation_ref: object,
) -> str | None:
    """Resolve one invocation-local role from a delegated Card policy."""

    authority = identity_authority if isinstance(identity_authority, Mapping) else {}
    binding = authority.get("delegated_card_binding")
    if not isinstance(binding, Mapping) or not str(binding.get("access_id") or "").strip():
        return None
    resolved = resolve_application_operation_role_policy(authority)
    if resolved is None:
        return None
    selected, policy = resolved
    operation = str(operation_ref or "").strip()
    if not operation or operation not in selected:
        raise ApplicationOperationPolicyError("application_operation_not_granted")
    return policy.role_for(operation)


__all__ = [
    "APPLICATION_OPERATION_POLICY_MODE_SELECTED",
    "APPLICATION_OPERATION_POLICY_PROPERTY",
    "APPLICATION_OPERATION_POLICY_SCHEMA",
    "APPLICATION_OPERATION_POLICY_SCHEMA_V1",
    "APPLICATION_OPERATION_POLICY_SCHEMA_V2",
    "APPLICATION_OPERATION_URN_PREFIX",
    "ApplicationOperationPolicyError",
    "ApplicationOperationRolePolicy",
    "api_application_operation_id",
    "api_application_operation_ref",
    "application_operation_policy",
    "application_operation_policy_declared",
    "application_operation_policy_enabled",
    "application_operation_ref",
    "data_bus_application_operation_ref",
    "data_bus_application_operation_id",
    "delegated_application_operation_selection",
    "delegated_application_operation_role",
    "normalize_application_operation_id",
    "parse_application_operation_ref",
    "resolve_application_operation_role_policy",
]
