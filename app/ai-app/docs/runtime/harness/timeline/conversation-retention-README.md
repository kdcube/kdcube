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
`conversation-archive` (02:20 UTC, one instance per tenant and project). It is
on by default: rows past the hot window move to the cold tier on the next
nightly run. The assembly property `routines.conversation_store.archive_enabled:
false` turns it off. The window is `routines.conversation_store.hot_days`
(default 90).

## How reads reach the cold tier

A read with an explicit date range whose start is older than the archive
watermark (the newest archived message time) also reads the cold days in that
range. `ConvIndex.fetch_turn_catalog`, which serves temporal conversation
search, appends cold turns by time, without ranking and without ordinals,
marked `"storage": "cold"`.

Opening and listing conversations reach the cold tier too, within their own
rolling window (`days`, 365 for the conversation browser; a conversation older
than the window is neither listed nor opened, hot or cold):

- `list_user_conversations` still lists a conversation whose messages were all
  archived, after every conversation with hot messages, and takes a
  conversation's start from its archived messages when they are older.
- `get_conversation_turn_ids_from_tags`, which opens a conversation, returns
  its archived turns before its hot ones, so every turn and its stored body
  appear.
- `fetch_recent` with a `conversation_id` continues newest-first into the
  conversation's archived messages when the hot rows do not fill the limit.
  Archived rows are older than the hot window, so this matters only for a
  caller whose `days` exceeds it (the browser passes 365; the default is 30).

These reads use `conv_archive_conversations`, written in the same transaction
as each batch's ledger row: one row per conversation scope in a batch, with its
time range and every conversation start with its own expiry (a scope can mix
TTLs, so a start that has expired is never listed). Listing reads only that
table; opening reads only the parts that hold the conversation. A batch
archived before the table, or before its per-start expiry, existed is indexed
by the next archive run.

Cross-conversation reads without a date range serve the hot index only.

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
