---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
title: "App Conversation Events And ReAct Output"
summary: "App contract for submitting conversation events into the platform event lane and consuming ReAct output from timeline/turn-log blocks instead of private runtime state."
status: active
tags: ["sdk", "app", "bundle-legacy-path", "events", "react", "timeline", "telegram", "webhooks", "integration"]
updated_at: 2026-07-06
keywords:
  [
    "app conversation events",
    "app react output",
    "chat submitter",
    "ExternalEventPayload",
    "external_events[]",
    "react timeline reducer",
    "telegram delivery",
    "state final_answer",
    "turn log",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/README.md
---
# App Conversation Events And ReAct Output

This page is the app contract for two operations:

1. sending conversation events into the platform event lane
2. reducing ReAct output for a non-browser delivery surface

These are separate contracts. Events are inputs. ReAct timeline/turn-log blocks
are outputs.

For the wider architecture that connects conversation events to scene surfaces,
Pinboard, named-service providers, and ReAct object materialization, read
[Components Ecosystem Architecture](../solutions/ecosystem-component/components-ecosystem-README.md).

## Ownership Boundary

| Area | Owner | App responsibility |
| --- | --- | --- |
| Transport authentication and user/session resolution | Platform ingress or platform integration component | Pass the request through the supported submitter/ingress contract. |
| `turn_id` minting for chat ingress | Platform transport adapter or platform submitter | Do not invent a second turn id after calling submitter. |
| `ExternalEventPayload` and ready wake package | Platform ingress core | Provide `external_events[]` and payload metadata; do not push directly to the ready queue. |
| Redis conversation event lane | Platform event bus | Do not write lane state directly from app code. |
| Event semantics | App or SDK subsystem that owns the event source | Use stable `event_source_id`, payload shape, and policy/reader registration. |
| ReAct runtime turn and timeline | Platform ReAct runtime | Consume public timeline/turn-log output. |
| Delivery to a custom channel | App adapter | Reduce timeline blocks into the channel format. |

## Submit Events Through Ingress

Browser clients normally submit through SSE or Socket.IO chat endpoints. Backend
webhooks, Telegram adapters, or scheduled jobs that want conversation semantics
should submit through the injected chat submitter or the same ingress core.

Do not submit conversation work by writing directly to:

- ready queues
- Redis event lane keys
- lane-state records
- `conv_messages`

Those are platform internals.

## Event Batch Shape

A batch is a list:

```text
external_events[]
```

The batch can include user text, attachments, UI selections, subsystem objects,
or domain events. Ingress stamps fields that callers may omit:

| Field | Caller may provide | Ingress guarantees |
| --- | --- | --- |
| `event_id` | Optional for authored events. | Present on accepted occurrence. |
| `batch_id` | Optional. | Present. All events in one submission group share a batch id. |
| `timestamp` | Optional. | Present on accepted occurrence. |
| `logical_path` | Optional for many callers. | Present when stored as an event occurrence. |
| `sequence` | No. | Present after lane publish. |

If the user sends text plus files plus context objects in one followup, those
items belong to one batch. Rendering policy may show them under one followup
section, but each item remains a separate event occurrence.

## Minimal Submitted Conversation Event

The exact helper depends on the integration, but the shape is the same.

```python
turn_id = new_turn_id()

message_data = {
    "tenant": tenant,
    "project": project,
    "bundle_id": bundle_id,
    "conversation_id": conversation_id,
    "turn_id": turn_id,
    "payload": {
        "target": {"agent_id": "default.react.agent"},
    },
    "external_events": [
        {
            "type": "event.user.prompt",
            "event_source_id": "event.user.prompt",
            "reactive": True,
            "payload": {
                "mime": "text/plain",
                "event": {"text": text},
            },
        }
    ],
}

result = await entrypoint.chat_submitter.submit(
    session=session,
    request_context=request_context,
    message_data=message_data,
    message_text=text,
    ingress=ingress_config,
    raw_attachments=raw_attachments,
)
```

Inside the chat processor, `entrypoint.chat_submitter` and
`entrypoint.conversation_browser` are bound on every call path that reaches the
app: bundle REST operations, local peer operations, and local named-service
calls. A named service can therefore submit a turn the same way an operation
does. A service the workflow already holds is kept.

The submitter calls the platform ingress path. The result's `turn_id` /
`queued_turn_id` is the processing turn id accepted for the batch. It does not
mean the UI must create a visible new turn immediately; if there is a live owner
the event can fold into the active turn.

## Telegram Pattern

Telegram uses this submitted path when possible:

```text
Telegram webhook
        |
        v
handle_webhook(...)
        |
        v
submit_telegram_turn(...)
        |
        v
entrypoint.chat_submitter.submit(...)
        |
        v
platform ingress -> Redis lane -> processor -> app workflow
```

When processor later runs the queued turn, the app uses the Telegram
delivery wrapper:

```text
processor-side app run
        |
        v
run_with_queued_telegram_delivery(entrypoint, runner=...)
        |
        +-- TelegramActivityStreamer observes progress
        |
        +-- runner() runs ReAct, LangGraph, CrewAI, or custom async code
            and returns the app's turn result
        |
        v
deliver_turn_to_telegram(...)
```

Telegram has no inline agent fallback. If shared ingress is unavailable, it
sends a transport-level retry response and executes no app workflow:

```text
handle_webhook(...)
  submit_telegram_turn(...) reports unavailable
  -> send retry response
  -> do not create or run a turn
```

An app author integrating Telegram should not infer conversation semantics
from the word "resubmitter". The deciding boundary is whether the code called
the platform submitter and received an ingress result. User-visible conversation
work always crosses that boundary.

## Scheduled Jobs And Backend Webhooks

Use the same rule:

| Need | Correct path |
| --- | --- |
| Add a user-visible event to an existing conversation and let ReAct respond. | Submit `external_events[]` through the platform submitter/ingress path. |
| Run private work and later post a summary or artifact. | Run an inline/job ReAct surface and then publish the job result explicitly. |
| Update a widget without waking ReAct. | Submit a non-reactive external event or write to the widget's own storage/API. |

Do not mix both paths for the same user action. A webhook that both submits a
conversation event and runs ReAct inline will duplicate or race the work.

## Consume ReAct Output From Timeline Blocks

ReAct output is the turn timeline, not the private runtime state.

Use:

```text
turn_log.blocks
conv.timeline.v1 blocks
stream events derived from blocks
```

Do not use:

```text
state["final_answer"] as the authoritative answer stream
```

`state["final_answer"]` may be useful as a compatibility last-answer field, but
it is not the contract for delivery. One turn can legally contain multiple
assistant completions when followups extend the turn. A reducer must decide how
to handle those blocks.

## Output Reducer Shape

A backend delivery adapter should reduce blocks explicitly:

```python
messages = []

for block in turn_log.get("blocks", []):
    block_type = block.get("type")
    text = str(block.get("text") or "").strip()

    if block_type == "assistant.completion" and text:
        messages.append({"role": "assistant", "text": text})

    if block_type in {"user.prompt", "user.followup"} and text:
        # Optional for audit or mirrored channel history.
        messages.append({"role": "user", "text": text})

    if block_type in {"artifact.file", "react.artifact"}:
        # Optional: translate files to the target channel's attachment model.
        pass
```

The browser chat widget already consumes stream envelopes. Non-browser
integrations such as Telegram should use a channel-specific reducer.

### Connected-Account Consent Output

When a ReAct tool cannot proceed because the user must connect, reconnect, or
approve an external account in Connection Hub, the tool/runtime should expose a
structured payload with:

```json
{
  "error": {
    "code": "needs_connected_account_consent",
    "message": "Connect or approve the required external account in Connection Hub.",
    "action_label": "Open Connection Hub",
    "action_url": "/api/integrations/bundles/.../connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube"
  },
  "consent": {
    "kind": "delegated_to_kdcube.connected_account",
    "provider_id": "google",
    "connector_app_id": "gmail",
    "claims": ["gmail:read"],
    "url": "/api/integrations/bundles/.../connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube",
    "action_label": "Open Connection Hub"
  },
  "action_label": "Open Connection Hub",
  "action_url": "/api/integrations/bundles/.../connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube"
}
```

Browser chat reduces this to a composer banner. A non-browser delivery adapter
should reduce the same payload to a channel-native action, for example a message
with the provider name, required claim, and a link from `action_url` or
`consent.url`. It should not turn this into a generic assistant failure such as
"try again later".

## Existing Reducers And Examples

| Example | What to inspect |
| --- | --- |
| `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/telegram/user_admin.py` | Submitted Telegram path, direct transport responses, and queued delivery boundary. |
| `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/telegram` | Telegram timeline-to-message rendering helpers. |
| `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/tasks/operations.py` | Task execution delivering ReAct results through Telegram rendering. |
| `repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/workspace@2026-03-31-13-36/agents/main.py` | Normal app ReAct construction and workspace persistence; the path keeps the historical `bundles` directory name. |

## Indexing Boundary

`conv_messages` indexing is a content projection from accepted timeline/TurnLog
blocks. ReAct writes it during rich finalization; the framework-neutral minimal
recorder derives the user and final-assistant rows from the same TurnLog payload:

| Row type | Source |
| --- | --- |
| `role='user'` | `user.prompt`, `user.followup`, and related materialized user blocks. |
| `role='assistant'` | `assistant.completion` blocks. |
| `role='artifact'` | Turn and conversation artifact records. |
| `chat:summary` | Working summary blocks when the finalization path produced them. |

If an accepted event exists in the lane but no user block appears in `turn.log`,
the issue is event materialization/policy. If the user block appears in
`turn.log` but no user row appears in `conv_messages`, the issue is indexing or
finalization.

## Checklist

- Submit conversation events as `external_events[]`.
- Let ingress stamp event occurrence fields.
- Treat ready-queue entries as wakeups only.
- Treat `ExternalEventPayload.routing.turn_id` as the effective runtime turn id.
- Consume ReAct output from timeline/turn-log blocks.
- Preserve or intentionally reduce multiple `assistant.completion` blocks.
- Keep channel delivery code outside the ReAct runtime state internals.
