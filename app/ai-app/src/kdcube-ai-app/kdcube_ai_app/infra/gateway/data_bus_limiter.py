# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.policy import DataBusPublishLimit
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
from kdcube_ai_app.infra.namespaces import REDIS, ns_key


@dataclass
class DataBusPublishLimitResult:
    ok: bool
    error: str = ""
    error_type: str = "data_bus_limit"
    limit: str = ""
    limit_value: int | None = None
    observed: int | None = None
    retry_after: int | None = None
    window_seconds: int | None = None
    stats: dict[str, int] = field(default_factory=dict)


def _user_type(session: UserSession) -> str:
    value = getattr(getattr(session, "user_type", None), "value", None)
    return str(value or getattr(session, "user_type", "") or "registered").strip().lower()


def _reject(
    *,
    error: str,
    limit: str,
    limit_value: int,
    observed: int,
    retry_after: int | None,
    window_seconds: int,
    stats: dict[str, int] | None = None,
) -> DataBusPublishLimitResult:
    return DataBusPublishLimitResult(
        ok=False,
        error=error,
        limit=limit,
        limit_value=limit_value,
        observed=observed,
        retry_after=retry_after,
        window_seconds=window_seconds,
        stats=stats or {},
    )


def _check_package_maxes(
    *,
    limit: DataBusPublishLimit,
    package_bytes: int,
    message_count: int,
) -> DataBusPublishLimitResult | None:
    window_seconds = max(1, int(limit.window_seconds or 60))
    if limit.max_package_bytes != -1 and package_bytes > limit.max_package_bytes:
        return _reject(
            error="data bus publish package is too large",
            limit="max_package_bytes",
            limit_value=limit.max_package_bytes,
            observed=package_bytes,
            retry_after=None,
            window_seconds=window_seconds,
        )
    if limit.max_messages_per_package != -1 and message_count > limit.max_messages_per_package:
        return _reject(
            error="data bus publish package has too many messages",
            limit="max_messages_per_package",
            limit_value=limit.max_messages_per_package,
            observed=message_count,
            retry_after=None,
            window_seconds=window_seconds,
        )
    return None


async def check_data_bus_publish_limits(
    *,
    redis: Any,
    gateway_config: GatewayConfiguration,
    session: UserSession,
    package_bytes: int,
    message_count: int,
    now: float | None = None,
) -> DataBusPublishLimitResult:
    """
    Admit a Socket.IO data_bus.publish package.

    This is a stream ingress policy: one publish package may contain many
    durable messages, so counters track packages, messages, and bytes rather
    than generic HTTP requests.
    """
    role = _user_type(session)
    limit = gateway_config.data_bus.get(role)
    if not limit.enabled:
        return DataBusPublishLimitResult(ok=True)

    max_rejection = _check_package_maxes(
        limit=limit,
        package_bytes=package_bytes,
        message_count=message_count,
    )
    if max_rejection:
        return max_rejection

    window_seconds = max(1, int(limit.window_seconds or 60))
    current_time = float(now if now is not None else time.time())
    window_index = int(current_time // window_seconds)
    retry_after = max(1, int(((window_index + 1) * window_seconds) - current_time))

    prefix = ns_key(
        f"{REDIS.SYSTEM.RATE_LIMIT}:data-bus-publish",
        tenant=gateway_config.tenant_id,
        project=gateway_config.project_id,
    )
    base_key = f"{prefix}:{session.session_id}:{window_index}"
    keys = {
        "packages_per_minute": f"{base_key}:packages",
        "messages_per_minute": f"{base_key}:messages",
        "bytes_per_minute": f"{base_key}:bytes",
    }
    increments = {
        "packages_per_minute": 1,
        "messages_per_minute": int(message_count),
        "bytes_per_minute": int(package_bytes),
    }
    ttl_seconds = max(window_seconds * 2, window_seconds + 10)

    pipe = redis.pipeline()
    for field_name, key in keys.items():
        amount = increments[field_name]
        if amount == 1:
            pipe.incr(key)
        else:
            pipe.incrby(key, amount)
        pipe.expire(key, ttl_seconds)
    result = await pipe.execute()

    stats = {
        "packages_per_minute": int(result[0] or 0),
        "messages_per_minute": int(result[2] or 0),
        "bytes_per_minute": int(result[4] or 0),
    }
    for field_name, observed in stats.items():
        configured = int(getattr(limit, field_name))
        if configured != -1 and observed > configured:
            label = field_name.replace("_per_minute", "")
            return _reject(
                error=f"data bus publish {label} rate exceeded",
                limit=field_name,
                limit_value=configured,
                observed=observed,
                retry_after=retry_after,
                window_seconds=window_seconds,
                stats=stats,
            )

    return DataBusPublishLimitResult(
        ok=True,
        retry_after=retry_after,
        window_seconds=window_seconds,
        stats=stats,
    )
