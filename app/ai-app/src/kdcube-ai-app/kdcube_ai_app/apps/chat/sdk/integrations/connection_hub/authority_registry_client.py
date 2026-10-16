# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube bundle-props adapter for Connection Hub's authority registry client."""

from __future__ import annotations

from typing import Any, Mapping

from connection_hub.authority_registry_client import (
    AuthorityRegistryClient as PortableAuthorityRegistryClient,
)


async def _load_bundle_props(
    redis: Any,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
) -> Mapping[str, Any] | None:
    # Resolve lazily so startup imports stay light and tests can replace the
    # platform loader at its canonical module boundary.
    from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

    return await get_bundle_props(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


class AuthorityRegistryClient(PortableAuthorityRegistryClient):
    """Authority client bound to KDCube's shared bundle-props store."""

    def __init__(
        self,
        entrypoint: Any = None,
        *,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        redis: Any = None,
        registry: Mapping[str, Any] | None = None,
        bundle_props: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # W303: keywords this wrapper does not handle go to the portable class unchanged.
        kwargs.setdefault("bundle_props_loader", _load_bundle_props)
        super().__init__(
            entrypoint,
            connection_hub_bundle_id=connection_hub_bundle_id,
            tenant=tenant,
            project=project,
            redis=redis,
            registry=registry,
            bundle_props=bundle_props,
            **kwargs,
        )


__all__ = ["AuthorityRegistryClient"]
