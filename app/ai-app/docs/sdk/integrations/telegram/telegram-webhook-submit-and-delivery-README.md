---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
title: "Telegram Webhook Submit And Queued Delivery"
summary: "Exact runtime data path for Telegram bot messages: webhook acknowledgement, chat ingress submission, processor-side ReAct execution, activity streaming, and final Telegram delivery."
tags: ["sdk", "integrations", "telegram", "webhook", "chat-ingress", "queued-delivery", "react"]
keywords: ["telegram webhook", "telegram submitter", "telegram queued delivery", "run_with_queued_telegram_delivery", "TelegramActivityStreamer", "deliver_react_turn_to_telegram"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
---

# Telegram Webhook Submit And Queued Delivery

This article describes the normal Telegram bot message path in KDCube. A
Telegram webhook request does not normally run the ReAct turn inline and does
not own final response delivery. The webhook submits the message to shared chat
ingress. The processor later runs the bundle and the bundle wraps that run with
`telegram_user_admin.run_with_queued_telegram_delivery(...)`.

## Normal Flow

```text
Telegram Bot API
  POST /public/telegram_webhook
    |
    v
bundle telegram_webhook(...)
  -> telegram_user_admin.handle_webhook(entrypoint, **update)
       - summarize Telegram update
       - claim update_id for idempotency
       - hydrate Telegram files when needed
       - resolve registered/admin Telegram user
       - acquire Telegram conversation lock
       - call submit_react_turn(...)
            |
            v
            ChatIngressSubmitter.submit(...)
              message_data.payload.source = "telegram"
              message_data.payload.telegram = {chat_id, update_id, turn_id, ...}
              message_data.external_events[] = event.user.prompt/followup/steer + attachments
            |
            v
            return accepted/rejected webhook acknowledgement

processor later claims queued chat turn
  -> creates bundle entrypoint with comm_context.request.payload.telegram
  -> bundle graph calls:
       telegram_user_admin.run_with_queued_telegram_delivery(entrypoint, runner=...)
          |
          +-- TelegramActivityStreamer observes comm events while runner executes
          |
          +-- runner() runs the real ReAct workflow
          |
          +-- deliver_react_turn_to_telegram(...)
                renders final Telegram messages from runner result and turn log
```

The fallback inline path `run_react_turn(...)` exists only for environments
where `entrypoint.chat_submitter.submit` is not available or the SDK cannot
submit a registered Telegram user to chat ingress. Reference bundles should use
the submitter path.

The Telegram payload can contain Telegram-specific ids and transport metadata.
The effective ReAct runtime turn id is still the chat ingress
`ExternalEventPayload.routing.turn_id`; see
[Event Ingress To React Turn](../../events/event-ingress-to-react-turn-README.md).

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
│ owner: bundle public API + telegram.user_admin.handle_webhook          │
│                                                                      │
│ input: raw Telegram update                                            │
│ output: HTTP acknowledgement to Telegram                              │
│                                                                      │
│ allowed work:                                                         │
│   - verify webhook secret                                             │
│   - claim update_id                                                   │
│   - hydrate Telegram files                                            │
│   - resolve Telegram user and conversation                            │
│   - submit external_events[] to chat ingress                          │
│                                                                      │
│ forbidden work on normal path:                                        │
│   - run ReAct inline                                                  │
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
│   - request payload with payload.telegram={chat_id, update_id, ...}    │
│   - external_events[] lane entries                                    │
│                                                                      │
│ does not know:                                                        │
│   - what answer Telegram will receive                                 │
│   - how the bundle will render its final ReAct result                  │
└───────────────┬──────────────────────────────────────────────────────┘
                │ processor claims queued turn
                v
┌──────────────────────────────────────────────────────────────────────┐
│ PROCESSOR-SIDE BUNDLE RUN                                             │
│ owner: bundle entrypoint graph                                        │
│                                                                      │
│ input: comm_context.request.payload.telegram                          │
│ required wrapper:                                                     │
│   telegram_user_admin.run_with_queued_telegram_delivery(...)           │
│                                                                      │
│ wrapper owns:                                                         │
│   - Telegram conversation delivery lock                               │
│   - TelegramActivityStreamer lifecycle                                │
│   - final call to deliver_react_turn_to_telegram(...)                  │
│                                                                      │
│ runner owns:                                                          │
│   - workflow.process_*_turn(...)                                      │
│   - ReAct construction and execution                                  │
│   - turn_log/timeline/result payload                                  │
└───────────────┬──────────────────────────────────────────────────────┘
                │ runner()
                v
┌──────────────────────────────────────────────────────────────────────┐
│ REACT WORKFLOW                                                        │
│ owner: bundle workflow + React SDK                                    │
│                                                                      │
│ model-facing input: external_events[] folded into the timeline         │
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
result.answer          = reduced final answer chosen by the bundle/workflow
Telegram final message = renderer output from result + turn_log/timeline
```

## Webhook Responsibility

The webhook handler should be thin:

```python
@api(
    method="POST",
    alias="telegram_webhook",
    route="public",
    public_auth=TELEGRAM_WEBHOOK_PUBLIC_AUTH,
)
async def telegram_webhook(self, **update):
    return await telegram_user_admin.handle_webhook(self, **update)
```

`handle_webhook(...)` owns:

| Step | Data | Result |
| --- | --- | --- |
| Extract update | raw Telegram JSON | normalized summary with text, chat id, user id, files |
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
Those are command responses, not the normal ReAct turn delivery path.

## Submitted Data Shape

`submit_react_turn(...)` sends one chat-ingress submission. The relevant fields
are:

```json
{
  "tenant": "demo-tenant",
  "project": "demo-project",
  "bundle_id": "my.bundle@1-0",
  "conversation_id": "telegram_chat_12345",
  "turn_id": "turn_2026-06-18-12-00-00-000",
  "payload": {
    "source": "telegram",
    "telegram": {
      "chat_id": "12345",
      "update_id": "98765",
      "message_id": 222,
      "kdcube_user_id": "internal:telegram:12345",
      "role": "registered",
      "conversation_id": "telegram_chat_12345",
      "turn_id": "turn_2026-06-18-12-00-00-000"
    }
  },
  "external_events": [
    {
      "type": "event.user.prompt",
      "event_source_id": "telegram.user.prompt",
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

`external_events[]` is the model/context input. Telegram text and Telegram
attachments must be represented there so ReAct sees the same event model used
by browser transports.

## Processor-Side Wrapper

Every Telegram-capable bundle that accepts submitted Telegram turns must wrap
the real ReAct run:

```python
async def _run_react_surface() -> dict:
    return await workflow.process_main_turn(payload)

result = await telegram_user_admin.run_with_queued_telegram_delivery(
    self,
    runner=_run_react_surface,
)
```

`run_with_queued_telegram_delivery(...)` performs this exact sequence:

| Step | Behavior |
| --- | --- |
| Read metadata | Looks for `comm_context.request.payload["telegram"]`. If absent, returns `await runner()` unchanged. |
| Lock | Acquires a per-Telegram-conversation async lock. |
| Stream progress | Opens `TelegramActivityStreamer(...)` around `await runner()`. |
| Run bundle | Executes the supplied `runner`, which owns the ReAct workflow. |
| Final delivery | Calls `deliver_react_turn_to_telegram(...)` with the runner result, delivered file keys, progress message id, and progress summary. |
| Return result | Adds `result["telegram"] = {...delivery metadata...}` and returns the result. |

The helper does not create the assistant answer. It only wraps execution and
performs Telegram transport delivery from the bundle's result and turn log.

## Runner Result Contract

The runner should return a dictionary with these fields when available:

| Field | Meaning | Used by Telegram delivery |
| --- | --- | --- |
| `answer` or `final_answer` | Reduced final answer text selected by the bundle/workflow. | Preferred first by the Telegram renderer when non-empty. |
| `suggested_followups` or `followups` | Optional next-action text. | Preserved in returned result; renderer may ignore unless explicitly supported. |
| `turn_log` | Current turn payload with `blocks[]`. | Used to render answer blocks, sources, and files when needed. |
| `timeline` | Timeline-like payload. | Fallback if `turn_log` is absent. |

Current Telegram final rendering uses `render_react_turn_messages(...)`, which
passes `prefer_react_turn_answer=True`. Therefore:

```text
if result.answer/result.final_answer is non-empty:
    send that reduced answer text
else:
    collect answer blocks from result.turn_log/result.timeline
```

This means a bundle must not synthesize a generic fallback answer unless that is
really the intended user-facing answer. A generic fallback in `result.answer`
will hide useful assistant completion blocks that are present in the turn log.

## Activity Streaming During The Processor Run

`TelegramActivityStreamer` observes the chat communicator while `runner()`
executes. It can update a Telegram progress card and send files/citation/status
notifications according to the SDK implementation.

The activity stream is transport-level progress. It is not the same thing as
the final answer reducer. The final Telegram answer is still produced after
`runner()` returns by `deliver_react_turn_to_telegram(...)`.

The wrapper passes `delivered_file_keys` from the streamer to final delivery so
file artifacts already sent during progress streaming are not sent twice.

## What Not To Build In A Bundle

Do not add these patterns to a Telegram bundle:

| Wrong pattern | Why it is wrong |
| --- | --- |
| Webhook-side final answer relay | Duplicates the processor-side delivery wrapper and can race with queued execution. |
| Webhook-side ReAct run for normal registered users | Bypasses shared chat ingress, external-event ordering, and processor ownership. |
| Separate background subscriber just for Telegram answers | Splits delivery from the actual turn result and turn log. |
| Generic fallback answer in `result.answer` when answer blocks exist | Masks useful legal assistant completions from `turn_log`. |
| Telegram-specific model input outside `external_events[]` | Makes Telegram turns diverge from browser/event semantics. |

## Debugging Checklist

When Telegram delivery looks wrong, inspect these facts in order:

| Check | Expected |
| --- | --- |
| Webhook log | `telegram submitter result` exists for the `update_id`. |
| Ingress result | accepted turn has `payload.telegram` and `external_events[]`. |
| Processor log | bundle run path calls `run_with_queued_telegram_delivery(...)`. |
| Wrapper metadata | `_queued_telegram_meta(...)` finds `chat_id`, `update_id`, and `turn_id`. |
| Runner result | returned dict has either a correct `answer` or useful answer blocks in `turn_log.blocks[]`. |
| Renderer log | `telegram response rendered` shows message count and source `turn_log` or `timeline`. |
| Delivery log | `telegram delivery finished` shows sent count or Bot API error. |
