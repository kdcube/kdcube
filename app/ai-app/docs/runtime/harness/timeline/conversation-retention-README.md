---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-retention-README.md
title: "Conversation Retention: Hot Index and Cold Tier"
summary: "Conversation index rows older than the hot window move to a verified cold tier in bundle storage, embeddings included; date-filtered reads reach them by time, and explicit deletions remove a scope from both tiers with an audit row."
tags: ["runtime", "conversation", "retention", "storage", "postgres"]
updated_at: 2026-10-04
keywords: ["conv_messages", "cold tier", "hot_days", "conv_archive_batches", "conv_archive_deletions", "ConversationRetention", "ConversationColdArchive", "conversation-archive"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/hosting/files-storage-system-README.md
---

# Conversation Retention: Hot Index and Cold Tier

## Why it exists

Every message an app records becomes a row in `conv_messages`: its text, its
embedding and its tags. Without retention the table grows for as long as the
project lives, and its vector and text indexes grow with it. Retention keeps
the recent window in PostgreSQL, where search ranks it, and moves older rows
to bundle storage, where they cost little and are still readable by date. The
embeddings move with them: they cost money to compute and stay on record.

## What lives where

- **Hot index:** `conv_messages` rows newer than the hot window. Every read
  works as before.
- **Message bodies:** unchanged. `ConversationStore` already keeps each body
  in bundle storage; a row's `hosted_uri` still resolves after the row moves.
- **Cold tier:** whole index rows, embedding and artifact edges included, as
  gzip JSONL parts in bundle storage, one folder per UTC day:

      cb/tenants/{tenant}/projects/{project}/conversation-cold/{yyyy}/{mm}/{dd}/{batch_id}.jsonl.gz
      cb/tenants/{tenant}/projects/{project}/conversation-cold/{yyyy}/{mm}/{dd}/{batch_id}.manifest.json

  A manifest carries the row count, the row ids, the time range and the
  sha256 of its part.

## How rows move

`ConversationRetention.archive_before(cutoff)` (SDK,
`context/vector/conv_retention.py`) takes the oldest rows before the cutoff in
batches, writes each day's part and manifest, reads the part back and checks
it against the manifest, and only then deletes those rows in the same
transaction that marks the batch `pruned` in `conv_archive_batches`. A failed
check deletes nothing and records the error on the batch. A run that stops
anywhere resumes from the ledger on the next run.

The built-in admin bundle runs it daily as the system cron
`conversation-archive` (02:20 UTC, one instance per tenant and project), and
only when the assembly property `routines.conversation_store.archive_enabled`
is true: retention is off until the operator turns it on. The window is
`routines.conversation_store.hot_days` (default 90).

## How reads reach the cold tier

A read with an explicit date range whose start is older than the archive
watermark (the newest archived message time) also reads the cold days in that
range. `ConvIndex.fetch_turn_catalog`, which serves temporal conversation
search, appends cold turns by time, without ranking and without ordinals,
marked `"storage": "cold"`. Reads without a date range serve the hot index
only.

## Deleting a scope

`ConversationRetention.delete_messages(actor, user_id, conversation_id,
bundle_id, tags_all, reason)` removes the matching hot rows, their cold
records and the stored bodies of both, rewriting each affected cold part as a
new verified batch. Apps pass their `ConversationStore` through
`ConvIndex.retention(store=...)` so the bodies go too. Every deletion is a row
in `conv_archive_deletions`, written before anything is deleted with the
actor, the time and the scope, and finished `completed` with its counts or
`failed` with the error. Bodies are deleted before the records that point at
them, so running a failed deletion again finds every body it left behind.

Reads and deletions follow the ledger: only batches it records as `pruned`
are cold data. A part left in storage by an interrupted archive or
retirement is never read.
