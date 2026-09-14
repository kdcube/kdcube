---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
title: "Telegram Webhook Submit And Queued Delivery"
summary: "Exact runtime data path for Telegram bot messages and private-chat topics: webhook acknowledgement, shared chat ingress, processor-side app execution, activity streaming, and final delivery."
tags: ["sdk", "integrations", "telegram", "topics", "webhook", "chat-ingress", "queued-delivery", "agent-runtime"]
keywords: ["telegram webhook", "telegram private chat topics", "message_thread_id", "telegram submitter", "telegram queued delivery", "submit_telegram_turn", "run_with_queued_telegram_delivery", "TelegramActivityStreamer", "deliver_turn_to_telegram"]
updated_at: 2026-09-14
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-react-events-and-artifact-history-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
---

# Telegram Webhook Submit And Queued Delivery

This article describes the normal Telegram bot message path in KDCube. A
Telegram webhook request does not run the app's agent or workflow inline and
does not own final response delivery. The webhook submits the message to shared
chat ingress. The processor later runs the app, and the app wraps that run with
`telegram_user_admin.run_with_queued_telegram_delivery(...)`.

## Local Direct-Agent Mode

The runnable programs under [`agents/`](../../../../../../agents/README.md)
also expose a separate development adapter named `--telegram-local`. That
adapter validates the Telegram webhook secret and reuses this SDK's update,
attachment, and final-delivery functions, then invokes one
`DirectAgentHarness` turn inline in the webhook process. Its update claims and
turn serialization belong to that one process, while concurrent request order
remains unspecified.

