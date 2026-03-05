---
id: ks:docs/sdk/bundle/bundle-firewall-README.md
title: "Bundle Outbound Firewall"
summary: "Bundle‑level event filter that decides which comm events are allowed to leave the bundle."
tags: ["sdk", "bundle", "comm", "security", "firewall"]
keywords: ["event filter", "outbound firewall", "ChatCommunicator", "IEventFilter", "allow_event"]
see_also:
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
---
# Bundle Outbound Firewall (Event Filter)

This document describes the **bundle‑level outbound firewall** for comm events.
It lets a bundle **decide which events can leave the bundle** and be delivered to the client.

## What it is

The firewall is an **event filter** (`IEventFilter`) passed into the bundle entrypoint.
It runs inside `ChatCommunicator.emit(...)`, *before* any event is published to Redis.

This is **outbound‑only**: it controls delivery **from bundle → client**.
It does **not** replace gateway/auth checks on inbound requests.

## What the filter sees

The filter receives:

- **User/session details**: `user_type`, `user_id`
- **Event metadata** (`EventFilterInput`):
  - `type`, `route`, `socket_event`, `agent`, `step`, `status`, `broadcast`
- **Payload** (`data`), if you need to inspect the event content

This allows heuristics based on:
- user role (privileged vs registered/anonymous)
- event type (e.g., suppress internal steps)
- route or socket event name
- payload properties (size, labels, source, etc.)

## Interface

```python
from kdcube_ai_app.apps.chat.sdk.comm.event_filter import IEventFilter, EventFilterInput

class MyFilter(IEventFilter):
    def allow_event(self, *, user_type: str, user_id: str,
                    event: EventFilterInput, data: dict | None = None) -> bool:
        # return True to allow, False to suppress
        return True
```

## Example (from demo bundle)

The `eco` demo bundle uses an event filter to suppress some `chat.step` types
for non‑privileged users:

- `apps/chat/sdk/examples/bundles/eco@2026-02-18-15-06/event_filter.py`

That filter allows **privileged** users to see everything,
but hides internal steps for other roles.

## How to wire it

Bundle entrypoints typically pass the filter to the workflow:

```python
from .event_filter import BundleEventFilter

workflow = WithReactWorkflow(
    ...,
    event_filter=BundleEventFilter(),
)
```

## Notes

- Filters are **fail‑open**: if the filter crashes, the event is allowed.
- This is a **bundle‑level** control. It can be different for each bundle.
- If you need org‑wide restrictions, implement them at the gateway or relay layer.
