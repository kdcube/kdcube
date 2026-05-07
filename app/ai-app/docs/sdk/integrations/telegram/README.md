---
id: ks:docs/sdk/integrations/telegram/README.md
title: "Telegram SDK Integration"
summary: "Reusable Telegram transport helpers for KDCube bundles: Bot API rendering, attachment hydration, activity streaming, Mini App auth, chat submitter helpers, and signed download links."
tags: ["sdk", "integrations", "telegram", "webhooks", "mini-apps", "bundles"]
keywords: ["telegram bot", "telegram webhook", "telegram mini app", "telegram web app", "telegram activity streamer", "chat submitter", "signed download"]
see_also:
  - ks:docs/service/servicing-interfaces-README.md
  - ks:docs/sdk/integrations/email/README.md
---

# Telegram SDK Integration

The Telegram SDK integration contains reusable transport code that bundles can
import directly from:

```python
from kdcube_ai_app.apps.chat.sdk.integrations import telegram
```

The SDK owns Telegram protocol mechanics. The bundle still owns application
state: user registry, role policy, which conversation a Telegram chat is bound
to, and which workflow handles the submitted message.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.integrations.telegram
  bot.py              Telegram Bot API calls, update summaries, attachment hydration,
                      timeline rendering, Markdown/HTML normalization, file sends
  stream.py           TelegramActivityStreamer for live ReAct progress updates
                      and progress-card finalization
  router.py           generic React-turn-to-Telegram rendering and delivery
  webapp_auth.py      Telegram Mini App initData extraction and signature validation
  chat_submit.py      helpers for /steer, /followup, RawAttachment, UserSession,
                      RequestContext, and IngressConfig
  signed_downloads.py short-lived signed link helpers re-exported for Telegram clients
  user_admin.py       configurable Telegram user registry/admin, webhook
                      authorization and submitter orchestration, attachment hosting
  widget_auth.py      configurable Mini App identity resolver backed by a bundle registry
  widget_ops.py       configurable Mini App operations for conversations, tasks,
                      executions, and artifact downloads
  webapp.py           configurable payload composer for task/memory/settings/chat widgets
```

Keep these concerns separate when reusing the SDK:

```text
bot.py      Telegram protocol primitives
stream.py   live progress message/card mechanics
router.py   final React-turn delivery to Telegram
user_admin  user allow-list, role, conversation binding, webhook orchestration
widget_*    Telegram Mini App identity and operations
```

The lower-level modules are pure protocol helpers. The higher-level modules
(`user_admin.py`, `widget_auth.py`, `widget_ops.py`, `webapp.py`) are reusable
subsystems that must be configured by a bundle with its storage and policy hooks.

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

user_admin.configure_telegram_user_admin(
    storage_factory=lambda entrypoint: MyTelegramRegistry(storage_root(entrypoint)),
    storage_root_or_error=storage_root,
    migrate_telegram_user_to_kdcube_scope=migrate_user_scope,
    bundle_id="my.bundle@1-0",
)
```

## Webhook Flow

```text
Telegram update
  -> summarize_telegram_update(update)
  -> hydrate_telegram_attachments(...)
  -> bundle resolves Telegram user, role, conversation_id
  -> telegram_command_kind_and_text(text)
  -> raw_attachments_from_telegram(attachments)
  -> ChatIngressSubmitter.submit(...)
  -> shared chat_core processing
  -> ReAct workflow
  -> deliver_react_turn_to_telegram(...)
       -> render_react_turn_messages(...)
       -> deliver_messages_preserving_progress_card(...)
```

`ChatIngressSubmitter` is provided by the chat service layer. Telegram helpers
prepare the transport-specific inputs; the submitter sends the message through
the same ingestion core used by browser transports.

## Incoming Updates

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    hydrate_telegram_attachments,
    summarize_telegram_update,
)

