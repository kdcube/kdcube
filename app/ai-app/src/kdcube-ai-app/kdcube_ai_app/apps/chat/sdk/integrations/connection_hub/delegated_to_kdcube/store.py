# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube user-property and secret bindings for Connection Hub account storage."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from kdcube_ai_app.apps.chat.sdk import config as sdk_config


_core = import_module("connection_hub.delegated_to_kdcube.store")


class _KdcubeUserConfigurationStore:
    async def get_user_prop(self, key: str, **kwargs: Any) -> Any:
        return await sdk_config.get_user_prop(key, **kwargs)

    async def set_user_prop(self, key: str, value: Any, **kwargs: Any) -> None:
        await sdk_config.set_user_prop(key, value, **kwargs)

    async def delete_user_prop(self, key: str, **kwargs: Any) -> None:
        await sdk_config.delete_user_prop(key, **kwargs)

    async def set_user_secret(self, key: str, value: str, **kwargs: Any) -> None:
        await sdk_config.set_user_secret(key, value, **kwargs)

    async def get_secret(self, key: str, **kwargs: Any) -> Any:
        return await sdk_config.get_secret(key, **kwargs)

    async def delete_user_secret(self, key: str, **kwargs: Any) -> None:
        await sdk_config.delete_user_secret(key, **kwargs)

    def clear_secret_cache(self, **kwargs: Any) -> None:
        sdk_config.clear_secret_cache(**kwargs)


class DelegatedToKdcubeStore(_core.DelegatedToKdcubeStore):
    def __init__(
        self,
        *,
        user_id: str,
        bundle_id: str = _core.CONNECTION_HUB_BUNDLE_ID,
        backend: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # W303: keywords this wrapper does not handle go to the portable class unchanged.
        super().__init__(
            user_id=user_id,
            bundle_id=bundle_id,
            backend=backend or _KdcubeUserConfigurationStore(),
            **kwargs,
        )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = list(_core.__all__)
