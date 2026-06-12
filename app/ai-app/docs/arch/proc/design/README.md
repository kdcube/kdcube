---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/proc/design/README.md
title: "Proc Design Notes"
summary: "Design notes for the next-generation processor scheduler and conversation ownership model."
tags: ["arch", "proc", "design", "scheduler", "redis-streams", "kafka", "cron"]
keywords: ["proc design", "conversation scheduler", "redis streams", "kafka", "leases", "wake stream", "cron longrun"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/processor-arch-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/longrun-protection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
---
# Proc Design Notes

This directory contains **forward-looking processor design notes**.

The source of truth for shipped processor/runtime behavior is outside this
directory:

- [processor-arch-README.md](../processor-arch-README.md)
- [longrun-protection-README.md](../longrun-protection-README.md)
- [events-orchestration-README.md](../events-orchestration-README.md)
- [Conversation Event Lane State](../../../sdk/events/conversation-event-lane-state-README.md)
- [event-bus-simulator-README.md](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/event-bus-simulator-README.md)

Current design notes:

- [conversation-scheduler-streams-README.md](conversation-scheduler-streams-README.md)
  The proposed migration from the current global Lists-based proc queue to a
  conversation-oriented scheduler built around Redis Streams, leases, and
  owner-loop execution, with an explicit Kafka mapping for the same scheduler
  semantics if we later want Kafka as the transport backend.

- [longrun-protection-for-cron-README.md](longrun-protection-for-cron-README.md)
  The proposed extension that brings `@cron` jobs under the same active-task
  heartbeat, useful-activity, and hard wall-time protection model as proc-owned
  chat work.
