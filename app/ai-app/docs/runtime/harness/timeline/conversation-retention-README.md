---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-retention-README.md
title: "Conversation Retention: Hot Index and Cold Tier"
summary: "Conversation index rows older than the hot window move to a verified cold tier in bundle storage, embeddings included; date-filtered reads reach them by time, and explicit deletions remove a scope from both tiers with an audit row."
tags: ["runtime", "conversation", "retention", "storage", "postgres"]
updated_at: 2026-10-05
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
  gzip JSONL parts in bundle storage, organized like the conversation store
  itself: one folder per user, one per conversation inside it, then the UTC
  day:

      cb/tenants/{tenant}/projects/{project}/conversation/{user}/{conversation}/...                      (bodies)
      cb/tenants/{tenant}/projects/{project}/conversation-cold/{user}/{conversation}/{yyyy}/{mm}/{dd}/{batch_id}.jsonl.gz
      cb/tenants/{tenant}/projects/{project}/conversation-cold/{user}/{conversation}/{yyyy}/{mm}/{dd}/{batch_id}.manifest.json

  Users, the projects they join and the agents they talk to have no bound,
  so a part never mixes users or conversations: one user's archive is one
  folder, and one conversation's archive is one folder inside it. A slash in
  an id is escaped, so an id never adds a folder. Parts written before this
  layout sit directly under `conversation-cold/{yyyy}/{mm}/{dd}/` and stay
  readable: the batch ledger records every part's own location. Each archive
  run moves them to the new layout: a legacy part is read and verified,
  rewritten as one verified part per user and conversation, retired in the
  transaction that records the new parts, and then removed.

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
(default 14).

## How reads reach the cold tier

A read with an explicit date range whose start is older than the archive
watermark (the newest archived message time) also reads the cold days in that
range. `ConvIndex.fetch_turn_catalog`, which serves temporal conversation
search, appends cold turns by time, without ranking and without ordinals,
marked `"storage": "cold"`.

Topic search (the hybrid path, a query plus a date filter) reads them too
(W536). `ConvIndex.search_cold_turns` takes the lower bound of the search's
`timestamp_filters` (`>` or `>=`; without one it reads nothing), reads the cold
records in that range under the hot index's scope rules (user, conversation
when scoped, bundle, agent, roles, any of the target's tags, TTL, recovery
sessions excluded) and ranks them by the share of the query's content terms
each text contains, one row per turn. `search_context` fuses that list as a
fourth rank arm, weighted like the lexical arms; an archived turn has no hot
arm. Every hit carries `storage` (`"hot"` or `"cold"`) as the backend read it,
through `run_conversation_search` to the ingress `ConversationSearchHit` and
the TypeScript `ConversationSearchHit`. The cold arm has no semantic or
trigram ranking, so a cold hit ranks by its term share and the recency lift.

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
- `fetch_message_page` with a `conversation_id` does the same on its keyset:
  once the hot rows run out, the conversation's archived messages continue
  newest-first strictly before the cursor's `(ts, id)`, each marked
  `"storage": "cold"`, so a cold search match can be opened (W536).

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
records and the stored bodies of both. It reads only the cold parts the
conversation index lists for that conversation, never the whole cold tier;
when part of a part stays (a project filter, or a legacy day part shared with
other conversations), what stays is rewritten as new verified parts, one per
user and conversation. Apps pass their `ConversationStore` through
`ConvIndex.retention(store=...)` so the bodies go too. Every deletion is a row
in `conv_archive_deletions`, written before anything is deleted with the
actor, the time and the scope, and finished `completed` with its counts or
`failed` with the error. Bodies are deleted before the records that point at
them, so running a failed deletion again finds every body it left behind.

Reads and deletions follow the ledger: only batches it records as `pruned`
are cold data. A part left in storage by an interrupted archive or
retirement is never read.
