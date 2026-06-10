# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_IDEMPOTENCY_OPTIONAL,
    DATA_BUS_IDEMPOTENCY_REQUIRED,
    DATA_BUS_INGRESS_SCHEMA,
    DATA_BUS_MESSAGE_SCHEMA,
    DATA_BUS_ORDERING_PARALLEL,
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
    DATA_BUS_PARTITION_NONE,
    DATA_BUS_PARTITION_OBJECT_REF,
    DATA_BUS_RESULT_SCHEMA,
    DataBusContext,
    DataBusHandlerSpec,
    DataBusMessage,
    DataBusReply,
    DataBusResult,
    data_bus_group_name,
    data_bus_stream_key,
    timestamp_message_id,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import (
    DataBusPublishAck,
    DataBusPublisher,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.policy import (
    DataBusPublishLimit,
    DataBusSettings,
    default_data_bus_publish_limits,
)

def data_bus_handler(*args, **kwargs):
    from kdcube_ai_app.infra.plugin.bundle_loader import data_bus_handler as _data_bus_handler

    return _data_bus_handler(*args, **kwargs)

__all__ = [
    "DATA_BUS_IDEMPOTENCY_OPTIONAL",
    "DATA_BUS_IDEMPOTENCY_REQUIRED",
    "DATA_BUS_INGRESS_SCHEMA",
    "DATA_BUS_MESSAGE_SCHEMA",
    "DATA_BUS_ORDERING_PARALLEL",
    "DATA_BUS_ORDERING_SERIAL_PER_PARTITION",
    "DATA_BUS_PARTITION_NONE",
    "DATA_BUS_PARTITION_OBJECT_REF",
    "DATA_BUS_RESULT_SCHEMA",
    "DataBusContext",
    "DataBusHandlerSpec",
    "DataBusMessage",
    "DataBusPublishAck",
    "DataBusPublishLimit",
    "DataBusPublisher",
    "DataBusReply",
    "DataBusResult",
    "DataBusSettings",
    "data_bus_group_name",
    "data_bus_handler",
    "data_bus_stream_key",
    "default_data_bus_publish_limits",
    "timestamp_message_id",
]
