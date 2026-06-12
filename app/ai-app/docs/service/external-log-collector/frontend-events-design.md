---
id: repo:kdcube-ai-app/app/ai-app/docs/service/external-log-collector/frontend-events-design.md
title: "Frontend Log Events Design"
summary: "Source event schema for browser log events, the planned metric extension, and normalization into the shared telemetry envelope."
tags: ["service", "frontend", "logging", "events", "observability", "design"]
keywords: ["frontend log event schema", "browser metric payloads", "client telemetry models", "log collector event contract", "frontend observability payload design"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/external-log-collector/Architecture.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/telemetry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/README-monitoring-observability.md
---
# Frontend Log Events — Design

## Purpose

The external log collector is an existing producer of KDCube client events. Its
base event context should be reused by telemetry and stats collectors instead
of defining another unrelated usage-event shape.

This document describes the source event shape accepted from the browser. The
telemetry stream design describes how these events are normalized into the
versioned `kdcube.telemetry.v1` envelope used by collectors.

## Base Event Context

All client events carry the same context:

| Field | Meaning |
| --- | --- |
| `event_type` | Coarse source event class, currently `log`; `metric` is the planned extension. |
| `origin` | Producer identity, for example `kdcube-frontend`. |
| `tenant` / `project` | KDCube routing and isolation scope. |
| `user_id` / `session_id` / `conversation_id` | Runtime user/session/conversation context when known. |
| `timestamp` | ISO 8601 event time. |
| `timezone` | Client timezone name, or `UTC` when unknown. |

## Python Models

Target backend model:

```python
from __future__ import annotations
from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel


class EventBase(BaseModel):
    event_type: Literal["log", "metric"]
    origin: str

    tenant:          str
    project:         str
    user_id:         str | None
    session_id:      str | None
    conversation_id: str | None
    timestamp:       datetime        # ISO 8601 UTC
    timezone:        str


class ExternalLogEvent(EventBase):
    level:   Literal["error", "warning", "warn", "info"]
    message: str
    args:    list[Any]


class ExternalMetricEvent(EventBase):
    name:  str             # e.g. "chat.message.sent", "file.upload.size"
    value: float
    tags:  dict[str, str]  # arbitrary key-value labels


ExternalEvent = ExternalLogEvent | ExternalMetricEvent
```

Implementation notes:

- the current frontend posts `ExternalLogEvent` to `/api/events/client`
- the current frontend sends one event per HTTP request after local buffering
- the current backend route validates `ExternalLogEvent`; metric events remain
  a planned extension
- the frontend currently emits `level="warning"` for `console.warn`; the
  backend model should accept that value or normalize it to `warn`

## Examples

**log:**
```json
{
  "event_type": "log",
  "origin": "kdcube-frontend",
  "tenant": "acme",
  "project": "sales-bot",
  "user_id": "u_8f3a1c",
  "session_id": "sess_4d9b22",
  "conversation_id": "conv_77e1a0",
  "timestamp": "2026-03-31T10:42:17.331000Z",
  "timezone": "UTC",
  "level": "error",
  "message": "Failed to load conversation history",
  "args": [{"status": 503, "url": "/api/conversations"}]
}
```

**planned metric extension:**
```json
{
  "event_type": "metric",
  "origin": "kdcube-frontend",
  "tenant": "acme",
  "project": "sales-bot",
  "user_id": "u_8f3a1c",
  "session_id": "sess_4d9b22",
  "conversation_id": "conv_77e1a0",
  "timestamp": "2026-03-31T10:42:18.004000Z",
  "timezone": "UTC",
  "name": "chat.message.sent",
  "value": 1.0,
  "tags": {"bundle": "react-v2", "model": "claude-sonnet"}
}
```

## Telemetry Normalization

The telemetry collector should normalize source events instead of storing
producer-specific shapes as analytics contracts.

| Client event field | Telemetry field |
| --- | --- |
| `event_type` | `event_type` |
| `origin` | `origin`, and often `source_component` |
| `timestamp` | `timestamp` |
| `timezone` | `timezone` |
| `level` | `name="client.log.<level>"`, `dimensions.level` |
| `name` on metric events | `name` |
| `value` on metric events | `value`, `metrics.value` |
| `tags` on metric events | `tags`, `dimensions` |

If a source event does not provide `event_id`, the normalizer should derive a
stable id from the source fields that make retries idempotent.
