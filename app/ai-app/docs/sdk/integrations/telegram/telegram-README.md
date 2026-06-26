---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
title: "Telegram SDK Integration"
summary: "Reusable Telegram transport helpers for KDCube bundles: Bot API rendering, attachment hydration, activity streaming, Mini App auth, chat submitter helpers, and signed download links."
tags: ["sdk", "integrations", "telegram", "webhooks", "mini-apps", "bundles"]
keywords: ["telegram bot", "telegram webhook", "telegram mini app", "telegram web app", "telegram activity streamer", "chat submitter", "signed download"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-webhook-submit-and-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/servicing-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
---

# Telegram SDK Integration

The Telegram SDK integration contains reusable transport code that bundles can
import directly from:

```python
from kdcube_ai_app.apps.chat.sdk.integrations import telegram
```

The SDK owns Telegram protocol mechanics and the reusable Telegram user
registry store. The bundle still owns application policy: where the registry is
stored, which roles are allowed, which conversation a Telegram chat is bound to,
which entrypoint/message handler handles the submitted message, and how the
Telegram update becomes conversation `external_events[]`.

External BotFather, webhook, public URL, and Mini App setup is documented
separately in `telegram-external-prereq-README.md`. Keep this article focused
on the SDK surface and bundle integration points.

## Bundle Wiring Checklist

Use this checklist when adding Telegram to a bundle. Do not start from a
generic public webhook unless the bundle intentionally does not use the
Telegram SDK.

### 1. Decide Which Telegram Surfaces The Bundle Exposes

Common combinations:

| Surface | Bundle route | Auth boundary | SDK subsystem |
| --- | --- | --- | --- |
| Telegram chat webhook | `@api(route="public", alias="telegram_webhook", method="POST")` | `X-Telegram-Bot-Api-Secret-Token` header secret | `user_admin.handle_webhook(...)` |
| Telegram Mini App static shell | `@ui_widget(alias="<widget_alias>")` plus `ui.widgets.<widget_alias>`; launch URL uses `/public/widgets/<widget_alias>/` | public static load; every data/action API verifies Telegram `initData` | source-folder widget build, `webapp`, `widget_auth`, `widget_ops` |
| Telegram Mini App data/actions | `@api(route="public", alias="telegram_*", method=...)` | Telegram WebApp `initData` verified inside each handler | `widget_auth`, `widget_ops`, `webapp` |
| Telegram user registry/admin from KDCube UI | `@api(route="operations", alias="telegram_user_admin_*")` | KDCube-authenticated operations role policy | `user_admin.payload/upsert/delete(...)` |
| Generated artifact downloads from Mini App | public download/action alias | signed link or Telegram `initData` | `widget_ops.download_execution_artifact(...)` |

Keep business behavior in the main workflow/tools. Telegram routes should
authorize, normalize, submit, and deliver; they should not duplicate task,
memory, or product logic.

If the user asks for "Telegram integration" without mentioning a Mini App,
start with the bot transport path: `telegram_webhook` plus
`user_admin.handle_webhook(...)` and
`user_admin.run_with_queued_telegram_delivery(...)`. The versatile reference
bundle demonstrates that bot transport and also includes a compact Mini App
reference (`ui/widgets/telegram_miniapp`) for memory canvas, chat channel
selection, and Telegram admin. Add Mini App APIs only when the product also
needs Telegram-hosted controls.

### 2. Configure The Reusable SDK Subsystems In `entrypoint.py`

For bot transport only, the bundle imports and configures `user_admin` once at
module load:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin as telegram_user_admin

BUNDLE_ID = "my.bundle@1-0"
TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEGRAM_WEBHOOK_PUBLIC_AUTH = {
    "mode": "header_secret",
    "header": TELEGRAM_WEBHOOK_SECRET_HEADER,
    "secret_key": "integrations.telegram.webhook_secret",
}


def _telegram_user_admin_storage(entrypoint):
    return TelegramUserAdminStorage(storage_root_or_error(entrypoint))


telegram_user_admin.configure_telegram_user_admin(
    storage_factory=_telegram_user_admin_storage,
    storage_root_or_error=storage_root_or_error,
    migrate_telegram_user_to_kdcube_scope=migrate_telegram_user_to_kdcube_scope,
    bundle_id=BUNDLE_ID,
)
```

If the bundle also exposes Telegram Mini App controls, configure the Mini App
helpers after `user_admin`:

```python
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import webapp
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_ops as telegram_widget_ops

telegram_widget_auth.configure_telegram_widget_auth(
    storage_for=telegram_user_admin.storage,
    bot_token=telegram_user_admin.bot_token,
    bundle_id=BUNDLE_ID,
)
webapp.configure_telegram_webapp(
    memory_widgets_module=memory_widgets,
    settings_widgets_module=settings_widgets,
    automation_widgets_module=automation_widgets,
    telegram_user_admin_module=telegram_user_admin,
    bundle_id=BUNDLE_ID,
)
telegram_widget_ops.configure_telegram_widget_ops(
    automation_operations_module=automation_operations,
    telegram_user_admin_module=telegram_user_admin,
    telegram_widget_auth_module=telegram_widget_auth,
    webapp_module=webapp,
    bundle_id=BUNDLE_ID,
)
```

The concrete modules passed into `webapp` and `widget_ops` depend on the
bundle. An automation bundle may pass automation widget modules; a simpler
chat-only bundle may expose fewer Mini App aliases.

### 3. Add Descriptor-Backed Defaults

The bundle should declare safe defaults in `configuration_defaults()`:

```python
def configuration_defaults(self):
    return {
        "enabled": {
            "api": {
                "telegram_webhook.POST": False,
                "telegram_profile.GET": False,
                "telegram_conversations_list.GET": False,
            },
        },
        "integrations": {
            "telegram": {
                "enabled": False,
                "webhook_url": "",
                "send_responses": True,
                "stream_activity": True,
                "stream_activity_display": True,
                "web_app_auth_max_age_seconds": 86400,
            },
        },
    }
```

Keep Telegram disabled by default unless the reference bundle is explicitly a
Telegram-first example. Deployment descriptors or Bundle Admin can enable the
specific public APIs that are safe for that environment.

### 4. Expose A Thin Webhook Handler

The webhook handler should delegate to the SDK user-admin subsystem:

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

The configured `public_auth` checks Telegram's header secret before the handler
runs. `handle_webhook(...)` owns duplicate update claims, registry lookup,
conversation binding, chat ingress submission, activity streaming, and final
Telegram delivery through the configured helpers.

### 5. Expose Mini App Operations Only Through `initData` Verification

Telegram Mini App operation routes are platform-public, but each handler must
delegate to SDK code that verifies raw Telegram `initData`:

```python
TELEGRAM_WEBAPP_PUBLIC_AUTH = "none"

@api(method="GET", alias="telegram_profile", route="public", public_auth=TELEGRAM_WEBAPP_PUBLIC_AUTH)
async def telegram_profile(self, request=None, telegram_init_data: str = "", **kwargs):
    del kwargs
    return await telegram_widget_ops.profile(
        self,
        request=request,
        telegram_init_data=telegram_init_data,
    )
```

The public route is not the trust boundary. The trust boundary is Telegram
`initData` validation inside `widget_auth` / `widget_ops`, using the configured
bot token and max-age policy. Do not trust caller-supplied `user_id`,
conversation id, role, or fingerprint from a Telegram Mini App request.

### 5.1 Serve The Mini App Shell Through The Public Widget Route

The Telegram Mini App browser must open the built widget shell through the
public static widget route:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/widgets/<WIDGET_ALIAS>/
```

There is no `public` flag on `@ui_widget(...)`. The decorator declares the
widget surface once; `ui.widgets.<WIDGET_ALIAS>` tells the loader how to build
the source-folder app. After the widget is built, KDCube serves the same static
app through both route families:

```text
/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/widgets/<WIDGET_ALIAS>/
/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/widgets/<WIDGET_ALIAS>/
```

Use `/widgets/...` from the KDCube-authenticated control plane. Use
`/public/widgets/...` as the BotFather menu button or Mini App URL.

The public widget route only loads static HTML, JS, CSS, and assets. The Mini
App must call bundle public API aliases for data/actions, and those handlers
must verify `window.Telegram.WebApp.initData`.

Widget visibility still applies to the static route. A Telegram Mini App widget
must not be restricted to KDCube-only user types or roles. For config-driven
visibility, leave the widget visibility unset or allow the public/anonymous
session that the public widget route uses.

Reference implementation:

```text
app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py
app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/widgets/telegram_miniapp
```

### 6. Keep Admin Operations Separate

KDCube operations APIs that manage the Telegram registry should be separate
from Telegram public APIs:

```python
@api(
    method="POST",
    alias="telegram_user_admin_data",
    route="operations",
    roles=("kdcube:role:super-admin",),
    roles_config="visibility.api.telegram_user_admin_data.roles",
    user_types_config="visibility.api.telegram_user_admin_data.user_types",
)
async def telegram_user_admin_data(self, **kwargs):
    return telegram_user_admin.payload(self)
```

Expose only the minimum admin operations needed by the bundle UI. If the bundle
uses configurable roles/user types, declare the corresponding `roles_config`
and `user_types_config` paths in the decorators.

### 7. Configure Deployment Values And External Provider State

Deployment config:

```yaml
integrations:
  telegram:
    enabled: true
    webhook_url: "https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/telegram_webhook"
    send_responses: true
    stream_activity: true
    stream_activity_display: true
    web_app_auth_max_age_seconds: 86400
```

Deployment secrets:

```yaml
integrations:
  telegram:
    bot_token: "<TELEGRAM_BOT_TOKEN>"
    webhook_secret: "<TELEGRAM_WEBHOOK_SECRET>"
```

For local development where Telegram must call a localhost KDCube, use
[Serving Local KDCube With Ngrok](../../../service/cicd/ngrok-README.md).
Use one public HTTPS origin through the local reverse proxy; do not expose proc
as a separate public URL.

### 8. Test The Telegram Boundary

Before calling the bundle done, prove:

- `enabled.api.public.telegram_webhook.POST` and any `telegram_*` Mini App APIs are
  explicitly enabled for the test deployment
- the BotFather Mini App/menu button URL uses `/public/widgets/<widget_alias>/`,
  not `/widgets/<widget_alias>/`
- the webhook rejects missing or wrong `X-Telegram-Bot-Api-Secret-Token`
- `setWebhook` points at the active public URL
- a Telegram user can be recorded, approved/mapped, and bound to a conversation
- a chat update submits through the shared chat ingress and receives a final
  Telegram delivery
- Mini App APIs reject invalid or stale `initData`
- generated downloads use signed links or verified `initData` and return
  Telegram-compatible attachment headers

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
  user_storage.py     file-backed TelegramUserAdminStorage registry for user
                      roles, KDCube user mapping, conversation binding, and
                      webhook update-id claims
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
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramUserAdminStorage,
    user_admin,
)

