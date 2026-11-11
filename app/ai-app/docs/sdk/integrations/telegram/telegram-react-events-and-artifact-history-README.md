---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-react-events-and-artifact-history-README.md
title: "ReAct Events To Telegram And Artifact History"
summary: "How the Telegram activity streamer converts ReAct chat events during a turn, how final delivery reduces the turn result, and why Telegram retains emitted file snapshots while web chat projects the latest artifact per path."
tags: ["sdk", "integrations", "telegram", "react", "streaming", "artifacts"]
keywords: ["telegram react events", "TelegramActivityStreamer", "chat.files", "chat.delta", "telegram artifact versions", "content_sha256", "telegram final delivery"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
---

# ReAct Events To Telegram And Artifact History

The Telegram integration converts one app turn in two passes:

1. `TelegramActivityStreamer` listens to selected `chat.*` communicator events
   while the app is running.
2. After the runner returns, final delivery reduces the returned answer and
   turn log or timeline into Telegram messages.

The live bridge is not tied to ReAct internals. ReAct emits the common chat
event contract, and LangGraph, CrewAI, or custom app runners can emit the same
events. This article uses ReAct because it produces the richest event stream.

## Two Delivery Passes

```text
ReAct runtime
  |
  | selected chat.* communicator events
  v
TelegramActivityStreamer                    LIVE PASS
  |- progress text -> one editable Telegram progress message
  |- chat.files    -> physical Telegram file messages
  `- chat.error    -> visible error

ReAct runner returns
  |
  | answer/final_answer + turn_log/timeline
  v
render_turn_messages(...)                   FINAL PASS
  |- choose the final answer
  |- render sources and externally visible files
  |- exclude exact file versions already sent live
  `- append final text to the progress card when it fits
```

The activity streamer is attached to both the local communicator listener and,
when available, the conversation relay. It filters by `turn_id` and suppresses
duplicate activity signatures so the same relayed event is not handled twice.

## Live Event Conversion

`TelegramActivityStreamer` handles these communicator envelopes:

| Chat event | Telegram behavior |
| --- | --- |
| `chat.delta`, marker `answer` | Ignored during the live pass. The accepted final answer is delivered after the runner returns. |
| `chat.delta`, marker `thinking` | Buffered and added to the editable progress message as **Thinking**. |
| `chat.delta`, marker `timeline_text` | Buffered and added to the progress message as **Notes**. |
| `chat.delta`, marker `canvas` | Adds one short "Working on..." progress update for that artifact. |
| `chat.delta`, marker `subsystem` | Converts web-search, web-fetch, code-exec, and other bounded subsystem updates into concise progress text. |
| `chat.step` or `chat.service` | Shows useful `started`, `running`, `completed`, or `error` statuses; internal persistence and setup statuses are filtered out. |
| `chat.citations` | Adds one "Sources ready" progress update with a bounded citation list. |
| `chat.compaction` | Reports context-compaction start, completion, skip, and useful token details. |
| `chat.files` | Sends each externally visible file immediately as a Telegram document or photo. Internal files are ignored. |
| `chat.error` | Sends a visible error immediately. |

Other event types are not converted by the activity streamer merely because
they exist on the communicator. In particular, partial `answer` deltas are not
sent as progress: Telegram receives the accepted final answer from the final
pass.

The progress updates above are accumulated in one Telegram text message. The
streamer edits that message as new progress arrives instead of sending one text
message per delta. File messages are separate physical Telegram messages.

## Final Turn Conversion

After the app runner returns, `deliver_turn_to_telegram(...)` calls the
framework-neutral final renderer.

The renderer applies this order:

```text
1. Use result.answer or result.final_answer when non-empty.
2. Otherwise read accepted answer/final-answer blocks from turn_log/timeline.
3. Add sources according to the timeline renderer policy.
4. Add externally visible current-turn files not already delivered live.
5. Append final text to the existing progress card when it fits;
   otherwise send the final text as normal follow-up messages.
```

The live streamer returns its `delivered_file_keys` to final delivery. The
final renderer uses those keys to avoid sending the same file version once
during execution and again after completion.

## File Identity And Versions

Telegram is a **copy channel**. Sending a document creates a physical Telegram
message whose bytes do not change when the KDCube artifact is edited later.

KDCube web chat is a **current-state view**. For one turn and logical artifact
path, its file projection keeps the latest artifact record. The hosted URI is
stable and resolves the current bytes.

Consequently, the same conversation intentionally has different artifact
history in the two interfaces:

```text
Round 1: react.write report.md with VERSION 1
  Telegram -> sends a physical VERSION 1 document
  Web      -> report.md resolves VERSION 1

Round 2 of the same turn: react.patch report.md to VERSION 2
  Telegram -> sends a second physical document containing VERSION 2
  Web      -> the same report.md artifact now resolves VERSION 2

After the turn
  Telegram -> contains both emitted snapshots: VERSION 1 and VERSION 2
  Web      -> shows the latest report.md artifact for that turn: VERSION 2
```

This is deliberate transport behavior, not cross-interface version parity.
Telegram preserves the distinct file snapshots that were successfully emitted
and delivered during the turn. Web chat preserves the latest artifact per turn
and logical path. The latest content agrees, but the visible file-message count
does not have to agree.

The historical Telegram copy can therefore be useful for seeing what was sent
at each point in a long turn. It is not the authoritative current artifact;
the KDCube hosted artifact is.

## Content-Scoped Deduplication

A Telegram file-delivery key is:

```text
delivery path + content_sha256
```

The delivery path is selected from the hosted URL, resource name, storage key,
logical path, physical path, or filename. Built-in artifact hosting calculates
`content_sha256` from the bytes it already reads and carries it as transport
metadata. The hash is not added to the model-facing artifact digest.

The resulting behavior is:

| Observation | Telegram action |
| --- | --- |
| Same path and same hash | Suppress as an exact duplicate. |
| Same path and changed hash | Deliver as another physical file snapshot. |
| File sent live, then found unchanged at finalization | Suppress the final duplicate. |
| Legacy event without a hash | Fall back to size, then path-only deduplication. |

Custom `chat.files` producers should include `content_sha256` when they can
emit changed bytes at the same path. Size alone cannot distinguish two
different same-size files.

The "all emitted versions" behavior applies to distinct file versions that the
live activity streamer successfully sends. If
`integrations.telegram.stream_activity=false`, Telegram does not receive those
live snapshots; final delivery renders from the returned turn output instead.

## ReAct Write And Patch Contract

The ReAct authoring contract prevents accidental full rewrites from being
mistaken for edits:

- `react.write` creates a current-turn path once;
- a second `react.write` for that path is rejected with
  `protocol_violation.write_path_already_exists`;
- `react.patch` performs an intentional in-place edit and can therefore produce
  a new Telegram snapshot at the same path.

Other authorized producers, such as an exec or renderer, can also emit changed
content at an existing output path. Content-scoped Telegram deduplication is
therefore still required even though duplicate `react.write` is rejected.

## Configuration Effects

| Configuration | Result |
| --- | --- |
| `stream_activity=true`, `stream_activity_display=true` | Stream progress, files, citations, statuses, and errors. |
| `stream_activity=true`, `stream_activity_display=false` | Suppress progress display, but still deliver `chat.files` and `chat.error` live. |
| `stream_activity=false` | Disable the live pass. With `send_responses=true`, final delivery sends the final answer, then the timeline's files and every file the turn announced through `chat.files` (collected silently while the turn ran), each once. |
| `send_responses=false` | Do not send the final rendered turn response. |

## Implementation Map

```text
sdk/integrations/telegram/stream.py
  TelegramActivityStreamer
  live chat.* event selection and conversion
  progress-card updates
  live file delivery and delivered_file_keys

sdk/integrations/telegram/router.py
  render_turn_messages
  deliver_turn_to_telegram
  framework-neutral final delivery

sdk/integrations/telegram/bot.py
  timeline answer/source/file reduction
  content-scoped file keys
  Telegram Bot API text/document/photo sends

sdk/solutions/react/solution_workspace.py
  artifact hosting and content_sha256 calculation

sdk/runtime/harness/timeline/turn_view.py
  latest-file-per-path web projection
```

## Regression Checks

For one Telegram-originated ReAct turn:

1. Emit one file and verify Telegram receives it immediately.
2. Emit the exact same file event again and verify no duplicate is sent.
3. Patch the file and verify Telegram receives the changed snapshot.
4. Complete the turn and verify final delivery does not resend the latest
   snapshot when it was already sent live.
5. Open the same conversation in web chat and verify its artifact list contains
   the latest file for that turn and path.

Expected result: Telegram contains the distinct successfully emitted file
snapshots; web chat exposes the latest artifact; both expose the same latest
content.
