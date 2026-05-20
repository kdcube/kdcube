---
id: ks:docs/arch/proc/design/README.md
title: "Proc Design Notes"
summary: "Design notes for the next-generation processor scheduler and conversation ownership model."
tags: ["arch", "proc", "design", "scheduler", "redis-streams", "kafka"]
keywords: ["proc design", "conversation scheduler", "redis streams", "kafka", "leases", "wake stream"]
see_also:
  - ks:docs/arch/proc/processor-arch-README.md
  - ks:docs/arch/proc/longrun-protection-README.md
---
# Proc Design Notes

This directory contains **forward-looking processor design notes**.

The current source-of-truth for the shipped processor remains:

- [processor-arch-README.md](../processor-arch-README.md)
- [longrun-protection-README.md](../longrun-protection-README.md)

Current design notes:

- [conversation-scheduler-streams-README.md](conversation-scheduler-streams-README.md)
  The proposed migration from the current global Lists-based proc queue to a
  conversation-oriented scheduler built around Redis Streams, leases, and
  owner-loop execution, with an explicit Kafka mapping for the same scheduler
  semantics if we later want Kafka as the transport backend.