user_admin.configure_telegram_user_admin(
    storage_factory=lambda entrypoint: TelegramUserAdminStorage(storage_root(entrypoint)),
    storage_root_or_error=storage_root,
    migrate_telegram_user_to_kdcube_scope=migrate_user_scope,
    bundle_id="my.bundle@1-0",
)
```

`TelegramUserAdminStorage` is SDK-owned. Bundles should not keep a copy of the
class. If old bundle code imports a local `TelegramUserAdminStorage`, keep only
a compatibility re-export.

The registry stores:

```text
telegram_user_id, telegram_chat_id, telegram_username
kdcube_user_id, role
active conversation_id and conversation list
webhook update claim/completion/failure state
```

The registry is intentionally storage-only. Authorization and routing happen in
`user_admin.py`, `widget_auth.py`, or the bundle policy that configures them.

## Webhook Flow

For the full boundary diagram, see
`telegram-webhook-submit-and-delivery-README.md`.

```text
Telegram update
  -> summarize_telegram_update(update)
  -> hydrate_telegram_attachments(...)
  -> bundle resolves Telegram user, role, conversation_id
  -> acquire per-conversation Telegram turn lock
  -> telegram_command_kind_and_text(text)
  -> raw_attachments_from_telegram(attachments)
  -> ChatIngressSubmitter.submit(...)
       message_data.external_events[] contains event.user.*
  -> shared conversation ingress processing
  -> ReAct workflow
  -> deliver_react_turn_to_telegram(...)
       -> render_react_turn_messages(...)
       -> deliver_messages_preserving_progress_card(...)
