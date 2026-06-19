# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation event-bus coordination primitives."""

from .orchestrator import (
    ConversationEventBusOrchestrator,
    EventBusAcceptDecision,
    EventBusCloseDecision,
    EventBusScheduleDecision,
)
from .exceptions import ExternalEventLaneWakeIgnored
from .state import EventLaneState, RedisEventLaneStateTable, event_is_handler_probe, event_timestamp
from .wakeup import (
    EventLaneWakePublishResult,
    EventLaneWakePublisher,
    RedisEventLaneWakeEnqueuer,
    build_event_lane_ref,
    build_event_lane_wakeup,
    event_lane_wakeup_queue_key,
)

__all__ = [
    "ConversationEventBusOrchestrator",
    "EventBusAcceptDecision",
    "EventBusCloseDecision",
    "EventBusScheduleDecision",
    "EventLaneWakePublishResult",
    "EventLaneWakePublisher",
    "ExternalEventLaneWakeIgnored",
    "EventLaneState",
    "RedisEventLaneWakeEnqueuer",
    "RedisEventLaneStateTable",
    "build_event_lane_ref",
    "build_event_lane_wakeup",
    "event_is_handler_probe",
    "event_lane_wakeup_queue_key",
    "event_timestamp",
]
