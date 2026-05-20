---
id: ks:docs/sdk/bundle/build/design/@subscribe-README.md
title: "Bundle Subscribe Design Pointer"
summary: "Compatibility pointer: stream subscription is now modeled as a helper inside the proposed bundle @longrun lifecycle."
status: superseded
updated_at: 2026-05-20
tags: ["sdk", "bundle", "design", "subscribe", "longrun", "streams"]
keywords: ["bundle subscribe", "stream subscription", "bundle longrun"]
see_also:
  - ks:docs/sdk/bundle/build/design/@longrun-README.md
  - ks:docs/service/streams/telemetry-README.md
---
# Bundle Subscribe Design Pointer

Do not define `@subscribe` as the base lifecycle primitive.

The current design direction is:

```text
@longrun method
  |
  | optional ctx.open_channel(...) subscription
  v
stream/channel processing
```

The canonical design doc is:

- [@longrun-README.md](@longrun-README.md)

Subscription remains an important runtime helper, but it needs the broader
longrun lifecycle first: scoped start, cooperative cancellation, props-change
reconfiguration, restart, health, backpressure, and durable ack semantics.