```

`ChatIngressSubmitter` is provided by the chat service layer. Telegram helpers
prepare the transport-specific inputs; the submitter sends the message through
the same ingestion core used by browser transports.

Telegram is not a separate downstream request shape. By the time a Telegram
turn reaches conversation ingress, user text and Telegram files are represented
as the same plural event batch used by every other client:

```json
{
  "conversation_id": "conv-main",
  "turn_id": "turn_2026-06-05-10-00-00-000",
  "payload": {
    "source": "telegram",
    "telegram": {
      "chat_id": "12345",
      "update_id": "98765",
      "turn_id": "turn_2026-06-05-10-00-00-000"
    }
  },
  "external_events": [
    {
      "event_id": "telegram.prompt.abc123",
      "type": "event.user.prompt",
      "event_source_id": "telegram.user.prompt",
      "logical_path": "ev:turn_2026-06-05-10-00-00-000.events/telegram.prompt.abc123",
      "reactive": true,
      "agent_id": "react",
      "payload": {
        "mime": "text/plain",
        "event": {"text": "hello from telegram"}
      }
    }
  ]
}
```

Telegram attachments are additional `event.user.attachment` entries in the same
`external_events[]` array. When a Telegram update contains attachments without
text, the attachment event is reactive and the SDK adds explicit prompt text
asking the agent to inspect the attachments and ask a focused follow-up if the
user's intent is unclear.

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

command_kind, processed_text = telegram_command_kind_and_text(text)
```