This direct adapter is the shortest path for trying one agent and one bot from
a shell. Ordered, restart-safe, asynchronous, and multiworker delivery uses the
normal managed flow below, or a durable queue supplied by the direct host. The
[direct-agent recipe](../../../recipes/quickstart/run-agent-harness-from-python-README.md#10-connect-a-local-telegram-bot)
owns the exact setup and boundary. Connection Hub participates in identity
linking and delegated authority for a hosted product; transport ingress remains
owned by the app and chat runtime.

## Normal Flow

```text
Telegram Bot API
  POST /public/telegram_webhook
    |
    v
app telegram_webhook(...)
  -> telegram_user_admin.handle_webhook(entrypoint, **update)
       - summarize Telegram update
       - claim update_id for idempotency
       - hydrate Telegram files when needed
       - resolve registered/admin Telegram user
       - bind (integration_id, chat_id, message_thread_id) to a conversation when present
       - call submit_telegram_turn(...)
            |
            v
            ChatIngressSubmitter.submit(...)
              message_data.payload.source = "telegram"
              message_data.payload.telegram = {chat_id, message_thread_id, update_id, turn_id, ...}
              message_data.agent_id = surfaces.as_consumer.default_agent
              message_data.external_events[] = event.user.prompt/followup/steer + attachments
            |
            v
            return accepted/rejected webhook acknowledgement

processor later claims queued chat turn
  -> creates app entrypoint with comm_context.request.payload.telegram
  -> app run path calls:
       telegram_user_admin.run_with_queued_telegram_delivery(entrypoint, runner=...)
          |
          +-- TelegramActivityStreamer observes comm events while runner executes
          |
          +-- runner() runs the app's configured workflow
              ReAct | LangGraph | CrewAI | custom async code
          |
          +-- deliver_turn_to_telegram(...)
                renders final Telegram messages from runner result and turn log
                sends them to the originating message_thread_id when present
```

There is no webhook-side agent-execution fallback. If shared chat ingress is
unavailable, the webhook sends a short retry response and does not run the app
in the webhook process. An unlinked user receives the Connection Hub linking
prompt directly; that prompt is a transport response, not an agent turn.

The submit and delivery paths do not use process-local conversation locks. Such
a lock cannot order requests across processors or replicas. Shared chat ingress,
conversation-state compare-and-set, the queue, and the retained Redis event lane
own admission and turn-order correctness.

`/stop` is normalized to the same `event.user.steer` event used by classic chat
and passes through the same shared ingress and Redis lane. It is accepted only
for the currently active turn. With no active turn it is a successful no-op; it
never queues a new turn.

Transport parity does not give every agent framework live steering by itself.
The built-in ReAct harness has a live event listener and can interrupt an active
phase. A run-to-completion app, including the current ported LangGraph example,
does not consume mid-turn lane events unless its runner adds a cancellation or
steer adapter. It still receives normal Telegram turns through the same ingress,
identity, attachment, ordering, and delivery path.

The Telegram payload can contain Telegram-specific ids and transport metadata.
The effective runtime turn id is still the chat ingress
`ExternalEventPayload.routing.turn_id`; see
[Event Ingress To React Turn](../../events/event-ingress-to-react-turn-README.md).

Private-chat topic identity and application authorization are separate
contracts. The webhook uses `message_thread_id` to select a stable conversation
and the delivery path uses it to return output to the same topic. The app still
checks which domain resources that authenticated user may access. The canonical
topic contract is
[Native Private-Chat Topics](telegram-README.md#native-private-chat-topics).

## Boundary Diagram

```text
┌──────────────────────────────┐
│ Telegram Bot API              │
│ external HTTP caller          │
└───────────────┬──────────────┘
                │ POST update
                v
┌──────────────────────────────────────────────────────────────────────┐
│ BUNDLE WEBHOOK REQUEST                                                │
│ owner: app public API + telegram.user_admin.handle_webhook             │
│                                                                      │
│ input: raw Telegram update                                            │
│ output: HTTP acknowledgement to Telegram                              │
│                                                                      │
│ allowed work:                                                         │
│   - verify webhook secret                                             │
│   - claim update_id                                                   │
│   - hydrate Telegram files                                            │
│   - resolve Telegram user and main-chat or topic-bound conversation     │
│   - submit external_events[] to chat ingress                          │
│                                                                      │
│ forbidden work on normal path:                                        │
│   - run the app workflow inline                                       │
│   - send the final assistant answer                                   │
│   - start a second answer relay                                       │
└───────────────┬──────────────────────────────────────────────────────┘
                │ ChatIngressSubmitter.submit(...)
                │ message_data.payload.telegram = transport metadata
                │ message_data.external_events[] = model/context input
                v
┌──────────────────────────────────────────────────────────────────────┐
│ CHAT INGRESS / CONVERSATION QUEUE                                     │
│ owner: platform ingress + processor queue                             │
│                                                                      │
│ stores/queues:                                                        │
│   - tenant/project/bundle/user/session/conversation/turn ids           │
│   - request payload with payload.source="telegram"                    │
│   - payload.telegram={chat_id, message_thread_id, update_id, ...}      │
│   - external_events[] lane entries                                    │
│                                                                      │
│ does not know:                                                        │
│   - what answer Telegram will receive                                 │
│   - which agent framework the app runs                                │
│   - how the app will reduce its final result                          │
└───────────────┬──────────────────────────────────────────────────────┘
                │ processor claims queued turn
                v
┌──────────────────────────────────────────────────────────────────────┐
│ PROCESSOR-SIDE BUNDLE RUN                                             │
│ owner: app entrypoint                                                 │
│                                                                      │
│ input: comm_context.request.payload.telegram                          │
│ required wrapper:                                                     │
│   telegram_user_admin.run_with_queued_telegram_delivery(...)           │
│                                                                      │
│ wrapper owns:                                                         │
│   - TelegramActivityStreamer lifecycle                                │
│   - final call to deliver_turn_to_telegram(...)                        │
│                                                                      │
│ runner owns:                                                          │
│   - the app's configured async workflow                               │
│   - ReAct, LangGraph, CrewAI, or custom execution                     │
│   - turn_log/timeline/result payload                                  │
└───────────────┬──────────────────────────────────────────────────────┘
                │ runner()
                v
┌──────────────────────────────────────────────────────────────────────┐
│ APP WORKFLOW                                                          │
│ owner: app (bundle)                                                   │
│                                                                      │
│ input: canonical external_events[] plus platform turn context          │
│ live output: comm events, progress, files, citations, answer blocks    │
│ durable output: turn_log.blocks[], artifacts, indexed messages         │
│ returned output: result.answer/final_answer plus turn_log/timeline     │
└───────────────┬──────────────────────────────────────────────────────┘
                │ result returns to queued-delivery wrapper
                v
┌──────────────────────────────────────────────────────────────────────┐
│ TELEGRAM FINAL DELIVERY                                               │
│ owner: telegram.router + telegram.bot + telegram.stream                │
│                                                                      │
│ inputs:                                                              │
│   - result.answer or result.final_answer                              │
│   - result.turn_log or result.timeline                                │
│   - delivered_file_keys from activity streamer                        │
│   - progress_message_id/progress_summary                              │
│                                                                      │
│ output: Telegram Bot API messages                                     │
│                                                                      │
│ current reducer rule:                                                 │
│   1. use non-empty result.answer/result.final_answer                   │
│   2. otherwise collect assistant answer blocks from turn_log/timeline  │
│   3. append sources/files according to renderer policy                 │
└──────────────────────────────────────────────────────────────────────┘
```

Key boundary rule:

```text
payload.telegram       = transport metadata for delivery
external_events[]      = context/model input
turn_log.blocks[]      = durable turn output and fallback render source
result.answer          = reduced final answer chosen by the app workflow
Telegram final message = renderer output from result + turn_log/timeline
```

## Webhook Responsibility

The webhook handler should be thin:

```python
@api(
    method="POST",
    alias="telegram_webhook",
    route="public",
)
async def telegram_webhook(self, **update):
    return await telegram_user_admin.handle_webhook(self, **update)
```

`handle_webhook(...)` owns:

| Step | Data | Result |
| --- | --- | --- |
| Extract update | raw Telegram JSON | normalized summary with text, chat id, optional topic id, user id, files |
| Claim update | `update_id` | duplicate updates are acknowledged and ignored |
| Hydrate files | Telegram file ids | byte payloads ready for hosting/submission |
| Resolve user | Telegram user/chat | KDCube user id, role, conversation id |
| Submit turn | normalized text/files | `ChatIngressSubmitter.submit(...)` result |
| Complete webhook update | accepted/rejected payload | idempotency state for the update |

The webhook should not start a background relay subscriber for the final answer.
It should not duplicate final Bot API sends that the processor-side wrapper
will perform.

Command handlers such as `/start`, `/pause`, or product-specific quick replies
may send immediate Telegram messages before delegating to `handle_webhook(...)`.
Those are command responses, not the normal app-turn delivery path.

## Submitted Data Shape

`submit_telegram_turn(...)` sends one chat-ingress submission. The relevant fields
are:

```json
{
  "tenant": "demo-tenant",
  "project": "demo-project",
  "bundle_id": "my.bundle@1-0",
  "conversation_id": "telegram_integration_telegram_default_chat_12345_topic_73",
  "turn_id": "turn_2026-06-18-12-00-00-000",
  "agent_id": "main",
  "payload": {
    "source": "telegram",
    "agent_id": "main",
    "telegram": {
      "chat_id": "12345",
      "message_thread_id": "73",
      "update_id": "98765",
      "message_id": 222,
      "kdcube_user_id": "internal:telegram:12345",
      "role": "registered",
      "conversation_id": "telegram_integration_telegram_default_chat_12345_topic_73",
      "turn_id": "turn_2026-06-18-12-00-00-000"
    }
  },
  "external_events": [
    {
      "type": "event.user.prompt",
      "event_source_id": "telegram.user.prompt",
      "agent_id": "main",
      "reactive": true,
      "payload": {
        "mime": "text/plain",
        "event": {"text": "hello"}
      }
    }
  ]
}
```

`payload.telegram` is not a model-facing object. It is transport metadata used
later by the processor-side wrapper to know where to stream and deliver the
Telegram response.

`agent_id` is the app's configured `surfaces.as_consumer.default_agent`. This
keeps event-lane identity, grants, accounting, and app dispatch aligned for
ReAct and non-ReAct apps. The legacy platform default is used only when the app
does not declare a default agent.

`external_events[]` is the canonical app-turn input. Telegram text and Telegram
attachments are represented there so every app framework sees the same event
model used by browser transports. Ingress receives the file bytes separately as
`RawAttachment`, validates and hosts them, then adds `hosted_uri`, `key`, and
`rn` to the corresponding attachment events before the turn is queued.

## Processor-Side Wrapper

Every Telegram-capable app that accepts submitted Telegram turns must wrap its
real async run. The runner is framework-neutral:

```python
async def _run_app_turn() -> dict:
    return await execute_core(state=state, thread_id=conversation_id)

result = await telegram_user_admin.run_with_queued_telegram_delivery(
    self,
    runner=_run_app_turn,
)
```

The built-in ReAct app can make `_run_app_turn` call its ReAct runtime. The
ported LangGraph example makes it call the same `execute_core` used by browser
chat. CrewAI and custom apps use their own async runner in the same position.

`run_with_queued_telegram_delivery(...)` performs this exact sequence:

| Step | Behavior |
| --- | --- |
| Read metadata | Looks for `comm_context.request.payload["telegram"]`. If absent, returns `await runner()` unchanged. |
| Stream progress | Opens `TelegramActivityStreamer(...)` around `await runner()`. |
| Run app | Executes the supplied `runner`, which owns the app workflow. |
| Final delivery | Calls `deliver_turn_to_telegram(...)` with the runner result, delivered file keys, progress message id, and progress summary. |
| Return result | Adds `result["telegram"] = {...delivery metadata...}` and returns the result. |

The helper does not create the assistant answer. It only wraps execution and
performs Telegram transport delivery from the app's result and turn log.

## Runner Result Contract

The runner should return a dictionary with these fields when available:

| Field | Meaning | Used by Telegram delivery |
| --- | --- | --- |
| `answer` or `final_answer` | Reduced final answer text selected by the app workflow. | Preferred first by the Telegram renderer when non-empty. |
| `suggested_followups` or `followups` | Optional next-action text. | Preserved in returned result; renderer may ignore unless explicitly supported. |
| `turn_log` | Current turn payload with `blocks[]`. | Used to render answer blocks, sources, and files when needed. |
| `timeline` | Timeline-like payload. | Fallback if `turn_log` is absent. |

Current Telegram final rendering uses `render_turn_messages(...)`. The older
`render_react_turn_messages(...)` and `deliver_react_turn_to_telegram(...)`
names remain compatibility wrappers. The reduction rule is:

```text
if result.answer/result.final_answer is non-empty:
    send that reduced answer text
else:
    collect answer blocks from result.turn_log/result.timeline
```

This means an app must not synthesize a generic fallback answer unless that is
really the intended user-facing answer. A generic fallback in `result.answer`
will hide useful assistant completion blocks that are present in the turn log.

## Activity Streaming During The Processor Run

`TelegramActivityStreamer` observes the chat communicator while `runner()`
executes. It can update a Telegram progress card and send files/citation/status
notifications according to the SDK implementation.

The activity stream is transport-level progress. It is not the same thing as
the final answer reducer. The final Telegram answer is still produced after
`runner()` returns by `deliver_turn_to_telegram(...)`.

`integrations.telegram.stream_activity_display=false` suppresses the progress
display part of the streamer, but the streamer still accepts `chat.files` and
`chat.error` events. This lets Telegram stay quiet during a turn while preserving
live file delivery and final-delivery de-duplication. Set
`integrations.telegram.stream_activity=false` only when the streamer itself
should be disabled.

The wrapper passes `delivered_file_keys` from the streamer to final delivery so
file artifacts already sent during progress streaming are not sent twice.

File keys are content-scoped. An exact live-versus-final duplicate is
suppressed, while a changed file emitted at the same path is a new Telegram
snapshot. Telegram retains every distinct snapshot successfully delivered
during the live pass. The web view of the same conversation projects only the
latest artifact per turn and logical path. This difference is intentional and
is documented with the complete event mapping in
[ReAct Events To Telegram And Artifact History](telegram-react-events-and-artifact-history-README.md).

## What Not To Build In A Bundle

Do not add these patterns to a Telegram-capable app:

| Wrong pattern | Why it is wrong |
| --- | --- |
| Webhook-side final answer relay | Duplicates the processor-side delivery wrapper and can race with queued execution. |
| Webhook-side app run for normal registered users | Bypasses shared chat ingress, external-event ordering, and processor ownership. |
| Separate background subscriber just for Telegram answers | Splits delivery from the actual turn result and turn log. |
| Generic fallback answer in `result.answer` when answer blocks exist | Masks useful legal assistant completions from `turn_log`. |
| Telegram-specific model input outside `external_events[]` | Makes Telegram turns diverge from browser/event semantics. |

## Debugging Checklist

When Telegram delivery looks wrong, inspect these facts in order:

| Check | Expected |
| --- | --- |
| Webhook log | `telegram submitter result` exists for the `update_id`. |
| Ingress result | accepted turn has `payload.telegram` and `external_events[]`. |
| Processor log | app run path calls `run_with_queued_telegram_delivery(...)`. |
| Wrapper metadata | `_queued_telegram_meta(...)` finds `chat_id`, optional `message_thread_id`, `update_id`, and `turn_id`. |
| Runner result | returned dict has either a correct `answer` or useful answer blocks in `turn_log.blocks[]`. |
| Renderer log | `telegram response rendered` shows message count and source `turn_log` or `timeline`. |
| Delivery log | `telegram delivery finished` shows sent count or Bot API error. |
