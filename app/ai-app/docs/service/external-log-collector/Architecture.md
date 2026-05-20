---
id: ks:docs/service/external-log-collector/Architecture.md
title: "External Log Collector Architecture"
summary: "Architecture of the browser-side log collector that intercepts frontend errors and warnings, enriches them with runtime context, and sends them to backend storage for analysis."
tags: ["service", "frontend", "logging", "observability", "browser", "design"]
keywords: ["browser log collection", "frontend error interception", "console warning capture", "context-enriched client logs", "backend log ingestion", "frontend observability architecture"]
see_also:
  - ks:docs/service/external-log-collector/frontend-events-design.md
  - ks:docs/service/streams/telemetry-README.md
  - ks:docs/service/README-monitoring-observability.md
  - ks:docs/service/comm/README-comm.md
---
# External Log Collector — Architecture & Design

## Overview

**External Log Collector** — a system for intercepting, enriching, and persisting logs generated in the user's browser. The system automatically collects errors and warnings from `console.error`, `console.warn`, and unhandled exceptions, enriches them with contextual information (tenant, project, user, session, conversation), and sends them to the backend for centralized storage and analysis.

This collector is also an existing KDCube event producer. Telemetry and stats
collectors should normalize from its source event shape instead of defining a
parallel client-event contract.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         React Frontend App                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │         Event Reporting Module (JavaScript)                  │  │
│  │                                                              │  │
│  │  ┌────────────────────────────────────────────────────────┐ │  │
│  │  │  1. Console Interceptor                                │ │  │
│  │  │     • Patches console.error                            │ │  │
│  │  │     • Patches console.warn                             │ │  │
│  │  │     • Patches console.info                             │ │  │
│  │  │     • Listens to unhandledrejection events             │ │  │
│  │  │     • Listens to global error events                   │ │  │
│  │  │     • Preserves original behavior (transparent)        │ │  │
│  │  └────────────────────────────────────────────────────────┘ │  │
│  │                           ↓                                 │  │
│  │  ┌────────────────────────────────────────────────────────┐ │  │
│  │  │  2. Metadata Enricher                                  │ │  │
│  │  │     • Reads tenant & project from ChatSettings Redux   │ │  │
│  │  │     • Reads user_id & session_id from UserProfile      │ │  │
│  │  │     • Reads conversation_id from ChatState Redux       │ │  │
│  │  │     • Adds timestamp & timezone                        │ │  │
│  │  │     (Read from store at intercept time, not at init)   │ │  │
│  │  └────────────────────────────────────────────────────────┘ │  │
│  │                           ↓                                 │  │
│  │  ┌────────────────────────────────────────────────────────┐ │  │
│  │  │  3. Deduplication & Buffering                          │ │  │
│  │  │     • Dedup: same message within 5s sent once          │ │  │
│  │  │     • Buffer size: 50 events max                       │ │  │
│  │  │     • Errors: immediate flush (no wait)               │ │  │
│  │  │     • Warnings/info: flush every 5s                    │ │  │
│  │  │     • Fire-and-forget: if collector down → dropped     │ │  │
│  │  └────────────────────────────────────────────────────────┘ │  │
│  │                           ↓                                 │  │
│  │  ┌────────────────────────────────────────────────────────┐ │  │
│  │  │  4. Event Queue & Sender                               │ │  │
│  │  │     • Collects multiple events in memory               │ │  │
│  │  │     • Sends one JSON event per POST /api/events/client │ │  │
│  │  │     • Automatic flush: every 5s or on error            │ │  │
│  │  └────────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                               ↓ HTTP POST
                    /api/events/client (JSON)
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│                External Log Collector Service                       │
│                 (KDCube metrics REST router)                        │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  1. HTTP Endpoint Handler                                   │  │
│  │     • Route: POST /api/events/client                        │  │
│  │     • Accepts: one client event (JSON)                      │  │
│  │     • Auth/context validation as configured                 │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           ↓                                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  2. Event Validator                                         │  │
│  │     • Validates against Pydantic models                     │  │
│  │     • Enforces: ExternalLogEvent today                     │  │
│  │     • Required fields: event_type, tenant, timestamp, etc.  │  │
│  │     • Type-safe: level, message, args for logs             │  │
│  │     • Metric event model is a planned extension            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           ↓                                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  3. Python Logging Handler                                  │  │
│  │     • Creates structured JSON log entry                     │  │
│  │     • Log level: ERROR for client errors, INFO for metrics  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           ↓                                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  4. Log File Persistence                                    │  │
│  │     • Rotation: by size (10 MB or configurable)             │  │
│  │     • Retention: 5-10 recent files kept                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           ↓                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
                    Disk Storage / External Systems
```

---

## Data Flow: Log Event Journey

### 1️⃣ **Capture** — In the Browser
```
User Action
     ↓
console.error("Failed to load") called
     ↓
Interceptor catches the call
     ↓
Enrich with: tenant, project, user_id, session_id, conversation_id, timestamp
     ↓
Check dedup: "error Failed to load" seen in last 5s? Skip if yes.
     ↓
Add to buffer
```

### 2️⃣ **Queue & Send** — Buffering
```
Buffer has events (or 5 seconds elapsed, or error occurred)
     ↓
Flush: POST /api/events/client once per buffered event
     ↓
Server responds 200 OK
     ↓
Buffer cleared, ready for new events
```

### 3️⃣ **Receive & Validate** — Backend Service
```
POST /api/events/client received
     ↓
Validate the event against Pydantic model
     ↓
Reject invalid events (malformed, missing fields)
     ↓
Log valid events as JSON to file
     ↓
Return success for accepted events; invalid payloads fail validation
```

### 4️⃣ **Persist** — Storage
```
Event written to log file: logs/external_events.log
     ↓
File rotated when reaching size limit (10 MB)
     ↓
Old files kept: external_events.log.1, .log.2, etc.
     ↓
Ready for analysis, dashboards, alerting
```

---

## Relationship To Telemetry

The collector source event is not the analytics schema. A telemetry collector
normalizes it into the unified event envelope documented in
`ks:docs/service/streams/telemetry-README.md`.

```text
browser ExternalLogEvent
  |
  | event_type/origin/context/timestamp/timezone preserved
  | level -> name="client.log.<level>"
  | message/args retained only by log-content policy
  | event_id derived if missing
  v
kdcube.telemetry.v1 normalized event
  |
  v
raw event store / observability log / rollups
```

Implementation alignment items:

- the frontend currently sends `level="warning"` for `console.warn`
- the backend model should accept `warning` or normalize it to `warn`
- metric events are documented as the next extension, but current backend
  routing accepts log events only