summary = summarize_telegram_update(update)
attachments = await hydrate_telegram_attachments(
    attachments=list(summary.get("attachments") or []),
    bot_token=bot_token,
    message_id=summary.get("message_id"),
)
summary["attachments"] = attachments
```

The summary is log-safe and normalized around:

```text
update_id, update_type, message_id, chat_id, chat_type, user_id, username,
text, attachments[]
```

Hydrated file attachments are converted to the common bundle attachment shape:

```text
filename, mime/mime_type, size/size_bytes, base64, telegram_file_path, summary
```

## Followup And Steer

Telegram slash commands can map to chat continuation kinds:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import telegram_command_kind_and_text

message_kind, processed_text = telegram_command_kind_and_text(text)
```

Mapping:

```text
/followup <text>  -> message_kind="followup", text=<text>
/f <text>         -> message_kind="followup", text=<text>
/steer <text>     -> message_kind="steer", text=<text>
/s <text>         -> message_kind="steer", text=<text>
anything else     -> normal message
```

The bundle puts `message_kind` into `message_data` when calling
`ChatIngressSubmitter.submit(...)`.

## Submitter Helpers

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    raw_attachments_from_telegram,
    telegram_ingress_config,
    telegram_request_context,
    telegram_user_session,
)

request_context = telegram_request_context(timezone="UTC")
session = telegram_user_session(
    conversation_id=conversation_id,
    user_id=kdcube_user_id,
    username=telegram_username,
    role=role,
    request_context=request_context,
)
ingress = telegram_ingress_config(
    chat_id=chat_id,
    update_id=update_id,
    message_id=message_id,
)
raw_attachments = raw_attachments_from_telegram(attachments)
```

These helpers only create normalized chat-core inputs. They do not read bundle
storage, choose a conversation, authorize a user, or enqueue a workflow.

## Rendering Responses

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    deliver_react_turn_to_telegram,
)

delivery = await deliver_react_turn_to_telegram(
    bundle_id="my.bundle@1-0",
    bot_token=bot_token,
    chat_id=chat_id,
    update_id=update_id,
    react_turn=react_turn,
    delivered_file_keys=already_streamed_file_keys,
    progress_message_id=progress_message_id,
    progress_summary=progress_summary,
    send_responses=True,
)
```

The router converts a React turn log or timeline into Telegram-safe
`TelegramMessage` values, then sends them through the Bot API. It emits text
chunks, sources text, and visible file artifacts.

Use the lower-level calls only when a bundle needs custom delivery:

```python
messages = render_telegram_messages_from_timeline(...)
result = await send_telegram_messages(bot_token=bot_token, chat_id=chat_id, messages=messages)
```

The sender handles:

- Telegram text length limits
- Markdown-to-Telegram-HTML normalization
- URL-based document/photo sends
- local or hosted file uploads
- Telegram-safe response logging

## Progress Card Finalization

Long ReAct turns can stream into a single Telegram progress card. The final
answer must not replace that card. The stream module owns this behavior:

```text
while turn runs:
  TelegramActivityStreamer edits one progress message
  progress card contains status, notes, thinking, sources, file notifications

when turn completes:
  deliver_react_turn_to_telegram(...)
    -> if final text fits:
         edit same progress message:
           <existing progress>
           <Final response>
           <answer>
         send only remaining file messages
    -> if final text does not fit:
         keep progress card with a short handoff note
         send full final text as normal follow-up messages
```

`progress_message_id` and `progress_summary` come from
`TelegramActivityStreamer.progress_message_id()` and
`TelegramActivityStreamer.progress_summary()`. File messages that were already
streamed are excluded by passing `delivered_file_keys`.

## Activity Streaming

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramActivityStreamer

async with TelegramActivityStreamer(
    comm=chat_comm,
    bot_token=bot_token,
    chat_id=chat_id,
) as streamer:
    result = await run_workflow(...)

