---
id: ks:docs/service/fs/file-lock-README.md
title: "File Lock Compatibility Pointer"
summary: "Compatibility pointer for the old observed file lock page. The canonical service-level concurrency document is now Synchronization Mechanisms."
status: superseded
tags: ["service", "fs", "locks", "synchronization", "critical-section"]
keywords: ["file lock", "observed file lock", "synchronization mechanisms", "critical section"]
see_also:
  - ks:docs/service/synch-mechanisms/critical-section-README.md
---
# File Lock Compatibility Pointer

This page moved.

The canonical document is now:

- [Synchronization Mechanisms](../synch-mechanisms/critical-section-README.md)

Use that page for choosing between:

- Postgres advisory locks for database bootstrap and migrations;
- Redis locks for cluster-wide scheduled or runtime coordination;
- observed file locks for shared filesystem mutation.
