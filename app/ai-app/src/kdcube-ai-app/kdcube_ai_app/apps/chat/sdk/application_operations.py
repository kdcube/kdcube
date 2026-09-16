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

from urllib.parse import quote, unquote


APPLICATION_OPERATION_URN_PREFIX = "urn:kdcube:application-operation:"
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

    if operation_id is not None:
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

    if operation_id is not None:
        return normalize_application_operation_id(operation_id), True
    resolved_subject = normalize_application_operation_id(subject)
    return normalize_application_operation_id(f"data_bus.{resolved_subject}"), False


__all__ = [
    "APPLICATION_OPERATION_URN_PREFIX",
    "api_application_operation_id",
    "application_operation_ref",
    "data_bus_application_operation_id",
    "normalize_application_operation_id",
    "parse_application_operation_ref",
]