delivered = streamer.delivered_file_keys()
progress_message_id = streamer.progress_message_id()
progress_summary = streamer.progress_summary()
```

`TelegramActivityStreamer` listens to chat communicator activity and updates a
single Telegram progress message while the turn runs. It is useful for long
ReAct turns where the final answer may take minutes.

Thinking and note deltas are rendered as Telegram HTML blockquotes. The streamer
tracks already-sent file keys so final delivery does not duplicate files that
were emitted during the turn.

## Mini App Auth

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    extract_telegram_init_data_from_request,
    validate_telegram_init_data,
)

init_data = extract_telegram_init_data_from_request(request)
verified = validate_telegram_init_data(
    init_data,
    bot_token=bot_token,
    max_age_seconds=86400,
)

telegram_user = verified.user
params = verified.params
```

The SDK validates Telegram Mini App `initData` signatures with the bot token and
checks optional age bounds. The bundle then maps the Telegram user to its own
KDCube user and role.

## Signed Downloads

Telegram Web Apps cannot reliably download generated blobs in every client.
Use short-lived signed links for direct browser downloads:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    make_signed_link,
    verify_signed_link_token,
)

signed = make_signed_link(
    download_url,
    secret=artifact_download_secret,
    subject=artifact_ref,
    claims={"user_id": kdcube_user_id},
    ttl_seconds=900,
)

payload = {"download_url": signed.url, "expires_at": signed.expires_at}
```

The public download endpoint verifies the token with the same `subject`.

## Signed Download Request Flow

Generated files that must be downloaded from a Telegram Mini App should be
served through a backend URL, not by buffering a browser-side Blob. Telegram
web clients are stricter than normal browsers, so the final response must be an
attachment response with Telegram-compatible CORS headers.

```text
Telegram Mini App
  user clicks "Download"
    |
    | GET <download_url>?artifact_ref=...&download_token=...
    v
KDCube public bundle operation route
  /api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/
    telegram_task_execution_artifact_download
    |
    | loads bundle workflow and invokes operation alias
    v
Bundle entrypoint
  telegram_task_execution_artifact_download(...)
    |
    | delegates transport auth / token auth
    v
Bundle Telegram widget subsystem
  download_execution_artifact(...)
    |
    | if download_token:
    |   verify signed token subject == artifact_ref
    | else:
    |   validate Telegram Mini App initData
    v
Bundle task operations
  download_execution_artifact(...)
    |
    | resolve execution_id from artifact_ref when needed
    | load execution record
    | read only user-visible file artifacts
    v
Bundle artifact reader
  read_execution_artifact_for_download(...)
    |
    | returns bytes, filename, mime_type
    v
BundleBinaryResponse
  content=<bytes>
  filename=<file_name>
  media_type=<mime_type>
  headers={
    "Access-Control-Allow-Origin": "https://web.telegram.org",
    "Access-Control-Expose-Headers": "Content-Disposition"
  }
    |
    | KDCube coerces BundleBinaryResponse to HTTP response
    v
HTTP response to Telegram Web App
  Content-Type: <mime_type>
  Content-Disposition: attachment; filename="<file_name>"
  Access-Control-Allow-Origin: https://web.telegram.org
  Access-Control-Expose-Headers: Content-Disposition
```

`Content-Disposition` is what makes the browser treat the response as a file
download. `Access-Control-Allow-Origin: https://web.telegram.org` is required
for consistent Telegram Web App behavior on web clients, and
`Access-Control-Expose-Headers: Content-Disposition` lets the client inspect
the filename header when it needs to. If more Telegram origins are supported
later, the backend should return the matched Telegram origin and include
`Vary: Origin`.

`widget_ops.py` adds the Telegram Web origin/exposed-header behavior around
bundle binary artifact responses. The endpoint that serves the bytes remains
responsible for `Content-Disposition`.

## Bundle Boundary

The protocol helpers do not decide:

- which Telegram users are allowed
- which KDCube user id a Telegram user maps to
- which conversation is connected to a Telegram chat
- whether a message should be ignored as duplicate
- which bundle workflow handles the submitted chat event

The configurable subsystem modules call the hooks that a bundle provides for
those decisions. That keeps the reusable transport and workflow glue in the SDK
while preserving bundle ownership of registry schema, migration policy, roles,
task operations, and widget composition.
