# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub Card admission for the Socket.IO Data Bus transport."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from connection_hub.delegated_credentials.resource_operations import (
    normalize_resource,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession


DELEGATED_CARD_SCOPE_SCHEMA = "kdcube.data_bus.delegated_card_scope.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _resource_bundle_coordinate(resource: str) -> tuple[str, str, str]:
    parsed = urlsplit(_text(resource))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "delegated_resource must be an absolute HTTP URL without query or fragment"
        )
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) < 6 or segments[:3] != ["api", "integrations", "bundles"]:
        raise ValueError("delegated_resource is not a KDCube bundle resource")
    return segments[3], segments[4], segments[5]


def _card_binding(session: UserSession) -> dict[str, Any]:
    authority = (
        dict(session.identity_authority)
        if isinstance(session.identity_authority, Mapping)
        else {}
    )
    binding = authority.get("delegated_card_binding")
    return dict(binding) if isinstance(binding, Mapping) else {}


@dataclass(frozen=True)
class DelegatedDataBusAdmission:
    session: UserSession
    scope: dict[str, Any]


async def admit_delegated_data_bus_bearer(
    *,
    app: Any,
    gateway_adapter: Any,
    tenant: str,
    project: str,
    bundle_id: str,
    resource: str,
    bearer_token: str,
    context: RequestContext,
) -> DelegatedDataBusAdmission:
    """Resolve a Card bearer and bind this socket to one bundle resource."""

    expected = (_text(tenant), _text(project), _text(bundle_id))
    if not all(expected):
        raise ValueError(
            "delegated Card admission requires tenant/project/bundle_id"
        )
    if _resource_bundle_coordinate(resource) != expected:
        raise ValueError(
            "delegated_resource does not belong to tenant/project/bundle_id"
        )
    resolver = getattr(gateway_adapter, "request_auth_resolver", None)
    resolve = getattr(resolver, "resolve_delegated_resource_session", None)
    if not callable(resolve):
        raise RuntimeError("Connection Hub delegated bearer authentication is unavailable")
    session = await resolve(
        app=app,
        resource=resource,
        bearer_token=bearer_token,
        context=context,
    )
    if session is None:
        raise PermissionError("delegated Card bearer was not accepted for this resource")

    binding = _card_binding(session)
    access_id = _text(binding.get("access_id"))
    expires_at = int(binding.get("expires_at") or 0)
    if not access_id or expires_at <= int(time.time()):
        raise PermissionError("delegated Card is missing an active Card binding")

    selected_resource = normalize_resource(resource)
    authority = dict(session.identity_authority or {})
    authority["delegated_resource"] = selected_resource
    session.identity_authority = authority

    return DelegatedDataBusAdmission(
        session=session,
        scope={
            "schema": DELEGATED_CARD_SCOPE_SCHEMA,
            "credential_kind": "delegated_card",
            "tenant": expected[0],
            "project": expected[1],
            "bundle_id": expected[2],
            "resource": selected_resource,
            "access_id": access_id,
            "client_id": _text(binding.get("client_id")),
            "grantor_user_id": _text(binding.get("grantor_user_id")),
            "delegate_identity": _text(binding.get("delegate_identity")),
            "expires_at": expires_at,
        },
    )


__all__ = [
    "DELEGATED_CARD_SCOPE_SCHEMA",
    "DelegatedDataBusAdmission",
    "admit_delegated_data_bus_bearer",
]
