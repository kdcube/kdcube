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

from typing import Any, Mapping
from urllib.parse import quote, unquote


APPLICATION_OPERATION_URN_PREFIX = "urn:kdcube:application-operation:"
APPLICATION_OPERATION_POLICY_PROPERTY = "kdcube.application_operations"
APPLICATION_OPERATION_POLICY_SCHEMA = "kdcube.application_operations.v1"
APPLICATION_OPERATION_POLICY_MODE_SELECTED = "selected"
_SAFE_COMPONENT = "-._~"
_MAX_COMPONENT_LENGTH = 512


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
        == APPLICATION_OPERATION_POLICY_SCHEMA
        and str(policy.get("mode") or "").strip()
        == APPLICATION_OPERATION_POLICY_MODE_SELECTED
    )


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
    if not application_operation_policy_enabled(authority):
        return None
    resource_operations = authority.get("resource_operations")
    if not isinstance(resource_operations, Mapping) or "*" not in resource_operations:
        return None
    raw = resource_operations.get("*")
    if isinstance(raw, str):
        values = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = ()
    return frozenset(str(value).strip() for value in values if str(value).strip())


__all__ = [
    "APPLICATION_OPERATION_POLICY_MODE_SELECTED",
    "APPLICATION_OPERATION_POLICY_PROPERTY",
    "APPLICATION_OPERATION_POLICY_SCHEMA",
    "APPLICATION_OPERATION_URN_PREFIX",
    "api_application_operation_id",
    "api_application_operation_ref",
    "application_operation_policy",
    "application_operation_policy_enabled",
    "application_operation_ref",
    "data_bus_application_operation_ref",
    "data_bus_application_operation_id",
    "delegated_application_operation_selection",
    "normalize_application_operation_id",
    "parse_application_operation_ref",
]
