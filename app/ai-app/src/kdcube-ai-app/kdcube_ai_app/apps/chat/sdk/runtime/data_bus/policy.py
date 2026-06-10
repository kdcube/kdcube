# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict


def coerce_data_bus_bool(value: Any, *, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"false", "disable", "disabled", "off", "0", "no"}


@dataclass
class DataBusPublishLimit:
    """Socket.IO data_bus.publish package admission policy."""
    enabled: bool = True
    packages_per_minute: int = 600
    messages_per_minute: int = 6000
    bytes_per_minute: int = 16 * 1024 * 1024
    max_messages_per_package: int = 200
    max_package_bytes: int = 1024 * 1024
    window_seconds: int = 60


def default_data_bus_publish_limits() -> Dict[str, DataBusPublishLimit]:
    return {
        "anonymous": DataBusPublishLimit(
            packages_per_minute=60,
            messages_per_minute=600,
            bytes_per_minute=2 * 1024 * 1024,
            max_messages_per_package=50,
            max_package_bytes=256 * 1024,
            window_seconds=60,
        ),
        "registered": DataBusPublishLimit(
            packages_per_minute=600,
            messages_per_minute=6000,
            bytes_per_minute=16 * 1024 * 1024,
            max_messages_per_package=200,
            max_package_bytes=1024 * 1024,
            window_seconds=60,
        ),
        "paid": DataBusPublishLimit(
            packages_per_minute=1200,
            messages_per_minute=12000,
            bytes_per_minute=32 * 1024 * 1024,
            max_messages_per_package=500,
            max_package_bytes=2 * 1024 * 1024,
            window_seconds=60,
        ),
        "privileged": DataBusPublishLimit(
            packages_per_minute=-1,
            messages_per_minute=-1,
            bytes_per_minute=-1,
            max_messages_per_package=1000,
            max_package_bytes=4 * 1024 * 1024,
            window_seconds=60,
        ),
    }


def coerce_data_bus_publish_limit(value: Any, default: DataBusPublishLimit) -> DataBusPublishLimit:
    if isinstance(value, DataBusPublishLimit):
        return value
    payload = asdict(default)
    if isinstance(value, dict):
        for key in payload:
            if key not in value:
                continue
            raw = value.get(key)
            if raw is None or raw == "":
                continue
            if key == "enabled":
                payload[key] = coerce_data_bus_bool(raw, default=getattr(default, key))
            else:
                try:
                    payload[key] = int(raw)
                except Exception:
                    payload[key] = getattr(default, key)
    return DataBusPublishLimit(**payload)


@dataclass
class DataBusSettings:
    """Durable Data Bus ingress settings."""
    publish_limits: Dict[str, DataBusPublishLimit] = field(default_factory=dict)

    def __post_init__(self):
        defaults = default_data_bus_publish_limits()
        source = dict(self.publish_limits or {})
        roles: Dict[str, DataBusPublishLimit] = {}
        for role, default in defaults.items():
            roles[role] = coerce_data_bus_publish_limit(source.get(role), default)
        for role, payload in source.items():
            role_key = str(role or "").strip().lower()
            if role_key in roles:
                continue
            roles[role_key] = coerce_data_bus_publish_limit(payload, defaults["registered"])
        self.publish_limits = roles

    def get(self, role: str) -> DataBusPublishLimit:
        role_key = str(role or "").strip().lower()
        if role_key in self.publish_limits:
            return self.publish_limits[role_key]
        if "registered" in self.publish_limits:
            return self.publish_limits["registered"]
        return next(iter(self.publish_limits.values()))
