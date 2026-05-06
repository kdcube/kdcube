---
id: ks:docs/service/servicing-interfaces-README.md
title: "Service-Facing Chat Interfaces"
summary: "How bundle webhooks and service code submit chat events through the shared ingestion core, and how to mint short-lived signed links for direct artifact downloads."
tags: ["service", "chat", "webhooks", "bundles", "signed-links", "artifacts"]
keywords: ["chat submitter", "webhook chat ingress", "service-facing interface", "signed download link", "short-lived artifact link", "followup", "steer"]
see_also:
  - ks:docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/service/gateway-README.md
---

# Service-Facing Chat Interfaces

KDCube exposes two small service-facing helpers for code that is not the
browser SSE or Socket.IO chat client:

- `ChatIngressSubmitter` lets proc-hosted bundle APIs, webhooks, and service
  adapters submit a normal chat event through the same ingestion core as SSE
  and Socket.IO.
- `signed_links` lets bundle endpoints mint short-lived HMAC links for direct
  artifact downloads when the client cannot attach platform headers, for
  example Telegram Web Apps opening a file in the system browser.

These helpers are intentionally thin. Business logic stays in the bundle or
React workflow; the helpers only connect external service events to KDCube
chat and artifact serving boundaries.

## Chat Submitter

Import:

```python
from kdcube_ai_app.apps.chat.ingress.chat_core import IngressConfig, RawAttachment
from kdcube_ai_app.apps.chat.ingress.chat_submitter import ChatIngressSubmitter
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
```

Minimal webhook shape:

```python
submitter = ChatIngressSubmitter(app)

result = await submitter.submit(
    session=UserSession(
        session_id=f"telegram:{telegram_chat_id}",
        user_type=UserType.REGISTERED,
        user_id=kdcube_user_id,
        username=telegram_username,
    ),
    request_context=RequestContext(
        client_ip=client_ip,
        user_agent="telegram-webhook",
        user_timezone=user_timezone,
    ),
    message_data={
        "conversation_id": conversation_id,
        "bundle_id": "task-and-memo-app@1-0",
        "payload": {
            "source": "telegram",
            "provider_update_id": update_id,
        },
    },
    message_text=text,
    ingress=IngressConfig(
        transport="telegram",
        entrypoint="/public/telegram_webhook",
        component="chat.telegram",
        instance_id="task-and-memo-app@1-0",
        metadata={"source": "webhook.telegram"},
    ),
)
```

The submitter uses `app.state` resources from the proc web app:

- `chat_queue_manager`
- `chat_comm`
- `conversation_browser`
- `conversation_store`
- `redis_async`

It then calls the shared `process_chat_message(...)` path. This means webhook
traffic gets the same bundle resolution, accounting envelope, attachment
hosting, busy-conversation handling, and `followup` / `steer` continuation
semantics as the browser transports.

## Followup And Steer

Set the continuation intent in `message_data`:

```python
message_data = {
    "conversation_id": conversation_id,
    "message_kind": "followup",  # or "steer"
    "target_turn_id": active_turn_id,
    "payload": {"source": "telegram"},
}
```

Rules:

- `followup` adds more user work to the active turn when a live owner can
  consume it; otherwise it is promoted into a later normal turn.
- `steer` is a control event. It can interrupt or redirect the active turn
  when the active runtime supports cancellation.
- If `message_kind` is omitted and the conversation is busy, ingestion treats
  the message as a followup.

## Attachments

Pass transport-normalized files as `RawAttachment` values:

```python
raw_attachments = [
    RawAttachment(
        content=file_bytes,
        name="invoice.pdf",
        mime="application/pdf",
        meta={"source": "telegram", "file_id": telegram_file_id},
    )
]
```

The shared ingress core enforces size limits, optional AV preflight, and stores
accepted files as conversation artifacts before the task is enqueued.

## Short-Lived Signed Links

Use signed links when a client needs a direct `GET` URL and cannot reliably
send platform auth headers. Telegram Web Apps are the main example: downloading
a fetched blob and then forcing a local browser download is not reliable across
Telegram clients, while opening a normal URL is.

Import:

```python
from kdcube_ai_app.apps.chat.ingress.signed_links import (
    make_signed_link,
    make_signed_link_token,
    verify_signed_link_token,
)
```

Mint:

```python
artifact_ref = "exec:exec_20260506181000_ab12:artifact:report.pdf"
download_url = (
    "/api/integrations/bundles/demo-tenant/demo-project/task-and-memo-app@1-0/"
    "public/task_execution_artifact_download"
    f"?artifact_ref={artifact_ref}"
)

signed = make_signed_link(
    download_url,
    secret=artifact_download_secret,
    subject=artifact_ref,
    claims={"user_id": kdcube_user_id},
    ttl_seconds=900,
)

payload = {
    "download_url": signed.url,
    "expires_at": signed.expires_at,
}
```

Verify in the public download endpoint:

```python
claims_payload = verify_signed_link_token(
    artifact_download_secret,
    download_token,
    subject=artifact_ref,
)
user_id = claims_payload["claims"]["user_id"]
```

Security rules:

- The `subject` must be the exact resource being authorized, such as the
  artifact ref. Verification must pass the same subject.
- Claims are not secret. Put only routing and scoping data there, such as
  `user_id`, `tenant`, `project`, or `bundle_id`.
- The signing secret must come from deployment or bundle secrets, never from
  frontend config.
- Keep TTL short. Five to fifteen minutes is the usual range for browser or
  Telegram download links.
- Verification authorizes only the short-lived link. The endpoint should still
  resolve the artifact normally and enforce visibility rules such as
  user scope and `visibility == "visible"`.

## Operational Shape

```text
external provider/webhook
  -> bundle public API
  -> ChatIngressSubmitter
  -> shared chat ingestion core
  -> queue / active conversation continuation source
  -> processor / React workflow

widget or Telegram Web App
  -> authenticated operation lists visible artifacts
  -> operation returns signed direct URLs
  -> browser opens public GET URL with download_token
  -> endpoint verifies token and streams the file
```

The webhook response is an acknowledgement that the event was accepted or
rejected. It is not the assistant's final answer. Outbound delivery belongs to
the transport renderer, relay listener, or bundle-specific response channel.