Mapping:

```text
/followup <text>  -> event.user.followup
/f <text>         -> event.user.followup
/steer <text>     -> event.user.steer
/s <text>         -> event.user.steer
anything else     -> event.user.prompt
```

The SDK maps the command into the event type before calling
`ChatIngressSubmitter.submit(...)`. Do not send a top-level text scalar as the
authoritative request. The authoritative request is the plural
`message_data.external_events[]` batch.

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

These helpers only create normalized chat ingress inputs. They do not read bundle
storage, choose a conversation, authorize a user, or enqueue a workflow.

For Telegram webhook turns that go through the queue, `submit_react_turn(...)`
must pass `message_data.external_events[]` to the shared ingress submitter.
Without that event batch, ingress rejects the submission as
`missing_external_events` before a workflow can be enqueued.

## Queued Delivery Boundary

This section is a summary. The canonical runtime boundary explanation is
`telegram-webhook-submit-and-delivery-README.md`.

Queued Telegram turns have two distinct phases:

```text
webhook request
  -> submit_react_turn(...)
       stores Telegram metadata under request.payload.telegram
       submits external_events[] through ChatIngressSubmitter
  -> returns accepted/rejected webhook acknowledgement

processor turn
  -> bundle entrypoint run path wraps the real ReAct runner with
     telegram_user_admin.run_with_queued_telegram_delivery(...)
       -> TelegramActivityStreamer streams progress for that turn
       -> deliver_react_turn_to_telegram(...) sends the final rendered result
```

The webhook should not spawn a separate relay-subscriber task to deliver the
final answer. Final delivery belongs to the processor-side queued-delivery
wrapper because that code sees the actual turn result, turn log, timeline,
streamed-file de-duplication keys, and configured `send_responses` policy.
A webhook-side background delivery path can duplicate messages for bundles that
already use the wrapper and can bypass the normal ReAct rendering path.

Reference bundles that support Telegram queued delivery should wrap their run
method, for example:

```python
res = await telegram_user_admin.run_with_queued_telegram_delivery(
    self,
    runner=lambda: super().run(*args, **kwargs),
)
```

If a bundle accepts Telegram webhook turns but never delivers the final answer,
first check whether its processor run path uses
`run_with_queued_telegram_delivery(...)`. Do not add a second delivery loop to
`handle_webhook(...)`.

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
    turn_id=turn_id,
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

Two bundle properties control this behavior:

| Property | Meaning |
| --- | --- |
| `integrations.telegram.stream_activity` | Enables the Telegram activity streamer. When this is `false`, no live progress or live file delivery is sent by the streamer. Final delivery can still run after the turn according to `send_responses`. |
| `integrations.telegram.stream_activity_display` | Controls the progress display inside the streamer. When this is `false`, the streamer suppresses progress/status/thinking/source messages but still delivers `chat.files` events and `chat.error` messages. Use this when Telegram should stay quiet during long turns but files produced during the turn must still be delivered immediately and de-duplicated from final delivery. |

When `turn_id` is provided, the streamer ignores activity whose
`conversation.turn_id` belongs to another turn. This matters because the relay
channel is conversation-scoped: two overlapping turns in one Telegram
conversation must not write progress into each other's cards. The webhook and
queued-delivery helpers also use a per-conversation async lock so Telegram
turns for the same conversation are processed and finalized in order.

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
    | loads bundle entrypoint and invokes operation alias
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
- which bundle entrypoint handles the submitted chat event

The configurable subsystem modules call the hooks that a bundle provides for
those decisions. That keeps the reusable transport and workflow glue in the SDK
while preserving bundle ownership of registry schema, migration policy, roles,
task operations, and widget composition.
